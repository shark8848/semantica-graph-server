from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .envelope import Envelope
from .headers import CallerIdentity
from .models.graph import (
    AnalyticsData,
    BuildResult,
    DeleteResult,
    DeprecateResult,
    EntityListData,
    ExportResult,
    GraphListData,
    GraphMeta,
    IndexStatusData,
    JobData,
    JobListData,
    NeighborData,
    PathListData,
    RelationListData,
    SearchData,
    SparqlResult,
    StatResult,
)
from .transport_async import AsyncTransport

_GRAPHS_PATH = "/api/v1/graph/graphs"
_JOBS_PATH = "/api/v1/graph/jobs"


class AsyncGraphEngineClient:
    """Semantica Graph Engine 独立承载服务异步客户端；与同步客户端共享模型与错误映射。"""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: tuple[float, float] | float | None = None,
        max_retries: int = 2,
        identity: CallerIdentity | None = None,
        extra_headers: dict[str, str] | None = None,
        trace_id: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._transport = AsyncTransport(
            base_url=base_url,
            token=token,
            timeout=timeout,
            max_retries=max_retries,
            identity=identity,
            extra_headers=extra_headers,
            trace_id=trace_id,
            http_client=http_client,
        )
        self.graphs = AsyncGraphClient(self)
        self.jobs = AsyncJobsClient(self)

    async def request(
        self,
        method: str,
        path: str,
        *,
        path_params: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Envelope:
        """低层业务调用：errCode != 0 时抛对应异常。"""
        return await self._transport.request(
            method, path, path_params=path_params, params=params, body=body, raise_for_error=True
        )

    async def raw(
        self,
        method: str,
        path: str,
        *,
        path_params: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Envelope:
        """逃生口：返回原始统一响应壳，业务错误码不抛异常。"""
        return await self._transport.request(
            method, path, path_params=path_params, params=params, body=body, raise_for_error=False
        )

    async def fetch_openapi(self) -> dict[str, Any]:
        """运行时自检入口：拉取 FastAPI 自动生成的 /openapi.json（接口目录）。"""
        return await self._transport.get_json("/openapi.json")

    async def health(self) -> dict[str, Any]:
        """存活探测（GET /health，非统一壳的裸 JSON）。"""
        return await self._transport.get_json("/health")

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self) -> "AsyncGraphEngineClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    def __repr__(self) -> str:
        token_state = "<set>" if self._transport.has_token else "None"
        return f"AsyncGraphEngineClient(base_url={self._transport.base_url!r}, token={token_state})"


class AsyncGraphClient:
    """图谱域异步客户端：方法集合与 GraphClient 一致。"""

    def __init__(self, client: AsyncGraphEngineClient) -> None:
        self._client = client

    async def create(
        self,
        *,
        graphId: str = "",
        name: str = "",
        kbId: str = "",
        tenantId: str = "",
        ownerId: str = "",
        graphSchema: dict[str, Any] | None = None,
    ) -> GraphMeta:
        body: dict[str, Any] = {}
        if graphId:
            body["graphId"] = graphId
        if name:
            body["name"] = name
        if kbId:
            body["kbId"] = kbId
        if tenantId:
            body["tenantId"] = tenantId
        if ownerId:
            body["ownerId"] = ownerId
        if graphSchema:
            body["graphSchema"] = dict(graphSchema)
        envelope = await self._client.request("POST", _GRAPHS_PATH, body=body)
        return GraphMeta.from_dict(envelope.data or {})

    async def list(self, *, tenantId: str = "", ownerId: str = "") -> GraphListData:
        params: dict[str, str] = {}
        if tenantId:
            params["tenantId"] = tenantId
        if ownerId:
            params["ownerId"] = ownerId
        envelope = await self._client.request("GET", _GRAPHS_PATH, params=params)
        return GraphListData.from_dict(envelope.data or {})

    async def get(self, graphId: str) -> GraphMeta:
        envelope = await self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}", path_params={"graph_id": graphId}
        )
        return GraphMeta.from_dict(envelope.data or {})

    async def delete(self, graphId: str) -> DeleteResult:
        envelope = await self._client.request(
            "DELETE", _GRAPHS_PATH + "/{graph_id}", path_params={"graph_id": graphId}
        )
        return DeleteResult.from_dict(envelope.data or {})

    async def build(
        self,
        graphId: str,
        *,
        text: str = "",
        title: str = "",
        docId: str = "",
        llm: bool | None = None,
        entities: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
        async_: bool = False,
    ) -> BuildResult | JobData:
        body: dict[str, Any] = {}
        if text:
            body["text"] = text
        if title:
            body["title"] = title
        if docId:
            body["docId"] = docId
        if llm is not None:
            body["llm"] = bool(llm)
        if entities is not None:
            body["entities"] = [dict(item) for item in entities]
        if relations is not None:
            body["relations"] = [dict(item) for item in relations]
        if async_:
            body["async"] = "1"
        envelope = await self._client.request(
            "POST", _GRAPHS_PATH + "/{graph_id}/build", path_params={"graph_id": graphId}, body=body
        )
        if async_:
            return JobData.from_dict(envelope.data or {})
        return BuildResult.from_dict(envelope.data or {})

    async def merge(
        self,
        graphId: str,
        *,
        entities: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
        docId: str = "",
    ) -> BuildResult:
        body: dict[str, Any] = {
            "entities": [dict(item) for item in (entities or [])],
            "relations": [dict(item) for item in (relations or [])],
        }
        if docId:
            body["docId"] = docId
        envelope = await self._client.request(
            "POST", _GRAPHS_PATH + "/{graph_id}/merge", path_params={"graph_id": graphId}, body=body
        )
        return BuildResult.from_dict(envelope.data or {})

    async def deprecate_doc(self, graphId: str, *, docId: str) -> DeprecateResult:
        envelope = await self._client.request(
            "POST",
            _GRAPHS_PATH + "/{graph_id}/deprecate-doc",
            path_params={"graph_id": graphId},
            body={"docId": docId},
        )
        return DeprecateResult.from_dict(envelope.data or {})

    async def stat(self, graphId: str) -> StatResult:
        envelope = await self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/stat", path_params={"graph_id": graphId}
        )
        return StatResult.from_dict(envelope.data or {})

    async def nodes(
        self,
        graphId: str,
        *,
        entityType: str = "",
        name: str = "",
        page: int = 1,
        pageSize: int = 20,
    ) -> EntityListData:
        params: dict[str, Any] = {"page": page, "pageSize": pageSize}
        if entityType:
            params["entityType"] = entityType
        if name:
            params["name"] = name
        envelope = await self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/nodes", path_params={"graph_id": graphId}, params=params
        )
        return EntityListData.from_dict(envelope.data or {})

    async def edges(
        self,
        graphId: str,
        *,
        relationType: str = "",
        page: int = 1,
        pageSize: int = 20,
    ) -> RelationListData:
        params: dict[str, Any] = {"page": page, "pageSize": pageSize}
        if relationType:
            params["relationType"] = relationType
        envelope = await self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/edges", path_params={"graph_id": graphId}, params=params
        )
        return RelationListData.from_dict(envelope.data or {})

    async def neighbors(self, graphId: str, *, entityId: str, depth: int = 1) -> NeighborData:
        envelope = await self._client.request(
            "GET",
            _GRAPHS_PATH + "/{graph_id}/neighbors",
            path_params={"graph_id": graphId},
            params={"entityId": entityId, "depth": depth},
        )
        return NeighborData.from_dict(envelope.data or {})

    async def paths(
        self,
        graphId: str,
        *,
        sourceEntityId: str,
        targetEntityId: str,
        maxDepth: int = 5,
    ) -> PathListData:
        envelope = await self._client.request(
            "GET",
            _GRAPHS_PATH + "/{graph_id}/paths",
            path_params={"graph_id": graphId},
            params={
                "sourceEntityId": sourceEntityId,
                "targetEntityId": targetEntityId,
                "maxDepth": maxDepth,
            },
        )
        return PathListData.from_dict(envelope.data or {})

    async def analytics(self, graphId: str) -> AnalyticsData:
        envelope = await self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/analytics", path_params={"graph_id": graphId}
        )
        return AnalyticsData.from_dict(envelope.data or {})

    async def export(self, graphId: str, *, format: str = "jsonl") -> ExportResult:
        envelope = await self._client.request(
            "GET",
            _GRAPHS_PATH + "/{graph_id}/export",
            path_params={"graph_id": graphId},
            params={"format": format},
        )
        return ExportResult.from_dict(envelope.data or {})

    async def sparql(self, graphId: str, *, query: str, limit: int = 0) -> SparqlResult:
        envelope = await self._client.request(
            "POST",
            _GRAPHS_PATH + "/{graph_id}/sparql",
            path_params={"graph_id": graphId},
            body={"query": query, "limit": limit},
        )
        return SparqlResult.from_dict(envelope.data or {})

    async def search(self, graphId: str, *, query: str, topK: int = 10) -> SearchData:
        envelope = await self._client.request(
            "GET",
            _GRAPHS_PATH + "/{graph_id}/search",
            path_params={"graph_id": graphId},
            params={"query": query, "topK": topK},
        )
        return SearchData.from_dict(envelope.data or {})

    async def index_status(self, graphId: str) -> IndexStatusData:
        envelope = await self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/index-status", path_params={"graph_id": graphId}
        )
        return IndexStatusData.from_dict(envelope.data or {})


class AsyncJobsClient:
    """异步任务域异步客户端：run / get / list / wait。"""

    def __init__(self, client: AsyncGraphEngineClient) -> None:
        self._client = client

    async def run(self, jobId: str) -> JobData:
        envelope = await self._client.request(
            "POST", _JOBS_PATH + "/{job_id}/run", path_params={"job_id": jobId}
        )
        return JobData.from_dict(envelope.data or {})

    async def get(self, jobId: str) -> JobData:
        envelope = await self._client.request(
            "GET", _JOBS_PATH + "/{job_id}", path_params={"job_id": jobId}
        )
        return JobData.from_dict(envelope.data or {})

    async def list(self, *, graphId: str = "", limit: int = 20) -> JobListData:
        params: dict[str, Any] = {"limit": limit}
        if graphId:
            params["graphId"] = graphId
        envelope = await self._client.request("GET", _JOBS_PATH, params=params)
        return JobListData.from_dict(envelope.data or {})

    async def wait(self, jobId: str, *, interval: float = 0.5, timeout: float = 60.0) -> JobData:
        """轮询任务直至 success/failed 或超时（超时返回最后一次观测结果，不抛异常）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, 0.0)
        job = await self.get(jobId)
        while not job.finished and loop.time() < deadline:
            await asyncio.sleep(max(interval, 0.0))
            job = await self.get(jobId)
        return job
