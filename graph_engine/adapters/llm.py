"""LLM 建图增强适配层：规则候选实体 → semantica ``LLMExtraction`` 增强。

对应设计文档「后续演进」：接入 semantica ``LLMExtraction``（支持 openai/gemini/
groq/anthropic/ollama/huggingface_llm）对建图候选做实体级增强（去噪/纠名/补置信）。

本模块守卫式依赖：provider 未配置、semantica 不可导入、provider 初始化失败或
无凭据时一律**确定降级**（返回原候选 + meta 说明），不抛错、不触发网络调用。
默认关闭：需设置 ``GRAPH_ENGINE_LLM_PROVIDER``（或在调用侧显式传 provider），
可选 ``GRAPH_ENGINE_LLM_MODEL`` / ``GRAPH_ENGINE_LLM_API_KEY``（缺省走 provider
原生环境变量，如 ``OPENAI_API_KEY``）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("graph_engine.llm")

#: 进程内可用性缓存：semantica semantic_extract 是否可导入
_llm_modules_available: bool | None = None


def llm_modules_available() -> bool:
    """semantica LLM 抽取模块是否可导入（守卫式，仅探测一次）。"""
    global _llm_modules_available
    if _llm_modules_available is not None:
        return _llm_modules_available
    try:
        import semantica.semantic_extract  # noqa: F401

        _llm_modules_available = True
    except Exception as exc:
        logger.warning("semantica semantic_extract 不可用：%s", exc)
        _llm_modules_available = False
    return _llm_modules_available


def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, "").strip().lower() in ("1", "true", "yes") if os.environ.get(key) else default


def _to_span(candidate: dict[str, Any], text: str) -> tuple[Any | None, int]:
    """候选实体 dict → semantica span Entity（仅当名称确在文本中可定位）。

    返回 (Entity|None, 定位数)：标题等不在原文出现的候选不做 LLM 增强，
    保持原样透传（增强只对可回证文本的候选生效）。
    """
    name = str(candidate.get("name") or "").strip()
    if not name or not text:
        return None, 0
    start = text.find(name)
    if start < 0:
        return None, 0
    from semantica.semantic_extract.ner_extractor import Entity

    return (
        Entity(
            text=name,
            label=str(candidate.get("type") or candidate.get("entityType") or "concept"),
            start_char=start,
            end_char=start + len(name),
            confidence=float(candidate.get("confidence") or 1.0),
            metadata={},
        ),
        1,
    )


def enhance_text_entities(
    text: str,
    candidates: list[dict[str, Any]],
    *,
    provider: str = "",
    model: str = "",
    api_key: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """LLM 增强文本建图候选实体。

    返回 (增强后候选列表, meta)。未启用/provider 缺失/不可用时的降级 meta 形态：
    ``{"enabled": false, "reason": ...}``；启用成功：
    ``{"enabled": true, "provider", "model", "input", "enhanced", "unavailable"?}``。
    """
    prov = str(provider or os.environ.get("GRAPH_ENGINE_LLM_PROVIDER", "") or "").strip()
    if not prov:
        return list(candidates), {
            "enabled": False,
            "reason": "未配置 LLM provider（GRAPH_ENGINE_LLM_PROVIDER）",
        }
    if not llm_modules_available():
        return list(candidates), {"enabled": False, "reason": "semantica semantic_extract 不可用"}

    model_name = str(model or os.environ.get("GRAPH_ENGINE_LLM_MODEL", "") or "").strip() or None
    key = str(api_key or os.environ.get("GRAPH_ENGINE_LLM_API_KEY", "") or "").strip() or None

    spans: list[Any] = []
    located = 0
    for candidate in candidates:
        span, found = _to_span(candidate, text or "")
        located += found
        if span is not None:
            spans.append(span)
    if not spans:
        return list(candidates), {
            "enabled": True,
            "provider": prov,
            "model": model_name or "",
            "reason": "候选实体均未在文本中定位到 span，跳过 LLM 增强",
            "located": 0,
        }

    try:
        from semantica.semantic_extract import LLMExtraction

        extraction = LLMExtraction(provider=prov, model=model_name, api_key=key)
    except Exception as exc:  # pragma: no cover - 双保险（LLMExtraction 内部已吞错）
        logger.warning("LLMExtraction 初始化失败：%s", exc)
        return list(candidates), {"enabled": False, "reason": f"LLMExtraction 初始化失败：{exc}"}

    if extraction.provider is None or not getattr(extraction.provider, "is_available", lambda: False)():
        return list(candidates), {
            "enabled": True,
            "provider": prov,
            "model": model_name or "",
            "reason": "provider 不可用（缺凭据/初始化失败），保持原候选",
            "located": located,
            "enhanced": 0,
        }

    enhanced = extraction.enhance_entities(str(text or ""), spans)
    if not isinstance(enhanced, list):
        enhanced = []

    # 按候选原名合并增强结果：以原候选为底（保留 docId 等字段），
    # 采用 LLM 返回的名称/类型/置信；未返回的候选原样保留，改名/新增实体追加。
    by_name: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        by_name[str(candidate.get("name") or "")] = dict(candidate)

    output: list[dict[str, Any]] = []
    for entity in enhanced:
        base = by_name.pop(str(entity.text or ""), None) or {}
        name = str(entity.text or "").strip() or str(base.get("name") or "")
        label = str(entity.label or "").strip()
        if not label or (label == "concept" and str(base.get("type") or "") not in ("", "concept")):
            label = str(base.get("type") or label or "concept")
        merged: dict[str, Any] = {**base, "name": name, "type": label}
        if entity.confidence is not None:
            merged["confidence"] = float(entity.confidence)
        output.append(merged)
    for leftover in by_name.values():
        output.append(leftover)

    return output, {
        "enabled": True,
        "provider": prov,
        "model": model_name or "",
        "located": located,
        "enhanced": len(enhanced),
        "total": len(output),
    }
