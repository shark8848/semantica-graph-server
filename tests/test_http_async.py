"""HTTP async=true 建图链路测试：登记 pending job → dispatch_job 投递（可 monkeypatch）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from graph_engine.application.service import GraphEngineService
from graph_engine.persistence.sqlite_store import SqliteGraphStore

SCHEMA = {
    "entityTypes": [{"type": "person"}, {"type": "org"}, {"type": "concept"}],
    "relationTypes": [{"type": "works_at", "sourceTypes": ["person"], "targetTypes": ["org"]}],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    store = SqliteGraphStore(str(tmp_path / "http_async.db"))
    svc = GraphEngineService(store)
    calls: list[dict] = []
    monkeypatch.setattr(
        "graph_engine.interfaces.celery_app.dispatch_job",
        lambda job_id, graph_id, task, payload=None: calls.append(
            {"jobId": job_id, "graphId": graph_id, "task": task, "payload": payload}
        )
        or True,
    )
    from graph_engine.interfaces.http_app import create_app

    return TestClient(create_app(svc)), calls


def test_http_async_build_text(client):
    """async + text → build_text 任务；dispatch_job 恰好一次、入参与请求一致。"""
    http, calls = client
    resp = http.post(
        "/api/v1/graph/graphs",
        json={"name": "HTTP 异步图", "kbId": "kb_async", "graphSchema": SCHEMA},
    )
    assert resp.status_code == 200
    gid = resp.json()["data"]["graphId"]

    payload = {"text": "介绍「Alice」。", "docId": "d1", "async": "true"}
    resp = http.post(f"/api/v1/graph/graphs/{gid}/build", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["errCode"] == "0"
    assert body["data"]["status"] == "pending"
    assert body["data"]["jobId"]
    job_id = body["data"]["jobId"]

    # dispatch_job 恰好被调一次，入参一致；text 存在 → task=build_text
    assert len(calls) == 1
    call = calls[0]
    assert call["jobId"] == job_id
    assert call["graphId"] == gid
    assert call["task"] == "build_text"
    assert call["payload"] == payload

    # 已登记 job 可查，状态保持 pending
    job = http.get(f"/api/v1/graph/jobs/{job_id}").json()["data"]
    assert job["status"] == "pending"
    assert job["task"] == "build_text"


def test_http_async_build_records(client):
    """async 不带 text → task=build；payload 原样透传。"""
    http, calls = client
    resp = http.post(
        "/api/v1/graph/graphs",
        json={"name": "HTTP 异步图2", "kbId": "kb_async2", "graphSchema": SCHEMA},
    )
    gid = resp.json()["data"]["graphId"]

    payload = {
        "entities": [{"name": "Alice", "type": "person"}],
        "docId": "d1",
        "async": "true",
    }
    resp = http.post(f"/api/v1/graph/graphs/{gid}/build", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["errCode"] == "0"
    assert body["data"]["status"] == "pending"
    job_id = body["data"]["jobId"]

    assert len(calls) == 1
    assert calls[0]["jobId"] == job_id
    assert calls[0]["graphId"] == gid
    assert calls[0]["task"] == "build"
    assert calls[0]["payload"] == payload


def test_http_async_dispatch_false(client, monkeypatch):
    """celery 未启用（dispatch_job 返回 False）→ 仍返回 pending，不抛错。"""
    http, calls = client
    monkeypatch.setattr(
        "graph_engine.interfaces.celery_app.dispatch_job",
        lambda job_id, graph_id, task, payload=None: False,
    )
    resp = http.post(
        "/api/v1/graph/graphs",
        json={"name": "HTTP 异步图3", "kbId": "kb_async3", "graphSchema": SCHEMA},
    )
    gid = resp.json()["data"]["graphId"]

    resp = http.post(
        f"/api/v1/graph/graphs/{gid}/build",
        json={"entities": [{"name": "Alice", "type": "person"}], "async": "true"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["errCode"] == "0"
    assert body["data"]["status"] == "pending"
    assert body["data"]["jobId"]


def test_http_sync_build(client):
    """不带 async → 同步建图，stat nodeCount=1；dispatch_job 不被调用。"""
    http, calls = client
    resp = http.post(
        "/api/v1/graph/graphs",
        json={"name": "HTTP 同步图", "kbId": "kb_sync", "graphSchema": SCHEMA},
    )
    gid = resp.json()["data"]["graphId"]

    resp = http.post(
        f"/api/v1/graph/graphs/{gid}/build",
        json={"entities": [{"name": "Alice", "type": "person"}], "docId": "d1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["errCode"] == "0"
    assert body["data"]["entityCount"] == 1

    resp = http.get(f"/api/v1/graph/graphs/{gid}/stat")
    assert resp.json()["data"]["nodeCount"] == 1

    assert calls == []


def test_http_async_graph_not_found(client):
    """不存在图谱 async 建图 → HTTP 404 + errCode=200404（提交前先校验图谱）。"""
    http, _ = client
    resp = http.post(
        "/api/v1/graph/graphs/nope/build",
        json={"entities": [{"name": "Alice", "type": "person"}], "async": "true"},
    )
    assert resp.status_code == 404
    assert resp.json()["errCode"] == "200404"
