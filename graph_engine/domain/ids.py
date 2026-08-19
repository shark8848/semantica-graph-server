"""稳定 ID 派生规则：与 open-ikc 现有实现保持一致，保证跨侧兼容。"""

from __future__ import annotations

import hashlib


def normalize_name(name: str) -> str:
    """实体名规范化：去除空白与标点、统一小写，用于稳定键派生与同名对齐。"""
    normalized = "".join(ch for ch in (name or "").strip().lower() if ch.isalnum())
    return normalized or "untitled"


def graph_id(kb_id: str) -> str:
    """库级图谱 ID：graph_ + sha1(kbId) 前 12 位，随库生命周期稳定。"""
    digest = hashlib.sha1(kb_id.encode("utf-8")).hexdigest()
    return f"graph_{digest[:12]}"


def entity_id(graph_id_value: str, entity_type: str, normalized_name: str) -> str:
    """稳定实体 ID：ent_ + sha1(graphId + type + normalizedName) 前 12 位。"""
    digest = hashlib.sha1(
        f"{graph_id_value}:{entity_type}:{normalized_name}".encode("utf-8")
    ).hexdigest()
    return f"ent_{digest[:12]}"


def relation_id(
    graph_id_value: str,
    relation_type: str,
    source_entity_id: str,
    target_entity_id: str,
) -> str:
    """稳定关系 ID：rel_ + sha1(graphId + type + source + target) 前 12 位。"""
    digest = hashlib.sha1(
        f"{graph_id_value}:{relation_type}:{source_entity_id}:{target_entity_id}".encode("utf-8")
    ).hexdigest()
    return f"rel_{digest[:12]}"
