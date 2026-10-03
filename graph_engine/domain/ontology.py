"""本体（Ontology）域：定义视图归一、候选聚合、一致性体检与产物 diff。

边界（`docs/设计方案-本体.md` D1 / D15）：

- **定义唯一权威在 core**（`/api/v1/ontologies/*`）；引擎只消费 core 下发的
  定义视图与**编译产物**（`graphSchema`），只缓存编译产物快照（见 persistence）。
- **不本地模拟写语义**：本模块纯只读推导（候选 / 校验 / 覆盖率 / diff），不产生定义。
- 越界**告警 + 计数，不阻断**（D4 / D5）：`validate_graph` 只出报告，不抛错。

字段形状单一来源 = `ikc_sdk.core.models.ontology`（问题码 / 候选 / 报告 / 导出结果）
与 `ikc_sdk.core.models.graph`（`GraphSchema` / `SchemaCoverage`）：本模块的常量与
返回值都按这些模型组织，禁止在本仓自维护同形字段清单。
"""

from __future__ import annotations

from typing import Any

# 问题码（与 ikc_sdk.core.models.ontology.ONTOLOGY_ISSUE_CODE_VALUES 对齐，逐字一致）
ISSUE_ENDPOINT_VIOLATION = "endpoint_violation"
ISSUE_MISSING_REQUIRED = "missing_required"
ISSUE_CARDINALITY_VIOLATION = "cardinality_violation"
ISSUE_DATA_TYPE_VIOLATION = "data_type_violation"
ISSUE_DANGLING_REF = "dangling_ref"
ISSUE_CYCLE = "cycle"
ISSUE_TYPE_UNDECLARED = "type_undeclared"

ISSUE_CODES = (
    ISSUE_ENDPOINT_VIOLATION,
    ISSUE_MISSING_REQUIRED,
    ISSUE_CARDINALITY_VIOLATION,
    ISSUE_DATA_TYPE_VIOLATION,
    ISSUE_DANGLING_REF,
    ISSUE_CYCLE,
    ISSUE_TYPE_UNDECLARED,
)

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

# dataType 校验基准（string/integer/number/boolean；未知类型不判，避免误报）
_DATA_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "text": (str,),
    "integer": (int,),
    "int": (int,),
    "long": (int,),
    "number": (int, float),
    "float": (int, float),
    "double": (int, float),
    "boolean": (bool,),
    "bool": (bool,),
    "date": (str,),
    "datetime": (str,),
}

# 基数上限口径：这些字面量表示「至多一个」（超出即 cardinality_violation）
_SINGLE_CARDINALITY = {"1", "0..1", "1..1", "one", "0-1", "1-1"}
_MAX_ISSUES_DEFAULT = 200
_MAX_CLASSES_DEFAULT = 40
_PAGE_SIZE_MAX = 200


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _concept_key(concept: dict[str, Any]) -> str:
    return str(concept.get("normalizedName") or concept.get("name") or "").strip()


def _relation_key(relation: dict[str, Any]) -> str:
    return str(relation.get("relationCode") or relation.get("name") or "").strip()


def normalize_definition(payload: dict[str, Any] | None) -> dict[str, Any]:
    """把 core 定义视图 / 编译产物归一为引擎内部定义字典（缺键补空，不改名）。"""
    data = dict(payload or {})
    return {
        "ontologyId": str(data.get("ontologyId") or data.get("ontology_id") or ""),
        "name": str(data.get("name") or ""),
        "version": int(data.get("version") or data.get("ontologyVersion") or 1),
        "concepts": [dict(item) for item in (data.get("concepts") or []) if isinstance(item, dict)],
        "properties": [dict(item) for item in (data.get("properties") or []) if isinstance(item, dict)],
        "relations": [dict(item) for item in (data.get("relations") or []) if isinstance(item, dict)],
        "graphSchema": dict(data.get("graphSchema") or data.get("graph_schema") or {}),
    }


def _resolve_concept_refs(
    refs: list[str], by_id: dict[str, dict[str, Any]], by_key: dict[str, dict[str, Any]]
) -> list[str]:
    """概念引用（conceptId 或 normalizedName）→ normalizedName，悬空引用原样保留。"""
    resolved: list[str] = []
    for ref in refs:
        target = by_id.get(ref) or by_key.get(ref)
        resolved.append(_concept_key(target) if target else ref)
    return resolved


def derive_graph_schema(definition: dict[str, Any]) -> dict[str, Any]:
    """由定义视图推导 graphSchema（编译产物的引擎侧等价物，仅作校验 / 导出基准）。

    显式传入 `graphSchema` 时**原样优先**（core 编译产物才是权威，D1）。
    """
    explicit = definition.get("graphSchema") or {}
    if explicit.get("entityTypes") or explicit.get("relationTypes"):
        return dict(explicit)

    concepts = [item for item in definition.get("concepts") or [] if _concept_key(item)]
    by_id = {str(item.get("conceptId") or ""): item for item in concepts if item.get("conceptId")}
    by_key = {_concept_key(item): item for item in concepts}

    props_by_concept: dict[str, list[dict[str, Any]]] = {}
    for prop in definition.get("properties") or []:
        concept_id = str(prop.get("conceptId") or "")
        if concept_id:
            props_by_concept.setdefault(concept_id, []).append(dict(prop))

    entity_types: list[dict[str, Any]] = []
    for concept in concepts:
        if str(concept.get("conceptType") or "class") == "enum_value":
            continue
        key = _concept_key(concept)
        concept_id = str(concept.get("conceptId") or "")
        parent = _resolve_concept_refs([str(concept.get("subClassOf") or "")], by_id, by_key)[0]
        entity_types.append(
            {
                "type": key,
                "name": str(concept.get("name") or key),
                "uri": str(concept.get("uri") or ""),
                "description": str(concept.get("description") or ""),
                "conceptType": str(concept.get("conceptType") or "class"),
                "subClassOf": parent,
                "equivalentClass": str(concept.get("equivalentClass") or ""),
                "properties": [
                    {
                        "name": str(item.get("name") or ""),
                        "propertyKind": str(item.get("propertyKind") or "data"),
                        "dataType": str(item.get("dataType") or ""),
                        "cardinality": str(item.get("cardinality") or ""),
                        "unit": str(item.get("unit") or ""),
                        "isIdentifier": bool(item.get("isIdentifier")),
                        "required": bool(item.get("required")),
                    }
                    for item in props_by_concept.get(concept_id, [])
                    if str(item.get("name") or "")
                ],
            }
        )

    relation_types: list[dict[str, Any]] = []
    for relation in definition.get("relations") or []:
        key = _relation_key(relation)
        if not key:
            continue
        source_types = _as_str_list(relation.get("sourceTypes")) or _resolve_concept_refs(
            _as_str_list(relation.get("sourceTypeIds")), by_id, by_key
        )
        target_types = _as_str_list(relation.get("targetTypes")) or _resolve_concept_refs(
            _as_str_list(relation.get("targetTypeIds")), by_id, by_key
        )
        relation_types.append(
            {
                "type": key,
                "name": str(relation.get("name") or key),
                "sourceTypes": source_types,
                "targetTypes": target_types,
                "inverseOf": str(relation.get("inverseOf") or ""),
                "cardinality": str(relation.get("cardinality") or ""),
            }
        )
    return {"entityTypes": entity_types, "relationTypes": relation_types}


def _issue(
    code: str,
    message: str,
    *,
    severity: str = SEVERITY_WARNING,
    **refs: str,
) -> dict[str, Any]:
    issue: dict[str, Any] = {"severity": severity, "code": code, "message": message, "detail": {}}
    for key in ("entityId", "relationId", "conceptId", "docId"):
        issue[key] = str(refs.get(key) or "")
    return issue


def validate_definition(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """本体定义体检（结构 / 悬空 / 环）：只报问题，不改定义（O-24 前的定义面校验）。"""
    concepts = [item for item in definition.get("concepts") or [] if isinstance(item, dict)]
    relations = [item for item in definition.get("relations") or [] if isinstance(item, dict)]
    by_id = {str(item.get("conceptId") or ""): item for item in concepts if item.get("conceptId")}
    by_key = {_concept_key(item): item for item in concepts if _concept_key(item)}
    issues: list[dict[str, Any]] = []

    for concept in concepts:
        concept_id = str(concept.get("conceptId") or "")
        key = _concept_key(concept)
        if not key:
            issues.append(
                _issue(ISSUE_TYPE_UNDECLARED, "概念缺少 name/normalizedName", severity=SEVERITY_ERROR, conceptId=concept_id)
            )
            continue
        parent_ref = str(concept.get("subClassOf") or "")
        if parent_ref and parent_ref not in by_id and parent_ref not in by_key:
            issues.append(
                _issue(
                    ISSUE_DANGLING_REF,
                    f"父类引用不存在：{parent_ref}",
                    severity=SEVERITY_ERROR,
                    conceptId=concept_id,
                )
            )

    # 层级环检测（subClassOf 链）
    edges: dict[str, str] = {}
    for concept in concepts:
        key = _concept_key(concept)
        parent_ref = str(concept.get("subClassOf") or "")
        parent = by_id.get(parent_ref) or by_key.get(parent_ref)
        if key and parent and _concept_key(parent):
            edges[key] = _concept_key(parent)
    for start in list(edges):
        seen: list[str] = []
        node = start
        while node in edges:
            if node in seen:
                issues.append(
                    _issue(ISSUE_CYCLE, f"subClassOf 存在环：{' → '.join(seen + [node])}", severity=SEVERITY_ERROR)
                )
                break
            seen.append(node)
            node = edges[node]

    for prop in definition.get("properties") or []:
        if not isinstance(prop, dict):
            continue
        concept_id = str(prop.get("conceptId") or "")
        name = str(prop.get("name") or "")
        if not name:
            issues.append(_issue(ISSUE_TYPE_UNDECLARED, "属性缺少 name", severity=SEVERITY_ERROR, conceptId=concept_id))
            continue
        if concept_id and concept_id not in by_id:
            issues.append(
                _issue(ISSUE_DANGLING_REF, f"属性宿主概念不存在：{concept_id}", severity=SEVERITY_ERROR, conceptId=concept_id)
            )
        if str(prop.get("propertyKind") or "data") == "data" and not str(prop.get("dataType") or ""):
            issues.append(
                _issue(
                    ISSUE_DATA_TYPE_VIOLATION,
                    f"数据属性缺少 dataType：{name}",
                    severity=SEVERITY_WARNING,
                    conceptId=concept_id,
                )
            )

    for relation in relations:
        key = _relation_key(relation)
        if not key:
            issues.append(_issue(ISSUE_TYPE_UNDECLARED, "关系缺少 relationCode/name", severity=SEVERITY_ERROR))
            continue
        for field, label in (("sourceTypeIds", "源端点"), ("targetTypeIds", "目标端点")):
            for ref in _as_str_list(relation.get(field)):
                if ref not in by_id and ref not in by_key:
                    issues.append(
                        _issue(
                            ISSUE_DANGLING_REF,
                            f"关系 {key} 的{label}概念不存在：{ref}",
                            severity=SEVERITY_ERROR,
                        )
                    )
    return issues


def _count_by_code(issues: list[dict[str, Any]]) -> dict[str, int]:
    counts = {code: 0 for code in ISSUE_CODES}
    for issue in issues:
        code = str(issue.get("code") or "")
        if code in counts:
            counts[code] += 1
    return counts


def _cap(issues: list[dict[str, Any]], max_issues: int) -> tuple[list[dict[str, Any]], bool]:
    limit = min(max(int(max_issues or _MAX_ISSUES_DEFAULT), 1), 1000)
    if len(issues) <= limit:
        return issues, False
    return issues[:limit], True


def validate_graph(
    definition: dict[str, Any],
    entities: list[Any],
    relations: list[Any],
    *,
    max_issues: int = _MAX_ISSUES_DEFAULT,
) -> dict[str, Any]:
    """实例级一致性体检（图谱 vs 定义 / 编译产物）：只报告，不落库、不阻断（D4 / D5）。

    每条问题都尽量带 `entityId` / `relationId` / `docId`，满足 §11.1 可追溯要求。
    """
    schema = derive_graph_schema(definition)
    entity_types = {
        str(item.get("type") or ""): item
        for item in (schema.get("entityTypes") or [])
        if isinstance(item, dict)
    }
    relation_types = {
        str(item.get("type") or ""): item
        for item in (schema.get("relationTypes") or [])
        if isinstance(item, dict)
    }
    issues: list[dict[str, Any]] = []
    type_of: dict[str, str] = {}

    for entity in entities:
        entity_id = str(getattr(entity, "entity_id", "") or "")
        entity_type = str(getattr(entity, "entity_type", "") or "")
        doc_id = str(getattr(entity, "doc_id", "") or "")
        properties = dict(getattr(entity, "properties", {}) or {})
        type_of[entity_id] = entity_type
        if entity_types and entity_type not in entity_types:
            issues.append(
                _issue(ISSUE_TYPE_UNDECLARED, f"实体类型未声明：{entity_type}", severity=SEVERITY_WARNING, entityId=entity_id, docId=doc_id)
            )
            continue
        declared = entity_types.get(entity_type) or {}
        for prop in declared.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            name = str(prop.get("name") or "")
            if not name:
                continue
            present = name in properties and properties.get(name) not in (None, "")
            if bool(prop.get("required")) and not present:
                issues.append(
                    _issue(
                        ISSUE_MISSING_REQUIRED,
                        f"必填属性缺失：{entity_type}.{name}",
                        severity=SEVERITY_WARNING,
                        entityId=entity_id,
                        docId=doc_id,
                    )
                )
                continue
            if not present:
                continue
            data_type = str(prop.get("dataType") or "").lower()
            expected = _DATA_TYPE_MAP.get(data_type)
            value = properties.get(name)
            if expected and not isinstance(value, expected):
                issues.append(
                    _issue(
                        ISSUE_DATA_TYPE_VIOLATION,
                        f"属性数据类型不符：{entity_type}.{name} 期望 {prop.get('dataType')}",
                        severity=SEVERITY_WARNING,
                        entityId=entity_id,
                        docId=doc_id,
                    )
                )

    source_counts: dict[tuple[str, str], int] = {}
    for relation in relations:
        relation_id = str(getattr(relation, "relation_id", "") or "")
        relation_type = str(getattr(relation, "relation_type", "") or "")
        doc_id = str(getattr(relation, "doc_id", "") or "")
        source_id = str(getattr(relation, "source_entity_id", "") or "")
        target_id = str(getattr(relation, "target_entity_id", "") or "")
        if relation_types and relation_type not in relation_types:
            issues.append(
                _issue(ISSUE_TYPE_UNDECLARED, f"关系类型未声明：{relation_type}", severity=SEVERITY_WARNING, relationId=relation_id, docId=doc_id)
            )
            continue
        declared = relation_types.get(relation_type) or {}
        source_types = _as_str_list(declared.get("sourceTypes"))
        target_types = _as_str_list(declared.get("targetTypes"))
        if source_types and type_of.get(source_id) and type_of[source_id] not in source_types:
            issues.append(
                _issue(
                    ISSUE_ENDPOINT_VIOLATION,
                    f"源端点类型越界：{relation_type} 允许 {source_types}，实为 {type_of[source_id]}",
                    severity=SEVERITY_WARNING,
                    relationId=relation_id,
                    entityId=source_id,
                    docId=doc_id,
                )
            )
        if target_types and type_of.get(target_id) and type_of[target_id] not in target_types:
            issues.append(
                _issue(
                    ISSUE_ENDPOINT_VIOLATION,
                    f"目标端点类型越界：{relation_type} 允许 {target_types}，实为 {type_of[target_id]}",
                    severity=SEVERITY_WARNING,
                    relationId=relation_id,
                    entityId=target_id,
                    docId=doc_id,
                )
            )
        if str(declared.get("cardinality") or "") in _SINGLE_CARDINALITY:
            key = (source_id, relation_type)
            source_counts[key] = source_counts.get(key, 0) + 1
            if source_counts[key] > 1:
                issues.append(
                    _issue(
                        ISSUE_CARDINALITY_VIOLATION,
                        f"基数越界：{entity_label(type_of, source_id)} 的 {relation_type} 出现 {source_counts[key]} 次",
                        severity=SEVERITY_WARNING,
                        relationId=relation_id,
                        entityId=source_id,
                        docId=doc_id,
                    )
                )

    counts = _count_by_code(issues)
    capped, truncated = _cap(issues, max_issues)
    valid = not any(item.get("severity") == SEVERITY_ERROR for item in issues) and (
        counts[ISSUE_ENDPOINT_VIOLATION] == 0
        and counts[ISSUE_MISSING_REQUIRED] == 0
        and counts[ISSUE_CARDINALITY_VIOLATION] == 0
        and counts[ISSUE_TYPE_UNDECLARED] == 0
    )
    return {
        "ontologyId": str(definition.get("ontologyId") or ""),
        "valid": bool(valid),
        "degraded": False,
        "issueCounts": {code: value for code, value in counts.items() if value},
        "issues": capped,
        "truncated": truncated,
    }


def entity_label(type_of: dict[str, str], entity_id: str) -> str:
    kind = type_of.get(entity_id) or "?"
    return f"{kind}:{entity_id}"


def coverage(
    definition: dict[str, Any], entities: list[Any], relations: list[Any]
) -> dict[str, Any]:
    """类型落地覆盖度 + 违规计数（O-21 引擎侧口径；不落库）。"""
    schema = derive_graph_schema(definition)
    declared_entities = {
        str(item.get("type") or "") for item in (schema.get("entityTypes") or []) if isinstance(item, dict)
    }
    declared_relations = {
        str(item.get("type") or "") for item in (schema.get("relationTypes") or []) if isinstance(item, dict)
    }

    def distribution(records: list[Any], attr: str) -> list[dict[str, Any]]:
        seen: dict[str, int] = {}
        for record in records:
            key = str(getattr(record, attr, "") or "")
            seen[key] = seen.get(key, 0) + 1
        return [
            {"type": key, "declared": key in (declared_entities if attr == "entity_type" else declared_relations), "count": value}
            for key, value in sorted(seen.items(), key=lambda item: (-item[1], item[0]))
        ]

    entity_types = distribution(entities, "entity_type")
    relation_types = distribution(relations, "relation_type")

    def ratio(declared: set[str], dist: list[dict[str, Any]]) -> float:
        total = sum(int(item["count"]) for item in dist)
        if total <= 0:
            return 1.0
        hit = sum(int(item["count"]) for item in dist if item["type"] in declared)
        return round(hit / total, 4)

    entity_ratio = ratio(declared_entities, entity_types)
    relation_ratio = ratio(declared_relations, relation_types)
    node_count = len(entities)
    edge_count = len(relations)
    total_records = node_count + edge_count
    overall = (
        round((node_count * entity_ratio + edge_count * relation_ratio) / total_records, 4)
        if total_records
        else 1.0
    )
    report = validate_graph(definition, entities, relations)
    return {
        "schemaCoverage": {"entity": entity_ratio, "relation": relation_ratio, "overall": overall},
        "entityTypes": entity_types,
        "relationTypes": relation_types,
        "undeclaredEntityTypes": [item["type"] for item in entity_types if not item["declared"]],
        "undeclaredRelationTypes": [item["type"] for item in relation_types if not item["declared"]],
        "violations": {
            "endpoint": report["issueCounts"].get(ISSUE_ENDPOINT_VIOLATION, 0),
            "missingRequired": report["issueCounts"].get(ISSUE_MISSING_REQUIRED, 0),
            "cardinality": report["issueCounts"].get(ISSUE_CARDINALITY_VIOLATION, 0),
            "dataType": report["issueCounts"].get(ISSUE_DATA_TYPE_VIOLATION, 0),
        },
    }


def generate_candidates(
    entities: list[Any],
    relations: list[Any],
    *,
    max_classes: int = _MAX_CLASSES_DEFAULT,
) -> dict[str, Any]:
    """从图谱记录聚合本体候选（O-23；只产候选，人工 publish，D6）。

    聚合口径（确定性，便于测试与复现）：

    - **候选概念**：按实体类型聚合，`count` = 实例数，`sampleEntityIds` 取前 5 个；
      候选属性 = 该类型实例里出现过的属性名，`coverage` = 出现实例占比。
    - **候选关系**：按关系类型聚合，`sourceTypes` / `targetTypes` 由实际边的端点类型汇总，
      `count` = 边数。
    """
    limit = min(max(int(max_classes or _MAX_CLASSES_DEFAULT), 1), 200)
    type_of = {str(getattr(item, "entity_id", "") or ""): str(getattr(item, "entity_type", "") or "") for item in entities}

    grouped: dict[str, list[Any]] = {}
    for entity in entities:
        grouped.setdefault(str(getattr(entity, "entity_type", "") or ""), []).append(entity)

    classes: list[dict[str, Any]] = []
    for entity_type, items in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if not entity_type:
            continue
        prop_seen: dict[str, int] = {}
        for entity in items:
            for name in dict(getattr(entity, "properties", {}) or {}):
                prop_seen[str(name)] = prop_seen.get(str(name), 0) + 1
        count = len(items)
        classes.append(
            {
                "name": entity_type,
                "conceptType": "class",
                "count": count,
                "properties": [
                    {
                        "name": name,
                        "propertyKind": "data",
                        "dataType": "",
                        "required": False,
                        "coverage": round(hit / count, 4) if count else 0.0,
                    }
                    for name, hit in sorted(prop_seen.items(), key=lambda kv: (-kv[1], kv[0]))
                ],
                "sampleEntityIds": [str(getattr(item, "entity_id", "") or "") for item in items[:5]],
            }
        )

    rel_grouped: dict[str, dict[str, Any]] = {}
    for relation in relations:
        key = str(getattr(relation, "relation_type", "") or "")
        if not key:
            continue
        bucket = rel_grouped.setdefault(
            key, {"name": key, "sourceTypes": [], "targetTypes": [], "count": 0}
        )
        bucket["count"] += 1
        source_type = type_of.get(str(getattr(relation, "source_entity_id", "") or ""))
        target_type = type_of.get(str(getattr(relation, "target_entity_id", "") or ""))
        if source_type and source_type not in bucket["sourceTypes"]:
            bucket["sourceTypes"].append(source_type)
        if target_type and target_type not in bucket["targetTypes"]:
            bucket["targetTypes"].append(target_type)

    relation_candidates = [
        item for _, item in sorted(rel_grouped.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    ]
    truncated = len(classes) > limit
    return {"classes": classes[:limit], "relations": relation_candidates, "truncated": truncated}


def diff_products(
    from_product: dict[str, Any], to_product: dict[str, Any]
) -> dict[str, Any]:
    """两个编译产物快照的 diff（O-24 / 版本比较；纯集合比较，不做语义等价判断）。"""
    def keys(product: dict[str, Any], section: str) -> set[str]:
        return {
            str(item.get("type") or "")
            for item in (product.get(section) or [])
            if isinstance(item, dict) and str(item.get("type") or "")
        }

    result: dict[str, Any] = {}
    for section, label in (("entityTypes", "entityTypes"), ("relationTypes", "relationTypes")):
        left, right = keys(from_product, section), keys(to_product, section)
        result[label] = {
            "added": sorted(right - left),
            "removed": sorted(left - right),
            "unchanged": sorted(left & right),
        }

    def detail(product: dict[str, Any], section: str) -> dict[str, Any]:
        return {
            str(item.get("type") or ""): item
            for item in (product.get(section) or [])
            if isinstance(item, dict) and str(item.get("type") or "")
        }

    changed: list[dict[str, Any]] = []
    for section in ("entityTypes", "relationTypes"):
        left, right = detail(from_product, section), detail(to_product, section)
        for key in sorted(set(left) & set(right)):
            if left[key] != right[key]:
                changed.append({"section": section, "type": key})
    result["changed"] = changed
    return result


def schema_check(
    graph_schema: dict[str, Any], entities: list[Any], relations: list[Any]
) -> dict[str, Any]:
    """构建期 schema 体检摘要（§9.4）：越界只计数不阻断（D4 / D5）。

    写入 `build_from_records` 响应的 `result.schemaCheck`，供 core / 平台观测。
    """
    report = validate_graph({"graphSchema": dict(graph_schema or {})}, entities, relations)
    counts = report["issueCounts"]
    return {
        "endpointViolations": counts.get(ISSUE_ENDPOINT_VIOLATION, 0),
        "missingRequired": counts.get(ISSUE_MISSING_REQUIRED, 0),
        "cardinalityViolations": counts.get(ISSUE_CARDINALITY_VIOLATION, 0),
        "typeFallbacks": counts.get(ISSUE_TYPE_UNDECLARED, 0),
    }


def issue_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    """按问题码计数（只含 >0 的码，便于报告紧凑输出）。"""
    counts = _count_by_code(issues)
    return {code: value for code, value in counts.items() if value}
