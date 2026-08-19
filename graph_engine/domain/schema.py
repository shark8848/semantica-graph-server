"""graphSchema 校验：与 open-ikc validate_graph_schema 规则一致，非法即抛错。"""

from __future__ import annotations

from typing import Any

from ..errors import InvalidParamsError


def validate_graph_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    schema = dict(schema or {})
    entity_types = schema.get("entityTypes", [])
    relation_types = schema.get("relationTypes", [])
    if not isinstance(entity_types, list) or not isinstance(relation_types, list):
        raise InvalidParamsError(
            "graphSchema 非法", field="graphSchema", reason="entityTypes/relationTypes 必须为数组"
        )

    seen_entity: set[str] = set()
    for item in entity_types:
        if not isinstance(item, dict) or not str(item.get("type", "")).strip():
            raise InvalidParamsError(
                "graphSchema 非法", field="graphSchema.entityTypes", reason="type 必填且不能为空"
            )
        entity_type = str(item["type"]).strip()
        if entity_type in seen_entity:
            raise InvalidParamsError(
                "graphSchema 非法", field="graphSchema.entityTypes", reason=f"实体类型重复：{entity_type}"
            )
        seen_entity.add(entity_type)

    seen_relation: set[str] = set()
    for item in relation_types:
        if not isinstance(item, dict) or not str(item.get("type", "")).strip():
            raise InvalidParamsError(
                "graphSchema 非法", field="graphSchema.relationTypes", reason="type 必填且不能为空"
            )
        relation_type = str(item["type"]).strip()
        if relation_type in seen_relation:
            raise InvalidParamsError(
                "graphSchema 非法", field="graphSchema.relationTypes", reason=f"关系类型重复：{relation_type}"
            )
        seen_relation.add(relation_type)
        for key in ("sourceTypes", "targetTypes"):
            value = item.get(key, [])
            if value is not None and (not isinstance(value, list) or not all(isinstance(x, str) for x in value)):
                raise InvalidParamsError(
                    "graphSchema 非法", field=f"graphSchema.relationTypes[].{key}", reason="必须为字符串数组"
                )
    return schema


def validate_entity_type(schema: dict[str, Any], entity_type: str) -> None:
    declared = {
        str(item.get("type", "")).strip()
        for item in (schema.get("entityTypes") or [])
        if isinstance(item, dict)
    }
    if declared and entity_type not in declared:
        raise InvalidParamsError(
            "实体类型未声明", field="type", reason=f"graphSchema 未声明实体类型：{entity_type}"
        )


def validate_relation_type(schema: dict[str, Any], relation_type: str) -> None:
    declared = {
        str(item.get("type", "")).strip()
        for item in (schema.get("relationTypes") or [])
        if isinstance(item, dict)
    }
    if declared and relation_type not in declared:
        raise InvalidParamsError(
            "关系类型未声明", field="type", reason=f"graphSchema 未声明关系类型：{relation_type}"
        )
