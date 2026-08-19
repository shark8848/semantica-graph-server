"""增量合并规则：对齐 open-ikc GraphStore.merge_node / merge_edge 语义。"""

from __future__ import annotations

from .models import EntityRecord, RelationRecord


def merge_entity(existing: EntityRecord | None, incoming: EntityRecord) -> EntityRecord:
    if existing is None:
        return incoming
    return EntityRecord(
        entity_id=incoming.entity_id,
        graph_id=incoming.graph_id,
        entity_type=incoming.entity_type,
        name=incoming.name,
        normalized_name=incoming.normalized_name,
        doc_id=incoming.doc_id,
        properties=dict(incoming.properties),
        aliases=sorted(set(existing.aliases) | set(incoming.aliases)),
        evidence=existing.evidence + [item for item in incoming.evidence if item not in existing.evidence],
        confidence=max(existing.confidence, incoming.confidence),
        status=incoming.status,
        created_at=existing.created_at,
        updated_at=incoming.updated_at,
    )


def merge_relation(existing: RelationRecord | None, incoming: RelationRecord) -> RelationRecord:
    if existing is None:
        return incoming
    return RelationRecord(
        relation_id=incoming.relation_id,
        graph_id=incoming.graph_id,
        relation_type=incoming.relation_type,
        source_entity_id=incoming.source_entity_id,
        target_entity_id=incoming.target_entity_id,
        doc_id=incoming.doc_id,
        properties=dict(incoming.properties),
        evidence=existing.evidence + [item for item in incoming.evidence if item not in existing.evidence],
        confidence=max(existing.confidence, incoming.confidence),
        status=incoming.status,
        created_at=existing.created_at,
        updated_at=incoming.updated_at,
    )
