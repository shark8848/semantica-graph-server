"""RDF/SPARQL 视图适配层：引擎 SQLite 记录 → OxigraphStore 内存视图。

对应设计文档「后续演进」：以 OxigraphStore 提供 RDF/SPARQL 视图（只读补强，
源仍是 SQLite 稳定 ID 记录）。本模块守卫式依赖 ``pyoxigraph`` 与
``semantica.triplet_store.oxigraph_store``：不可用时由 service 层判定降级，
不影响引擎核心功能（持久化、查询、导出 jsonl/json）。

词汇表约定（稳定 ID → 稳定 IRI，跨重建稳定）：
- 实体/关系 IRI：``urn:ge:entity|relation:{graph_id}:{stable_id}``
  （分段经 IRI 百分号编码，保证任何 ID 字符安全）；
- 谓词命名空间 ``urn:ge:kg#``：``type`` 用 rdf:type，名称用 rdfs:label，
  其余字段语义与 EntityRecord/RelationRecord 对齐（entityType/relationType、
  docId/confidence/alias/property/evidence、source/target）。

属性值类型映射：bool/int/float 映射为 xsd 类型字面量，其余（含 dict/list）
序列化为 JSON 字符串字面量，保证往返可读。
"""

from __future__ import annotations

import io
import json
import logging
import urllib.parse
from typing import Any, Iterable

logger = logging.getLogger("graph_engine.rdf")

#: 进程内探测结果缓存（导入失败开销集中于 pyoxigraph 本地库加载，只探测一次）
_rdf_available: bool | None = None

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
XSD_BOOLEAN = "http://www.w3.org/2001/XMLSchema#boolean"
XSD_INTEGER = "http://www.w3.org/2001/XMLSchema#integer"
XSD_DOUBLE = "http://www.w3.org/2001/XMLSchema#double"
KG = "urn:ge:kg#"
_ENTITY_NS = "urn:ge:entity:"
_RELATION_NS = "urn:ge:relation:"

FORMATS = ("turtle", "ttl", "nt", "nq", "rdfxml")


def _quote(segment: str) -> str:
    """IRI 段编码：任何 ID/图 ID 字符都能安全落入 IRI。"""
    return urllib.parse.quote(str(segment), safe="")


def entity_uri(graph_id_value: str, entity_id_value: str) -> str:
    return f"{_ENTITY_NS}{_quote(graph_id_value)}:{_quote(entity_id_value)}"


def relation_uri(graph_id_value: str, relation_id_value: str) -> str:
    return f"{_RELATION_NS}{_quote(graph_id_value)}:{_quote(relation_id_value)}"


def rdf_available() -> bool:
    """Oxigraph/RDF 视图可用性：pyoxigraph + semantica oxigraph store 可导入。"""
    global _rdf_available
    if _rdf_available is not None:
        return _rdf_available
    try:
        import pyoxigraph  # noqa: F401
        from semantica.triplet_store.oxigraph_store import OxigraphStore  # noqa: F401

        _rdf_available = True
    except Exception as exc:  # 守卫式：缺本地库/缺 semantica 均视为不可用
        logger.warning("RDF/SPARQL 视图不可用：%s", exc)
        _rdf_available = False
    return _rdf_available


def _triplet(subject: str, predicate: str, object_value: str, *, datatype: str = "") -> Any:
    """构造 semantica Triplet（与 OxigraphStore.add_triplets 兼容）。"""
    from semantica.semantic_extract.triplet_extractor import Triplet

    metadata: dict[str, Any] = {}
    if datatype:
        metadata["datatype"] = datatype
    return Triplet(
        subject=subject,
        predicate=predicate,
        object=object_value,
        confidence=1.0,
        metadata=metadata,
    )


def _scalar_literal(value: Any) -> tuple[str, str]:
    """JSON 标量 → (字面量文本, xsd datatype IRI)；非标量序列化为 JSON 字符串。"""
    if isinstance(value, bool):
        return ("true" if value else "false", XSD_BOOLEAN)
    if isinstance(value, int):
        return (str(value), XSD_INTEGER)
    if isinstance(value, float):
        return (str(value), XSD_DOUBLE)
    if value is None:
        return ("", "")
    if isinstance(value, (dict, list)):
        return (json.dumps(value, ensure_ascii=False), "")
    return (str(value), "")


def _property_predicate(key: str) -> str:
    return f"{KG}prop:{_quote(key)}"


def _evidence_triplets(subject: str, evidence: list[dict[str, Any]]) -> list[Any]:
    return [
        _triplet(subject, f"{KG}evidence", json.dumps(item, ensure_ascii=False))
        for item in evidence or []
    ]


def _entity_triplets(record: Any) -> list[Any]:
    graph_id_value = str(record.graph_id)
    subject = entity_uri(graph_id_value, str(record.entity_id))
    out = [
        _triplet(subject, RDF_TYPE, f"{KG}Entity"),
        _triplet(subject, RDFS_LABEL, str(record.name or record.normalized_name)),
        _triplet(subject, f"{KG}entityType", str(record.entity_type)),
        _triplet(subject, f"{KG}name", str(record.name)),
    ]
    if record.normalized_name and record.normalized_name != record.name:
        out.append(_triplet(subject, f"{KG}normalizedName", str(record.normalized_name)))
    if record.doc_id:
        out.append(_triplet(subject, f"{KG}docId", str(record.doc_id)))
    out.append(
        _triplet(subject, f"{KG}confidence", str(float(record.confidence)), datatype=XSD_DOUBLE)
    )
    for alias in record.aliases or []:
        out.append(_triplet(subject, f"{KG}alias", str(alias)))
    for key, value in (record.properties or {}).items():
        literal, datatype = _scalar_literal(value)
        out.append(_triplet(subject, _property_predicate(key), literal, datatype=datatype))
    out.extend(_evidence_triplets(subject, record.evidence))
    return out


def _relation_triplets(record: Any) -> list[Any]:
    graph_id_value = str(record.graph_id)
    subject = relation_uri(graph_id_value, str(record.relation_id))
    out = [
        _triplet(subject, RDF_TYPE, f"{KG}Relation"),
        _triplet(subject, RDFS_LABEL, str(record.relation_type)),
        _triplet(subject, f"{KG}relationType", str(record.relation_type)),
        _triplet(subject, f"{KG}source", entity_uri(graph_id_value, str(record.source_entity_id))),
        _triplet(subject, f"{KG}target", entity_uri(graph_id_value, str(record.target_entity_id))),
    ]
    if record.doc_id:
        out.append(_triplet(subject, f"{KG}docId", str(record.doc_id)))
    out.append(
        _triplet(subject, f"{KG}confidence", str(float(record.confidence)), datatype=XSD_DOUBLE)
    )
    for key, value in (record.properties or {}).items():
        literal, datatype = _scalar_literal(value)
        out.append(_triplet(subject, _property_predicate(key), literal, datatype=datatype))
    out.extend(_evidence_triplets(subject, record.evidence))
    return out


def build_view(graph_id_value: str, entities: Iterable[Any], relations: Iterable[Any]) -> Any:
    """活动记录 → 内存 OxigraphStore 视图（图内实体/关系导入为 RDF 三元组）。"""
    if not rdf_available():
        raise RuntimeError("RDF/SPARQL 视图不可用：缺少 pyoxigraph 或 semantica oxigraph store")
    from semantica.triplet_store.oxigraph_store import OxigraphStore

    store = OxigraphStore()
    triplets: list[Any] = []
    for record in entities:
        triplets.extend(_entity_triplets(record))
    for record in relations:
        triplets.extend(_relation_triplets(record))
    if triplets:
        store.add_triplets(triplets)
    return store


def run_sparql(
    graph_id_value: str,
    entities: Iterable[Any],
    relations: Iterable[Any],
    query: str,
) -> dict[str, Any]:
    """执行 SPARQL 查询，返回 OxigraphStore 的 bindings/boolean/triples 结果形状。"""
    store = build_view(graph_id_value, entities, relations)
    return store.execute_sparql(str(query))


def _format_object(fmt: str) -> Any:
    from pyoxigraph import RdfFormat

    return {
        "turtle": RdfFormat.TURTLE,
        "ttl": RdfFormat.TURTLE,
        "nt": RdfFormat.N_TRIPLES,
        "nq": RdfFormat.N_QUADS,
        "rdfxml": RdfFormat.RDF_XML,
    }[fmt]


def export_rdf(
    graph_id_value: str,
    entities: Iterable[Any],
    relations: Iterable[Any],
    fmt: str = "turtle",
) -> str:
    """RDF 序列化导出（turtle/nt/nq/rdfxml），返回文本内容。"""
    if fmt not in FORMATS:
        raise ValueError(f"不支持的 RDF 格式：{fmt}")
    store = build_view(graph_id_value, entities, relations)
    import pyoxigraph

    buffer = io.BytesIO()
    store.store.dump(
        buffer,
        format=_format_object(fmt),
        from_graph=pyoxigraph.DefaultGraph(),
    )
    return buffer.getvalue().decode("utf-8")
