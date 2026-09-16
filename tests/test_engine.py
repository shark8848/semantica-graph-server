"""引擎 MVP 冒烟与语义测试：domain / 存储 / application / 五面接口。"""

from __future__ import annotations

import json

import pytest

from graph_engine.application.service import GraphEngineService
from graph_engine.domain.ids import entity_id, graph_id, relation_id
from graph_engine.errors import GraphEngineError, InvalidParamsError, NotFoundError
from graph_engine.persistence.sqlite_store import SqliteGraphStore

SCHEMA = {
    "entityTypes": [{"type": "person"}, {"type": "org"}, {"type": "concept"}],
    "relationTypes": [{"type": "works_at", "sourceTypes": ["person"], "targetTypes": ["org"]}],
}


@pytest.fixture()
def store(tmp_path):
    return SqliteGraphStore(str(tmp_path / "test.db"))


@pytest.fixture()
def svc(store):
    return GraphEngineService(store)


def _create(svc, kb_id="kb_test_1"):
    return svc.create_graph(name="测试图", kb_id=kb_id, schema=SCHEMA)


# ---------- domain ----------


def test_stable_ids():
    gid = graph_id("kb_test_1")
    assert gid.startswith("graph_")
    assert gid == graph_id("kb_test_1")
    eid = entity_id(gid, "person", "alice")
    assert eid == entity_id(gid, "person", "alice")
    rid = relation_id(gid, "works_at", eid, entity_id(gid, "org", "acme"))
    assert rid == relation_id(gid, "works_at", eid, entity_id(gid, "org", "acme"))


# ---------- graph CRUD ----------


def test_create_get_list_delete(svc):
    created = _create(svc)
    assert created["graphId"] == graph_id("kb_test_1")
    assert svc.get_graph(created["graphId"])["graphId"] == created["graphId"]
    assert svc.list_graphs()["total"] == 1
    svc.delete_graph(created["graphId"])
    with pytest.raises(NotFoundError):
        svc.get_graph(created["graphId"])


def test_schema_validation(svc):
    with pytest.raises(InvalidParamsError) as exc:
        svc.create_graph(name="x", schema={"entityTypes": [{"type": "a"}, {"type": "a"}]})
    assert exc.value.code == "200001"


# ---------- build / merge / queries ----------


def test_build_records_stat_nodes_edges(svc):
    gid = _create(svc)["graphId"]
    alice = entity_id(gid, "person", "alice")
    bob = entity_id(gid, "person", "bob")
    acme = entity_id(gid, "org", "acme")
    result = svc.build_from_records(
        gid,
        entities=[
            {"name": "Alice", "type": "person", "docId": "doc1", "properties": {"age": 30}},
            {"name": "Bob", "type": "person", "docId": "doc1"},
            {"name": "Acme", "type": "org", "docId": "doc1"},
        ],
        relations=[
            {"type": "works_at", "source": alice, "target": acme, "docId": "doc1"},
        ],
        doc_id="doc1",
    )
    assert result["entityCount"] == 3
    assert result["relationCount"] == 1

    stat = svc.stat(gid)
    assert stat["nodeCount"] == 3
    assert stat["edgeCount"] == 1
    assert stat["schemaCoverage"]["overall"] == 1.0

    nodes = svc.list_nodes(gid, entity_type="person")
    assert nodes["total"] == 2
    edges = svc.list_edges(gid)
    assert edges["total"] == 1

    nb = svc.neighbors(gid, entity_id_value=alice, depth=1)
    assert nb["center"]["entityId"] == alice
    assert {n["entityId"] for n in nb["nodes"]} == {alice, acme}
    assert len(nb["edges"]) == 1

    paths = svc.paths(gid, source_entity_id=alice, target_entity_id=acme)
    assert paths["total"] == 1
    assert paths["items"][0]["length"] == 1


def test_merge_and_deprecate(svc):
    gid = _create(svc)["graphId"]
    svc.build_from_records(
        gid, entities=[{"name": "Alice", "type": "person", "docId": "doc1"}], doc_id="doc1"
    )
    svc.build_from_records(
        gid, entities=[{"name": "Alice", "type": "person", "docId": "doc2", "aliases": ["A. Lee"]}], doc_id="doc2"
    )
    node = svc.list_nodes(gid)["items"][0]
    assert "A. Lee" in node["aliases"]
    assert len(node["evidence"]) == 2

    # doc1 未再构建 → 其专属实体废弃（此处 doc1/doc2 共享同一实体，不应废弃）
    deprecated = svc.deprecate_doc(gid, doc_id="doc1")
    assert deprecated["deprecated"] == 0

    # 独立实体：doc3 专属，废弃后不再出现在活跃列表
    svc.build_from_records(
        gid, entities=[{"name": "Carol", "type": "person", "docId": "doc3"}], doc_id="doc3"
    )
    deprecated = svc.deprecate_doc(gid, doc_id="doc3")
    assert deprecated["deprecated"] == 1
    assert svc.list_nodes(gid, name="Carol")["total"] == 0


def test_export_jsonl_json(svc):
    gid = _create(svc)["graphId"]
    svc.build_from_records(
        gid,
        entities=[{"name": "Alice", "type": "person"}],
        relations=[{"type": "works_at", "source": entity_id(gid, "person", "alice"), "target": entity_id(gid, "org", "acme")}],
    )
    out = svc.export(gid, format="jsonl")
    lines = [json.loads(line) for line in out["content"].splitlines()]
    assert len(lines) == 2  # 1 entity + 1 relation（relation 端点实体未建，边仍入册）
    assert out["format"] == "jsonl"
    out_json = svc.export(gid, format="json")
    payload = json.loads(out_json["content"])
    assert payload["entities"][0]["name"] == "Alice"


def test_build_from_text(svc):
    gid = _create(svc)["graphId"]
    result = svc.build_from_text(gid, text="介绍「Alice」与「Acme」。", title="文档标题")
    assert result["entityCount"] >= 3  # 标题 + 两个引号词
    names = {item["name"] for item in svc.list_nodes(gid)["items"]}
    assert "Alice" in names and "Acme" in names


def test_analytics_json_serializable(svc):
    """回归：GraphAnalyzer 结果含 frozenset，analytics 必须 JSON 可序列化。"""
    gid = _create(svc, kb_id="kb_analytics")["graphId"]
    svc.build_from_records(
        gid,
        entities=[
            {"name": "Alice", "type": "person", "docId": "doc1"},
            {"name": "Acme", "type": "org", "docId": "doc1"},
        ],
        relations=[
            {
                "type": "works_at",
                "source": entity_id(gid, "person", "alice"),
                "target": entity_id(gid, "org", "acme"),
                "docId": "doc1",
            }
        ],
        doc_id="doc1",
    )
    analytics = svc.analytics(gid)
    json.dumps(analytics, ensure_ascii=False)  # 不得抛 TypeError
    assert isinstance(analytics["analysis"], dict)
    communities = analytics["analysis"].get("communities", {}).get("communities")
    if communities is not None:
        assert isinstance(communities, list)
        assert all(isinstance(item, list) for item in communities)
    with pytest.raises(NotFoundError):
        svc.analytics("不存在")


# ---------- HTTP ----------


def test_http_app(svc):
    from fastapi.testclient import TestClient

    from graph_engine.interfaces.http_app import create_app

    client = TestClient(create_app(svc))
    # G2 口径：合法 23 位数字 traceId 原样透传；非法值重新生成（禁止继续透传）
    trace_id = "17579000000001234567890"
    resp = client.post(
        "/api/v1/graph/graphs",
        json={"name": "HTTP 图", "kbId": "kb_http", "graphSchema": SCHEMA},
        headers={"X-Trace-Id": trace_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["traceId"] == trace_id
    assert body["errCode"] == "0"
    gid = body["data"]["graphId"]

    resp = client.post(
        f"/api/v1/graph/graphs/{gid}/build",
        json={"entities": [], "relations": []},
        headers={"X-Trace-Id": "trace-1"},
    )
    regenerated = resp.json()["traceId"]
    assert regenerated != "trace-1" and len(regenerated) == 23 and regenerated.isdigit()

    resp = client.post(f"/api/v1/graph/graphs/{gid}/build", json={"entities": [{"name": "Alice", "type": "person"}], "docId": "d1"})
    assert resp.json()["errCode"] == "0"

    resp = client.get(f"/api/v1/graph/graphs/{gid}/stat")
    assert resp.json()["data"]["nodeCount"] == 1

    resp = client.get("/api/v1/graph/graphs/nope/stat")
    assert resp.json()["errCode"] == "200404"

    resp = client.get("/health")
    assert resp.json()["status"] == "ok"


# ---------- CLI ----------


def test_cli(svc):
    from typer.testing import CliRunner

    from graph_engine.interfaces import cli

    cli._service = svc
    runner = CliRunner()
    result = runner.invoke(cli.app, ["create", "--name", "CLI 图", "--kb-id", "kb_cli", "--schema", json.dumps(SCHEMA)])
    assert result.exit_code == 0, result.output
    gid = json.loads(result.output)["graphId"]

    result = runner.invoke(cli.app, ["build", gid, "--records", json.dumps({"entities": [{"name": "Alice", "type": "person"}]})])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli.app, ["stat", gid])
    assert json.loads(result.output)["nodeCount"] == 1

    result = runner.invoke(cli.app, ["export", gid])
    assert result.exit_code == 0


# ---------- MCP ----------


def test_mcp(svc):
    from graph_engine.interfaces.mcp_server import _handle_request, _tool_handlers

    handlers = _tool_handlers(svc)
    init = _handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, handlers)
    assert init["result"]["serverInfo"]["name"] == "graph-engine"

    tools = _handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, handlers)
    names = {t["name"] for t in tools["result"]["tools"]}
    assert "graph_create" in names and "graph_export" in names

    resp = _handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "graph_create", "arguments": {"name": "MCP 图", "kbId": "kb_mcp", "graphSchema": SCHEMA}}},
        handlers,
    )
    gid = json.loads(resp["result"]["content"][0]["text"])["graphId"]
    resp = _handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "graph_stat", "arguments": {"graphId": gid}}},
        handlers,
    )
    assert "nodeCount" in resp["result"]["content"][0]["text"]


# ---------- gRPC ----------


def test_grpc(svc):
    from graph_engine.interfaces.grpc_server import build_grpc_server, grpc_client

    server = build_grpc_server(svc)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        client = grpc_client("127.0.0.1", port)
        resp = client(
            "CreateGraph",
            {"name": "gRPC 图", "kbId": "kb_grpc", "graphSchema": SCHEMA},
        )
        assert resp["errCode"] == "0"
        gid = resp["data"]["graphId"]
        resp = client("BuildGraph", {"graphId": gid, "entities": [{"name": "Alice", "type": "person"}]})
        assert resp["errCode"] == "0"
        resp = client("Stat", {"graphId": gid})
        assert resp["data"]["nodeCount"] == 1
        resp = client("Stat", {"graphId": "nope"})
        assert resp["errCode"] == "200404"
    finally:
        server.stop(0)


# ---------- Celery ----------


def test_celery_tasks(tmp_path):
    from graph_engine import runtime
    from graph_engine.interfaces.celery_app import build_task

    runtime.reset_runtime()
    rt_svc = runtime.get_service(str(tmp_path / "celery.db"))
    gid = rt_svc.create_graph(name="Celery 图", kb_id="kb_celery", schema=SCHEMA)["graphId"]
    job = rt_svc.submit_job("build", gid, {"entities": [{"name": "Alice", "type": "person"}], "docId": "d1"})
    result = build_task.run(gid, entities=[{"name": "Alice", "type": "person"}], doc_id="d1", job_id=job["jobId"])
    assert result["entityCount"] == 1
    assert rt_svc.get_job(job["jobId"])["status"] == "success"


def test_celery_task_failure_marks_job_failed(tmp_path):
    """任务抛异常时必须把作业落 failed + error：否则作业永远停在 pending，调用方只能靠轮询超时猜。"""
    from graph_engine import runtime
    from graph_engine.errors import NotFoundError
    from graph_engine.interfaces.celery_app import build_task

    runtime.reset_runtime()
    rt_svc = runtime.get_service(str(tmp_path / "celery-fail.db"))
    payload = {"entities": [{"name": "Alice", "type": "person"}], "docId": "d1"}
    job = rt_svc.submit_job("build", "graph_missing", payload)
    with pytest.raises(NotFoundError):
        build_task.run(
            "graph_missing", entities=payload["entities"], doc_id="d1", job_id=job["jobId"]
        )
    stored = rt_svc.get_job(job["jobId"])
    assert stored["status"] == "failed"
    assert stored["error"]
    assert stored["result"] is None


def test_celery_dispatch_job_disabled_noop(tmp_path, monkeypatch):
    """未启用 celery 时 dispatch_job 返回 False、不投递，也不触碰 broker。"""
    from graph_engine import runtime
    from graph_engine.interfaces.celery_app import dispatch_job

    monkeypatch.delenv("GRAPH_ENGINE_CELERY_ENABLED", raising=False)
    runtime.reset_runtime()
    rt_svc = runtime.get_service(str(tmp_path / "celery-disabled.db"))
    gid = rt_svc.create_graph(name="Celery 图", kb_id="kb_celery_off", schema=SCHEMA)["graphId"]
    payload = {"entities": [{"name": "Alice", "type": "person"}], "docId": "d1"}
    job = rt_svc.submit_job("build", gid, payload)
    assert dispatch_job(job["jobId"], gid, "build", payload) is False
    # 未启用只登记 pending，状态保持不变
    assert rt_svc.get_job(job["jobId"])["status"] == "pending"
    # 未知 task 不抛异常
    assert dispatch_job(job["jobId"], gid, "no_such_task", payload) is False


def test_celery_dispatch_job_enabled_eager_success(tmp_path, monkeypatch):
    """启用 celery + task_always_eager 时 submit_job + dispatch 后 job 为 success。"""
    from graph_engine import runtime
    from graph_engine.interfaces.celery_app import celery_app, dispatch_job

    monkeypatch.setenv("GRAPH_ENGINE_CELERY_ENABLED", "1")
    runtime.reset_runtime()
    rt_svc = runtime.get_service(str(tmp_path / "celery-eager.db"))
    gid = rt_svc.create_graph(name="Celery 图", kb_id="kb_celery_on", schema=SCHEMA)["graphId"]
    payload = {"entities": [{"name": "Alice", "type": "person"}], "docId": "d1"}
    job = rt_svc.submit_job("build", gid, payload)
    previous_eager = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        assert dispatch_job(job["jobId"], gid, "build", payload) is True
    finally:
        celery_app.conf.task_always_eager = previous_eager
    assert rt_svc.get_job(job["jobId"])["status"] == "success"
