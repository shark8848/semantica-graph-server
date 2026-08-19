"""semantica 适配层：建图（GraphBuilder）、分析（GraphAnalyzer）、导出（exporters）。

所有导入均为守卫式：semantica 的可选重依赖（torch / spacy / vector_store 链路）
不可用时，引擎核心功能（持久化、查询、导出）不受影响。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("graph_engine.semantica")


def semantica_available() -> bool:
    try:
        import semantica  # noqa: F401

        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("semantica 不可用：%s", exc)
        return False


def _to_entity_dict(record: Any) -> dict[str, Any]:
    """EntityRecord → semantica GraphBuilder 接受的实体 dict。"""
    return {
        "id": record.entity_id,
        "name": record.name,
        "type": record.entity_type,
        "confidence": float(record.confidence),
        "metadata": dict(record.properties),
    }


def _to_relation_dict(record: Any) -> dict[str, Any]:
    """RelationRecord → semantica GraphBuilder 接受的关系 dict。"""
    return {
        "source": record.source_entity_id,
        "target": record.target_entity_id,
        "type": record.relation_type,
        "confidence": float(record.confidence),
        "metadata": dict(record.properties),
    }


def build_kg(
    entities: list[Any],
    relationships: list[Any],
    *,
    merge_entities: bool = False,
) -> dict[str, Any]:
    """调用 semantica GraphBuilder 建图。

    返回 ``{"entities": [...], "relationships": [...], "metadata": {...}}``；
    semantica 不可用时降级为原样透传，metadata.semantica=False 标记。
    """
    entity_dicts = [_to_entity_dict(item) for item in entities]
    relation_dicts = [_to_relation_dict(item) for item in relationships]
    if not semantica_available():
        return {
            "entities": entity_dicts,
            "relationships": relation_dicts,
            "metadata": {"semantica": False, "reason": "semantica unavailable"},
        }
    from semantica.kg import GraphBuilder

    builder = GraphBuilder(merge_entities=merge_entities, resolve_conflicts=False)
    result = builder.build(
        sources=[{"entities": entity_dicts, "relationships": relation_dicts}],
        extract=False,
        extract_relations=False,
    )
    return result


def analyze_graph(entities: list[Any], relationships: list[Any]) -> dict[str, Any]:
    """调用 semantica GraphAnalyzer 输出图结构分析（度数/连通分量等）。"""
    if not semantica_available():
        return {"semantica": False}
    try:
        from semantica.kg import GraphAnalyzer
        from semantica.kg.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(
            entities=[_to_entity_dict(item) for item in entities],
            relationships=[_to_relation_dict(item) for item in relationships],
        )
        analyzer = GraphAnalyzer()
        return analyzer.analyze_graph(kg)
    except Exception as exc:  # pragma: no cover
        logger.warning("GraphAnalyzer 分析失败：%s", exc)
        return {"semantica": False, "error": str(exc)}
