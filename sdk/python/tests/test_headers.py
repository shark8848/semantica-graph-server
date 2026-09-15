from __future__ import annotations

from semantica_graph_sdk import CallerIdentity, identity_defaults
from semantica_graph_sdk._version import __version__
from semantica_graph_sdk.headers import build_headers


def test_build_headers_without_identity():
    headers = build_headers(token="tk", trace_id="t-1")
    assert headers["Authorization"] == "Bearer tk"
    assert headers["X-Trace-Id"] == "t-1"
    assert headers["X-Request-Id"] == "t-1"
    assert headers["User-Agent"] == f"semantica-graph-sdk/{__version__}"
    assert "X-User-Id" not in headers


def test_build_headers_identity_and_extra_headers():
    identity = CallerIdentity(user_id="u1", tenant_id="t1", roles=["a", "b"])
    headers = build_headers(
        token=None, trace_id="t-2", identity=identity, extra_headers={"X-Trace-Id": "override"}
    )
    assert "Authorization" not in headers
    assert headers["X-User-Id"] == "u1"
    assert headers["X-Tenant-Id"] == "t1"
    assert headers["X-User-Roles"] == "a,b"
    assert headers["X-Trace-Id"] == "override"


def test_identity_defaults_maps_tenant_and_owner():
    assert identity_defaults(None) == {}
    assert identity_defaults(CallerIdentity()) == {}
    assert identity_defaults(CallerIdentity(user_id="u1", tenant_id="t1")) == {
        "tenantId": "t1",
        "ownerId": "u1",
    }
    assert identity_defaults(CallerIdentity(tenant_id="t1")) == {"tenantId": "t1"}
