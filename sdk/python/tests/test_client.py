from __future__ import annotations

import httpx
import pytest

from semantica_graph_sdk import CallerIdentity, GraphEngineClient, JobData
from semantica_graph_sdk.errors import (
    GraphEngineHTTPStatusError,
    GraphEngineNotFoundError,
    GraphEngineTimeoutError,
    GraphEngineValidationError,
)
from tests.helpers import IDENTITY, envelope, make_client, recorder

GRAPH_ID = "graph_1"


def test_request_returns_envelope_and_sends_headers():
    calls, handler = recorder({"graphId": GRAPH_ID})
    client = make_client(handler, identity=IDENTITY, trace_id="fixed-trace")
    response = client.request("GET", "/api/v1/graph/graphs/{graph_id}", path_params={"graph_id": GRAPH_ID})
    assert response.ok and response.trace_id == "t-1"
    request = calls[0]
    assert str(request.url) == "http://ge.test/api/v1/graph/graphs/graph_1"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["X-Trace-Id"] == "fixed-trace"
    assert request.headers["X-User-Id"] == "u1"
    assert request.headers["X-Tenant-Id"] == "t1"
    assert request.headers["X-User-Roles"] == "km_admin"
    client.close()


def test_business_error_maps_to_exception():
    client = make_client(
        lambda request: httpx.Response(200, json=envelope(None, err_code="200001", err_msg="graphSchema 非法"))
    )
    with pytest.raises(GraphEngineValidationError) as exc_info:
        client.graphs.create(name="x")
    assert exc_info.value.err_code == "200001"
    assert exc_info.value.err_msg == "graphSchema 非法"
    client.close()


def test_http_status_without_envelope():
    client = make_client(lambda request: httpx.Response(503, text="upstream down"))
    with pytest.raises(GraphEngineHTTPStatusError) as exc_info:
        client.health()
    assert exc_info.value.status_code == 503
    client.close()


def test_raw_does_not_raise_on_business_error():
    client = make_client(lambda request: httpx.Response(404, json=envelope(None, err_code="200404", err_msg="图谱不存在")))
    response = client.raw("GET", "/api/v1/graph/graphs/{graph_id}", path_params={"graph_id": "missing"})
    assert response.err_code == "200404"
    client.close()


def test_not_found_via_domain_method():
    client = make_client(lambda request: httpx.Response(404, json=envelope(None, err_code="200404", err_msg="图谱不存在")))
    with pytest.raises(GraphEngineNotFoundError):
        client.graphs.stat("missing")
    client.close()


def test_fetch_openapi_and_health_return_raw_json():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json={"openapi": "3.1.0", "paths": {"/health": {}}})
        return httpx.Response(200, json={"status": "ok", "service": "graph-engine"})

    client = make_client(handler)
    assert client.fetch_openapi()["openapi"] == "3.1.0"
    assert client.health()["status"] == "ok"
    client.close()


def test_graph_create_injects_identity_defaults_and_keeps_explicit_values():
    calls, handler = recorder({"graphId": GRAPH_ID})
    client = make_client(handler, identity=IDENTITY)
    client.graphs.create(name="演示图", kbId="kb_1")
    client.graphs.create(name="演示图", kbId="kb_1", tenantId="t-other", ownerId="u-other")
    first, second = calls[0], calls[1]
    import json as _json

    assert _json.loads(first.content) == {
        "name": "演示图",
        "kbId": "kb_1",
        "tenantId": "t1",
        "ownerId": "u1",
    }
    assert _json.loads(second.content) == {
        "name": "演示图",
        "kbId": "kb_1",
        "tenantId": "t-other",
        "ownerId": "u-other",
    }
    client.close()


def test_graph_list_injects_identity_defaults_into_params():
    calls, handler = recorder({"total": 0, "items": []})
    client = make_client(handler, identity=IDENTITY)
    client.graphs.list()
    query = dict(httpx.URL(str(calls[0].url)).params)
    assert query == {"tenantId": "t1", "ownerId": "u1"}
    client.close()


def test_graph_domain_paths_and_payloads():
    calls, handler = recorder({"graphId": GRAPH_ID})
    client = make_client(handler)
    client.graphs.get(GRAPH_ID)
    client.graphs.delete(GRAPH_ID)
    client.graphs.stat(GRAPH_ID)
    client.graphs.nodes(GRAPH_ID, entityType="person", name="Alice", page=2, pageSize=50)
    client.graphs.edges(GRAPH_ID, relationType="works_at", page=1, pageSize=10)
    client.graphs.neighbors(GRAPH_ID, entityId="ent_1", depth=2)
    client.graphs.paths(GRAPH_ID, sourceEntityId="ent_1", targetEntityId="ent_2", maxDepth=3)
    client.graphs.analytics(GRAPH_ID)
    client.graphs.export(GRAPH_ID, format="turtle")
    client.graphs.sparql(GRAPH_ID, query="SELECT * WHERE { ?s ?p ?o }", limit=10)
    client.graphs.search(GRAPH_ID, query="Alice", topK=5)
    client.graphs.index_status(GRAPH_ID)
    client.jobs.run("job_1")
    client.jobs.get("job_1")
    client.jobs.list(graphId=GRAPH_ID, limit=5)

    urls = [f"{request.method} {request.url.path}?{request.url.query.decode()}" for request in calls]
    assert urls == [
        f"GET /api/v1/graph/graphs/{GRAPH_ID}?",
        f"DELETE /api/v1/graph/graphs/{GRAPH_ID}?",
        f"GET /api/v1/graph/graphs/{GRAPH_ID}/stat?",
        f"GET /api/v1/graph/graphs/{GRAPH_ID}/nodes?page=2&pageSize=50&entityType=person&name=Alice",
        f"GET /api/v1/graph/graphs/{GRAPH_ID}/edges?page=1&pageSize=10&relationType=works_at",
        f"GET /api/v1/graph/graphs/{GRAPH_ID}/neighbors?entityId=ent_1&depth=2",
        f"GET /api/v1/graph/graphs/{GRAPH_ID}/paths?sourceEntityId=ent_1&targetEntityId=ent_2&maxDepth=3",
        f"GET /api/v1/graph/graphs/{GRAPH_ID}/analytics?",
        f"GET /api/v1/graph/graphs/{GRAPH_ID}/export?format=turtle",
        f"POST /api/v1/graph/graphs/{GRAPH_ID}/sparql?",
        f"GET /api/v1/graph/graphs/{GRAPH_ID}/search?query=Alice&topK=5",
        f"GET /api/v1/graph/graphs/{GRAPH_ID}/index-status?",
        "POST /api/v1/graph/jobs/job_1/run?",
        "GET /api/v1/graph/jobs/job_1?",
        f"GET /api/v1/graph/jobs?limit=5&graphId={GRAPH_ID}",
    ]
    client.close()


def test_build_sync_and_async_shapes():
    sync_calls, sync_handler = recorder(
        {"graphId": GRAPH_ID, "entityCount": 1, "relationCount": 0, "semantica": {"semantica": True}}
    )
    client = make_client(sync_handler)
    result = client.graphs.build(GRAPH_ID, text="「Alice」加入「Acme」", title="手册", docId="doc_1", llm=True)
    assert result.entityCount == 1
    assert result.semantica.semantica is True
    import json as _json

    assert _json.loads(sync_calls[0].content) == {
        "text": "「Alice」加入「Acme」",
        "title": "手册",
        "docId": "doc_1",
        "llm": True,
    }

    async_calls, async_handler = recorder(
        {"jobId": "job_9", "graphId": GRAPH_ID, "task": "build_text", "status": "pending"}
    )
    async_client = make_client(async_handler)
    job = async_client.graphs.build(GRAPH_ID, text="x", async_=True)
    assert isinstance(job, JobData) and job.status == "pending"
    assert _json.loads(async_calls[0].content)["async"] == "1"
    client.close()
    async_client.close()


def test_merge_deprecate_and_explicit_record_build():
    calls, handler = recorder({"graphId": GRAPH_ID, "entityCount": 0, "relationCount": 0})
    client = make_client(handler, identity=IDENTITY)
    client.graphs.build(
        GRAPH_ID,
        entities=[{"name": "Alice", "type": "person"}],
        relations=[{"type": "works_at", "sourceEntityId": "a", "targetEntityId": "b"}],
        docId="doc_1",
        llm=False,
    )
    client.graphs.merge(GRAPH_ID, entities=[{"name": "Bob"}], docId="doc_2")
    client.graphs.deprecate_doc(GRAPH_ID, docId="doc_2")

    import json as _json

    build_body = _json.loads(calls[0].content)
    assert build_body["entities"] == [{"name": "Alice", "type": "person"}]
    assert build_body["llm"] is False
    assert build_body["tenantId"] == "t1" and build_body["ownerId"] == "u1"
    assert _json.loads(calls[1].content) == {
        "entities": [{"name": "Bob"}],
        "relations": [],
        "docId": "doc_2",
        "tenantId": "t1",
        "ownerId": "u1",
    }
    assert calls[2].url.path == f"/api/v1/graph/graphs/{GRAPH_ID}/deprecate-doc"
    client.close()


def test_jobs_wait_polls_until_finished():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        status = "pending" if len(calls) < 3 else "success"
        return httpx.Response(200, json=envelope({"jobId": "job_1", "status": status, "task": "build"}))

    client = make_client(handler)
    job = client.jobs.wait("job_1", interval=0.0, timeout=1.0)
    assert job.status == "success" and job.finished
    assert len(calls) == 3
    client.close()


def test_jobs_wait_returns_last_observation_on_timeout():
    client = make_client(lambda request: httpx.Response(200, json=envelope({"jobId": "job_1", "status": "pending"})))
    job = client.jobs.wait("job_1", interval=0.0, timeout=0.0)
    assert job.status == "pending"
    client.close()


def test_timeout_maps_to_sdk_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    client = make_client(handler, max_retries=0)
    with pytest.raises(GraphEngineTimeoutError):
        client.graphs.stat(GRAPH_ID)
    client.close()


def test_repr_and_context_manager():
    client = make_client(lambda request: httpx.Response(200, json=envelope({})), token="tk")
    assert "token=<set>" in repr(client)
    with GraphEngineClient("http://ge.test") as managed:
        assert repr(managed).endswith("token=None)")


def test_caller_identity_defaults_to_none_in_headers():
    calls, handler = recorder({"query": "x", "topK": 10, "semantica": False, "hits": []})
    client = make_client(handler, identity=CallerIdentity())
    client.graphs.search(GRAPH_ID, query="x")
    assert "X-User-Id" not in calls[0].headers
    client.close()
