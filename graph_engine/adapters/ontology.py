"""semantica.ontology 适配层：本体候选推断 / 定义校验 / OWL·SHACL 导出 / 定义导入。

所有导入均为**守卫式**（同 `adapters/semantica.py` 口径）：`semantica.ontology`
或 RDF 依赖（rdflib / pyshacl）不可用时返回 `None`，由应用层按 `260009` 如实降级，
**不拦图谱主流程**（`docs/设计方案-本体.md` §9.5）。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

logger = logging.getLogger("graph_engine.ontology")

_T = TypeVar("_T")

# 引擎面本体导出格式（与核心定义面一致；json 由应用层直接回编译产物，不经 semantica）
ONTOLOGY_EXPORT_FORMATS = ("json", "owl", "turtle", "shacl")


def ontology_available() -> bool:
    """semantica.ontology 是否可导入（只判依赖，不做重活）。"""
    try:
        import semantica.ontology  # noqa: F401

        return True
    except Exception as exc:  # pragma: no cover - 环境相关
        logger.warning("semantica.ontology 不可用：%s", exc)
        return False


def rdflib_available() -> bool:
    try:
        import rdflib  # noqa: F401

        return True
    except Exception as exc:  # pragma: no cover - 环境相关
        logger.warning("rdflib 不可用：%s", exc)
        return False


@contextmanager
def _quiet() -> Iterator[None]:
    """临时关闭 semantica 进度条输出（HTTP 面 stdout 只留结构化日志）。"""
    try:
        from semantica.utils.progress_tracker import get_progress_tracker

        tracker = get_progress_tracker()
        previous = bool(tracker.enabled)
        tracker.enabled = False
        try:
            yield
        finally:
            tracker.enabled = previous
    except Exception:  # pragma: no cover - 版本差异时退化为直接执行
        yield


def _guard(fn: Callable[[], _T]) -> _T | None:
    """执行 semantica 调用；依赖缺失 / 运行时异常 → None（降级，不抛）。"""
    if not ontology_available():
        return None
    try:
        with _quiet():
            return fn()
    except Exception as exc:  # pragma: no cover - 依赖 / 数据相关
        logger.warning("semantica.ontology 调用失败（降级）：%s", exc)
        return None


def _jsonable(value: Any) -> Any:
    """递归归一化为 JSON 可序列化结构（对齐 adapters.semantica._jsonable）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_jsonable(item) for item in value]
        try:
            return sorted(items)
        except TypeError:
            return sorted(items, key=repr)
    return str(value)


def to_semantica_ontology(graph_schema: dict[str, Any], *, name: str = "", uri: str = "") -> dict[str, Any]:
    """graphSchema（编译产物）→ semantica 本体字典（classes / properties）。"""
    entity_types = [item for item in (graph_schema.get("entityTypes") or []) if isinstance(item, dict)]
    relation_types = [item for item in (graph_schema.get("relationTypes") or []) if isinstance(item, dict)]
    base = (uri or "https://semantica.dev/ontology/").rstrip("#/") + "#"

    classes: list[dict[str, Any]] = []
    properties: list[dict[str, Any]] = []
    known: set[str] = set()
    for item in entity_types:
        type_key = str(item.get("type") or "")
        if not type_key:
            continue
        known.add(type_key)
        classes.append(
            {
                "uri": f"{base}{type_key}",
                "name": type_key,
                "label": str(item.get("name") or type_key),
                "comment": str(item.get("description") or ""),
                "subClassOf": str(item.get("subClassOf") or ""),
            }
        )
        for prop in item.get("properties") or []:
            if not isinstance(prop, dict) or not str(prop.get("name") or ""):
                continue
            properties.append(
                {
                    "uri": f"{base}{type_key}_{prop['name']}",
                    "name": str(prop["name"]),
                    "type": "data",
                    "data_type": str(prop.get("dataType") or "string"),
                    "domain": type_key,
                    "required": bool(prop.get("required")),
                    "cardinality": str(prop.get("cardinality") or ""),
                }
            )
    for item in relation_types:
        type_key = str(item.get("type") or "")
        if not type_key:
            continue
        properties.append(
            {
                "uri": f"{base}{type_key}",
                "name": type_key,
                "type": "object",
                "domain": str((item.get("sourceTypes") or [""])[0] if item.get("sourceTypes") else ""),
                "range": str((item.get("targetTypes") or [""])[0] if item.get("targetTypes") else ""),
                "source_types": [str(x) for x in (item.get("sourceTypes") or [])],
                "target_types": [str(x) for x in (item.get("targetTypes") or [])],
                "cardinality": str(item.get("cardinality") or ""),
            }
        )
    return {
        "uri": base,
        "name": name or "GraphEngineOntology",
        "version": "1.0",
        "classes": classes,
        "properties": properties,
        "imports": [],
        "metadata": {"entity_type_count": len(known), "relation_type_count": len(relation_types)},
    }


def validate_definition(graph_schema: dict[str, Any], *, name: str = "") -> dict[str, Any] | None:
    """semantica OntologyValidator 结构校验（结果原样归一化；降级 None）。"""
    ontology = to_semantica_ontology(graph_schema, name=name)

    def run() -> dict[str, Any]:
        from semantica.ontology import OntologyValidator

        result = OntologyValidator().validate(ontology)
        return _jsonable(getattr(result, "__dict__", result))

    return _guard(run)


def export_ontology(
    graph_schema: dict[str, Any], fmt: str, *, name: str = "", uri: str = ""
) -> dict[str, Any] | None:
    """导出 owl / turtle / shacl（json 不走本函数）。返回 {contentType, content}；降级 None。"""
    ontology = to_semantica_ontology(graph_schema, name=name, uri=uri)
    normalized = (fmt or "").strip().lower()

    def run() -> dict[str, Any]:
        from semantica.ontology import OntologyEngine

        engine = OntologyEngine()
        if normalized == "shacl":
            return {"contentType": "text/turtle", "content": str(engine.to_shacl(ontology))}
        target = "turtle" if normalized == "turtle" else "xml"
        return {"contentType": "text/turtle" if target == "turtle" else "application/rdf+xml",
                "content": str(engine.to_owl(ontology, format=target))}

    return _guard(run)


def infer_classes(entities: list[Any], relations: list[Any]) -> list[dict[str, Any]] | None:
    """semantica 类推断（候选概念增强；降级 None）。"""
    payload = {
        "entities": [
            {"id": str(getattr(item, "entity_id", "") or ""), "name": str(getattr(item, "name", "") or ""),
             "type": str(getattr(item, "entity_type", "") or ""), "properties": dict(getattr(item, "properties", {}) or {})}
            for item in entities
        ],
        "relationships": [
            {"source": str(getattr(item, "source_entity_id", "") or ""),
             "target": str(getattr(item, "target_entity_id", "") or ""),
             "type": str(getattr(item, "relation_type", "") or "")}
            for item in relations
        ],
    }

    def run() -> list[dict[str, Any]]:
        from semantica.ontology import OntologyGenerator

        inferred = OntologyGenerator(min_occurrences=1).infer_classes(payload)
        return [_jsonable(item) for item in (inferred or [])]

    return _guard(run)


def parse_ontology(content: str, fmt: str = "owl") -> dict[str, Any] | None:
    """导入 OWL / Turtle / RDF-XML：抽概念 / 数据属性 / 对象属性为定义视图（降级 None）。"""
    if not content or not rdflib_available():
        return None

    def run() -> dict[str, Any]:
        from rdflib import Graph, RDF, RDFS, OWL

        rdf_format = {
            "owl": "xml",
            "rdfxml": "xml",
            "xml": "xml",
            "turtle": "turtle",
            "ttl": "turtle",
            "nt": "nt",
            "jsonld": "json-ld",
        }.get((fmt or "owl").strip().lower(), "xml")
        graph = Graph()
        graph.parse(data=content, format=rdf_format)

        def local(node: Any) -> str:
            text = str(node)
            for sep in ("#", "/"):
                if sep in text:
                    return text.rsplit(sep, 1)[-1]
            return text

        concepts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for cls in list(graph.subjects(RDF.type, OWL.Class)) + list(graph.subjects(RDF.type, RDFS.Class)):
            name = local(cls)
            if not name or name in seen:
                continue
            seen.add(name)
            parent = ""
            for parent_node in graph.objects(cls, RDFS.subClassOf):
                parent = local(parent_node)
                break
            concepts.append(
                {
                    "conceptId": f"con_{name}",
                    "uri": str(cls),
                    "name": name,
                    "normalizedName": name,
                    "subClassOf": parent,
                    "conceptType": "class",
                    "status": "draft",
                    "source": "ingest",
                }
            )

        properties: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        for prop_node in list(graph.subjects(RDF.type, OWL.DatatypeProperty)):
            domain = next((local(x) for x in graph.objects(prop_node, RDFS.domain)), "")
            properties.append(
                {
                    "conceptId": f"con_{domain}" if domain else "",
                    "name": local(prop_node),
                    "propertyKind": "data",
                    "dataType": "",
                    "required": False,
                }
            )
        for prop_node in list(graph.subjects(RDF.type, OWL.ObjectProperty)):
            domain = next((local(x) for x in graph.objects(prop_node, RDFS.domain)), "")
            rng = next((local(x) for x in graph.objects(prop_node, RDFS.range)), "")
            relations.append(
                {
                    "relationDefId": 0,
                    "name": local(prop_node),
                    "relationCode": local(prop_node),
                    "sourceTypeIds": [f"con_{domain}"] if domain else [],
                    "targetTypeIds": [f"con_{rng}"] if rng else [],
                }
            )
        return {
            "concepts": concepts,
            "properties": properties,
            "relations": relations,
            "format": rdf_format,
            "conceptCount": len(concepts),
            "relationCount": len(relations),
        }

    return _guard(run)
