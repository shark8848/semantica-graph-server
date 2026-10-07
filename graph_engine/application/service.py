"""应用层：GraphEngineService — HTTP/gRPC/Celery/MCP/CLI 五面共用的用例编排。"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from ikc_sdk.core.api.graph.edges import GraphEdgesResponse
from ikc_sdk.core.api.graph.nodes import GraphNodesResponse
from ikc_sdk.core.api.graph.stat import GraphStatResponse
from ikc_sdk.core.models.task import EngineJobView
from ikc_sdk.core.models.ontology import (
    OntologyCandidateResult,
    OntologyCoverageView,
    OntologyExportResult,
    OntologyValidationReport,
)

from .. import adapters
from ..adapters import core_writeback
from ..config import Settings
from ..adapters.retrieval import RetrievalAdapter
from ..adapters.retrieval_builtin import BuiltinLexicalBackend
from ..domain.constraints import ExtractionConstraints
from ..domain.ids import entity_id, graph_id, normalize_name, relation_id
from ..domain.models import EntityRecord, GraphMeta, RelationRecord
from ..adapters.ontology import ONTOLOGY_EXPORT_FORMATS
from ..adapters import ontology as ontology_adapter
from ..domain import ontology as ontology_domain
from ..domain.schema import validate_entity_type, validate_graph_schema, validate_relation_type
from ..errors import InvalidParamsError, NotFoundError
from ..errors import ONTOLOGY_UNAVAILABLE, GraphEngineError
from ..persistence import GraphStore


logger = logging.getLogger("graph_engine.application")


def _schema_relation_type(schema: dict[str, Any]) -> str:
    """schema 推导的缺省关系类型（与 adapters.relations.relation_type_for 同口径）。"""
    from ..adapters.relations import relation_type_for

    return relation_type_for(schema)


def _clamp_entity_types(
    entities: list[dict[str, Any]], declared: list[str], default_type: str
) -> tuple[list[dict[str, Any]], int]:
    """把候选实体的 ``type`` 收敛到 graphSchema 声明的实体类型集合（声明为空时不拦）。

    LLM 增强可能给出自由标签（person / product…）：不收敛就会一路带到 core 触发
    ``250003`` 让**整批构建**失败。收敛口径 = 换 ``default_type``（声明集合的首个类型）。
    返回 (候选列表, 被收敛条数)。
    """
    allowed = {str(item).strip() for item in declared if str(item).strip()}
    if not allowed:
        return entities, 0
    clamped = 0
    for item in entities:
        if isinstance(item, dict) and str(item.get("type") or "").strip() not in allowed:
            item["type"] = default_type
            clamped += 1
    return entities, clamped


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


# 分页口径（G-02/G-03，与 W-01 一致）：page≥1、pageSize 1~200、缺省 20
def _page_no(page: int) -> int:
    return max(int(page), 1)


def _page_size(page_size: int) -> int:
    return min(max(int(page_size), 1), 200)


def _total_pages(total: int, page_size: int) -> int:
    """总页数（无数据为 0）；契约要求 totalPages 必填，与 core 视图同口径。"""
    return 0 if total <= 0 else (int(total) + int(page_size) - 1) // int(page_size)


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    """作业视图（G3）：形状校验走 sdk `EngineJobView`。

    引擎本地态（pending/running/success/failed）保留在 `status`（不改写引擎载荷），
    外部态经 `engine_status_to_task_status()` 映射后以 `taskStatus` 一并给出，
    供 core / 调度层直接消费；未知字面量 fail-closed 为 FAILED。
    """
    view = EngineJobView.model_validate(job)
    payload = view.model_dump(exclude_unset=True)
    payload["taskStatus"] = view.taskStatus.value
    return payload


def _env_bool(key: str, default: bool = False) -> bool:
    """读布尔环境变量：1/true/yes（不区分大小写）视为 True。"""
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes")


def _paginate(records: list[Any], page: int, page_size: int) -> tuple[int, list[Any]]:
    total = len(records)
    start = (page - 1) * page_size
    return total, records[start : start + page_size]


class GraphEngineService:
    """图引擎应用服务。构造时注入存储；所有用例返回普通 dict（协议层负责序列化）。"""

    def __init__(
        self,
        store: GraphStore,
        *,
        semantica_enabled: bool = True,
        retrieval: RetrievalAdapter | None = None,
        retrieval_backend: str = "builtin",
    ) -> None:
        """构造服务；retrieval 为语义检索适配器（默认 None，首次检索时惰性构造）。

        `retrieval_backend` 只在惰性构造时生效：`builtin`（缺省）= 内置词法后端
        （零依赖、现算打分，见 `adapters/retrieval_builtin.py`）；`none` = 退回旧口径
        （不注入后端，`/search` 恒为确定降级），供只跑结构面 / 压测环境使用。
        显式传入 `retrieval=` 时以传入者为准（注入向量后端即替换掉内置后端）。
        """
        self.store = store
        self.semantica_enabled = semantica_enabled
        self._retrieval = retrieval
        self.retrieval_backend = str(retrieval_backend or "builtin").strip().lower()

    def _retrieval_adapter(self) -> RetrievalAdapter:
        if self._retrieval is None:
            self._retrieval = RetrievalAdapter(backend=self._default_retrieval_backend())
        return self._retrieval

    def _default_retrieval_backend(self) -> BuiltinLexicalBackend | None:
        """缺省检索后端：内置词法（`retrieval_backend="none"` 时返回 None → 确定降级）。"""
        if self.retrieval_backend == "none":
            return None
        return BuiltinLexicalBackend(self._retrieval_records)

    def _retrieval_records(
        self, graph_id_value: str
    ) -> tuple[list[EntityRecord], list[RelationRecord]]:
        """检索用记录源：每次查询现读存储（活跃记录），不做索引缓存 → 写后立即生效。"""
        return (
            self.store.list_entities(graph_id_value),
            self.store.list_relations(graph_id_value),
        )

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
        constraints: ExtractionConstraints | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """按记录建图：schema 校验 → 稳定 ID 派生 → semantica 建图 → 增量合并入库。

        响应含**已决策记录**（`entities` / `relations`，与回写载荷同一批 `to_dict()`），
        供 core 侧唯一写库方直接落库；既有 `entityCount` / `relationCount` 等键语义不变。

        ``constraints``（P4）：随请求下发的抽取约束画像，与按图沉淀的画像合并后持久化；
        记录路径只做黑名单收敛（改名会改稳定 ID 并打断关系端点，故只允许在候选阶段改名）。
        """
        meta = self._require_graph(graph_id_value)
        profile = self._absorb_constraints(graph_id_value, constraints)
        schema = dict(meta.schema)
        # 记录路径才做端点级联过滤：候选形态（无 entityId，文本建图的中间态）已由
        # apply_candidates 收敛，此处按 ID 过滤会把整批关系误删。
        record_shaped = any(
            str(item.get("entityId") or "")
            for item in (entities or [])
            if isinstance(item, dict)
        )
        if not profile.is_empty() and record_shaped:
            entities, relations, constraint_stats = profile.apply_records(
                list(entities or []), list(relations or [])
            )
        else:
            constraint_stats = None
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
        result = {
            "graphId": graph_id_value,
            "entityCount": len(saved_entities),
            "relationCount": len(saved_relations),
            "semantica": semantica_meta,
            # 已决策记录（增量字段）：抽什么在引擎、怎么落地在 core——core 只按记录 upsert，
            # 不重做抽取，避免第二套实现。
            "entities": [record.to_dict() for record in saved_entities],
            "relations": [record.to_dict() for record in saved_relations],
        }
        # §9.4 本体感知抽取：端点 / 必填 / 基数越界**只计数不阻断**（D4 / D5）
        result["schemaCheck"] = ontology_domain.schema_check(schema, saved_entities, saved_relations)
        # 引擎 → core 数据面回写（合并/计数/build_log/废弃/审计在 core 落地）：
        # 未配置 IKC_CORE_BASE_URL 时返回 None，返回体形状与既有行为一致。
        if not profile.is_empty():
            result["constraints"] = {
                "applied": constraint_stats or {"dropped": 0},
                "renames": len(profile.entity_renames),
                "types": len(profile.entity_types),
                "blacklist": len(profile.entity_blacklist),
                "relationTypes": len(profile.relation_types),
                "examples": len(profile.examples),
            }
        writeback = core_writeback.write_assets(
            meta.kb_id,
            doc_id=doc_id,
            entities=[record.to_dict() for record in saved_entities],
            relations=[record.to_dict() for record in saved_relations],
        )
        if writeback is not None:
            result["writeback"] = writeback
        return result

    def build_from_text(
        self,
        graph_id_value: str,
        *,
        text: str,
        doc_id: str = "",
        title: str = "",
        llm: bool | None = None,
        constraints: ExtractionConstraints | dict[str, Any] | None = None,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """文本建图（规则抽取）：文档标题 + markdown 结构性术语（标题/粗体/行内代码）与「引号词」
        候选实体 + 同句共现关系。

        ``constraints``（P4）：人工修正沉淀的抽取约束——在**派生稳定 ID 之前**对候选实体做
        改名 / 锁类型 / 黑名单，关系按人工确认的端点对定类型；画像合并后按图持久化。

        ``schema``：本次构建使用的 graphSchema（core 随构建载荷下发**当前权威**值）。
        缺省回落图谱创建时的快照 ``meta.schema``——那份可能早于本体绑定，故显式传入优先。

        ``llm`` 开启时对候选实体做 semantica LLMExtraction 增强，并对关系做 semantica
        RelationExtractor 增强（provider/依赖不可用时均确定降级）；缺省读
        ``GRAPH_ENGINE_LLM_ENHANCE``。关系口径与 graphSchema 约束见 adapters.relations。
        """
        if llm is None:
            llm = _env_bool("GRAPH_ENGINE_LLM_ENHANCE", False)
        meta = self._require_graph(graph_id_value)
        schema = validate_graph_schema(schema or meta.schema)
        entity_types = [
            str(e.get("type", "")).strip()
            for e in (schema.get("entityTypes") or [])
            if isinstance(e, dict) and str(e.get("type", "")).strip()
        ]
        default_type = entity_types[0] if entity_types else "concept"

        entities: list[dict[str, Any]] = []
        title = (title or "").strip() or "未命名文档"
        entities.append({"name": title, "type": default_type, "docId": doc_id})

        # 规则候选：markdown 结构性术语（标题/粗体/行内代码）+「引号词」（见 adapters.entities）。
        # 关系抽取只让**能在正文定位**的实体参与共现，故候选必须覆盖正文术语，否则只剩标题 → 0 条边。
        seen: set[str] = {normalize_name(title)}
        for item in adapters.candidate_terms(text or ""):
            key = normalize_name(item["name"])
            if key in seen:
                continue
            seen.add(key)
            entities.append({"name": item["name"], "type": default_type, "docId": doc_id})

        llm_meta: dict[str, Any] | None = None
        if llm:
            entities, llm_meta = adapters.enhance_text_entities(
                text or "", entities, allowed_types=entity_types
            )

        # P4：人工修正沉淀的抽取约束（改名 / 锁类型 / 黑名单 / 样例），在稳定 ID 派生之前收敛。
        profile = self._absorb_constraints(graph_id_value, constraints)
        constraint_stats: dict[str, int] | None = None
        if not profile.is_empty():
            entities, constraint_stats = profile.apply_candidates(entities, default_type=default_type)

        # graphSchema 声明了实体类型时收敛候选（放在约束之后 = 人工锁定的类型已经生效）：
        # LLM 自由标签 / 约束里的历史值都不得带出声明集合，否则 core 会整批 250003。
        entities, clamped_types = _clamp_entity_types(entities, entity_types, default_type)

        # 关系抽取（规则共现，llm 开启时叠加 semantica 增强）：端点为实体稳定 ID，
        # 类型受 graphSchema + 人工确认样例约束，证据带 docId + 片段
        relations = adapters.extract_text_relations(
            text or "",
            entities,
            schema=schema,
            graph_id_value=graph_id_value,
            doc_id=doc_id,
            llm=bool(llm),
            type_resolver=(
                (lambda left, right: profile.relation_type_for(
                    str(left.get("name") or ""), str(right.get("name") or ""), _schema_relation_type(schema)
                ))
                if profile.relation_types
                else None
            ),
        )

        result = self.build_from_records(
            graph_id_value,
            entities=entities,
            relations=relations,
            doc_id=doc_id,
            constraints=constraints,
        )
        if clamped_types:
            result.setdefault("types", {})["clamped"] = clamped_types
        if llm_meta:
            result["llm"] = llm_meta
        if constraint_stats is not None:
            result.setdefault("constraints", {})["candidates"] = constraint_stats
        return result

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
        meta = self._require_graph(graph_id_value)
        deprecated = self.store.deprecate_doc_assets(graph_id_value, doc_id, set())
        result = {"graphId": graph_id_value, "docId": doc_id, "deprecated": deprecated}
        # 空记录回写 = core 侧同口径 doc 级废弃（实体/关系对称；未启用时无副作用）
        writeback = core_writeback.write_assets(
            meta.kb_id, doc_id=doc_id, entities=[], relations=[]
        )
        if writeback is not None:
            result["writeback"] = writeback
        return result

    # ---------- 抽取约束画像（P4） ----------

    def get_constraints(self, graph_id_value: str) -> dict[str, Any]:
        """读该图沉淀的抽取约束画像（不存在返回空画像，不报错）。"""
        self._require_graph(graph_id_value)
        return ExtractionConstraints.from_dict(self.store.get_constraints(graph_id_value)).to_dict()

    def put_constraints(
        self, graph_id_value: str, constraints: dict[str, Any] | None
    ) -> dict[str, Any]:
        """合并写入画像（后写优先），返回合并后的完整画像。"""
        self._require_graph(graph_id_value)
        profile = self._absorb_constraints(graph_id_value, constraints)
        return profile.to_dict()

    def _absorb_constraints(
        self,
        graph_id_value: str,
        constraints: ExtractionConstraints | dict[str, Any] | None,
    ) -> ExtractionConstraints:
        """把请求画像合并进按图沉淀画像并持久化（沉淀 = 下一次构建自动生效）。"""
        stored = ExtractionConstraints.from_dict(self.store.get_constraints(graph_id_value))
        incoming = ExtractionConstraints.from_dict(constraints)
        if incoming.is_empty():
            return stored
        merged = stored.merged(incoming)
        try:
            self.store.put_constraints(graph_id_value, merged.to_dict())
        except Exception as exc:  # noqa: BLE001 - 沉淀失败不阻断本次构建
            logger.warning("抽取约束画像持久化失败（graph_id=%s）：%s", graph_id_value, exc)
        return merged

    # ---------- 查询 ----------

    def stat(self, graph_id_value: str) -> dict[str, Any]:
        meta = self._require_graph(graph_id_value)
        stat = self.store.stat(graph_id_value)
        return GraphStatResponse.model_validate(
            {
                "graphId": graph_id_value,
                "kbId": meta.kb_id,
                "nodeCount": stat["nodeCount"],
                "edgeCount": stat["edgeCount"],
                "entityTypes": stat["entityTypes"],
                "relationTypes": stat["relationTypes"],
                "schemaCoverage": self._schema_coverage(meta.schema, stat),
            }
        ).model_dump(exclude_unset=True)

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
        page_no, size = _page_no(page), _page_size(page_size)
        total, items = _paginate(records, page_no, size)
        return GraphNodesResponse.model_validate(
            {
                "graphId": graph_id_value,
                "total": total,
                "page": page_no,
                "pageSize": size,
                "totalPages": _total_pages(total, size),
                "items": [record.to_dict() for record in items],
            }
        ).model_dump(exclude_unset=True)

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
        page_no, size = _page_no(page), _page_size(page_size)
        total, items = _paginate(records, page_no, size)
        return GraphEdgesResponse.model_validate(
            {
                "graphId": graph_id_value,
                "total": total,
                "page": page_no,
                "pageSize": size,
                "totalPages": _total_pages(total, size),
                "items": [record.to_dict() for record in items],
            }
        ).model_dump(exclude_unset=True)

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
        if fmt == "ttl":
            fmt = "turtle"
        if fmt in ("turtle", "nt", "nq", "rdfxml"):
            if not adapters.rdf_available():
                raise InvalidParamsError(
                    "RDF 导出暂不可用",
                    field="format",
                    reason="缺少 pyoxigraph / semantica oxigraph store",
                )
            entities = self.store.list_entities(graph_id_value)
            relations = self.store.list_relations(graph_id_value)
            content = adapters.export_rdf(graph_id_value, entities, relations, fmt)
            return {
                "graphId": graph_id_value,
                "format": fmt,
                "total": len(entities) + len(relations),
                "content": content,
            }
        raise InvalidParamsError(
            "导出格式暂不支持",
            field="format",
            reason=f"支持 jsonl/json/turtle/nt/nq/rdfxml，当前：{fmt}",
        )

    # ---------- RDF/SPARQL 视图（OxigraphStore，只读补强） ----------

    def sparql(self, graph_id_value: str, *, query: str = "", limit: int = 0) -> dict[str, Any]:
        """SPARQL 查询当前图（活动记录实时构建内存 Oxigraph 视图）。

        pyoxigraph / semantica oxigraph store 不可用时抛 200001（字段 query）；
        SPARQL 语法/执行错误、非只读表单（白名单外）、超时同样收敛为
        200001（字段 query），便于调用方按字段纠错。

        护栏：白名单仅允许 SELECT/ASK/CONSTRUCT/DESCRIBE；结果按
        ``min(limit, GRAPH_ENGINE_SPARQL_MAX_ROWS)`` 分页截断（limit<=0 走
        默认上限），查询超时（``GRAPH_ENGINE_SPARQL_TIMEOUT`` 秒，0 关闭）
        即时中止；响应含 ``rowLimit``/``truncated`` 供调用方感知分页。
        """
        self._require_graph(graph_id_value)
        q = str(query or "").strip()
        if not q:
            raise InvalidParamsError("SPARQL 查询为空", field="query")
        if not adapters.rdf_available():
            raise InvalidParamsError(
                "RDF/SPARQL 视图暂不可用",
                field="query",
                reason="缺少 pyoxigraph / semantica oxigraph store",
            )
        entities = self.store.list_entities(graph_id_value)
        relations = self.store.list_relations(graph_id_value)
        settings = Settings()
        try:
            result = adapters.run_sparql(
                graph_id_value,
                entities,
                relations,
                q,
                limit=limit,
                max_rows=settings.sparql_max_rows,
                timeout=settings.sparql_timeout,
            )
        except Exception as exc:
            raise InvalidParamsError("SPARQL 执行失败", field="query", reason=str(exc)) from exc
        return {"graphId": graph_id_value, "query": q, **result}

    # ---------- 语义检索（骨架：真实语义链待依赖修复后激活） ----------

    def semantic_search(
        self, graph_id_value: str, query: str = "", top_k: int = 10
    ) -> dict[str, Any]:
        """语义检索：自然语言查询文本 → 相关实体/关系记录。

        先校验图谱存在（缺失抛 NotFoundError）；semantica 检索链不可用或检索
        后端未配置时返回确定降级结果（semantica:false、hits:[]），不抛错。
        """
        self._require_graph(graph_id_value)
        q = str(query or "")
        k = min(max(int(top_k), 1), 200)
        result = self._retrieval_adapter().search(graph_id_value, q, k)
        return {"graphId": graph_id_value, "query": q, "topK": k, **result}

    def index_status(self, graph_id_value: str) -> dict[str, Any]:
        """语义检索/向量索引可用状态（供运维与调试；降级不抛错）。"""
        self._require_graph(graph_id_value)
        return {"graphId": graph_id_value, **self._retrieval_adapter().status()}

    # ---------- jobs（异步任务登记与执行） ----------

    def submit_job(self, task: str, graph_id_value: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        self.store.create_job(job_id, graph_id_value, task, dict(payload or {}))
        return _job_view(
            {
                "jobId": job_id,
                "graphId": graph_id_value,
                "task": task,
                "status": "pending",
                "payload": dict(payload or {}),
            }
        )

    def run_job(self, job_id: str) -> dict[str, Any]:
        """同步执行已登记任务（Celery worker 与 HTTP async 复用）。"""
        job = self.store.get_job(job_id)
        if job is None:
            raise NotFoundError("任务不存在", field="jobId", reason=job_id)
        if job["status"] in ("success", "failed"):
            return _job_view(job)
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
                    schema=payload.get("graphSchema") or None,
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
            elif task == "ontology_candidates":
                result = self.generate_ontology_candidates(
                    graph_id_value,
                    ontology_id=str(payload.get("ontologyId") or ""),
                    sources=payload.get("sources"),
                    max_classes=int(payload.get("maxClasses") or 40),
                )
            elif task == "ontology_validate_graph":
                result = self.validate_graph_ontology(
                    graph_id_value, payload=payload, max_issues=int(payload.get("maxIssues") or 200)
                )
            else:
                raise InvalidParamsError("未知任务", field="task", reason=task)
            self.store.update_job(job_id, status="success", result=result)
        except Exception as exc:
            self.store.update_job(job_id, status="failed", error=str(exc))
            raise
        return _job_view(self.store.get_job(job_id))

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise NotFoundError("任务不存在", field="jobId", reason=job_id)
        return _job_view(job)

    def list_jobs(self, *, graph_id_value: str = "", limit: int = 20) -> dict[str, Any]:
        jobs = self.store.list_jobs(graph_id=graph_id_value, limit=limit)
        return {"total": len(jobs), "items": [_job_view(job) for job in jobs]}

    # ---------- 本体面（O-21 ~ O-24：semantica.ontology 守卫式封装，降级 260009） ----------

    def generate_ontology_candidates(
        self,
        graph_id_value: str,
        *,
        ontology_id: str = "",
        sources: list[str] | None = None,
        max_classes: int = 40,
    ) -> dict[str, Any]:
        """O-23：从图谱记录聚合本体候选（只产候选，人工收敛后 publish，D6）。"""
        meta = self._require_graph(graph_id_value)
        entities = self.store.list_entities(graph_id_value)
        relations = self.store.list_relations(graph_id_value)
        candidates = ontology_domain.generate_candidates(
            entities, relations, max_classes=max_classes
        )
        payload = OntologyCandidateResult.model_validate(
            {
                "kbId": meta.kb_id,
                "ontologyId": ontology_id,
                "sources": [str(item) for item in (sources or ["graph"])],
                "classes": candidates["classes"],
                "relations": candidates["relations"],
                "truncated": candidates["truncated"],
            }
        ).model_dump(exclude_unset=True)
        inferred = ontology_adapter.infer_classes(entities, relations)
        if inferred:
            # semantica 类推断只作**增强参考**（extra 键），不覆盖确定性聚合候选
            payload["semantica"] = {"classes": inferred}
        return payload

    def validate_ontology_definition(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """定义面结构体检（悬空 / 环 / 缺字段）；semantica 校验结果附 extra 键。"""
        definition = ontology_domain.normalize_definition(payload)
        issues = ontology_domain.validate_definition(definition)
        result: dict[str, Any] = {
            "ontologyId": definition["ontologyId"],
            "valid": not any(item.get("severity") == "error" for item in issues),
            "degraded": False,
            "issueCounts": ontology_domain.issue_counts(issues),
            "issues": issues,
            "truncated": False,
        }
        semantica_report = ontology_adapter.validate_definition(
            ontology_domain.derive_graph_schema(definition), name=definition["name"]
        )
        if semantica_report is not None:
            result["semantica"] = semantica_report
        return result

    def ingest_ontology(self, content: str, *, format: str = "owl") -> dict[str, Any]:
        """导入 OWL / Turtle：抽概念 / 属性 / 关系为引擎定义视图（semantica 缺失时降级 260009）。"""
        parsed = ontology_adapter.parse_ontology(str(content or ""), fmt=str(format or "owl"))
        if parsed is None:
            raise GraphEngineError(
                ONTOLOGY_UNAVAILABLE,
                "本体导入不可用（rdflib 缺失或正文非法）",
                field="content",
                reason=str(format or "owl"),
            )
        return {"format": str(format or "owl"), **parsed}

    def export_ontology(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        """O-24：导出 json（编译产物）/ owl / turtle / shacl。"""
        body = dict(payload or {})
        definition = ontology_domain.normalize_definition(body)
        fmt = str(body.get("format") or "json").strip().lower()
        if fmt not in ONTOLOGY_EXPORT_FORMATS:
            raise InvalidParamsError(
                "不支持的导出格式", field="format", reason=f"{fmt}（支持 {'/'.join(ONTOLOGY_EXPORT_FORMATS)}）"
            )
        schema = ontology_domain.derive_graph_schema(definition)
        if fmt == "json":
            return OntologyExportResult.model_validate(
                {
                    "ontologyId": definition["ontologyId"],
                    "format": "json",
                    "contentType": "application/json",
                    "content": json.dumps(schema, ensure_ascii=False),
                    "graphSchema": schema,
                }
            ).model_dump(exclude_unset=True)
        exported = ontology_adapter.export_ontology(
            schema, fmt, name=definition["name"]
        )
        if exported is None:
            raise GraphEngineError(
                ONTOLOGY_UNAVAILABLE,
                "本体导出不可用（semantica.ontology 缺失或导出失败）",
                field="format",
                reason=fmt,
            )
        return OntologyExportResult.model_validate(
            {
                "ontologyId": definition["ontologyId"],
                "format": fmt,
                "contentType": exported["contentType"],
                "content": exported["content"],
            }
        ).model_dump(exclude_unset=True)

    def validate_graph_ontology(
        self,
        graph_id_value: str,
        *,
        payload: dict[str, Any] | None = None,
        max_issues: int = 200,
        include_shacl: bool = False,
    ) -> dict[str, Any]:
        """O-22：实例级一致性体检（图谱 vs 定义）；只报告，不落库、不阻断（D4/D5）。"""
        meta = self._require_graph(graph_id_value)
        definition = ontology_domain.normalize_definition(payload)
        entities = self.store.list_entities(graph_id_value)
        relations = self.store.list_relations(graph_id_value)
        report = ontology_domain.validate_graph(
            definition, entities, relations, max_issues=max_issues
        )
        report["kbId"] = meta.kb_id
        report["graphId"] = graph_id_value
        report["coverage"] = ontology_domain.coverage(definition, entities, relations)
        if include_shacl:
            shacl = ontology_adapter.export_ontology(
                ontology_domain.derive_graph_schema(definition), "shacl", name=definition["name"]
            )
            report["shacl"] = {"available": shacl is not None}
        return OntologyValidationReport.model_validate(report).model_dump(exclude_unset=True)

    def ontology_coverage(
        self, graph_id_value: str, *, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """O-21 引擎侧落地体检：类型覆盖 + 违规计数（不落库）。"""
        meta = self._require_graph(graph_id_value)
        definition = ontology_domain.normalize_definition(payload)
        entities = self.store.list_entities(graph_id_value)
        relations = self.store.list_relations(graph_id_value)
        report = ontology_domain.coverage(definition, entities, relations)
        return OntologyCoverageView.model_validate(
            {
                "kbId": meta.kb_id,
                "graphId": graph_id_value,
                "ontologyId": definition["ontologyId"],
                "ontologyVersion": int(definition["version"] or 0),
                **report,
            }
        ).model_dump(exclude_unset=True)

    def put_ontology_snapshot(
        self,
        graph_id_value: str,
        *,
        ontology_id: str = "",
        ontology_version: int = 0,
        graph_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """缓存编译产物快照（D1：只缓存产物，不存定义）；版本号必填且同图唯一。"""
        self._require_graph(graph_id_value)
        version = int(ontology_version or 0)
        if version <= 0:
            raise InvalidParamsError("ontologyVersion 必填（正整数）", field="ontologyVersion")
        schema = validate_graph_schema(graph_schema)
        return self.store.put_ontology(
            graph_id_value,
            ontology_id=str(ontology_id or ""),
            ontology_version=version,
            product=schema,
        )

    def list_ontology_snapshots(self, graph_id_value: str) -> dict[str, Any]:
        self._require_graph(graph_id_value)
        items = self.store.list_ontologies(graph_id_value)
        return {"graphId": graph_id_value, "total": len(items), "items": items}

    def ontology_version_diff(
        self, graph_id_value: str, *, from_version: int = 0, to_version: int = 0
    ) -> dict[str, Any]:
        """版本比较（编译产物 diff；缺任一版本 → 200404）。"""
        self._require_graph(graph_id_value)
        source = self.store.get_ontology(graph_id_value, int(from_version or 0))
        if source is None:
            raise NotFoundError("起始版本不存在", field="fromVersion", reason=str(from_version))
        target = self.store.get_ontology(graph_id_value, int(to_version or 0))
        if target is None:
            raise NotFoundError("目标版本不存在", field="toVersion", reason=str(to_version))
        return {
            "graphId": graph_id_value,
            "ontologyId": target.get("ontologyId") or source.get("ontologyId") or "",
            "fromVersion": int(from_version),
            "toVersion": int(to_version),
            "diff": ontology_domain.diff_products(
                dict(source.get("graphSchema") or {}), dict(target.get("graphSchema") or {})
            ),
        }
