from __future__ import annotations

from typing import Any

OK = "0"
INVALID_PARAMS = "200001"
NOT_FOUND = "200404"
CONFLICT = "200409"
INTERNAL = "200500"

# 本体（Ontology）域稳定码（对齐 ikc-sdk-lib 260xxx；引擎不可用时如实降级）
ONTOLOGY_VALIDATION_FAILED = "260008"
ONTOLOGY_UNAVAILABLE = "260009"


class GraphEngineError(Exception):
    """引擎领域异常：携带 open-ikc 兼容的错误码（2xxxxx 风格）。"""

    def __init__(
        self,
        code: str = INTERNAL,
        message: str = "",
        *,
        field: str = "",
        reason: str = "",
    ) -> None:
        super().__init__(message or reason)
        self.code = code
        self.message = message or reason
        self.field = field
        self.reason = reason

    def detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field:
            detail["field"] = self.field
        if self.reason:
            detail["reason"] = self.reason
        return detail


class InvalidParamsError(GraphEngineError):
    def __init__(self, message: str = "参数非法", *, field: str = "", reason: str = "") -> None:
        super().__init__(INVALID_PARAMS, message, field=field, reason=reason)


class NotFoundError(GraphEngineError):
    def __init__(self, message: str = "资源不存在", *, field: str = "", reason: str = "") -> None:
        super().__init__(NOT_FOUND, message, field=field, reason=reason)


class ConflictError(GraphEngineError):
    def __init__(self, message: str = "资源冲突", *, field: str = "", reason: str = "") -> None:
        super().__init__(CONFLICT, message, field=field, reason=reason)
