"""内置词法检索后端：**不需要** semantica 向量链 / 外部索引的图谱检索。

为什么需要它（2026-10-06）：`semantica` 的 vector_store/context/embeddings 在本机环境被
pinecone 改名桩卡死（见 `docs/方案-原生MCP检索接入.md` §1），于是 `/search` 恒返回
``semantica:false / hits:[]`` 的降级结果——引擎实际**没有可用的图检索**。本模块把「引擎自己就能
给的检索」补齐：在图内记录（实体 / 关系）上做确定性词法打分召回，零外部依赖、离线可用。

设计取舍（对齐 openwiki 引擎的 builtin fulltext）：

- **查询时按需读存储、现算打分**，不另存一份索引 —— 没有「索引与图谱不一致」的维护窗口：
  G-06 构建、merge、deprecate、人工修正落库后**立即**反映到检索结果；
- 打分口径确定（同输入同输出）、权重可解释（见 `_entity_score` / `_relation_score`）；
- 只召回 ``status='active'`` 的记录（存储层默认口径），软删/废弃的不召回；
- `upsert` / `delete` 是**空实现**（返回值 0）：本后端不持有索引，写入由存储负责；
  这两个方法是 `RetrievalBackend` 契约的一部分，留着是为了让调用方按同一份契约切换后端。

向量召回（真实语义）仍是可选演进：注入一个实现了 `RetrievalBackend` 的向量后端即可替换，
本模块不改变那条路径。
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ..domain.models import EntityRecord, RelationRecord
from .retrieval import record_to_doc

#: 后端名（`/search` 与 `/index-status` 的原样回报，便于运维判断「这条命中是谁给的」）
BACKEND_NAME = "builtin-lexical"

#: 记录读取回调：graphId → 该图的活跃实体 / 关系记录（两个 store 后端同签名）
RecordsProvider = Callable[[str], tuple[list[EntityRecord], list[RelationRecord]]]

_NOISE = re.compile(r"[\s#*`>_\-.,;:!?'\"()\[\]{}<>/\\|~、，。；：！？“”‘’（）【】《》]+")


def normalize_query(value: str) -> str:
    """检索用的归一化：去空白/标点/标记符 + 小写（中文不受大小写影响，英文不区分大小写）。"""
    return _NOISE.sub("", str(value or "")).lower()


def _evidence_text(evidence: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        for key in ("snippet", "title", "name", "text", "value"):
            raw = item.get(key)
            if isinstance(raw, str) and raw:
                parts.append(raw)
    return normalize_query(" ".join(parts))


def _entity_score(query: str, record: EntityRecord) -> float:
    """实体打分：名称 > 别名 > 类型 > 证据/文档号（与 openwiki 的标题加权 > 正文加权同思路）。"""
    score = 0.0
    name = normalize_query(record.name)
    normalized = normalize_query(record.normalized_name)
    if name and name == query:
        score += 8.0
    elif query in name or (normalized and query in normalized):
        score += 5.0
    if any(query in normalize_query(alias) for alias in record.aliases if alias):
        score += 3.0
    if query in normalize_query(record.entity_type):
        score += 2.0
    if query in normalize_query(record.doc_id):
        score += 1.0
    if query and query in _evidence_text(record.evidence):
        score += 1.0
    return score


def _relation_score(
    query: str, record: RelationRecord, names: dict[str, str]
) -> float:
    """关系打分：端点实体名（两端各算一次）> 关系类型 > 文档号 / 证据。"""
    score = 0.0
    for entity_id_value in (record.source_entity_id, record.target_entity_id):
        name = normalize_query(names.get(entity_id_value, ""))
        if name and (name == query or query in name):
            score += 3.0
    if query in normalize_query(record.relation_type):
        score += 4.0
    if query in normalize_query(record.doc_id):
        score += 1.0
    if query and query in _evidence_text(record.evidence):
        score += 1.0
    return score


def _normalize_score(raw: float) -> float:
    """原始权重 → [0,1]：名称精确命中（8.0）为满分，其余按比例（便于跨后端比较）。"""
    return round(min(1.0, max(0.0, raw) / 8.0), 4)


class BuiltinLexicalBackend:
    """图内词法检索后端（`RetrievalBackend` 契约的实现之一，零依赖）。"""

    name = BACKEND_NAME

    def __init__(self, provider: RecordsProvider) -> None:
        self._provider = provider

    # ---- RetrievalBackend 契约（写入侧为空实现：本后端不持索引，见模块 docstring） ----

    def upsert(self, graph_id_value: str, records: list[dict[str, Any]]) -> int:
        return 0

    def delete(self, graph_id_value: str, record_ids: list[str]) -> int:
        return 0

    def query(self, graph_id_value: str, text: str, top_k: int) -> list[dict[str, Any]]:
        """词法召回：返回 `record_to_doc` 形状的原始命中（+ score），交给适配层转线缆形状。"""
        query = normalize_query(text)
        if query == "":
            return []
        entities, relations = self._provider(graph_id_value)
        names = {record.entity_id: record.name for record in entities}
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for record in entities:
            raw = _entity_score(query, record)
            if raw > 0:
                scored.append((raw, record.entity_id, record_to_doc(record)))
        for record in relations:
            raw = _relation_score(query, record, names)
            if raw > 0:
                scored.append((raw, record.relation_id, record_to_doc(record)))
        # 分数降序；同分按稳定 ID 升序 → 同输入同输出（可复现，便于单测与分页）
        scored.sort(key=lambda item: (-item[0], item[1]))
        hits: list[dict[str, Any]] = []
        for raw_score, _stable_id, doc in scored[: max(int(top_k), 0)]:
            hits.append({**doc, "score": _normalize_score(raw_score)})
        return hits

    def status(self) -> dict[str, Any]:
        return {"available": True, "backend": BACKEND_NAME, "mode": "lexical"}
