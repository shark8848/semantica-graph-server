from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _extra(data: dict[str, Any], known: set[str]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key not in known}


def _records(data: Any) -> list[dict[str, Any]]:
    return [item for item in (data or []) if isinstance(item, dict)]


@dataclass
class GraphMeta:
    """图谱元信息（对应 POST/GET /api/v1/graph/graphs）。"""

    graphId: str
    name: str = ""
    kbId: str = ""
    tenantId: str = ""
    ownerId: str = ""
    graphSchema: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    createdAt: str = ""
    updatedAt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphMeta":
        return cls(
            graphId=str(data.get("graphId", "")),
            name=str(data.get("name", "")),
            kbId=str(data.get("kbId", "")),
            tenantId=str(data.get("tenantId", "")),
            ownerId=str(data.get("ownerId", "")),
            graphSchema=dict(data.get("graphSchema") or {}),
            status=str(data.get("status", "active")),
            createdAt=str(data.get("createdAt", "")),
            updatedAt=str(data.get("updatedAt", "")),
            extra=_extra(
                data,
                {
                    "graphId",
                    "name",
                    "kbId",
                    "tenantId",
                    "ownerId",
                    "graphSchema",
                    "status",
                    "createdAt",
                    "updatedAt",
                },
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "name": self.name,
            "kbId": self.kbId,
            "tenantId": self.tenantId,
            "ownerId": self.ownerId,
            "graphSchema": dict(self.graphSchema),
            "status": self.status,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
            **self.extra,
        }


@dataclass
class GraphListData:
    """图谱列表（GET /api/v1/graph/graphs）。"""

    total: int = 0
    items: list[GraphMeta] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphListData":
        return cls(
            total=int(data.get("total") or 0),
            items=[GraphMeta.from_dict(item) for item in _records(data.get("items"))],
        )

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "items": [item.to_dict() for item in self.items]}


@dataclass
class DeleteResult:
    """删除图谱结果（DELETE /api/v1/graph/graphs/{graphId}）。"""

    graphId: str = ""
    deleted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeleteResult":
        return cls(
            graphId=str(data.get("graphId", "")),
            deleted=bool(data.get("deleted")),
            extra=_extra(data, {"graphId", "deleted"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"graphId": self.graphId, "deleted": self.deleted, **self.extra}


@dataclass
class SemanticaMeta:
    """建图时 semantica 链路元信息（build/merge 响应的 semantica 字段）。"""

    semantica: bool = False
    entities: int = 0
    relationships: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SemanticaMeta":
        data = dict(data or {})
        return cls(
            semantica=bool(data.get("semantica")),
            entities=int(data.get("entities") or 0),
            relationships=int(data.get("relationships") or 0),
            metadata=dict(data.get("metadata") or {}),
            error=str(data.get("error") or ""),
            extra=_extra(data, {"semantica", "entities", "relationships", "metadata", "error"}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "semantica": self.semantica,
            "entities": self.entities,
            "relationships": self.relationships,
            "metadata": dict(self.metadata),
        }
        if self.error:
            payload["error"] = self.error
        return {**payload, **self.extra}


@dataclass
class BuildResult:
    """建图/合并结果（POST .../build、POST .../merge）。"""

    graphId: str = ""
    entityCount: int = 0
    relationCount: int = 0
    semantica: SemanticaMeta = field(default_factory=SemanticaMeta)
    llm: dict[str, Any] = field(default_factory=dict)
    deprecated: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BuildResult":
        deprecated_raw = data.get("deprecated")
        deprecated = int(deprecated_raw) if isinstance(deprecated_raw, (int, float)) else 0
        return cls(
            graphId=str(data.get("graphId", "")),
            entityCount=int(data.get("entityCount") or 0),
            relationCount=int(data.get("relationCount") or 0),
            semantica=SemanticaMeta.from_dict(data.get("semantica")),
            llm=dict(data.get("llm") or {}),
            deprecated=deprecated,
            extra=_extra(
                data,
                {"graphId", "entityCount", "relationCount", "semantica", "llm", "deprecated"},
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "graphId": self.graphId,
            "entityCount": self.entityCount,
            "relationCount": self.relationCount,
            "semantica": self.semantica.to_dict(),
        }
        if self.llm:
            payload["llm"] = dict(self.llm)
        if self.deprecated:
            payload["deprecated"] = self.deprecated
        return {**payload, **self.extra}


@dataclass
class DeprecateResult:
    """按 docId 增量废弃结果（POST .../deprecate-doc）。"""

    graphId: str = ""
    docId: str = ""
    deprecated: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeprecateResult":
        return cls(
            graphId=str(data.get("graphId", "")),
            docId=str(data.get("docId", "")),
            deprecated=int(data.get("deprecated") or 0),
            extra=_extra(data, {"graphId", "docId", "deprecated"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "docId": self.docId,
            "deprecated": self.deprecated,
            **self.extra,
        }


@dataclass
class TypeCount:
    """类型计数（entityTypes/relationTypes 条目）。"""

    type: str = ""
    count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TypeCount":
        return cls(
            type=str(data.get("type", "")),
            count=int(data.get("count") or 0),
            extra=_extra(data, {"type", "count"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "count": self.count, **self.extra}


@dataclass
class SchemaCoverage:
    """schema 覆盖率（声明类型在活动记录中的占比）。"""

    entity: float = 0.0
    relation: float = 0.0
    overall: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SchemaCoverage":
        data = dict(data or {})
        return cls(
            entity=float(data.get("entity") or 0.0),
            relation=float(data.get("relation") or 0.0),
            overall=float(data.get("overall") or 0.0),
            extra=_extra(data, {"entity", "relation", "overall"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "relation": self.relation,
            "overall": self.overall,
            **self.extra,
        }


@dataclass
class StatResult:
    """图谱统计（GET .../stat）。"""

    graphId: str = ""
    kbId: str = ""
    nodeCount: int = 0
    edgeCount: int = 0
    entityTypes: list[TypeCount] = field(default_factory=list)
    relationTypes: list[TypeCount] = field(default_factory=list)
    schemaCoverage: SchemaCoverage = field(default_factory=SchemaCoverage)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StatResult":
        return cls(
            graphId=str(data.get("graphId", "")),
            kbId=str(data.get("kbId", "")),
            nodeCount=int(data.get("nodeCount") or 0),
            edgeCount=int(data.get("edgeCount") or 0),
            entityTypes=[TypeCount.from_dict(item) for item in _records(data.get("entityTypes"))],
            relationTypes=[TypeCount.from_dict(item) for item in _records(data.get("relationTypes"))],
            schemaCoverage=SchemaCoverage.from_dict(data.get("schemaCoverage")),
            extra=_extra(
                data,
                {
                    "graphId",
                    "kbId",
                    "nodeCount",
                    "edgeCount",
                    "entityTypes",
                    "relationTypes",
                    "schemaCoverage",
                },
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "kbId": self.kbId,
            "nodeCount": self.nodeCount,
            "edgeCount": self.edgeCount,
            "entityTypes": [item.to_dict() for item in self.entityTypes],
            "relationTypes": [item.to_dict() for item in self.relationTypes],
            "schemaCoverage": self.schemaCoverage.to_dict(),
            **self.extra,
        }


@dataclass
class Entity:
    """实体记录 DTO（nodes/neighbors/center 等复用）。"""

    entityId: str
    graphId: str = ""
    docId: str = ""
    type: str = "concept"
    name: str = ""
    normalizedName: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    status: str = "active"
    createdAt: str = ""
    updatedAt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    _KNOWN = {
        "entityId",
        "graphId",
        "docId",
        "type",
        "name",
        "normalizedName",
        "properties",
        "aliases",
        "evidence",
        "confidence",
        "status",
        "createdAt",
        "updatedAt",
    }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        return cls(
            entityId=str(data.get("entityId", "")),
            graphId=str(data.get("graphId", "")),
            docId=str(data.get("docId", "")),
            type=str(data.get("type", "concept")),
            name=str(data.get("name", "")),
            normalizedName=str(data.get("normalizedName", "")),
            properties=dict(data.get("properties") or {}),
            aliases=[str(item) for item in (data.get("aliases") or [])],
            evidence=[dict(item) for item in _records(data.get("evidence"))],
            confidence=float(data.get("confidence") or 1.0),
            status=str(data.get("status", "active")),
            createdAt=str(data.get("createdAt", "")),
            updatedAt=str(data.get("updatedAt", "")),
            extra=_extra(data, cls._KNOWN),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entityId": self.entityId,
            "graphId": self.graphId,
            "docId": self.docId,
            "type": self.type,
            "name": self.name,
            "normalizedName": self.normalizedName,
            "properties": dict(self.properties),
            "aliases": list(self.aliases),
            "evidence": [dict(item) for item in self.evidence],
            "confidence": self.confidence,
            "status": self.status,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
            **self.extra,
        }


@dataclass
class Relation:
    """关系记录 DTO（edges/neighbors 等复用）。"""

    relationId: str
    graphId: str = ""
    docId: str = ""
    type: str = "related_to"
    sourceEntityId: str = ""
    targetEntityId: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    status: str = "active"
    createdAt: str = ""
    updatedAt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    _KNOWN = {
        "relationId",
        "graphId",
        "docId",
        "type",
        "sourceEntityId",
        "targetEntityId",
        "properties",
        "evidence",
        "confidence",
        "status",
        "createdAt",
        "updatedAt",
    }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Relation":
        return cls(
            relationId=str(data.get("relationId", "")),
            graphId=str(data.get("graphId", "")),
            docId=str(data.get("docId", "")),
            type=str(data.get("type", "related_to")),
            sourceEntityId=str(data.get("sourceEntityId", "")),
            targetEntityId=str(data.get("targetEntityId", "")),
            properties=dict(data.get("properties") or {}),
            evidence=[dict(item) for item in _records(data.get("evidence"))],
            confidence=float(data.get("confidence") or 1.0),
            status=str(data.get("status", "active")),
            createdAt=str(data.get("createdAt", "")),
            updatedAt=str(data.get("updatedAt", "")),
            extra=_extra(data, cls._KNOWN),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "relationId": self.relationId,
            "graphId": self.graphId,
            "docId": self.docId,
            "type": self.type,
            "sourceEntityId": self.sourceEntityId,
            "targetEntityId": self.targetEntityId,
            "properties": dict(self.properties),
            "evidence": [dict(item) for item in self.evidence],
            "confidence": self.confidence,
            "status": self.status,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
            **self.extra,
        }


@dataclass
class EntityListData:
    """分页实体列表（GET .../nodes）。"""

    graphId: str = ""
    total: int = 0
    page: int = 1
    pageSize: int = 20
    items: list[Entity] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EntityListData":
        return cls(
            graphId=str(data.get("graphId", "")),
            total=int(data.get("total") or 0),
            page=int(data.get("page") or 1),
            pageSize=int(data.get("pageSize") or 20),
            items=[Entity.from_dict(item) for item in _records(data.get("items"))],
            extra=_extra(data, {"graphId", "total", "page", "pageSize", "items"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "total": self.total,
            "page": self.page,
            "pageSize": self.pageSize,
            "items": [item.to_dict() for item in self.items],
            **self.extra,
        }


@dataclass
class RelationListData:
    """分页关系统列表（GET .../edges）。"""

    graphId: str = ""
    total: int = 0
    page: int = 1
    pageSize: int = 20
    items: list[Relation] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelationListData":
        return cls(
            graphId=str(data.get("graphId", "")),
            total=int(data.get("total") or 0),
            page=int(data.get("page") or 1),
            pageSize=int(data.get("pageSize") or 20),
            items=[Relation.from_dict(item) for item in _records(data.get("items"))],
            extra=_extra(data, {"graphId", "total", "page", "pageSize", "items"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "total": self.total,
            "page": self.page,
            "pageSize": self.pageSize,
            "items": [item.to_dict() for item in self.items],
            **self.extra,
        }


@dataclass
class NeighborData:
    """实体邻域（GET .../neighbors）。"""

    graphId: str = ""
    entityId: str = ""
    depth: int = 1
    center: Entity | None = None
    nodes: list[Entity] = field(default_factory=list)
    edges: list[Relation] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NeighborData":
        center = data.get("center")
        return cls(
            graphId=str(data.get("graphId", "")),
            entityId=str(data.get("entityId", "")),
            depth=int(data.get("depth") or 1),
            center=Entity.from_dict(center) if isinstance(center, dict) else None,
            nodes=[Entity.from_dict(item) for item in _records(data.get("nodes"))],
            edges=[Relation.from_dict(item) for item in _records(data.get("edges"))],
            extra=_extra(data, {"graphId", "entityId", "depth", "center", "nodes", "edges"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "entityId": self.entityId,
            "depth": self.depth,
            "center": self.center.to_dict() if self.center else None,
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
            **self.extra,
        }


@dataclass
class GraphPath:
    """最短路径条目（GET .../paths 的 items 元素）。"""

    entityIds: list[str] = field(default_factory=list)
    nodeNames: list[str] = field(default_factory=list)
    length: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphPath":
        return cls(
            entityIds=[str(item) for item in (data.get("entityIds") or [])],
            nodeNames=[str(item) for item in (data.get("nodeNames") or [])],
            length=int(data.get("length") or 0),
            extra=_extra(data, {"entityIds", "nodeNames", "length"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entityIds": list(self.entityIds),
            "nodeNames": list(self.nodeNames),
            "length": self.length,
            **self.extra,
        }


@dataclass
class PathListData:
    """最短路径结果（GET .../paths）。"""

    graphId: str = ""
    sourceEntityId: str = ""
    targetEntityId: str = ""
    total: int = 0
    items: list[GraphPath] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PathListData":
        return cls(
            graphId=str(data.get("graphId", "")),
            sourceEntityId=str(data.get("sourceEntityId", "")),
            targetEntityId=str(data.get("targetEntityId", "")),
            total=int(data.get("total") or 0),
            items=[GraphPath.from_dict(item) for item in _records(data.get("items"))],
            extra=_extra(
                data,
                {"graphId", "sourceEntityId", "targetEntityId", "total", "items"},
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "sourceEntityId": self.sourceEntityId,
            "targetEntityId": self.targetEntityId,
            "total": self.total,
            "items": [item.to_dict() for item in self.items],
            **self.extra,
        }


@dataclass
class AnalyticsData:
    """图结构分析（GET .../analytics）；analysis 结构随 semantica 可用性变化，按原样透传。"""

    graphId: str = ""
    analysis: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalyticsData":
        return cls(
            graphId=str(data.get("graphId", "")),
            analysis=dict(data.get("analysis") or {}),
            extra=_extra(data, {"graphId", "analysis"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"graphId": self.graphId, "analysis": dict(self.analysis), **self.extra}


@dataclass
class ExportResult:
    """导出结果（GET .../export）。"""

    graphId: str = ""
    format: str = "jsonl"
    total: int = 0
    content: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExportResult":
        return cls(
            graphId=str(data.get("graphId", "")),
            format=str(data.get("format", "jsonl")),
            total=int(data.get("total") or 0),
            content=str(data.get("content", "")),
            extra=_extra(data, {"graphId", "format", "total", "content"}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "format": self.format,
            "total": self.total,
            "content": self.content,
            **self.extra,
        }


@dataclass
class SparqlResult:
    """SPARQL 查询结果（POST .../sparql）。"""

    graphId: str = ""
    query: str = ""
    success: bool = True
    bindings: list[dict[str, Any]] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    triples: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    rowLimit: int = 0
    truncated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SparqlResult":
        return cls(
            graphId=str(data.get("graphId", "")),
            query=str(data.get("query", "")),
            success=bool(data.get("success", True)),
            bindings=[dict(item) for item in _records(data.get("bindings"))],
            variables=[str(item) for item in (data.get("variables") or [])],
            triples=list(data.get("triples") or []),
            metadata=dict(data.get("metadata") or {}),
            rowLimit=int(data.get("rowLimit") or 0),
            truncated=bool(data.get("truncated")),
            extra=_extra(
                data,
                {
                    "graphId",
                    "query",
                    "success",
                    "bindings",
                    "variables",
                    "triples",
                    "metadata",
                    "rowLimit",
                    "truncated",
                },
            ),
        )

    @property
    def boolean(self) -> bool | None:
        """ASK 查询结果（metadata.boolean）；非 ASK 查询返回 None。"""
        value = self.metadata.get("boolean")
        return bool(value) if value is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "query": self.query,
            "success": self.success,
            "bindings": [dict(item) for item in self.bindings],
            "variables": list(self.variables),
            "triples": list(self.triples),
            "metadata": dict(self.metadata),
            "rowLimit": self.rowLimit,
            "truncated": self.truncated,
            **self.extra,
        }


@dataclass
class SearchHit:
    """语义检索命中（GET .../search 的 hits 元素）。"""

    id: str = ""
    kind: str = "record"
    type: str = ""
    name: str = ""
    docId: str = ""
    score: float = 0.0
    confidence: float = 0.0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchHit":
        return cls(
            id=str(data.get("id", "")),
            kind=str(data.get("kind", "record")),
            type=str(data.get("type", "")),
            name=str(data.get("name", "")),
            docId=str(data.get("docId", "")),
            score=float(data.get("score") or 0.0),
            confidence=float(data.get("confidence") or 0.0),
            evidence=[dict(item) for item in _records(data.get("evidence"))],
            extra=_extra(
                data,
                {"id", "kind", "type", "name", "docId", "score", "confidence", "evidence"},
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "type": self.type,
            "name": self.name,
            "docId": self.docId,
            "score": self.score,
            "confidence": self.confidence,
            "evidence": [dict(item) for item in self.evidence],
            **self.extra,
        }


@dataclass
class SearchData:
    """语义检索结果（GET .../search）；检索链不可用时为确定降级结果。"""

    graphId: str = ""
    query: str = ""
    topK: int = 10
    semantica: bool = False
    total: int = 0
    reason: str = ""
    hits: list[SearchHit] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchData":
        return cls(
            graphId=str(data.get("graphId", "")),
            query=str(data.get("query", "")),
            topK=int(data.get("topK") or 10),
            semantica=bool(data.get("semantica")),
            total=int(data.get("total") or 0),
            reason=str(data.get("reason", "")),
            hits=[SearchHit.from_dict(item) for item in _records(data.get("hits"))],
            extra=_extra(
                data,
                {"graphId", "query", "topK", "semantica", "total", "reason", "hits"},
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graphId,
            "query": self.query,
            "topK": self.topK,
            "semantica": self.semantica,
            "total": self.total,
            "reason": self.reason,
            "hits": [item.to_dict() for item in self.hits],
            **self.extra,
        }


@dataclass
class IndexStatusData:
    """语义检索/向量索引可用状态（GET .../index-status）。"""

    graphId: str = ""
    semantica: bool = False
    backend: str = ""
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IndexStatusData":
        return cls(
            graphId=str(data.get("graphId", "")),
            semantica=bool(data.get("semantica")),
            backend=str(data.get("backend", "")),
            reason=str(data.get("reason", "")),
            extra=_extra(data, {"graphId", "semantica", "backend", "reason"}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"graphId": self.graphId, "semantica": self.semantica}
        if self.backend:
            payload["backend"] = self.backend
        if self.reason:
            payload["reason"] = self.reason
        return {**payload, **self.extra}


@dataclass
class JobData:
    """异步任务（POST .../build async、jobs 域）。"""

    jobId: str
    graphId: str = ""
    task: str = ""
    status: str = "pending"
    payload: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None
    createdAt: str = ""
    updatedAt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobData":
        return cls(
            jobId=str(data.get("jobId", "")),
            graphId=str(data.get("graphId", "")),
            task=str(data.get("task", "")),
            status=str(data.get("status", "pending")),
            payload=dict(data.get("payload") or {}),
            result=data.get("result"),
            error=data.get("error"),
            createdAt=str(data.get("createdAt", "")),
            updatedAt=str(data.get("updatedAt", "")),
            extra=_extra(
                data,
                {
                    "jobId",
                    "graphId",
                    "task",
                    "status",
                    "payload",
                    "result",
                    "error",
                    "createdAt",
                    "updatedAt",
                },
            ),
        )

    @property
    def finished(self) -> bool:
        """任务是否已终结（success/failed）。"""
        return self.status in ("success", "failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.jobId,
            "graphId": self.graphId,
            "task": self.task,
            "status": self.status,
            "payload": dict(self.payload),
            "result": self.result,
            "error": self.error,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
            **self.extra,
        }


@dataclass
class JobListData:
    """任务列表（GET /api/v1/graph/jobs）。"""

    total: int = 0
    items: list[JobData] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobListData":
        return cls(
            total=int(data.get("total") or 0),
            items=[JobData.from_dict(item) for item in _records(data.get("items"))],
        )

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "items": [item.to_dict() for item in self.items]}
