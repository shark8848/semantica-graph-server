from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

import httpx

from .envelope import Envelope, parse_envelope
from .errors import (
    GraphEngineConnectionError,
    GraphEngineHTTPStatusError,
    GraphEngineProtocolError,
    GraphEngineTimeoutError,
    exception_from_code,
)
from .headers import CallerIdentity, build_headers, identity_defaults
from .trace import ensure_trace_id

logger = logging.getLogger("semantica_graph_sdk")

_RETRYABLE_STATUS_CODES = {502, 503, 504}
_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)


def resolve_timeout(timeout: tuple[float, float] | float | None) -> httpx.Timeout:
    if timeout is None:
        return _DEFAULT_TIMEOUT
    if isinstance(timeout, (int, float)):
        return httpx.Timeout(timeout)
    connect, read = timeout
    return httpx.Timeout(connect=connect, read=read, write=read, pool=connect)


def build_url(base_url: str, path: str, path_params: dict[str, str] | None) -> str:
    if path_params:
        for key, value in path_params.items():
            path = path.replace("{" + key + "}", str(value))
    return base_url.rstrip("/") + path


def can_retry(method: str, body: dict[str, Any] | None) -> bool:
    """GET/HEAD/OPTIONS 幂等可重试；POST 仅显式携带 reqId 时允许重试。"""
    upper = method.upper()
    if upper in {"GET", "HEAD", "OPTIONS"}:
        return True
    if upper == "POST":
        return bool(body and "reqId" in body)
    return False


def backoff_delay(attempt: int) -> float:
    return min(0.5 * (2**attempt), 4.0) + random.uniform(0, 0.2)


def sleep_backoff(attempt: int) -> None:
    time.sleep(backoff_delay(attempt))


def handle_response(response: httpx.Response, *, trace_id: str, raise_for_error: bool) -> Envelope:
    text = response.text
    if response.status_code >= 400:
        try:
            envelope = parse_envelope(text)
        except GraphEngineProtocolError:
            raise GraphEngineHTTPStatusError(
                f"HTTP {response.status_code} 且响应不符合统一响应壳协议",
                status_code=response.status_code,
                body=text,
                trace_id=trace_id,
            ) from None
        if raise_for_error:
            raise exception_from_code(envelope.err_code, envelope.err_msg, envelope.trace_id or trace_id)
        return envelope
    try:
        envelope = parse_envelope(text)
    except GraphEngineProtocolError as exc:
        raise GraphEngineHTTPStatusError(
            f"HTTP {response.status_code} 响应不符合统一响应壳协议",
            status_code=response.status_code,
            body=text,
            trace_id=trace_id,
        ) from exc
    if raise_for_error and not envelope.ok:
        raise exception_from_code(envelope.err_code, envelope.err_msg, envelope.trace_id or trace_id)
    return envelope


class Transport:
    """同步 HTTP 传输：超时、重试、统一壳解析与异常映射。"""

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        timeout: tuple[float, float] | float | None = None,
        max_retries: int = 2,
        identity: CallerIdentity | None = None,
        extra_headers: dict[str, str] | None = None,
        trace_id: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = resolve_timeout(timeout)
        self._max_retries = max(0, max_retries)
        self._identity = identity
        self._extra_headers = dict(extra_headers or {})
        self._fixed_trace_id = trace_id
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=self._timeout)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    @property
    def identity(self) -> CallerIdentity | None:
        return self._identity

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def with_identity_defaults(
        self,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """按身份补齐 tenantId/ownerId 缺省值；显式入参优先，无身份或无载体时原样返回。"""
        defaults = identity_defaults(self._identity)
        if not defaults:
            return params, body
        if body is not None:
            body = {**{key: value for key, value in defaults.items() if key not in body}, **body}
        elif params is not None:
            params = {**{key: value for key, value in defaults.items() if key not in params}, **params}
        return params, body

    def request(
        self,
        method: str,
        path: str,
        *,
        path_params: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        raise_for_error: bool = True,
    ) -> Envelope:
        url = build_url(self._base_url, path, path_params)
        trace_id = ensure_trace_id(self._fixed_trace_id)
        headers = build_headers(
            token=self._token,
            trace_id=trace_id,
            identity=self._identity,
            extra_headers=self._extra_headers,
        )
        params, body = self.with_identity_defaults(params=params, body=body)
        response = self._send(method, url, params=params, body=body, headers=headers, trace_id=trace_id)
        return handle_response(response, trace_id=trace_id, raise_for_error=raise_for_error)

    def get_json(self, path: str) -> Any:
        """运行时自检入口：拉取 FastAPI 自动生成的 /openapi.json。"""
        url = self._base_url + path
        trace_id = ensure_trace_id(self._fixed_trace_id)
        headers = build_headers(
            token=self._token,
            trace_id=trace_id,
            identity=self._identity,
            extra_headers=self._extra_headers,
        )
        response = self._send("GET", url, params=None, body=None, headers=headers, trace_id=trace_id)
        if response.status_code >= 400:
            raise GraphEngineHTTPStatusError(
                f"HTTP {response.status_code}: GET {url}",
                status_code=response.status_code,
                body=response.text,
                trace_id=trace_id,
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise GraphEngineHTTPStatusError(
                f"HTTP {response.status_code} 响应不是合法 JSON",
                status_code=response.status_code,
                body=response.text,
                trace_id=trace_id,
            ) from exc

    def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        headers: dict[str, str],
        trace_id: str,
    ) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, url, params=params, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                if not can_retry(method, body) or attempt >= self._max_retries:
                    raise GraphEngineTimeoutError(f"请求超时: {method} {url}", trace_id=trace_id) from exc
                sleep_backoff(attempt)
                continue
            except httpx.ConnectError as exc:
                if not can_retry(method, body) or attempt >= self._max_retries:
                    raise GraphEngineConnectionError(f"连接失败: {method} {url}", trace_id=trace_id) from exc
                sleep_backoff(attempt)
                continue
            except httpx.HTTPError as exc:
                raise GraphEngineConnectionError(f"传输失败: {method} {url}: {exc}", trace_id=trace_id) from exc
            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries and can_retry(method, body):
                sleep_backoff(attempt)
                continue
            return response
        raise GraphEngineConnectionError(f"重试耗尽: {method} {url}", trace_id=trace_id)
