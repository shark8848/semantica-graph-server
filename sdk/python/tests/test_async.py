from __future__ import annotations

import asyncio

import httpx
import pytest

from semantica_graph_sdk import AsyncGraphEngineClient
from semantica_graph_sdk.errors import GraphEngineNotFoundError, GraphEngineTimeoutError
from tests.helpers import IDENTITY, envelope, make_async_client, recorder

GRAPH_ID = "graph_1"


def test_async_request_and_headers():
    async def scenario() -> None:
        calls, handler = recorder({"graphId": GRAPH_ID})
        client = make_async_client(handler, identity=IDENTITY, token="tk")
        response = await client.request(
            "GET", "/api/v1/graph/graphs/{graph_id}", path_params={"graph_id": GRAPH_ID}
        )
        assert response.ok
        assert calls[0].headers["Authorization"] == "Bearer tk"
        assert calls[0].headers["X-Tenant-Id"] == "t1"
        await client.close()

    asyncio.run(scenario())


def test_async_graph_domain_methods():
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/stat"):
                return httpx.Response(
                    200,
                    json=envelope(
                        {
                            "graphId": GRAPH_ID,
                            "nodeCount": 2,
                            "edgeCount": 1,
                            "entityTypes": [{"type": "person", "count": 2}],
                            "relationTypes": [{"type": "works_at", "count": 1}],
                            "schemaCoverage": {"entity": 1.0, "relation": 1.0, "overall": 1.0},
                        }
                    ),
                )
            if path.endswith("/build"):
                return httpx.Response(
                    200,
                    json=envelope(
                        {"jobId": "job_1", "graphId": GRAPH_ID, "task": "build_text", "status": "pending"}
                    ),
                )
            if path.endswith("/search"):
                return httpx.Response(
                    200, json=envelope({"graphId": GRAPH_ID, "query": "q", "topK": 3, "semantica": False, "hits": []})
                )
            if path.endswith("/jobs") or "/jobs/" in path:
                return httpx.Response(200, json=envelope({"jobId": "job_1", "status": "success"}))
            return httpx.Response(200, json=envelope({"graphId": GRAPH_ID, "total": 1, "items": []}))

        client = make_async_client(handler)
        async with client:
            created = await client.graphs.create(name="异步图", kbId="kb_async")
            assert created.graphId == GRAPH_ID
            stat = await client.graphs.stat(GRAPH_ID)
            assert stat.nodeCount == 2 and stat.entityTypes[0].count == 2
            built = await client.graphs.build(GRAPH_ID, text="x", async_=True)
            assert built.jobId == "job_1"
            search = await client.graphs.search(GRAPH_ID, query="q", topK=3)
            assert search.semantica is False
            job = await client.jobs.wait("job_1", interval=0.0, timeout=0.5)
            assert job.finished
            raw = await client.raw("GET", f"/api/v1/graph/graphs/{GRAPH_ID}")
            assert raw.ok

    asyncio.run(scenario())


def test_async_identity_defaults_and_errors():
    async def scenario() -> None:
        calls, handler = recorder({"total": 0, "items": []})
        client = make_async_client(handler, identity=IDENTITY)
        await client.graphs.list()
        query = dict(httpx.URL(str(calls[0].url)).params)
        assert query == {"tenantId": "t1", "ownerId": "u1"}
        await client.close()

        not_found = make_async_client(
            lambda request: httpx.Response(404, json=envelope(None, err_code="200404", err_msg="图谱不存在"))
        )
        with pytest.raises(GraphEngineNotFoundError):
            await not_found.graphs.get("missing")
        await not_found.close()

        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        slow = make_async_client(timeout_handler, max_retries=0)
        with pytest.raises(GraphEngineTimeoutError):
            await slow.graphs.stat(GRAPH_ID)
        await slow.close()

    asyncio.run(scenario())


def test_async_fetch_openapi_repr():
    async def scenario() -> None:
        client = make_async_client(
            lambda request: httpx.Response(200, json={"openapi": "3.1.0", "paths": {}}), token="tk"
        )
        assert (await client.fetch_openapi())["openapi"] == "3.1.0"
        assert isinstance(client, AsyncGraphEngineClient)
        assert "token=<set>" in repr(client)
        await client.close()

    asyncio.run(scenario())
