"""统一响应协议：traceId + errCode + errMsg + data，与 open-ikc envelope 对齐。"""

from __future__ import annotations

import json
from typing import Any

from ikc_sdk.core.trace import generate_trace_id

from .errors import OK, GraphEngineError

TRACE_ID_HEADER = "X-Trace-Id"


def new_trace_id() -> str:
    """生成 23 位纯数字 traceId（13 位毫秒 + 10 位随机）。

    算法单一来源 = `ikc_sdk.core.trace`（G2 收口），与 core `api/core/trace.py`、
    引擎客户端 SDK 及各层一致；调用方显式传入 `X-Trace-Id` 时仍按原值透传（见各面入口）。
    """
    return generate_trace_id()


def ok(trace_id: str, data: Any) -> dict[str, Any]:
    return {"traceId": trace_id, "errCode": OK, "errMsg": "", "data": data}


def error(trace_id: str, err: GraphEngineError | Exception) -> dict[str, Any]:
    if isinstance(err, GraphEngineError):
        code, msg = err.code, err.message
    else:
        code, msg = "200500", str(err) or "内部错误"
    return {"traceId": trace_id, "errCode": code, "errMsg": msg, "data": None}


def error_plain(trace_id: str, code: str, msg: str) -> dict[str, Any]:
    return {"traceId": trace_id, "errCode": code, "errMsg": msg, "data": None}


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
