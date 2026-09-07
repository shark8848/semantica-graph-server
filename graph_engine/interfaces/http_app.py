"""HTTP 协议面：FastAPI，前缀 /api/v1/graph，响应 envelope 与 open-ikc 对齐。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..errors import GraphEngineError
from ..protocol import TRACE_ID_HEADER, error, new_trace_id, ok
from ..runtime import get_service


def _trace(request: Request) -> str:
    return request.headers.get(TRACE_ID_HEADER) or new_trace_id()


def _handle(trace_id: str, fn) -> JSONResponse:
    try:
        return JSONResponse(ok(trace_id, fn()))
    except GraphEngineError as exc:
        return JSONResponse(error(trace_id, exc), status_code=400 if exc.code == "200001" else 404 if exc.code == "200404" else 409 if exc.code == "200409" else 500)
    except Exception as exc:  # pragma: no cover
        return JSONResponse(error(trace_id, exc), status_code=500)


def create_app(service: Any | None = None) -> FastAPI:
    svc = service or get_service()
    app = FastAPI(title="Semantica Graph Engine", version="0.1.0")
    router = app

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
        return _handle(
            tid,
            lambda: (
                svc.build_from_text(
                    graph_id,
                    text=str(payload.get("text") or ""),
                    doc_id=str(payload.get("docId") or ""),
                    title=str(payload.get("title") or ""),
                )
                if payload.get("text")
                else svc.build_from_records(
                    graph_id,
                    entities=payload.get("entities") or [],
                    relations=payload.get("relations") or [],
                    doc_id=str(payload.get("docId") or ""),
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
