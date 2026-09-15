from __future__ import annotations

import os

from .async_client import AsyncGraphEngineClient
from .client import GraphEngineClient
from .headers import CallerIdentity

DEFAULT_BASE_URL = "http://127.0.0.1:18010"

_ENV_BASE_URL = "SEMANTICA_GRAPH_BASE_URL"
_ENV_TOKEN = "SEMANTICA_GRAPH_TOKEN"
_ENV_USER_ID = "SEMANTICA_GRAPH_USER_ID"
_ENV_TENANT_ID = "SEMANTICA_GRAPH_TENANT_ID"
_ENV_ROLES = "SEMANTICA_GRAPH_ROLES"


def _identity_from_env(
    user_id: str | None,
    tenant_id: str | None,
    roles: str | None,
) -> CallerIdentity | None:
    """组装身份上下文；所有字段为空时返回 None（不附加身份头、不补齐归属）。"""
    user_id = user_id or os.getenv(_ENV_USER_ID, "").strip()
    tenant_id = tenant_id or os.getenv(_ENV_TENANT_ID, "").strip()
    roles_value = roles or os.getenv(_ENV_ROLES, "").strip()

    roles_list: list[str] | None = None
    if roles_value:
        roles_list = [item.strip() for item in roles_value.split(",") if item.strip()]

    if not user_id and not tenant_id and not roles_list:
        return None
    return CallerIdentity(user_id=user_id or None, tenant_id=tenant_id or None, roles=roles_list)


def client_from_env(
    *,
    base_url: str | None = None,
    token: str | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    roles: str | None = None,
    **kwargs,
) -> GraphEngineClient:
    """从环境变量 + 显式参数构造同步客户端；显式参数优先。

    env：SEMANTICA_GRAPH_BASE_URL（默认 http://127.0.0.1:18010）/ SEMANTICA_GRAPH_TOKEN /
    SEMANTICA_GRAPH_USER_ID / SEMANTICA_GRAPH_TENANT_ID / SEMANTICA_GRAPH_ROLES。
    额外关键字参数（timeout/max_retries/http_client 等）原样透传。
    """
    resolved_base_url = base_url or os.getenv(_ENV_BASE_URL, DEFAULT_BASE_URL)
    resolved_token = token or os.getenv(_ENV_TOKEN, "").strip() or None
    identity = _identity_from_env(user_id, tenant_id, roles)
    return GraphEngineClient(base_url=resolved_base_url, token=resolved_token, identity=identity, **kwargs)


def async_client_from_env(
    *,
    base_url: str | None = None,
    token: str | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    roles: str | None = None,
    **kwargs,
) -> AsyncGraphEngineClient:
    """从环境变量 + 显式参数构造异步客户端；语义与 client_from_env 一致。"""
    resolved_base_url = base_url or os.getenv(_ENV_BASE_URL, DEFAULT_BASE_URL)
    resolved_token = token or os.getenv(_ENV_TOKEN, "").strip() or None
    identity = _identity_from_env(user_id, tenant_id, roles)
    return AsyncGraphEngineClient(base_url=resolved_base_url, token=resolved_token, identity=identity, **kwargs)
