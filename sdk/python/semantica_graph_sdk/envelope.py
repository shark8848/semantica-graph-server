from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .errors import GraphEngineProtocolError

SUCCESS_CODE = "0"


@dataclass
class Envelope:
    """统一响应壳：errCode / errMsg / data / traceId（与图引擎五面接口对齐）。"""

    err_code: str
    err_msg: str = ""
    data: Any = None
    trace_id: str = ""

    @property
    def ok(self) -> bool:
        return self.err_code == SUCCESS_CODE


def parse_envelope(text: str) -> Envelope:
    """解析统一响应壳；不符合协议时抛 GraphEngineProtocolError。"""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GraphEngineProtocolError("响应不是合法 JSON，不符合统一响应壳协议") from exc
    if not isinstance(payload, dict) or "errCode" not in payload:
        raise GraphEngineProtocolError("响应缺少 errCode，不符合统一响应壳协议")
    return Envelope(
        err_code=str(payload.get("errCode", "")),
        err_msg=str(payload.get("errMsg", "")),
        data=payload.get("data"),
        trace_id=str(payload.get("traceId", "")),
    )
