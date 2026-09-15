from __future__ import annotations

from typing import Any, Callable

import httpx

from semantica_graph_sdk import AsyncGraphEngineClient, CallerIdentity, GraphEngineClient

BASE_URL = "http://ge.test"


def envelope(data: Any, *, err_code: str = "0", err_msg: str = "", trace_id: str = "t-1") -> dict[str, Any]:
    """构造统一响应壳（与图引擎 protocol.ok 形状一致）。"""
    return {"traceId": trace_id, "errCode": err_code, "errMsg": err_msg, "data": data}


def make_client(handler: Callable[[httpx.Request], httpx.Response], **kwargs) -> GraphEngineClient:
    kwargs.setdefault("token", "secret-token")
    kwargs.setdefault("timeout", 1)
    return GraphEngineClient(
        BASE_URL,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def make_async_client(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs
) -> AsyncGraphEngineClient:
    kwargs.setdefault("timeout", 1)
    return AsyncGraphEngineClient(
        BASE_URL,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def recorder(data: Any) -> tuple[list[httpx.Request], Callable[[httpx.Request], httpx.Response]]:
    """记录请求并恒定返回同一 envelope 的 handler。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=envelope(data))

    return calls, handler


IDENTITY = CallerIdentity(user_id="u1", tenant_id="t1", roles=["km_admin"])
