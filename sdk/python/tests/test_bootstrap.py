from __future__ import annotations

from semantica_graph_sdk import async_client_from_env, client_from_env
from semantica_graph_sdk._bootstrap import DEFAULT_BASE_URL


def test_defaults_without_env(monkeypatch):
    for key in (
        "SEMANTICA_GRAPH_BASE_URL",
        "SEMANTICA_GRAPH_TOKEN",
        "SEMANTICA_GRAPH_USER_ID",
        "SEMANTICA_GRAPH_TENANT_ID",
        "SEMANTICA_GRAPH_ROLES",
    ):
        monkeypatch.delenv(key, raising=False)
    client = client_from_env()
    assert client._transport.base_url == DEFAULT_BASE_URL
    assert client._transport.has_token is False
    assert client._transport.identity is None
    client.close()


def test_env_values_are_applied(monkeypatch):
    monkeypatch.setenv("SEMANTICA_GRAPH_BASE_URL", "http://engine.internal:18180")
    monkeypatch.setenv("SEMANTICA_GRAPH_TOKEN", "env-token")
    monkeypatch.setenv("SEMANTICA_GRAPH_USER_ID", "u-env")
    monkeypatch.setenv("SEMANTICA_GRAPH_TENANT_ID", "t-env")
    monkeypatch.setenv("SEMANTICA_GRAPH_ROLES", "km_admin, viewer ,")

    client = client_from_env()
    assert client._transport.base_url == "http://engine.internal:18180"
    assert client._transport.has_token is True
    identity = client._transport.identity
    assert identity is not None
    assert (identity.user_id, identity.tenant_id) == ("u-env", "t-env")
    assert identity.roles == ["km_admin", "viewer"]
    client.close()


def test_explicit_arguments_override_env(monkeypatch):
    monkeypatch.setenv("SEMANTICA_GRAPH_BASE_URL", "http://env.test")
    monkeypatch.setenv("SEMANTICA_GRAPH_USER_ID", "u-env")
    client = client_from_env(base_url="http://explicit.test", user_id="u-explicit", max_retries=0)
    assert client._transport.base_url == "http://explicit.test"
    assert client._transport.identity is not None
    assert client._transport.identity.user_id == "u-explicit"
    client.close()


def test_async_client_from_env(monkeypatch):
    monkeypatch.setenv("SEMANTICA_GRAPH_BASE_URL", "http://async.test")
    monkeypatch.setenv("SEMANTICA_GRAPH_TENANT_ID", "t-async")
    client = async_client_from_env()
    assert client._transport.base_url == "http://async.test"
    assert client._transport.identity is not None
    assert client._transport.identity.tenant_id == "t-async"
    assert client._transport.identity.user_id is None
