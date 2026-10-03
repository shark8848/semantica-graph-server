"""MCP 协议面：stdio JSON-RPC 服务（MCP 最小实现）。

与 semantica 自带 mcp_server 同思路，但只依赖引擎 application 层，
避开当前 venv 中损坏的 semantica.context/vector_store 导入链。
支持 initialize / tools/list / tools/call。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from ikc_sdk.core.trace import normalize_trace_id

from ..errors import GraphEngineError
from ..protocol import error, ok
from ..runtime import get_service

SERVER_INFO = {"name": "graph-engine", "version": "0.1.0"}
PROTOCOL_VERSION = "2025-03-26"


def _text_content(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}]


def _flag(value: Any) -> bool | None:
    """宽松布尔解析：None 保持 None（走服务端 env 缺省）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


TOOLS: list[dict[str, Any]] = [
    {
        "name": "graph_create",
        "description": "创建图谱（含 graphSchema 校验）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graphId": {"type": "string"},
                "name": {"type": "string"},
                "kbId": {"type": "string"},
                "tenantId": {"type": "string"},
                "ownerId": {"type": "string"},
                "graphSchema": {"type": "object"},
            },
        },
    },
    {"name": "graph_list", "description": "列出图谱", "inputSchema": {"type": "object", "properties": {"tenantId": {"type": "string"}, "ownerId": {"type": "string"}}}},
    {"name": "graph_get", "description": "查询图谱元信息", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}}, "required": ["graphId"]}},
    {"name": "graph_delete", "description": "删除图谱", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}}, "required": ["graphId"]}},
    {"name": "graph_stat", "description": "图谱统计（含 schema 覆盖率）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}}, "required": ["graphId"]}},
    {"name": "graph_build", "description": "建图（text 或 entities/relations 记录；text 时可传 llm=true 开启 LLM 实体增强）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "text": {"type": "string"}, "docId": {"type": "string"}, "title": {"type": "string"}, "llm": {"type": "boolean"}, "entities": {"type": "array"}, "relations": {"type": "array"}}, "required": ["graphId"]}},
    {"name": "graph_merge", "description": "增量合并实体/关系", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "entities": {"type": "array"}, "relations": {"type": "array"}, "docId": {"type": "string"}}, "required": ["graphId"]}},
    {"name": "graph_deprecate_doc", "description": "按 docId 增量废弃", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "docId": {"type": "string"}}, "required": ["graphId", "docId"]}},
    {"name": "graph_nodes", "description": "分页查询实体节点", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "entityType": {"type": "string"}, "name": {"type": "string"}, "page": {"type": "integer"}, "pageSize": {"type": "integer"}}, "required": ["graphId"]}},
    {"name": "graph_edges", "description": "分页查询关系边", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "relationType": {"type": "string"}, "page": {"type": "integer"}, "pageSize": {"type": "integer"}}, "required": ["graphId"]}},
    {"name": "graph_neighbors", "description": "实体邻域（depth 1/2）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "entityId": {"type": "string"}, "depth": {"type": "integer"}}, "required": ["graphId", "entityId"]}},
    {"name": "graph_paths", "description": "最短路径", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "sourceEntityId": {"type": "string"}, "targetEntityId": {"type": "string"}, "maxDepth": {"type": "integer"}}, "required": ["graphId", "sourceEntityId", "targetEntityId"]}},
    {"name": "graph_export", "description": "导出图谱（jsonl/json/turtle/nt/nq/rdfxml）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "format": {"type": "string"}}, "required": ["graphId"]}},
    {"name": "graph_sparql", "description": "SPARQL 查询当前图（RDF 视图，需 pyoxigraph；SELECT/ASK/CONSTRUCT/DESCRIBE）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["graphId", "query"]}},
    {"name": "graph_search", "description": "语义检索：自然语言查询 → 召回相关实体/关系记录（检索链不可用时降级返回空命中）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "query": {"type": "string"}, "topK": {"type": "integer"}}, "required": ["graphId", "query"]}},
    {"name": "graph_index_status", "description": "语义检索/向量索引可用状态", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}}, "required": ["graphId"]}},
    {"name": "graph_job_run", "description": "同步执行任务", "inputSchema": {"type": "object", "properties": {"jobId": {"type": "string"}}, "required": ["jobId"]}},
    {"name": "graph_ontology_candidates", "description": "O-23 从图谱记录聚合本体候选（只产候选，人工收敛）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "ontologyId": {"type": "string"}, "sources": {"type": "array"}, "maxClasses": {"type": "integer"}}, "required": ["graphId"]}},
    {"name": "graph_ontology_validate", "description": "本体定义体检（结构 / 悬空 / 环）", "inputSchema": {"type": "object", "properties": {"ontologyId": {"type": "string"}, "graphSchema": {"type": "object"}, "concepts": {"type": "array"}, "properties": {"type": "array"}, "relations": {"type": "array"}}}},
    {"name": "graph_ontology_ingest", "description": "导入 OWL / Turtle 定义", "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}, "format": {"type": "string"}}, "required": ["content"]}},
    {"name": "graph_ontology_export", "description": "导出本体（json / owl / turtle / shacl）", "inputSchema": {"type": "object", "properties": {"ontologyId": {"type": "string"}, "format": {"type": "string"}, "graphSchema": {"type": "object"}, "concepts": {"type": "array"}, "properties": {"type": "array"}, "relations": {"type": "array"}}, "required": ["format"]}},
    {"name": "graph_ontology_validate_graph", "description": "O-22 实例级一致性体检（图谱 vs 本体；只报告不阻断）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "ontologyId": {"type": "string"}, "graphSchema": {"type": "object"}, "concepts": {"type": "array"}, "properties": {"type": "array"}, "relations": {"type": "array"}, "maxIssues": {"type": "integer"}, "includeShacl": {"type": "boolean"}}, "required": ["graphId"]}},
    {"name": "graph_ontology_coverage", "description": "O-21 类型覆盖 + 违规计数", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "ontologyId": {"type": "string"}, "graphSchema": {"type": "object"}}, "required": ["graphId"]}},
    {"name": "graph_ontology_snapshot", "description": "缓存编译产物快照（版本）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "ontologyId": {"type": "string"}, "ontologyVersion": {"type": "integer"}, "graphSchema": {"type": "object"}}, "required": ["graphId", "ontologyVersion", "graphSchema"]}},
    {"name": "graph_ontology_versions", "description": "列出编译产物快照", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}}, "required": ["graphId"]}},
    {"name": "graph_ontology_diff", "description": "编译产物版本 diff", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "fromVersion": {"type": "integer"}, "toVersion": {"type": "integer"}}, "required": ["graphId", "fromVersion", "toVersion"]}},
    {"name": "graph_job_get", "description": "查询任务状态", "inputSchema": {"type": "object", "properties": {"jobId": {"type": "string"}}, "required": ["jobId"]}},
]


def _tool_handlers(service: Any) -> dict[str, Callable[..., dict[str, Any]]]:
    return {
        "graph_create": lambda p: service.create_graph(
            graph_id_value=str(p.get("graphId") or ""),
            name=str(p.get("name") or ""),
            kb_id=str(p.get("kbId") or ""),
            tenant_id=str(p.get("tenantId") or ""),
            owner_id=str(p.get("ownerId") or ""),
            schema=p.get("graphSchema"),
        ),
        "graph_list": lambda p: service.list_graphs(tenant_id=str(p.get("tenantId") or ""), owner_id=str(p.get("ownerId") or "")),
        "graph_get": lambda p: service.get_graph(str(p.get("graphId") or "")),
        "graph_delete": lambda p: service.delete_graph(str(p.get("graphId") or "")),
        "graph_stat": lambda p: service.stat(str(p.get("graphId") or "")),
        "graph_build": lambda p: (
            service.build_from_text(str(p.get("graphId") or ""), text=str(p.get("text") or ""), doc_id=str(p.get("docId") or ""), title=str(p.get("title") or ""), llm=_flag(p.get("llm")))
            if p.get("text")
            else service.build_from_records(str(p.get("graphId") or ""), entities=p.get("entities") or [], relations=p.get("relations") or [], doc_id=str(p.get("docId") or ""))
        ),
        "graph_merge": lambda p: service.merge_records(str(p.get("graphId") or ""), entities=p.get("entities") or [], relations=p.get("relations") or [], doc_id=str(p.get("docId") or "")),
        "graph_deprecate_doc": lambda p: service.deprecate_doc(str(p.get("graphId") or ""), doc_id=str(p.get("docId") or "")),
        "graph_nodes": lambda p: service.list_nodes(str(p.get("graphId") or ""), entity_type=str(p.get("entityType") or ""), name=str(p.get("name") or ""), page=int(p.get("page") or 1), page_size=int(p.get("pageSize") or 20)),
        "graph_edges": lambda p: service.list_edges(str(p.get("graphId") or ""), relation_type=str(p.get("relationType") or ""), page=int(p.get("page") or 1), page_size=int(p.get("pageSize") or 20)),
        "graph_neighbors": lambda p: service.neighbors(str(p.get("graphId") or ""), entity_id_value=str(p.get("entityId") or ""), depth=int(p.get("depth") or 1)),
        "graph_paths": lambda p: service.paths(str(p.get("graphId") or ""), source_entity_id=str(p.get("sourceEntityId") or ""), target_entity_id=str(p.get("targetEntityId") or ""), max_depth=int(p.get("maxDepth") or 5)),
        "graph_export": lambda p: service.export(str(p.get("graphId") or ""), format=str(p.get("format") or "jsonl")),
        "graph_sparql": lambda p: service.sparql(str(p.get("graphId") or ""), query=str(p.get("query") or ""), limit=int(p.get("limit") or 0)),
        "graph_search": lambda p: service.semantic_search(str(p.get("graphId") or ""), query=str(p.get("query") or ""), top_k=int(p.get("topK") or 10)),
        "graph_index_status": lambda p: service.index_status(str(p.get("graphId") or "")),
        "graph_job_run": lambda p: service.run_job(str(p.get("jobId") or "")),
        "graph_ontology_candidates": lambda p: service.generate_ontology_candidates(
            str(p.get("graphId") or ""),
            ontology_id=str(p.get("ontologyId") or ""),
            sources=p.get("sources"),
            max_classes=int(p.get("maxClasses") or 40),
        ),
        "graph_ontology_validate": lambda p: service.validate_ontology_definition(p),
        "graph_ontology_ingest": lambda p: service.ingest_ontology(str(p.get("content") or ""), format=str(p.get("format") or "owl")),
        "graph_ontology_export": lambda p: service.export_ontology(p),
        "graph_ontology_validate_graph": lambda p: service.validate_graph_ontology(
            str(p.get("graphId") or ""), payload=p, max_issues=int(p.get("maxIssues") or 200), include_shacl=_flag(p.get("includeShacl")) is True
        ),
        "graph_ontology_coverage": lambda p: service.ontology_coverage(str(p.get("graphId") or ""), payload=p),
        "graph_ontology_snapshot": lambda p: service.put_ontology_snapshot(
            str(p.get("graphId") or ""), ontology_id=str(p.get("ontologyId") or ""), ontology_version=int(p.get("ontologyVersion") or 0), graph_schema=p.get("graphSchema")
        ),
        "graph_ontology_versions": lambda p: service.list_ontology_snapshots(str(p.get("graphId") or "")),
        "graph_ontology_diff": lambda p: service.ontology_version_diff(
            str(p.get("graphId") or ""), from_version=int(p.get("fromVersion") or 0), to_version=int(p.get("toVersion") or 0)
        ),
        "graph_job_get": lambda p: service.get_job(str(p.get("jobId") or "")),
    }


def _handle_request(req: dict[str, Any], handlers: dict[str, Callable]) -> dict[str, Any] | None:
    method = str(req.get("method") or "")
    req_id = req.get("id")
    if req_id is None:
        return None  # notification，无需响应
    trace_id = normalize_trace_id(req.get("traceId"))  # G2：非法 traceId 重新生成
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = dict(req.get("params") or {})
        tool = str(params.get("name") or "")
        args = dict(params.get("arguments") or {})
        handler = handlers.get(tool)
        if handler is None:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"未知工具：{tool}"}}
        try:
            result = handler(args)
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": _text_content(json.dumps(result, ensure_ascii=False, default=str))}}
        except GraphEngineError as exc:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": exc.message, "data": exc.detail()}}
        except Exception as exc:  # pragma: no cover
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(exc)}}
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"未知方法：{method}"}}


def serve_mcp(service: Any | None = None) -> None:
    """阻塞读取 stdin 的 MCP stdio 服务。"""
    svc = service or get_service()
    handlers = _tool_handlers(svc)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle_request(req, handlers)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve_mcp()
