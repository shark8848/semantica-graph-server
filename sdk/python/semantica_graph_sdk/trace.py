from __future__ import annotations

import secrets
import time

_TRACE_ID_DIGITS = 23


def generate_trace_id() -> str:
    """生成 23 位纯数字 traceId（13 位毫秒时间戳 + 10 位随机数），与 X-Trace-Id 对齐。"""
    timestamp_ms = int(time.time() * 1000)
    random_digits = secrets.randbelow(10**10)
    return f"{timestamp_ms:013d}{random_digits:010d}"


def ensure_trace_id(trace_id: str | None) -> str:
    """复用调用方显式传入的 traceId，否则生成新的。"""
    if trace_id:
        return str(trace_id)
    return generate_trace_id()
