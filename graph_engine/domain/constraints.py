"""抽取约束（P4 引擎侧沉淀）：把人工修正沉淀为下一次抽取的确定性约束。

分层（同 ``adapters.*``）：*抽什么*在引擎，*怎么落地*在 core。人工修正是 core 侧的事实
（``graph_override`` / 人工行 / ``graph_revision``），core 在投递构建时把**约束画像**随请求下发；
引擎负责「按约束收敛抽取结果」，并把画像**按图持久化**（``graph_constraints``）——后续任何直接打到
引擎的构建（含 Celery worker 复用旧载荷）同样生效，这就是「沉淀」。

画像四件套（方案 §4.5）：

1. ``entityTypes``：命中即强制实体类型（人工确认 + schema 白名单）；
2. ``entityRenames``：别名表——命中即改名，是人工「改名保 ID」在引擎侧的对偶；原名进
   ``aliases`` 保留召回；
3. ``entityBlacklist``：人工软删（含其别名）不再进图；
4. ``examples``：few-shot 样例——人工确认的实体/关系样例，展开后并入上面三张表；关系样例另
   落 ``relationTypes``（无序端点对 → 关系类型）。

口径：名称一律经 :func:`graph_engine.domain.ids.normalize_name` 归一后比较（与稳定 ID 同口径）；
约束只做**收敛**（改名 / 锁类型 / 删行 / 定关系类型），不凭空新增实体或关系，因此不会造出没有
证据的行。任何非法输入都按「忽略该项」处理（守卫式：约束坏不能拖垮构建）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .ids import normalize_name

__all__ = ["ExtractionConstraints", "relation_pair_key"]

#: 画像版本（后续字段增删时用于兼容判断）
CONSTRAINTS_VERSION = 1

_EMPTY_STATS = {"renamed": 0, "typed": 0, "typeFallbacks": 0, "dropped": 0, "deduped": 0}


def relation_pair_key(source: str, target: str) -> str:
    """无序端点对键：``归一化名`` 升序拼接（A→B 与 B→A 同键，与关系方向无关）。"""
    left, right = normalize_name(source), normalize_name(target)
    return f"{left}|{right}" if left <= right else f"{right}|{left}"


def _keys(values: Any) -> frozenset[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(key for key in (normalize_name(str(v)) for v in values) if key)


def _str_map(values: Any) -> dict[str, str]:
    """把 ``{名称: 值}`` 归一为 ``{归一化名: 值}``（跳过空键/空值）。"""
    if not isinstance(values, dict):
        return {}
    out: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        key, value = normalize_name(str(raw_key)), str(raw_value or "").strip()
        if value and key != "untitled":
            out[key] = value
    return out


def _pair_map(values: Any) -> dict[str, str]:
    """关系类型表：键可以是 ``"A|B"`` / ``"A->B"`` / ``"A,B"`` / ``[A, B]`` 四种形态。"""
    if not isinstance(values, dict):
        return {}
    out: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        relation_type = str(raw_value or "").strip()
        if not relation_type:
            continue
        if isinstance(raw_key, (list, tuple)) and len(raw_key) == 2:
            key = relation_pair_key(str(raw_key[0]), str(raw_key[1]))
        else:
            parts = [
                part
                for chunk in str(raw_key).replace(",", "|").replace("->", "|").split("|")
                for part in [chunk.strip()]
                if part
            ]
            if len(parts) != 2:
                continue
            key = relation_pair_key(parts[0], parts[1])
        if key:
            out[key] = relation_type
    return out


@dataclass(frozen=True, slots=True)
class ExtractionConstraints:
    """一张图的抽取约束画像（不可变；合并产生新实例）。"""

    entity_renames: dict[str, str] = field(default_factory=dict)
    entity_types: dict[str, str] = field(default_factory=dict)
    entity_blacklist: frozenset[str] = field(default_factory=frozenset)
    relation_types: dict[str, str] = field(default_factory=dict)
    type_whitelist: tuple[str, ...] = ()
    examples: tuple[dict[str, Any], ...] = ()
    version: int = CONSTRAINTS_VERSION

    # ---------------------------------------------------------------- 构造

    @classmethod
    def from_dict(cls, data: Any) -> "ExtractionConstraints":
        """解析线缆画像（camelCase 优先，兼容 snake_case）；非法输入按空画像处理。"""
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:  # noqa: BLE001 - 非法 JSON 视作空画像
                return cls()
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict) or not data:
            return cls()

        examples = tuple(
            item for item in (data.get("examples") or []) if isinstance(item, dict)
        )
        whitelist = data.get("typeWhitelist", data.get("type_whitelist"))
        renames = _str_map(data.get("entityRenames", data.get("entity_renames")))
        types = _str_map(data.get("entityTypes", data.get("entity_types")))
        relations = _pair_map(data.get("relationTypes", data.get("relation_types")))
        blacklist = _keys(data.get("entityBlacklist", data.get("entity_blacklist")))

        # few-shot 样例展开：实体样例锁类型，关系样例锁关系类型（人工确认过的最强信号）。
        for example in examples:
            for item in example.get("entities") or []:
                if not isinstance(item, dict):
                    continue
                name, entity_type = str(item.get("name") or ""), str(item.get("type") or "")
                key = normalize_name(name)
                if name and entity_type and key != "untitled":
                    types.setdefault(key, entity_type)
            for item in example.get("relations") or []:
                if not isinstance(item, dict):
                    continue
                source, target = str(item.get("source") or ""), str(item.get("target") or "")
                relation_type = str(item.get("type") or "")
                if source and target and relation_type:
                    relations.setdefault(relation_pair_key(source, target), relation_type)

        declared = [str(item).strip() for item in (whitelist or []) if str(item).strip()]
        return cls(
            entity_renames=renames,
            entity_types=types,
            entity_blacklist=blacklist,
            relation_types=relations,
            type_whitelist=tuple(dict.fromkeys(declared)),
            examples=examples,
            version=int(data.get("version") or CONSTRAINTS_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "entityRenames": dict(self.entity_renames),
            "entityTypes": dict(self.entity_types),
            "entityBlacklist": sorted(self.entity_blacklist),
            "relationTypes": dict(self.relation_types),
            "typeWhitelist": list(self.type_whitelist),
            "examples": [dict(item) for item in self.examples],
        }

    # ---------------------------------------------------------------- 合并

    def merged(self, other: "ExtractionConstraints | dict[str, Any] | None") -> "ExtractionConstraints":
        """后写优先合并：``other`` 覆盖本画像（黑名单取并集、白名单非空覆盖、样例去重追加）。"""
        incoming = self.from_dict(other)
        if incoming.is_empty():
            return self
        if self.is_empty():
            return incoming
        examples: list[dict[str, Any]] = [dict(item) for item in self.examples]
        seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in examples}
        for item in incoming.examples:
            marker = json.dumps(dict(item), ensure_ascii=False, sort_keys=True)
            if marker not in seen:
                seen.add(marker)
                examples.append(dict(item))
        return ExtractionConstraints(
            entity_renames={**self.entity_renames, **incoming.entity_renames},
            entity_types={**self.entity_types, **incoming.entity_types},
            entity_blacklist=self.entity_blacklist | incoming.entity_blacklist,
            relation_types={**self.relation_types, **incoming.relation_types},
            type_whitelist=incoming.type_whitelist or self.type_whitelist,
            examples=tuple(examples),
            version=max(self.version, incoming.version),
        )

    def is_empty(self) -> bool:
        return not (
            self.entity_renames
            or self.entity_types
            or self.entity_blacklist
            or self.relation_types
            or self.type_whitelist
            or self.examples
        )

    # ---------------------------------------------------------------- 应用

    def apply_candidates(
        self,
        entities: list[dict[str, Any]],
        *,
        default_type: str = "concept",
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """把约束应用到**候选实体**（改名 / 锁类型 / 黑名单 / 按名去重）。

        必须在派生稳定 ID **之前**调用（改名会改稳定 ID——这正是「别名收敛到同一实体」的
        期望结果：同一个规范名只留一个 ID）。改名时原名并入 ``aliases``，保留召回与溯源。
        """
        stats = dict(_EMPTY_STATS)
        out: list[dict[str, Any]] = []
        index: dict[str, int] = {}
        for item in entities or []:
            if not isinstance(item, dict):
                continue
            raw_name = str(item.get("name") or "").strip()
            key = normalize_name(raw_name)
            if key in self.entity_blacklist:
                stats["dropped"] += 1
                continue
            current = dict(item)
            canonical = self.entity_renames.get(key)
            if canonical and canonical != raw_name:
                current["name"] = canonical
                aliases = [str(a) for a in (current.get("aliases") or []) if str(a).strip()]
                if raw_name and raw_name not in aliases:
                    aliases.append(raw_name)
                current["aliases"] = aliases
                key = normalize_name(canonical)
                stats["renamed"] += 1
            forced = self.entity_types.get(key)
            declared = str(current.get("type") or default_type)
            effective = forced or declared or default_type
            if forced:
                stats["typed"] += 1
            if self.type_whitelist and effective not in self.type_whitelist:
                effective = default_type
                stats["typeFallbacks"] += 1
            current["type"] = effective
            if key in index:
                # 同名（含改名后同名）收敛为一行：证据/别名取并集，置信度取大。
                target = out[index[key]]
                target["evidence"] = [*(target.get("evidence") or []), *(current.get("evidence") or [])]
                target["aliases"] = list(
                    dict.fromkeys([*(target.get("aliases") or []), *(current.get("aliases") or [])])
                )
                target["confidence"] = max(
                    float(target.get("confidence") or 0.0), float(current.get("confidence") or 0.0)
                ) or target.get("confidence")
                stats["deduped"] += 1
                continue
            index[key] = len(out)
            out.append(current)
        return out, stats

    def relation_type_for(self, source_name: str, target_name: str, default: str) -> str:
        """关系类型：人工确认的样例优先，其次调用方缺省（schema 推导值）。"""
        return self.relation_types.get(relation_pair_key(source_name, target_name), default)

    def apply_records(
        self,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
        """把约束应用到**已决策记录**（blacklist 过滤 + 关系端点级联）。

        记录路径的实体已带稳定 ID（由上游派生），改名会改 ID 并打断关系端点，故此处只做
        黑名单收敛；改名 / 锁类型只在候选阶段生效（见 :meth:`apply_candidates`）。
        """
        stats = dict(_EMPTY_STATS)
        kept_ids: set[str] = set()
        kept_entities: list[dict[str, Any]] = []
        for item in entities or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if normalize_name(name) in self.entity_blacklist:
                stats["dropped"] += 1
                continue
            entity_id = str(item.get("entityId") or "")
            if entity_id:
                kept_ids.add(entity_id)
            kept_entities.append(item)
        kept_relations: list[dict[str, Any]] = []
        for item in relations or []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("sourceEntityId") or "")
            target = str(item.get("targetEntityId") or "")
            if (source and source not in kept_ids) or (target and target not in kept_ids):
                stats["dropped"] += 1
                continue
            kept_relations.append(item)
        return kept_entities, kept_relations, stats
