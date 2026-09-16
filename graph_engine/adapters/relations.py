"""规则关系抽取：同句共现候选 → **已决策关系记录**（默认路径不依赖 LLM、不联网）。

分层（同 `core_writeback`）：*抽什么*在引擎，*怎么落地*在 core。本模块产出的是已决策记录——
端点用 `domain.ids` 的实体稳定 ID（与 `build_from_records` 同一派生规则），类型受
`graphSchema.relationTypes` 约束，证据带 `docId` + 原文片段，因此 core 侧可直接 upsert，
不重做抽取。

口径（最小实现，全部可解释）：

1. 切句：按中英文句读 ``。！？；!?;``、换行与西文 ``". "`` 切分；
2. 定位：只让**能在原文中定位**的实体参与（文档标题等不在正文的实体不产关系）；
3. 关系对：同一句内共现的实体两两成对，按**无序对去重**（一对一条边）；方向取原文中
   先出现者为 source（同位置按实体 ID 排序定序，保证确定性）；
4. 类型：schema 声明了 `relationTypes` 时优先取名称含 ``related``/``关联`` 的声明类型，
   否则取首个声明类型；未声明时用 ``related_to``（与 core/SDK 缺省口径一致）；
5. 置信度：两端实体置信度**取小**；`properties` 记 `cooccurrence`（该对共现次数）与
   `methods`（抽取方法清单）。

``llm=True``（即 `GRAPH_ENGINE_LLM_ENHANCE`）且 semantica 可用时，再叠加
`RelationExtractor(method="cooccurrence")` 的候选，与规则结果按同一无序对合并（置信度取大、
方法并入）。**任何失败/不可用一律确定降级为规则结果**：不抛错、不联网、不引入新依赖。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..domain.ids import entity_id, normalize_name

logger = logging.getLogger("graph_engine.relations")

#: 句读切分：中英文句读 + 换行 + 西文句点后空白
_SENTENCE_SPLIT = re.compile(r"[。！？；!?;\n]+|\.\s+")
#: 证据片段截断长度（core 只作展示，不解析）
_SNIPPET_LIMIT = 200
#: 未声明 relationTypes 时的缺省关系类型（与 core/SDK 缺省口径一致）
DEFAULT_RELATION_TYPE = "related_to"
#: semantica 关系抽取方法（守卫式增强，默认关闭）
_SEMANTICA_METHOD = "cooccurrence"


def relation_type_for(schema: dict[str, Any] | None) -> str:
    """关系类型取值：优先声明中名称含 related/关联 者，其次首个声明类型，缺省 related_to。"""
    declared = [
        str(item.get("type", "")).strip()
        for item in (schema or {}).get("relationTypes") or []
        if isinstance(item, dict)
    ]
    declared = [item for item in declared if item]
    if not declared:
        return DEFAULT_RELATION_TYPE
    for item in declared:
        if "related" in item.lower() or "关联" in item:
            return item
    return declared[0]


def _confidence(item: dict[str, Any]) -> float:
    try:
        return float(item.get("confidence"))
    except (TypeError, ValueError):
        return 1.0


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_SPLIT.split(text or "") if part.strip()]


def _located_entities(
    text: str, entities: list[dict[str, Any]], graph_id_value: str
) -> list[dict[str, Any]]:
    """可选定位实体（实体名在原文出现）+ 稳定 ID，按实体 ID 去重（保持入参顺序）。"""
    haystack = (text or "").lower()
    located: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entities or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        probe = name.lower()
        if probe not in haystack:
            continue
        key = str(item.get("entityId") or "").strip() or entity_id(
            graph_id_value, str(item.get("type") or "concept"), normalize_name(name)
        )
        if key in seen:
            continue
        seen.add(key)
        located.append(
            {
                "id": key,
                "name": name,
                "type": str(item.get("type") or "concept"),
                "confidence": _confidence(item),
                "probe": probe,
            }
        )
    return located


def _pair(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    relation_type: str,
    doc_id: str,
    snippet: str,
    method: str,
) -> dict[str, Any]:
    """已决策关系记录（`RelationRecord.from_dict` 可直接解析；relationId 由稳定键派生）。"""
    return {
        "type": relation_type,
        "sourceEntityId": source["id"],
        "targetEntityId": target["id"],
        "docId": doc_id,
        "confidence": round(min(source["confidence"], target["confidence"]), 4),
        "properties": {"cooccurrence": 1, "methods": [method]},
        "evidence": [{"docId": doc_id, "snippet": (snippet or "")[:_SNIPPET_LIMIT]}],
    }


def _merge(pairs: dict[tuple[str, str], dict[str, Any]], record: dict[str, Any], method: str) -> None:
    """按无序对合并：同一对只保留一条边，置信度取大、共现次数累加、方法并入。"""
    key = tuple(sorted((record["sourceEntityId"], record["targetEntityId"])))
    current = pairs.get(key)
    if current is None:
        pairs[key] = record
        return
    current["confidence"] = round(max(float(current["confidence"]), float(record["confidence"])), 4)
    current["properties"]["cooccurrence"] += int(record["properties"]["cooccurrence"])
    if method not in current["properties"]["methods"]:
        current["properties"]["methods"].append(method)


def _rule_pairs(
    text: str,
    located: list[dict[str, Any]],
    *,
    relation_type: str,
    doc_id: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """规则共现：同句内两两成对（句内不见面即不产关系，避免「跨句远距离共现」噪声）。"""
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for sentence in _sentences(text):
        probe = sentence.lower()
        present: list[tuple[int, dict[str, Any]]] = []
        for item in located:
            index = probe.find(item["probe"])
            if index >= 0:
                present.append((index, item))
        if len(present) < 2:
            continue
        present.sort(key=lambda pair: (pair[0], pair[1]["id"]))
        for left in range(len(present)):
            for right in range(left + 1, len(present)):
                _merge(
                    pairs,
                    _pair(
                        present[left][1],
                        present[right][1],
                        relation_type=relation_type,
                        doc_id=doc_id,
                        snippet=sentence,
                        method="rule_cooccurrence",
                    ),
                    "rule_cooccurrence",
                )
    return pairs


def _relation_extractor(method: str = _SEMANTICA_METHOD) -> Any | None:
    """惰性构造 semantica `RelationExtractor`（守卫式；不可用返回 None）。"""
    try:
        from semantica.semantic_extract.relation_extractor import RelationExtractor

        return RelationExtractor(method=method)
    except Exception as exc:  # noqa: BLE001 - 依赖缺失/构造失败一律降级
        logger.warning("semantica RelationExtractor 不可用：%s", exc)
        return None


def _to_spans(text: str, located: list[dict[str, Any]]) -> list[Any]:
    """已定位实体 → semantica span Entity（semantica 关系抽取需要字符区间）。"""
    from semantica.semantic_extract.ner_extractor import Entity

    haystack = text.lower()
    spans: list[Any] = []
    for item in located:
        start = haystack.find(item["probe"])
        if start < 0:
            continue
        spans.append(
            Entity(
                text=item["name"],
                label=item["type"],
                start_char=start,
                end_char=start + len(item["name"]),
                confidence=item["confidence"],
                metadata={},
            )
        )
    return spans


def _semantica_pairs(
    text: str,
    located: list[dict[str, Any]],
    *,
    relation_type: str,
    schema: dict[str, Any] | None,
    doc_id: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """semantica 共现抽取（可选增强）：只保留两端都在本引擎实体集内的候选。"""
    extractor = _relation_extractor()
    if extractor is None:
        return {}
    spans = _to_spans(text, located)
    if len(spans) < 2:
        return {}
    relations = extractor.extract_relations(text, spans)
    if not isinstance(relations, list):
        return {}

    by_probe = {item["probe"]: item for item in located}
    declared = {
        str(item.get("type", "")).strip()
        for item in (schema or {}).get("relationTypes") or []
        if isinstance(item, dict)
    }
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for relation in relations:
        subject = getattr(relation, "subject", None)
        object_ = getattr(relation, "object", None)
        source = by_probe.get(str(getattr(subject, "text", "") or "").strip().lower())
        target = by_probe.get(str(getattr(object_, "text", "") or "").strip().lower())
        if source is None or target is None or source["id"] == target["id"]:
            continue
        predicate = str(getattr(relation, "predicate", "") or "").strip()
        record = _pair(
            source,
            target,
            relation_type=predicate if predicate in declared else relation_type,
            doc_id=doc_id,
            snippet=str(getattr(relation, "context", "") or "") or text,
            method="semantica_cooccurrence",
        )
        confidence = getattr(relation, "confidence", None)
        if isinstance(confidence, (int, float)):
            record["confidence"] = round(min(float(confidence), float(record["confidence"])), 4)
        _merge(pairs, record, "semantica_cooccurrence")
    return pairs


def extract_text_relations(
    text: str,
    entities: list[dict[str, Any]],
    *,
    schema: dict[str, Any] | None = None,
    graph_id_value: str = "",
    doc_id: str = "",
    llm: bool = False,
) -> list[dict[str, Any]]:
    """文本 + 候选实体 → 已决策关系列表（规则共现为主，``llm`` 开启时叠加 semantica 增强）。"""
    body = text or ""
    located = _located_entities(body, entities, graph_id_value)
    if len(located) < 2:
        return []

    relation_type = relation_type_for(schema)
    pairs = _rule_pairs(body, located, relation_type=relation_type, doc_id=doc_id)
    if llm:
        try:
            for record in _semantica_pairs(
                body, located, relation_type=relation_type, schema=schema, doc_id=doc_id
            ).values():
                _merge(pairs, record, "semantica_cooccurrence")
        except Exception as exc:  # noqa: BLE001 - 增强失败回落规则结果，不阻断建图
            logger.warning("semantica 关系增强失败，回落规则结果：%s", exc)

    for record in pairs.values():
        record["properties"]["methods"] = sorted(set(record["properties"]["methods"]))
    return list(pairs.values())
