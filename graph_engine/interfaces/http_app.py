"""HTTP 协议面：FastAPI，前缀 /api/v1/graph，响应 envelope 与 open-ikc 对齐。"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from ikc_sdk.core.trace import extract_request_trace_id, normalize_trace_id

from ..errors import GraphEngineError
from ..protocol import error, ok
from ..runtime import get_service


def _trace(request: Request) -> str:
    """入口 traceId（G2）：按契约头优先级提取，非法值重新生成（禁止继续透传）。"""
    return normalize_trace_id(extract_request_trace_id(request.headers))


def _flag(value: Any) -> bool | None:
    """宽松布尔解析：None 保持 None（走服务端 env 缺省），1/true/yes 视为 True。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def _handle(trace_id: str, fn) -> JSONResponse:
    try:
        return JSONResponse(ok(trace_id, fn()))
    except GraphEngineError as exc:
        if exc.code == "200001":
            status = 400
        elif exc.code == "200404":
            status = 404
        elif exc.code == "200409":
            status = 409
        elif exc.code == "260009":  # 本体能力不可用：如实降级，不用 500
            status = 501
        else:
            status = 500
        return JSONResponse(error(trace_id, exc), status_code=status)
    except Exception as exc:  # pragma: no cover
        return JSONResponse(error(trace_id, exc), status_code=500)


def create_app(service: Any | None = None) -> FastAPI:
    from ..config import Settings
    from ..logging_setup import configure_logging, set_trace_id

    settings = Settings()
    configure_logging(level=settings.log_level, log_center=settings.log_center)

    svc = service or get_service()
    app = FastAPI(title="Semantica Graph Engine", version="0.1.0")
    router = app

    access_logger = logging.getLogger("graph_engine.access")

    @app.middleware("http")
    async def _access_log(request: Request, call_next):
        """请求访问日志（投递日志中心）：绑定 traceId 并记录方法/路径/状态/耗时。"""
        tid = _trace(request)
        set_trace_id(tid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            access_logger.exception(
                "http.request method=%s path=%s status=500 duration_ms=%.1f",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
            )
            raise
        # 健康探针每 15s 一次，不进日志中心（避免淹没业务日志）
        if request.url.path not in ("/health", "/ready"):
            access_logger.info(
                "http.request method=%s path=%s status=%s duration_ms=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                (time.perf_counter() - started) * 1000,
            )
        return response

    @app.exception_handler(GraphEngineError)
    async def _graph_error_handler(request: Request, exc: GraphEngineError) -> JSONResponse:
        return JSONResponse(error(_trace(request), exc), status_code=400)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "graph-engine"}

    @app.get("/ready")
    def ready() -> dict[str, Any]:
        return {"status": "ready"}

    # ---------- graph CRUD ----------

    @app.post("/api/v1/graph/graphs")
    def create_graph(request: Request, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.create_graph(
                graph_id_value=str(payload.get("graphId") or ""),
                name=str(payload.get("name") or ""),
                kb_id=str(payload.get("kbId") or ""),
                tenant_id=str(payload.get("tenantId") or ""),
                owner_id=str(payload.get("ownerId") or ""),
                schema=payload.get("graphSchema"),
            ),
        )

    @app.get("/api/v1/graph/graphs/{graph_id}")
    def get_graph(request: Request, graph_id: str) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.get_graph(graph_id))

    @app.delete("/api/v1/graph/graphs/{graph_id}")
    def delete_graph(request: Request, graph_id: str) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.delete_graph(graph_id))

    @app.get("/api/v1/graph/graphs")
    def list_graphs(
        request: Request,
        tenantId: str = Query(default=""),
        ownerId: str = Query(default=""),
    ) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.list_graphs(tenant_id=tenantId, owner_id=ownerId))

    # ---------- build / merge / deprecate ----------

    @app.post("/api/v1/graph/graphs/{graph_id}/build")
    def build_graph(request: Request, graph_id: str, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        if str(payload.get("async") or "").lower() in ("1", "true", "yes"):
            job_task = "build_text" if payload.get("text") else "build"

            def _submit_and_dispatch() -> dict[str, Any]:
                """校验图谱存在后登记 pending job；celery 启用时再投递，未启用仅登记。"""
                svc.get_graph(graph_id)  # async 提交前先校验图谱存在（缺失抛 200404）
                job = svc.submit_job(job_task, graph_id, payload)
                from .celery_app import dispatch_job

                dispatch_job(job["jobId"], graph_id, job_task, payload)
                return job

            return _handle(tid, _submit_and_dispatch)
        constraints = payload.get("constraints")
        return _handle(
            tid,
            lambda: (
                svc.build_from_text(
                    graph_id,
                    text=str(payload.get("text") or ""),
                    doc_id=str(payload.get("docId") or ""),
                    title=str(payload.get("title") or ""),
                    llm=_flag(payload.get("llm")),
                    constraints=constraints,
                )
                if payload.get("text")
                else svc.build_from_records(
                    graph_id,
                    entities=payload.get("entities") or [],
                    relations=payload.get("relations") or [],
                    doc_id=str(payload.get("docId") or ""),
                    constraints=constraints,
                )
            ),
        )

    @app.post("/api/v1/graph/graphs/{graph_id}/merge")
    def merge_graph(request: Request, graph_id: str, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.merge_records(
                graph_id,
                entities=payload.get("entities") or [],
                relations=payload.get("relations") or [],
                doc_id=str(payload.get("docId") or ""),
            ),
        )

    @app.get("/api/v1/graph/graphs/{graph_id}/constraints")
    def graph_constraints(request: Request, graph_id: str) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.get_constraints(graph_id))

    @app.put("/api/v1/graph/graphs/{graph_id}/constraints")
    def put_graph_constraints(
        request: Request, graph_id: str, payload: dict[str, Any]
    ) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.put_constraints(graph_id, payload))

    @app.post("/api/v1/graph/graphs/{graph_id}/deprecate-doc")
    def deprecate_doc(request: Request, graph_id: str, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.deprecate_doc(graph_id, doc_id=str(payload.get("docId") or "")))

    # ---------- 查询 ----------

    @app.get("/api/v1/graph/graphs/{graph_id}/stat")
    def graph_stat(request: Request, graph_id: str) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.stat(graph_id))

    @app.get("/api/v1/graph/graphs/{graph_id}/nodes")
    def graph_nodes(
        request: Request,
        graph_id: str,
        entityType: str = Query(default=""),
        name: str = Query(default=""),
        page: int = Query(default=1),
        pageSize: int = Query(default=20),
    ) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.list_nodes(graph_id, entity_type=entityType, name=name, page=page, page_size=pageSize),
        )

    @app.get("/api/v1/graph/graphs/{graph_id}/edges")
    def graph_edges(
        request: Request,
        graph_id: str,
        relationType: str = Query(default=""),
        page: int = Query(default=1),
        pageSize: int = Query(default=20),
    ) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.list_edges(graph_id, relation_type=relationType, page=page, page_size=pageSize),
        )

    @app.get("/api/v1/graph/graphs/{graph_id}/neighbors")
    def graph_neighbors(
        request: Request,
        graph_id: str,
        entityId: str = Query(...),
        depth: int = Query(default=1),
    ) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.neighbors(graph_id, entity_id_value=entityId, depth=depth))

    @app.get("/api/v1/graph/graphs/{graph_id}/paths")
    def graph_paths(
        request: Request,
        graph_id: str,
        sourceEntityId: str = Query(...),
        targetEntityId: str = Query(...),
        maxDepth: int = Query(default=5),
    ) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.paths(graph_id, source_entity_id=sourceEntityId, target_entity_id=targetEntityId, max_depth=maxDepth),
        )

    @app.get("/api/v1/graph/graphs/{graph_id}/analytics")
    def graph_analytics(request: Request, graph_id: str) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.analytics(graph_id))

    @app.get("/api/v1/graph/graphs/{graph_id}/export")
    def graph_export(request: Request, graph_id: str, format: str = Query(default="jsonl")) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.export(graph_id, format=format))

    @app.post("/api/v1/graph/graphs/{graph_id}/sparql")
    def graph_sparql(request: Request, graph_id: str, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.sparql(
                graph_id,
                query=str(payload.get("query") or ""),
                limit=int(payload.get("limit") or 0),
            ),
        )

    @app.get("/api/v1/graph/graphs/{graph_id}/search")
    def graph_search(
        request: Request,
        graph_id: str,
        query: str = Query(default=""),
        topK: int = Query(default=10),
    ) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.semantic_search(graph_id, query=query, top_k=topK))

    @app.get("/api/v1/graph/graphs/{graph_id}/index-status")
    def graph_index_status(request: Request, graph_id: str) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.index_status(graph_id))

    # ---------- 本体面（O-21 ~ O-24；semantica.ontology 守卫式封装） ----------

    @app.post("/api/v1/graph/ontology/candidates")
    def ontology_candidates(request: Request, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.generate_ontology_candidates(
                str(payload.get("graphId") or ""),
                ontology_id=str(payload.get("ontologyId") or ""),
                sources=payload.get("sources"),
                max_classes=int(payload.get("maxClasses") or 40),
            ),
        )

    @app.post("/api/v1/graph/ontology/validate")
    def ontology_validate(request: Request, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.validate_ontology_definition(payload))

    @app.post("/api/v1/graph/ontology/ingest")
    def ontology_ingest(request: Request, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.ingest_ontology(
                str(payload.get("content") or ""), format=str(payload.get("format") or "owl")
            ),
        )

    @app.post("/api/v1/graph/ontology/export")
    def ontology_export(request: Request, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.export_ontology(payload))

    @app.post("/api/v1/graph/ontology/validate-graph")
    def ontology_validate_graph(request: Request, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.validate_graph_ontology(
                str(payload.get("graphId") or ""),
                payload=payload,
                max_issues=int(payload.get("maxIssues") or 200),
                include_shacl=_flag(payload.get("includeShacl")) is True,
            ),
        )

    @app.post("/api/v1/graph/ontology/coverage")
    def ontology_coverage(request: Request, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.ontology_coverage(str(payload.get("graphId") or ""), payload=payload),
        )

    @app.post("/api/v1/graph/ontology/snapshots")
    def ontology_put_snapshot(request: Request, payload: dict[str, Any]) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.put_ontology_snapshot(
                str(payload.get("graphId") or ""),
                ontology_id=str(payload.get("ontologyId") or ""),
                ontology_version=int(payload.get("ontologyVersion") or 0),
                graph_schema=payload.get("graphSchema"),
            ),
        )

    @app.get("/api/v1/graph/ontology/snapshots")
    def ontology_list_snapshots(request: Request, graphId: str = Query(default="")) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.list_ontology_snapshots(graphId))

    @app.get("/api/v1/graph/ontology/snapshots/diff")
    def ontology_snapshot_diff(
        request: Request,
        graphId: str = Query(default=""),
        fromVersion: int = Query(default=0),
        toVersion: int = Query(default=0),
    ) -> JSONResponse:
        tid = _trace(request)
        return _handle(
            tid,
            lambda: svc.ontology_version_diff(
                graphId, from_version=fromVersion, to_version=toVersion
            ),
        )

    # ---------- jobs ----------

    @app.post("/api/v1/graph/jobs/{job_id}/run")
    def run_job(request: Request, job_id: str) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.run_job(job_id))

    @app.get("/api/v1/graph/jobs/{job_id}")
    def get_job(request: Request, job_id: str) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.get_job(job_id))

    @app.get("/api/v1/graph/jobs")
    def list_jobs(
        request: Request,
        graphId: str = Query(default=""),
        limit: int = Query(default=20),
    ) -> JSONResponse:
        tid = _trace(request)
        return _handle(tid, lambda: svc.list_jobs(graph_id_value=graphId, limit=limit))

    return app
