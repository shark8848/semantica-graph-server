"""应用层：GraphEngineService — HTTP/gRPC/Celery/MCP/CLI 五面共用的用例编排。"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .. import adapters
from ..domain.ids import entity_id, graph_id, normalize_name, relation_id
from ..domain.models import EntityRecord, GraphMeta, RelationRecord
from ..domain.schema import validate_entity_type, validate_graph_schema, validate_relation_type
from ..errors import InvalidParamsError, NotFoundError
from ..persistence.sqlite_store import SqliteGraphStore


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _with_doc_id(record: Any, doc_id: str) -> Any:
    """为记录补齐 docId，并确保 evidence 挂接 docId（对齐 open-ikc 语义）。"""
    from dataclasses import replace

    evidence = [dict(item) for item in record.evidence]
    if doc_id and not any(str(item.get("docId") or "") == doc_id for item in evidence):
        evidence.append({"docId": doc_id})
    return replace(record, doc_id=doc_id, evidence=evidence)


def _paginate(records: list[Any], page: int, page_size: int) -> tuple[int, list[Any]]:
    total = len(records)
    start = (page - 1) * page_size
    return total, records[start : start + page_size]


class GraphEngineService:
    """图引擎应用服务。构造时注入存储；所有用例返回普通 dict（协议层负责序列化）。"""

    def __init__(self, store: SqliteGraphStore, *, semantica_enabled: bool = True) -> None:
        self.store = store
        self.semantica_enabled = semantica_enabled

    # ---------- graph CRUD ----------

    def create_graph(
        self,
        *,
        graph_id_value: str = "",
        name: str = "",
        kb_id: str = "",
        tenant_id: str = "",
        owner_id: str = "",
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        schema = validate_graph_schema(schema)
        gid = graph_id_value.strip() or (graph_id(kb_id) if kb_id else f"graph_{uuid.uuid4().hex[:12]}")
        now = _now_iso()
        meta = GraphMeta(
            graph_id=gid,
            name=name,
            kb_id=kb_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            schema=schema,
            created_at=now,
            updated_at=now,
        )
        self.store.create_graph(meta)
        return meta.to_dict()

    def get_graph(self, graph_id_value: str) -> dict[str, Any]:
        meta = self.store.get_graph_or_raise(graph_id_value)
        return meta.to_dict()

    def delete_graph(self, graph_id_value: str) -> dict[str, Any]:
        meta = self.store.get_graph_or_raise(graph_id_value)
        self.store.delete_graph(graph_id_value)
        return {"graphId": graph_id_value, "deleted": True}

    def list_graphs(self, *, tenant_id: str = "", owner_id: str = "") -> dict[str, Any]:
        metas = self.store.list_graphs(tenant_id=tenant_id, owner_id=owner_id)
        return {"total": len(metas), "items": [meta.to_dict() for meta in metas]}

    # ---------- build / merge ----------

    def _require_graph(self, graph_id_value: str) -> GraphMeta:
        return self.store.get_graph_or_raise(graph_id_value)

    def build_from_records(
        self,
        graph_id_value: str,
        *,
        entities: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
        doc_id: str = "",
        merge: bool = True,
    ) -> dict[str, Any]:
        """按记录建图：schema 校验 → 稳定 ID 派生 → semantica 建图 → 增量合并入库。"""
        meta = self._require_graph(graph_id_value)
        schema = dict(meta.schema)
        entity_records: list[EntityRecord] = []
        for item in entities or []:
            record = EntityRecord.from_dict(item, graph_id=graph_id_value)
            effective_doc = record.doc_id or doc_id
            if effective_doc:
                record = _with_doc_id(record, effective_doc)
            validate_entity_type(schema, record.entity_type)
            entity_records.append(record)

        relation_records: list[RelationRecord] = []
        for item in relations or []:
            record = RelationRecord.from_dict(item, graph_id=graph_id_value)
            effective_doc = record.doc_id or doc_id
            if effective_doc:
                record = _with_doc_id(record, effective_doc)
            validate_relation_type(schema, record.relation_type)
            if not record.source_entity_id or not record.target_entity_id:
                raise InvalidParamsError(
                    "关系缺少端点", field="relations", reason="sourceEntityId/targetEntityId 必填"
                )
            relation_records.append(record)

        semantica_meta: dict[str, Any] = {"semantica": False}
        if self.semantica_enabled:
            try:
                kg_result = adapters.build_kg(entity_records, relation_records)
                semantica_meta = {
                    "semantica": True,
                    "entities": len(kg_result.get("entities", [])),
                    "relationships": len(kg_result.get("relationships", [])),
                    "metadata": kg_result.get("metadata", {}),
                }
            except Exception as exc:  # pragma: no cover
                semantica_meta = {"semantica": False, "error": str(exc)}

        saved_entities = [self.store.upsert_entity(record) for record in entity_records]
        saved_relations = [self.store.upsert_relation(record) for record in relation_records]
        return {
            "graphId": graph_id_value,
            "entityCount": len(saved_entities),
            "relationCount": len(saved_relations),
            "semantica": semantica_meta,
        }

    def build_from_text(
        self,
        graph_id_value: str,
        *,
        text: str,
        doc_id: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        """文本建图（MVP 规则占位抽取）：文档标题作为 concept 实体 + 「引号词」候选实体。

        后续可替换为 semantica.semantic_extract（NER/RelationExtractor/LLM 抽取）。
        """
        meta = self._require_graph(graph_id_value)
        schema = dict(meta.schema)
        entity_types = [e.get("type", "") for e in (schema.get("entityTypes") or []) if isinstance(e, dict)]
        default_type = entity_types[0] if entity_types else "concept"

        entities: list[dict[str, Any]] = []
        title = (title or "").strip() or "未命名文档"
        entities.append({"name": title, "type": default_type, "docId": doc_id})

        # MVP 规则：抽取「...」与“...”中的候选词作为实体
        candidates = re.findall(r"[「“]([^」”]{1,40})[」”]", text or "")
        seen: set[str] = set()
        for candidate in candidates:
            key = normalize_name(candidate)
            if key and key not in seen:
                seen.add(key)
                entities.append({"name": candidate, "type": default_type, "docId": doc_id})

        return self.build_from_records(
            graph_id_value,
            entities=entities,
            relations=[],
            doc_id=doc_id,
        )

    def merge_records(
        self,
        graph_id_value: str,
        *,
        entities: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
        doc_id: str = "",
    ) -> dict[str, Any]:
        """增量合并：与 build_from_records 同链路（对齐 open-ikc build_from_doc 语义）。"""
        result = self.build_from_records(
            graph_id_value, entities=entities, relations=relations, doc_id=doc_id, merge=True
        )
        if doc_id:
            active_keys = {
                f"{rec.entity_type}:{rec.normalized_name}"
                for rec in self.store.list_entities(graph_id_value)
                if rec.doc_id == doc_id or any(str(e.get("docId") or "") == doc_id for e in rec.evidence)
            }
            deprecated = self.store.deprecate_doc_assets(graph_id_value, doc_id, active_keys)
            result["deprecated"] = deprecated
        return result

    def deprecate_doc(self, graph_id_value: str, *, doc_id: str) -> dict[str, Any]:
        self._require_graph(graph_id_value)
        deprecated = self.store.deprecate_doc_assets(graph_id_value, doc_id, set())
        return {"graphId": graph_id_value, "docId": doc_id, "deprecated": deprecated}

    # ---------- 查询 ----------

    def stat(self, graph_id_value: str) -> dict[str, Any]:
        meta = self._require_graph(graph_id_value)
        stat = self.store.stat(graph_id_value)
        return {
            "graphId": graph_id_value,
            "kbId": meta.kb_id,
            "nodeCount": stat["nodeCount"],
            "edgeCount": stat["edgeCount"],
            "entityTypes": stat["entityTypes"],
            "relationTypes": stat["relationTypes"],
            "schemaCoverage": self._schema_coverage(meta.schema, stat),
        }

    @staticmethod
    def _schema_coverage(schema: dict[str, Any], stat: dict[str, Any]) -> dict[str, Any]:
        declared_entity_types = {
            str(item.get("type", "")).strip()
            for item in (schema.get("entityTypes") or [])
            if isinstance(item, dict)
        }
        declared_relation_types = {
            str(item.get("type", "")).strip()
            for item in (schema.get("relationTypes") or [])
            if isinstance(item, dict)
        }

        def ratio(declared: set[str], types: list[dict[str, Any]]) -> float:
            total = sum(int(item.get("count") or 0) for item in types)
            if total <= 0:
                return 1.0
            hit = sum(int(item.get("count") or 0) for item in types if item.get("type") in declared)
            return round(hit / total, 4)

        entity_coverage = ratio(declared_entity_types, stat["entityTypes"])
        relation_coverage = ratio(declared_relation_types, stat["relationTypes"])
        node_count = int(stat["nodeCount"])
        edge_count = int(stat["edgeCount"])
        total_records = node_count + edge_count
        overall = 1.0
        if total_records > 0:
            overall = round(
                (node_count * entity_coverage + edge_count * relation_coverage) / total_records, 4
            )
        return {"entity": entity_coverage, "relation": relation_coverage, "overall": overall}

    def list_nodes(
        self,
        graph_id_value: str,
        *,
        entity_type: str = "",
        name: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        self._require_graph(graph_id_value)
        records = self.store.list_entities(
            graph_id_value, entity_type=entity_type.strip() or None, name=name.strip() or None
        )
        total, items = _paginate(records, max(page, 1), min(max(page_size, 1), 200))
        return {
            "graphId": graph_id_value,
            "total": total,
            "page": max(page, 1),
            "pageSize": min(max(page_size, 1), 200),
            "items": [record.to_dict() for record in items],
        }

    def list_edges(
        self,
        graph_id_value: str,
        *,
        relation_type: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        self._require_graph(graph_id_value)
        records = self.store.list_relations(graph_id_value, relation_type=relation_type.strip() or None)
        total, items = _paginate(records, max(page, 1), min(max(page_size, 1), 200))
        return {
            "graphId": graph_id_value,
            "total": total,
            "page": max(page, 1),
            "pageSize": min(max(page_size, 1), 200),
            "items": [record.to_dict() for record in items],
        }

    def neighbors(
        self, graph_id_value: str, *, entity_id_value: str, depth: int = 1
    ) -> dict[str, Any]:
        self._require_graph(graph_id_value)
        if depth not in (1, 2):
            raise InvalidParamsError("邻域深度非法", field="depth", reason="仅支持 1 或 2")
        center, nodes, edges = self.store.neighbors(graph_id_value, entity_id_value, depth)
        if center is None:
            raise NotFoundError("实体不存在", field="entityId", reason=entity_id_value)
        return {
            "graphId": graph_id_value,
            "entityId": entity_id_value,
            "depth": depth,
            "center": center.to_dict(),
            "nodes": [node.to_dict() for node in nodes],
            "edges": [edge.to_dict() for edge in edges],
        }

    def paths(
        self,
        graph_id_value: str,
        *,
        source_entity_id: str,
        target_entity_id: str,
        max_depth: int = 5,
    ) -> dict[str, Any]:
        self._require_graph(graph_id_value)
        paths_result = self.store.paths(graph_id_value, source_entity_id, target_entity_id, max_depth)
        return {
            "graphId": graph_id_value,
            "sourceEntityId": source_entity_id,
            "targetEntityId": target_entity_id,
            "total": len(paths_result),
            "items": paths_result,
        }

    def analytics(self, graph_id_value: str) -> dict[str, Any]:
        """semantica GraphAnalyzer 分析（守卫式：不可用时返回降级标记）。"""
        self._require_graph(graph_id_value)
        entities = self.store.list_entities(graph_id_value)
        relations = self.store.list_relations(graph_id_value)
        result = adapters.analyze_graph(entities, relations)
        return {"graphId": graph_id_value, "analysis": result}

    def export(self, graph_id_value: str, *, format: str = "jsonl") -> dict[str, Any]:
        self._require_graph(graph_id_value)
        lines = self.store.export_lines(graph_id_value)
        fmt = (format or "jsonl").strip().lower()
        if fmt == "jsonl":
            return {"graphId": graph_id_value, "format": "jsonl", "total": len(lines), "content": "\n".join(lines)}
        if fmt == "json":
            records = [json.loads(line) for line in lines]
            return {
                "graphId": graph_id_value,
                "format": "json",
                "total": len(records),
                "content": json.dumps({"entities": [r for r in records if r["kind"] == "entity"],
                                       "relations": [r for r in records if r["kind"] == "relation"]},
                                      ensure_ascii=False),
            }
        raise InvalidParamsError("导出格式暂不支持", field="format", reason=f"支持 jsonl/json，当前：{fmt}")

    # ---------- jobs（异步任务登记与执行） ----------

    def submit_job(self, task: str, graph_id_value: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        self.store.create_job(job_id, graph_id_value, task, dict(payload or {}))
        return {"jobId": job_id, "graphId": graph_id_value, "task": task, "status": "pending"}

    def run_job(self, job_id: str) -> dict[str, Any]:
        """同步执行已登记任务（Celery worker 与 HTTP async 复用）。"""
        job = self.store.get_job(job_id)
        if job is None:
            raise NotFoundError("任务不存在", field="jobId", reason=job_id)
        if job["status"] in ("success", "failed"):
            return job
        task = job["task"]
        payload = dict(job["payload"] or {})
        graph_id_value = str(job.get("graphId") or "")
        try:
            if task == "build":
                result = self.build_from_records(
                    graph_id_value,
                    entities=payload.get("entities") or [],
                    relations=payload.get("relations") or [],
                    doc_id=str(payload.get("docId") or ""),
                )
            elif task == "build_text":
                result = self.build_from_text(
                    graph_id_value,
                    text=str(payload.get("text") or ""),
                    doc_id=str(payload.get("docId") or ""),
                    title=str(payload.get("title") or ""),
                )
            elif task == "merge":
                result = self.merge_records(
                    graph_id_value,
                    entities=payload.get("entities") or [],
                    relations=payload.get("relations") or [],
                    doc_id=str(payload.get("docId") or ""),
                )
            elif task == "deprecate_doc":
                result = self.deprecate_doc(graph_id_value, doc_id=str(payload.get("docId") or ""))
            elif task == "export":
                result = self.export(graph_id_value, format=str(payload.get("format") or "jsonl"))
            else:
                raise InvalidParamsError("未知任务", field="task", reason=task)
            self.store.update_job(job_id, status="success", result=result)
        except Exception as exc:
            self.store.update_job(job_id, status="failed", error=str(exc))
            raise
        return self.store.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise NotFoundError("任务不存在", field="jobId", reason=job_id)
        return job

    def list_jobs(self, *, graph_id_value: str = "", limit: int = 20) -> dict[str, Any]:
        jobs = self.store.list_jobs(graph_id=graph_id_value, limit=limit)
        return {"total": len(jobs), "items": jobs}
