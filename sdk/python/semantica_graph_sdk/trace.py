from __future__ import annotations

# traceId 算法单一来源 = ikc_sdk.core.trace（G2 收口）：23 位纯数字（13 位毫秒 + 10 位随机）。
# 非法值归一（重新生成）发生在**接收方边界**（引擎 HTTP/gRPC/MCP 入口按 sdk
# `normalize_trace_id` 处理）；客户端保持「调用方显式传入即原样透传」语义。
from ikc_sdk.core.trace import generate_trace_id as _sdk_generate_trace_id

_TRACE_ID_DIGITS = 23


def generate_trace_id() -> str:
    """生成 23 位纯数字 traceId（13 位毫秒时间戳 + 10 位随机数），与 X-Trace-Id 对齐。"""
    return _sdk_generate_trace_id()


def ensure_trace_id(trace_id: str | None) -> str:
    """复用调用方显式传入的 traceId，否则生成新的。"""
    if trace_id:
        return str(trace_id)
    return generate_trace_id()
