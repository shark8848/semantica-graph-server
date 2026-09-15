from __future__ import annotations

from dataclasses import dataclass

from ._version import __version__


@dataclass
class CallerIdentity:
    """调用方身份上下文；仅非空字段透传为请求头，并作为图谱归属默认值（tenantId/ownerId）。"""

    user_id: str | None = None
    tenant_id: str | None = None
    roles: list[str] | None = None


def build_headers(
    *,
    token: str | None,
    trace_id: str,
    identity: CallerIdentity | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """构建请求头：认证、trace 与身份头；extra_headers 优先级最高。"""
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": f"semantica-graph-sdk/{__version__}",
        "X-Request-Id": trace_id,
        "X-Trace-Id": trace_id,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if identity:
        if identity.user_id:
            headers["X-User-Id"] = identity.user_id
        if identity.tenant_id:
            headers["X-Tenant-Id"] = identity.tenant_id
        if identity.roles:
            headers["X-User-Roles"] = ",".join(identity.roles)
    if extra_headers:
        headers.update(extra_headers)
    return headers


def identity_defaults(identity: CallerIdentity | None) -> dict[str, str]:
    """身份 → 图谱资源默认归属：tenantId 取租户，ownerId 取调用方用户。

    图引擎的租户/归属是请求参数（而非请求头），SDK 在请求缺省时按身份补齐，
    使调用方无需在每个方法上重复传 tenantId/ownerId；显式入参始终优先。
    """
    defaults: dict[str, str] = {}
    if identity:
        if identity.tenant_id:
            defaults["tenantId"] = identity.tenant_id
        if identity.user_id:
            defaults["ownerId"] = identity.user_id
    return defaults
