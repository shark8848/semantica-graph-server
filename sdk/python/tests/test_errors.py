from __future__ import annotations

from semantica_graph_sdk.errors import (
    GraphEngineAPIError,
    GraphEngineBusinessError,
    GraphEngineConflictError,
    GraphEngineError,
    GraphEngineHTTPStatusError,
    GraphEngineNotFoundError,
    GraphEngineProtocolError,
    GraphEngineSystemError,
    GraphEngineTimeoutError,
    GraphEngineTransportError,
    GraphEngineValidationError,
    exception_from_code,
)


def test_error_code_mapping():
    assert isinstance(exception_from_code("200001", "参数非法"), GraphEngineValidationError)
    assert isinstance(exception_from_code("200404", "不存在"), GraphEngineNotFoundError)
    assert isinstance(exception_from_code("200409", "冲突"), GraphEngineConflictError)
    assert isinstance(exception_from_code("200500", "内部错误"), GraphEngineSystemError)
    assert isinstance(exception_from_code("200418", "未知"), GraphEngineBusinessError)


def test_api_error_carries_fields():
    error = exception_from_code("200404", "实体不存在", "t-9")
    assert error.err_code == "200404"
    assert error.err_msg == "实体不存在"
    assert error.trace_id == "t-9"
    assert "200404 实体不存在" == str(error)
    assert isinstance(error, GraphEngineAPIError)
    assert isinstance(error, GraphEngineError)


def test_hierarchy_transport_vs_business():
    assert issubclass(GraphEngineTimeoutError, GraphEngineTransportError)
    assert issubclass(GraphEngineProtocolError, GraphEngineTransportError)
    assert not issubclass(GraphEngineHTTPStatusError, GraphEngineAPIError)
    assert issubclass(GraphEngineValidationError, GraphEngineAPIError)


def test_http_status_error_fields():
    error = GraphEngineHTTPStatusError("boom", status_code=503, body="oops", trace_id="t-3")
    assert error.status_code == 503
    assert error.body == "oops"
    assert error.trace_id == "t-3"
