from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class GraphMeta:
    graph_id: str
    name: str = ""
    kb_id: str = ""
    tenant_id: str = ""
    owner_id: str = ""
    schema: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphId": self.graph_id,
            "name": self.name,
            "kbId": self.kb_id,
            "tenantId": self.tenant_id,
            "ownerId": self.owner_id,
            "graphSchema": dict(self.schema),
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphMeta":
        return cls(
            graph_id=str(data.get("graphId") or data.get("graph_id") or ""),
            name=str(data.get("name") or ""),
            kb_id=str(data.get("kbId") or ""),
            tenant_id=str(data.get("tenantId") or ""),
            owner_id=str(data.get("ownerId") or ""),
            schema=dict(data.get("graphSchema") or {}),
            status=str(data.get("status") or "active"),
            created_at=str(data.get("createdAt") or _now_iso()),
            updated_at=str(data.get("updatedAt") or _now_iso()),
        )


@dataclass(frozen=True, slots=True)
class EntityRecord:
    entity_id: str
    graph_id: str
    entity_type: str
    name: str
    normalized_name: str
    doc_id: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    confidence: float = 1.0
    status: str = "active"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entityId": self.entity_id,
            "graphId": self.graph_id,
            "type": self.entity_type,
            "name": self.name,
            "normalizedName": self.normalized_name,
            "properties": dict(self.properties),
            "aliases": list(self.aliases),
            "evidence": [dict(item) for item in self.evidence],
            "confidence": float(self.confidence),
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, graph_id: str = "") -> "EntityRecord":
        name = str(data.get("name") or "")
        entity_type = str(data.get("type") or data.get("entityType") or "concept")
        gid = str(data.get("graphId") or graph_id or "")
        from .ids import entity_id, normalize_name

        normalized = normalize_name(name)
        return cls(
            entity_id=str(data.get("entityId") or entity_id(gid, entity_type, normalized)),
            graph_id=gid,
            entity_type=entity_type,
            name=name,
            normalized_name=str(data.get("normalizedName") or normalized),
            doc_id=str(data.get("docId") or ""),
            properties=dict(data.get("properties") or {}),
            aliases=[str(x) for x in (data.get("aliases") or [])],
            evidence=[dict(x) for x in (data.get("evidence") or []) if isinstance(x, dict)],
            confidence=float(data.get("confidence") or 1.0),
            status=str(data.get("status") or "active"),
            created_at=str(data.get("createdAt") or _now_iso()),
            updated_at=str(data.get("updatedAt") or _now_iso()),
        )


@dataclass(frozen=True, slots=True)
class RelationRecord:
    relation_id: str
    graph_id: str
    relation_type: str
    source_entity_id: str
    target_entity_id: str
    doc_id: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, str]] = field(default_factory=list)
    confidence: float = 1.0
    status: str = "active"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relationId": self.relation_id,
            "graphId": self.graph_id,
            "type": self.relation_type,
            "sourceEntityId": self.source_entity_id,
            "targetEntityId": self.target_entity_id,
            "properties": dict(self.properties),
            "evidence": [dict(item) for item in self.evidence],
            "confidence": float(self.confidence),
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, graph_id: str = "") -> "RelationRecord":
        from .ids import relation_id

        gid = str(data.get("graphId") or graph_id or "")
        source = str(data.get("sourceEntityId") or data.get("source") or "")
        target = str(data.get("targetEntityId") or data.get("target") or "")
        rel_type = str(data.get("type") or data.get("relationType") or "related_to")
        return cls(
            relation_id=str(data.get("relationId") or relation_id(gid, rel_type, source, target)),
            graph_id=gid,
            relation_type=rel_type,
            source_entity_id=source,
            target_entity_id=target,
            doc_id=str(data.get("docId") or ""),
            properties=dict(data.get("properties") or {}),
            evidence=[dict(x) for x in (data.get("evidence") or []) if isinstance(x, dict)],
            confidence=float(data.get("confidence") or 1.0),
            status=str(data.get("status") or "active"),
            created_at=str(data.get("createdAt") or _now_iso()),
            updated_at=str(data.get("updatedAt") or _now_iso()),
        )
