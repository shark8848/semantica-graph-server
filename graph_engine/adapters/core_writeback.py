"""core 数据面回写（引擎 → core 内部写通道）。

契约单一来源：ikc-core-service `docs/API接口与任务契约.md §2.5`
（实现见 core `domain/services/graph_write_service.py`）：

- `POST {base}/internal/graph/build`
  `{kbId, docId, entities[], relations[], engine, engineVersion, taskId, config}`
  → 稳定 ID upsert（别名并集、evidence 去重追加、confidence 取大、废弃行复活）
    + `graph_build_log` 每次运行一行 + `node_count/edge_count` 刷新 + doc 级增量废弃。
- 鉴权：请求头 `X-Internal-Token` = core `IKC_CORE_ADMIN_TOKEN`。

**分层**：*抽什么*（NER/LLM 抽取、实体消解、证据挂接）在本引擎；*怎么落地*（合并口径、
计数、日志、废弃、审计）在 core，只此一处实现——避免各引擎各写一套（G-05 类跨仓漂移）。

**开关**：未配置 `IKC_CORE_BASE_URL` 时整体关闭（返回 None，无副作用，本地 SQLite 仍是
引擎自带视图的来源）；配置后构建返回体追加 `writeback` 字段。回写失败**不阻断**本地构建
（返回 `{"ok": false, "error": ...}` 并记 warning）。

空 `entities/relations` 是**合法**载荷：语义为「该 doc 本次未再产出资产」，core 只做 doc 级废弃
（`deprecate_doc` 走此路径）。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

def _resolve_version(dist: str) -> str:
    """引擎版本号：repo 内 pyproject 优先（可编辑安装的 dist 元数据常滞后），回落包元数据。"""
    try:
        import tomllib

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            declared = str((data.get("project") or {}).get("version") or "").strip()
            if declared:
                return declared
    except Exception:  # noqa: BLE001 - 任何异常都回落元数据
        pass
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version(dist)
    except Exception:  # noqa: BLE001 - 源码直跑未安装
        return "0.0.0"


ENGINE_VERSION = _resolve_version("graph-engine")

ENGINE_NAME = "semantica-graph-server"

# core 契约字段（extra=forbid）：本地字段（entityId/graphId/status/时间戳/normalizedName）不外投
_ENTITY_FIELDS = (
    "type",
    "name",
    "aliases",
    "properties",
    "evidence",
    "confidence",
    "canonicalConceptId",
)
_RELATION_FIELDS = (
    "type",
    "sourceEntityId",
    "targetEntityId",
    "properties",
    "evidence",
    "confidence",
)


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def base_url() -> str:
    return _env("IKC_CORE_BASE_URL").rstrip("/")


def writeback_enabled() -> bool:
    """配置了 core 地址且未显式关闭时启用（默认关闭 = 不改变既有行为）。"""
    if _env("IKC_CORE_WRITEBACK", "1") in ("0", "false", "False", "no", "off"):
        return False
    return bool(base_url())


def entity_payload(record: dict[str, Any]) -> dict[str, Any]:
    """实体记录 → core 回写契约字段。"""
    return {key: record[key] for key in _ENTITY_FIELDS if key in record}


def relation_payload(record: dict[str, Any]) -> dict[str, Any]:
    """关系记录 → core 回写契约字段（两端为实体稳定 ID，core 不再派生）。"""
    return {key: record[key] for key in _RELATION_FIELDS if key in record}


def _post(path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url()}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": _env("IKC_CORE_ADMIN_TOKEN"),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    if str(body.get("errCode")) != "000000":
        raise RuntimeError(f"core 回写失败 {body.get('errCode')}：{body.get('errMsg')}")
    return dict(body.get("data") or {})


def write_assets(
    kb_id: str,
    *,
    doc_id: str = "",
    entities: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    task_id: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """把已决策的实体/关系记录回写 core；未启用或无 kbId → None（无副作用）。"""
    entities = entities or []
    relations = relations or []
    if not writeback_enabled() or not kb_id or not (entities or relations or doc_id):
        return None
    payload = {
        "kbId": kb_id,
        "docId": doc_id,
        "entities": [entity_payload(item) for item in entities],
        "relations": [relation_payload(item) for item in relations],
        "engine": ENGINE_NAME,
        "engineVersion": ENGINE_VERSION,
        "taskId": task_id,
        "config": dict(config or {}),
    }
    timeout = float(_env("IKC_CORE_WRITEBACK_TIMEOUT", "10") or 10)
    try:
        data = _post("/internal/graph/build", payload, timeout)
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
        logger.warning("core 回写未成功（本地构建已完成，数据面未同步）：%s", exc)
        return {"ok": False, "endpoint": "graph/build", "error": str(exc)}
    return {"ok": True, "endpoint": "graph/build", **data}


__all__ = [
    "ENGINE_NAME",
    "ENGINE_VERSION",
    "base_url",
    "entity_payload",
    "relation_payload",
    "writeback_enabled",
    "write_assets",
]
