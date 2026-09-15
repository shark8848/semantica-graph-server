from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ikc_sdk.core.models.graph import EntityView as _EntityViewModel
from ikc_sdk.core.models.graph import GraphMeta as _GraphMetaModel
from ikc_sdk.core.models.graph import RelationView as _RelationViewModel

# 线缆形状单一来源 = ikc-sdk-lib 图谱资产模型（G8 归位，0.7.0）：本模块的 dataclass 是
# 引擎**内部记录类型**，`to_dict()` / `from_dict()` 一律经 sdk 模型校验与序列化——字段增删、
# 改名与默认值只在 sdk 契约处发生，引擎侧不再自维护字段清单。`model_dump(exclude_unset=True)`
# 保留「只输出行内确有数据」的既有语义（不注入 sdk 默认值，如 core 视图的
# schemaVersion/nodeCount/edgeCount）。


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
        return _GraphMetaModel.model_validate(
            {
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
        ).model_dump(exclude_unset=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GraphMeta":
        model = _GraphMetaModel.model_validate(
            {
                "graphId": str(data.get("graphId") or data.get("graph_id") or ""),
                "name": str(data.get("name") or ""),
                "kbId": str(data.get("kbId") or ""),
                "tenantId": str(data.get("tenantId") or ""),
                "ownerId": str(data.get("ownerId") or ""),
                "graphSchema": dict(data.get("graphSchema") or {}),
                "status": str(data.get("status") or "active"),
                "createdAt": str(data.get("createdAt") or _now_iso()),
                "updatedAt": str(data.get("updatedAt") or _now_iso()),
            }
        )
        return cls(
            graph_id=model.graphId,
            name=model.name,
            kb_id=model.kbId,
            tenant_id=model.tenantId,
            owner_id=model.ownerId,
            schema=model.graphSchema.model_dump(exclude_unset=True),
            status=model.status,
            created_at=str(model.createdAt or ""),
            updated_at=str(model.updatedAt or ""),
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
        """实体记录/视图线缆形状（记录与视图同形，sdk 侧为 EntityView）。"""
        return _EntityViewModel.model_validate(
            {
                "entityId": self.entity_id,
                "graphId": self.graph_id,
                "docId": self.doc_id,
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
        ).model_dump(exclude_unset=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, graph_id: str = "") -> "EntityRecord":
        name = str(data.get("name") or "")
        entity_type = str(data.get("type") or data.get("entityType") or "concept")
        gid = str(data.get("graphId") or graph_id or "")
        from .ids import entity_id, normalize_name

        normalized = normalize_name(name)
        model = _EntityViewModel.model_validate(
            {
                "entityId": str(data.get("entityId") or entity_id(gid, entity_type, normalized)),
                "graphId": gid,
                "docId": str(data.get("docId") or ""),
                "type": entity_type,
                "name": name,
                "normalizedName": str(data.get("normalizedName") or normalized),
                "properties": dict(data.get("properties") or {}),
                "aliases": [str(x) for x in (data.get("aliases") or [])],
                "evidence": [dict(x) for x in (data.get("evidence") or []) if isinstance(x, dict)],
                "confidence": float(data.get("confidence") or 1.0),
                "status": str(data.get("status") or "active"),
                "createdAt": str(data.get("createdAt") or _now_iso()),
                "updatedAt": str(data.get("updatedAt") or _now_iso()),
            }
        )
        return cls(
            entity_id=model.entityId,
            graph_id=model.graphId,
            entity_type=model.type,
            name=model.name,
            normalized_name=model.normalizedName,
            doc_id=model.docId,
            properties=dict(model.properties),
            aliases=list(model.aliases),
            evidence=[item.model_dump(exclude_unset=True) for item in model.evidence],
            confidence=float(model.confidence),
            status=model.status,
            created_at=str(model.createdAt or ""),
            updated_at=str(model.updatedAt or ""),
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
        """关系记录/视图线缆形状（记录与视图同形，sdk 侧为 RelationView）。"""
        return _RelationViewModel.model_validate(
            {
                "relationId": self.relation_id,
                "graphId": self.graph_id,
                "docId": self.doc_id,
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
        ).model_dump(exclude_unset=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, graph_id: str = "") -> "RelationRecord":
        from .ids import relation_id

        gid = str(data.get("graphId") or graph_id or "")
        source = str(data.get("sourceEntityId") or data.get("source") or "")
        target = str(data.get("targetEntityId") or data.get("target") or "")
        rel_type = str(data.get("type") or data.get("relationType") or "related_to")
        model = _RelationViewModel.model_validate(
            {
                "relationId": str(data.get("relationId") or relation_id(gid, rel_type, source, target)),
                "graphId": gid,
                "docId": str(data.get("docId") or ""),
                "type": rel_type,
                "sourceEntityId": source,
                "targetEntityId": target,
                "properties": dict(data.get("properties") or {}),
                "evidence": [dict(x) for x in (data.get("evidence") or []) if isinstance(x, dict)],
                "confidence": float(data.get("confidence") or 1.0),
                "status": str(data.get("status") or "active"),
                "createdAt": str(data.get("createdAt") or _now_iso()),
                "updatedAt": str(data.get("updatedAt") or _now_iso()),
            }
        )
        return cls(
            relation_id=model.relationId,
            graph_id=model.graphId,
            relation_type=model.type,
            source_entity_id=model.sourceEntityId,
            target_entity_id=model.targetEntityId,
            doc_id=model.docId,
            properties=dict(model.properties),
            evidence=[item.model_dump(exclude_unset=True) for item in model.evidence],
            confidence=float(model.confidence),
            status=model.status,
            created_at=str(model.createdAt or ""),
            updated_at=str(model.updatedAt or ""),
        )
