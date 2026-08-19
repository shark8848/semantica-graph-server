"""MCP 协议面：stdio JSON-RPC 服务（MCP 最小实现）。

与 semantica 自带 mcp_server 同思路，但只依赖引擎 application 层，
避开当前 venv 中损坏的 semantica.context/vector_store 导入链。
支持 initialize / tools/list / tools/call。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from ..errors import GraphEngineError
from ..protocol import error, new_trace_id, ok
from ..runtime import get_service

SERVER_INFO = {"name": "graph-engine", "version": "0.1.0"}
PROTOCOL_VERSION = "2025-03-26"


def _text_content(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}]


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
    {"name": "graph_build", "description": "建图（text 或 entities/relations 记录）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "text": {"type": "string"}, "docId": {"type": "string"}, "title": {"type": "string"}, "entities": {"type": "array"}, "relations": {"type": "array"}}, "required": ["graphId"]}},
    {"name": "graph_merge", "description": "增量合并实体/关系", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "entities": {"type": "array"}, "relations": {"type": "array"}, "docId": {"type": "string"}}, "required": ["graphId"]}},
    {"name": "graph_deprecate_doc", "description": "按 docId 增量废弃", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "docId": {"type": "string"}}, "required": ["graphId", "docId"]}},
    {"name": "graph_nodes", "description": "分页查询实体节点", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "entityType": {"type": "string"}, "name": {"type": "string"}, "page": {"type": "integer"}, "pageSize": {"type": "integer"}}, "required": ["graphId"]}},
    {"name": "graph_edges", "description": "分页查询关系边", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "relationType": {"type": "string"}, "page": {"type": "integer"}, "pageSize": {"type": "integer"}}, "required": ["graphId"]}},
    {"name": "graph_neighbors", "description": "实体邻域（depth 1/2）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "entityId": {"type": "string"}, "depth": {"type": "integer"}}, "required": ["graphId", "entityId"]}},
    {"name": "graph_paths", "description": "最短路径", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "sourceEntityId": {"type": "string"}, "targetEntityId": {"type": "string"}, "maxDepth": {"type": "integer"}}, "required": ["graphId", "sourceEntityId", "targetEntityId"]}},
    {"name": "graph_export", "description": "导出图谱（jsonl/json）", "inputSchema": {"type": "object", "properties": {"graphId": {"type": "string"}, "format": {"type": "string"}}, "required": ["graphId"]}},
    {"name": "graph_job_run", "description": "同步执行任务", "inputSchema": {"type": "object", "properties": {"jobId": {"type": "string"}}, "required": ["jobId"]}},
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
            service.build_from_text(str(p.get("graphId") or ""), text=str(p.get("text") or ""), doc_id=str(p.get("docId") or ""), title=str(p.get("title") or ""))
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
        "graph_job_run": lambda p: service.run_job(str(p.get("jobId") or "")),
        "graph_job_get": lambda p: service.get_job(str(p.get("jobId") or "")),
    }


def _handle_request(req: dict[str, Any], handlers: dict[str, Callable]) -> dict[str, Any] | None:
    method = str(req.get("method") or "")
    req_id = req.get("id")
    if req_id is None:
        return None  # notification，无需响应
    trace_id = str(req.get("traceId") or new_trace_id())
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
