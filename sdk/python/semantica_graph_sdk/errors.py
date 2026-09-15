from __future__ import annotations


class GraphEngineError(Exception):
    """SDK 所有异常基类。"""


class GraphEngineTransportError(GraphEngineError):
    """传输层异常：连接、超时、非预期 HTTP 状态。"""

    def __init__(self, message: str, *, trace_id: str = "") -> None:
        super().__init__(message)
        self.trace_id = trace_id


class GraphEngineConnectionError(GraphEngineTransportError):
    """无法建立连接。"""


class GraphEngineTimeoutError(GraphEngineTransportError):
    """请求超时。"""


class GraphEngineProtocolError(GraphEngineTransportError):
    """响应不符合统一响应壳协议。"""


class GraphEngineHTTPStatusError(GraphEngineTransportError):
    """非 2xx 且无法解析统一响应壳。"""

    def __init__(self, message: str, *, status_code: int, body: str = "", trace_id: str = "") -> None:
        super().__init__(message, trace_id=trace_id)
        self.status_code = status_code
        self.body = body


class GraphEngineAPIError(GraphEngineError):
    """服务端返回统一响应壳但 errCode != 0。"""

    def __init__(self, message: str, *, err_code: str, err_msg: str, trace_id: str = "") -> None:
        super().__init__(message)
        self.err_code = err_code
        self.err_msg = err_msg
        self.trace_id = trace_id


class GraphEngineValidationError(GraphEngineAPIError):
    """参数校验失败（200001）。"""


class GraphEngineNotFoundError(GraphEngineAPIError):
    """资源不存在（200404）。"""


class GraphEngineConflictError(GraphEngineAPIError):
    """资源冲突（200409）。"""


class GraphEngineSystemError(GraphEngineAPIError):
    """服务端系统内部错误（200500）。"""


class GraphEngineBusinessError(GraphEngineAPIError):
    """其他业务错误码兜底。"""


_ERROR_CODE_CLASSES: dict[str, type[GraphEngineAPIError]] = {
    "200001": GraphEngineValidationError,
    "200404": GraphEngineNotFoundError,
    "200409": GraphEngineConflictError,
    "200500": GraphEngineSystemError,
}


def exception_from_code(err_code: str, err_msg: str, trace_id: str = "") -> GraphEngineAPIError:
    """按错误码生成对应异常；未知错误码映射为 GraphEngineBusinessError。"""
    error_class = _ERROR_CODE_CLASSES.get(err_code, GraphEngineBusinessError)
    return error_class(
        f"{err_code} {err_msg}".strip(), err_code=err_code, err_msg=err_msg, trace_id=trace_id
    )
