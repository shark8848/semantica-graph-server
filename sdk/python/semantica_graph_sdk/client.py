from __future__ import annotations

import time
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
from .transport import Transport

_GRAPHS_PATH = "/api/v1/graph/graphs"
_JOBS_PATH = "/api/v1/graph/jobs"


class GraphEngineClient:
    """Semantica Graph Engine 独立承载服务同步客户端。

    覆盖服务端全部图谱能力：图谱 CRUD / 建图与增量合并（build/merge/deprecate-doc）/
    查询（stat/nodes/edges/neighbors/paths/analytics/export/sparql/search）/ 异步任务（jobs）。
    """

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
        http_client: httpx.Client | None = None,
    ) -> None:
        self._transport = Transport(
            base_url=base_url,
            token=token,
            timeout=timeout,
            max_retries=max_retries,
            identity=identity,
            extra_headers=extra_headers,
            trace_id=trace_id,
            http_client=http_client,
        )
        self.graphs = GraphClient(self)
        self.jobs = JobsClient(self)

    def request(
        self,
        method: str,
        path: str,
        *,
        path_params: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Envelope:
        """低层业务调用：errCode != 0 时抛对应异常。"""
        return self._transport.request(
            method, path, path_params=path_params, params=params, body=body, raise_for_error=True
        )

    def raw(
        self,
        method: str,
        path: str,
        *,
        path_params: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Envelope:
        """逃生口：返回原始统一响应壳，业务错误码不抛异常。"""
        return self._transport.request(
            method, path, path_params=path_params, params=params, body=body, raise_for_error=False
        )

    def fetch_openapi(self) -> dict[str, Any]:
        """运行时自检入口：拉取 FastAPI 自动生成的 /openapi.json（接口目录）。"""
        return self._transport.get_json("/openapi.json")

    def health(self) -> dict[str, Any]:
        """存活探测（GET /health，非统一壳的裸 JSON）。"""
        return self._transport.get_json("/health")

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "GraphEngineClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        token_state = "<set>" if self._transport.has_token else "None"
        return f"GraphEngineClient(base_url={self._transport.base_url!r}, token={token_state})"


class GraphClient:
    """图谱域客户端：create / list / get / delete / build / merge / deprecate_doc /
    stat / nodes / edges / neighbors / paths / analytics / export / sparql / search / index_status。"""

    def __init__(self, client: GraphEngineClient) -> None:
        self._client = client

    # ---------- 图谱 CRUD ----------

    def create(
        self,
        *,
        graphId: str = "",
        name: str = "",
        kbId: str = "",
        tenantId: str = "",
        ownerId: str = "",
        graphSchema: dict[str, Any] | None = None,
    ) -> GraphMeta:
        """创建图谱；graphId 缺省由服务端从 kbId 稳定派生（无 kbId 时随机生成）。"""
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
        envelope = self._client.request("POST", _GRAPHS_PATH, body=body)
        return GraphMeta.from_dict(envelope.data or {})

    def list(self, *, tenantId: str = "", ownerId: str = "") -> GraphListData:
        """列出图谱（tenantId/ownerId 过滤；身份上下文可补齐缺省值）。"""
        params: dict[str, str] = {}
        if tenantId:
            params["tenantId"] = tenantId
        if ownerId:
            params["ownerId"] = ownerId
        envelope = self._client.request("GET", _GRAPHS_PATH, params=params)
        return GraphListData.from_dict(envelope.data or {})

    def get(self, graphId: str) -> GraphMeta:
        """查询图谱元信息。"""
        envelope = self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}", path_params={"graph_id": graphId}
        )
        return GraphMeta.from_dict(envelope.data or {})

    def delete(self, graphId: str) -> DeleteResult:
        """删除图谱（级联删除实体/关系/任务）。"""
        envelope = self._client.request(
            "DELETE", _GRAPHS_PATH + "/{graph_id}", path_params={"graph_id": graphId}
        )
        return DeleteResult.from_dict(envelope.data or {})

    # ---------- 建图 / 合并 / 废弃 ----------

    def build(
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
        """建图：``text`` 文本建图（``llm`` 开启 LLM 实体增强）或 ``entities``/``relations`` 记录建图。

        ``async_=True`` 时登记异步任务并返回 JobData（Celery 未启用时保持 pending，
        可用 ``client.jobs.run(jobId)`` 手动同步执行或 ``client.jobs.wait(jobId)`` 轮询）。
        """
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
        envelope = self._client.request(
            "POST", _GRAPHS_PATH + "/{graph_id}/build", path_params={"graph_id": graphId}, body=body
        )
        if async_:
            return JobData.from_dict(envelope.data or {})
        return BuildResult.from_dict(envelope.data or {})

    def merge(
        self,
        graphId: str,
        *,
        entities: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
        docId: str = "",
    ) -> BuildResult:
        """增量合并实体/关系（传 docId 时同时按该文档失效旧资产，对齐 open-ikc build_from_doc）。"""
        body: dict[str, Any] = {
            "entities": [dict(item) for item in (entities or [])],
            "relations": [dict(item) for item in (relations or [])],
        }
        if docId:
            body["docId"] = docId
        envelope = self._client.request(
            "POST", _GRAPHS_PATH + "/{graph_id}/merge", path_params={"graph_id": graphId}, body=body
        )
        return BuildResult.from_dict(envelope.data or {})

    def deprecate_doc(self, graphId: str, *, docId: str) -> DeprecateResult:
        """按 docId 增量废弃该文档贡献的实体/关系。"""
        envelope = self._client.request(
            "POST",
            _GRAPHS_PATH + "/{graph_id}/deprecate-doc",
            path_params={"graph_id": graphId},
            body={"docId": docId},
        )
        return DeprecateResult.from_dict(envelope.data or {})

    # ---------- 查询 ----------

    def stat(self, graphId: str) -> StatResult:
        """图谱统计（节点/边计数、类型分布、schema 覆盖率）。"""
        envelope = self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/stat", path_params={"graph_id": graphId}
        )
        return StatResult.from_dict(envelope.data or {})

    def nodes(
        self,
        graphId: str,
        *,
        entityType: str = "",
        name: str = "",
        page: int = 1,
        pageSize: int = 20,
    ) -> EntityListData:
        """分页查询实体节点（entityType/name 过滤；pageSize 服务端上限 200）。"""
        params: dict[str, Any] = {"page": page, "pageSize": pageSize}
        if entityType:
            params["entityType"] = entityType
        if name:
            params["name"] = name
        envelope = self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/nodes", path_params={"graph_id": graphId}, params=params
        )
        return EntityListData.from_dict(envelope.data or {})

    def edges(
        self,
        graphId: str,
        *,
        relationType: str = "",
        page: int = 1,
        pageSize: int = 20,
    ) -> RelationListData:
        """分页查询关系边（relationType 过滤）。"""
        params: dict[str, Any] = {"page": page, "pageSize": pageSize}
        if relationType:
            params["relationType"] = relationType
        envelope = self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/edges", path_params={"graph_id": graphId}, params=params
        )
        return RelationListData.from_dict(envelope.data or {})

    def neighbors(self, graphId: str, *, entityId: str, depth: int = 1) -> NeighborData:
        """实体邻域（depth 仅支持 1/2）。"""
        envelope = self._client.request(
            "GET",
            _GRAPHS_PATH + "/{graph_id}/neighbors",
            path_params={"graph_id": graphId},
            params={"entityId": entityId, "depth": depth},
        )
        return NeighborData.from_dict(envelope.data or {})

    def paths(
        self,
        graphId: str,
        *,
        sourceEntityId: str,
        targetEntityId: str,
        maxDepth: int = 5,
    ) -> PathListData:
        """最短路径（BFS；无路径时 items 为空、total 为 0）。"""
        envelope = self._client.request(
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

    def analytics(self, graphId: str) -> AnalyticsData:
        """semantica GraphAnalyzer 结构分析（不可用时 analysis 内为降级标记）。"""
        envelope = self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/analytics", path_params={"graph_id": graphId}
        )
        return AnalyticsData.from_dict(envelope.data or {})

    def export(self, graphId: str, *, format: str = "jsonl") -> ExportResult:
        """导出图谱（jsonl/json/turtle/nt/nq/rdfxml；RDF 格式需服务端 pyoxigraph 可用）。"""
        envelope = self._client.request(
            "GET",
            _GRAPHS_PATH + "/{graph_id}/export",
            path_params={"graph_id": graphId},
            params={"format": format},
        )
        return ExportResult.from_dict(envelope.data or {})

    def sparql(self, graphId: str, *, query: str, limit: int = 0) -> SparqlResult:
        """SPARQL 查询（白名单 SELECT/ASK/CONSTRUCT/DESCRIBE；limit<=0 走服务端行数上限）。"""
        envelope = self._client.request(
            "POST",
            _GRAPHS_PATH + "/{graph_id}/sparql",
            path_params={"graph_id": graphId},
            body={"query": query, "limit": limit},
        )
        return SparqlResult.from_dict(envelope.data or {})

    def search(self, graphId: str, *, query: str, topK: int = 10) -> SearchData:
        """语义检索：自然语言查询 → 相关实体/关系（检索链不可用时返回确定降级结果）。"""
        envelope = self._client.request(
            "GET",
            _GRAPHS_PATH + "/{graph_id}/search",
            path_params={"graph_id": graphId},
            params={"query": query, "topK": topK},
        )
        return SearchData.from_dict(envelope.data or {})

    def index_status(self, graphId: str) -> IndexStatusData:
        """语义检索/向量索引可用状态（运维与调试）。"""
        envelope = self._client.request(
            "GET", _GRAPHS_PATH + "/{graph_id}/index-status", path_params={"graph_id": graphId}
        )
        return IndexStatusData.from_dict(envelope.data or {})


class JobsClient:
    """异步任务域客户端：run / get / list / wait。"""

    def __init__(self, client: GraphEngineClient) -> None:
        self._client = client

    def run(self, jobId: str) -> JobData:
        """同步执行已登记任务（broker 不可达时的确定性回退路径）。"""
        envelope = self._client.request(
            "POST", _JOBS_PATH + "/{job_id}/run", path_params={"job_id": jobId}
        )
        return JobData.from_dict(envelope.data or {})

    def get(self, jobId: str) -> JobData:
        """查询任务状态（pending/active/success/failed）。"""
        envelope = self._client.request("GET", _JOBS_PATH + "/{job_id}", path_params={"job_id": jobId})
        return JobData.from_dict(envelope.data or {})

    def list(self, *, graphId: str = "", limit: int = 20) -> JobListData:
        """列出任务（graphId 过滤）。"""
        params: dict[str, Any] = {"limit": limit}
        if graphId:
            params["graphId"] = graphId
        envelope = self._client.request("GET", _JOBS_PATH, params=params)
        return JobListData.from_dict(envelope.data or {})

    def wait(self, jobId: str, *, interval: float = 0.5, timeout: float = 60.0) -> JobData:
        """轮询任务直至 success/failed 或超时（超时返回最后一次观测结果，不抛异常）。"""
        deadline = time.monotonic() + max(timeout, 0.0)
        job = self.get(jobId)
        while not job.finished and time.monotonic() < deadline:
            time.sleep(max(interval, 0.0))
            job = self.get(jobId)
        return job
