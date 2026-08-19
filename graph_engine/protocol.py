"""统一响应协议：traceId + errCode + errMsg + data，与 open-ikc envelope 对齐。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .errors import OK, GraphEngineError

TRACE_ID_HEADER = "X-Trace-Id"


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


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
