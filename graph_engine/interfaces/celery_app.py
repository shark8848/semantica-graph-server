"""Celery 协议面：异步任务（build / merge / deprecate / export）。

任务与 HTTP async 模式共用 application 层；broker/backend 由环境变量配置，
默认 redis://localhost:6379/0。
"""

from __future__ import annotations

from typing import Any

from celery import Celery

from ..config import Settings
from ..runtime import get_service

_settings = Settings()

celery_app = Celery(
    "graph_engine",
    broker=_settings.celery_broker,
    backend=_settings.celery_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    timezone="UTC",
    enable_utc=True,
)


def _finish_job(job_id: str, result: dict[str, Any]) -> None:
    """任务完成后回写引擎 job 表（HTTP async 可轮询）。"""
    if job_id:
        get_service().store.update_job(job_id, status="success", result=result)


@celery_app.task(name="graph_engine.build")
def build_task(
    graph_id: str,
    entities: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    text: str = "",
    doc_id: str = "",
    title: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    """建图任务：--text 走规则抽取；否则走显式记录。"""
    svc = get_service()
    if text:
        result = svc.build_from_text(graph_id, text=text, doc_id=doc_id, title=title)
    else:
        result = svc.build_from_records(
            graph_id, entities=entities or [], relations=relations or [], doc_id=doc_id
        )
    _finish_job(job_id, result)
    return result


@celery_app.task(name="graph_engine.merge")
def merge_task(
    graph_id: str,
    entities: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    doc_id: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    """增量合并任务。"""
    svc = get_service()
    result = svc.merge_records(graph_id, entities=entities or [], relations=relations or [], doc_id=doc_id)
    _finish_job(job_id, result)
    return result


@celery_app.task(name="graph_engine.deprecate_doc")
def deprecate_doc_task(graph_id: str, doc_id: str, job_id: str = "") -> dict[str, Any]:
    """按 docId 增量废弃任务。"""
    svc = get_service()
    result = svc.deprecate_doc(graph_id, doc_id=doc_id)
    _finish_job(job_id, result)
    return result


@celery_app.task(name="graph_engine.export")
def export_task(graph_id: str, format: str = "jsonl", job_id: str = "") -> dict[str, Any]:
    """导出任务。"""
    svc = get_service()
    result = svc.export(graph_id, format=format)
    _finish_job(job_id, result)
    return result


def worker_main(argv: list[str] | None = None) -> None:
    """启动 worker（供 `graph-engine serve worker` 调用）。"""
    celery_app.worker_main(argv or ["worker", "-l", "info"])
