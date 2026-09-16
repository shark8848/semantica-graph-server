"""Celery 协议面：异步任务（build / merge / deprecate / export）。

任务与 HTTP async 模式共用 application 层；broker/backend 由环境变量配置，
默认 redis://localhost:6379/0。celery_enabled（GRAPH_ENGINE_CELERY_ENABLED）
为 False 时只登记 pending job、不投递，也绝不触碰 broker。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from celery import Celery

from ..config import Settings
from ..runtime import get_service

# 模块级快照：仅用于 import 时固定 worker 的 broker/backend；
# 投递开关 dispatch_job 每次调用重新读取 Settings，以便运行时按环境变量判断。
_settings = Settings()

logger = logging.getLogger(__name__)

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


def _run_job(job_id: str, run: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """执行任务体并把**终态**回写引擎 job 表（HTTP async / G-07 轮询据此判定）。

    异常必须落 `failed` + `error`：引擎只在成功时回写 job 的话，任务抛异常（如目标图谱不存在）
    会让作业永远停在 `pending`，调用方只能靠轮询超时猜失败。异常照旧向上抛，Celery 保持
    标准的 FAILURE 语义。
    """
    try:
        result = run()
    except Exception as exc:
        if job_id:
            get_service().store.update_job(job_id, status="failed", error=str(exc))
        raise
    if job_id:
        get_service().store.update_job(job_id, status="success", result=result)
    return result


@celery_app.task(name="graph_engine.build")
def build_task(
    graph_id: str,
    entities: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
    text: str = "",
    doc_id: str = "",
    title: str = "",
    llm: bool = False,
    job_id: str = "",
) -> dict[str, Any]:
    """建图任务：--text 走规则抽取（可开 LLM 增强）；否则走显式记录。"""
    svc = get_service()
    if text:
        return _run_job(
            job_id,
            lambda: svc.build_from_text(graph_id, text=text, doc_id=doc_id, title=title, llm=llm),
        )
    return _run_job(
        job_id,
        lambda: svc.build_from_records(
            graph_id, entities=entities or [], relations=relations or [], doc_id=doc_id
        ),
    )


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
    return _run_job(
        job_id,
        lambda: svc.merge_records(
            graph_id, entities=entities or [], relations=relations or [], doc_id=doc_id
        ),
    )


@celery_app.task(name="graph_engine.deprecate_doc")
def deprecate_doc_task(graph_id: str, doc_id: str, job_id: str = "") -> dict[str, Any]:
    """按 docId 增量废弃任务。"""
    svc = get_service()
    return _run_job(job_id, lambda: svc.deprecate_doc(graph_id, doc_id=doc_id))


@celery_app.task(name="graph_engine.export")
def export_task(graph_id: str, format: str = "jsonl", job_id: str = "") -> dict[str, Any]:
    """导出任务。"""
    svc = get_service()
    return _run_job(job_id, lambda: svc.export(graph_id, format=format))


def _flag(value: Any) -> bool:
    """宽松布尔解析（payload 可能是 JSON bool 或字符串）。"""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes")


def dispatch_job(
    job_id: str,
    graph_id: str,
    task: str,
    payload: dict[str, Any] | None = None,
) -> bool:
    """把已登记 job 投递到 Celery broker；成功返回 True，否则返回 False。

    task 映射：build/build_text → build_task（text 建图时把 text/title/llm 传入
    build_task 参数）；merge → merge_task；
    deprecate_doc → deprecate_doc_task；export → export_task。
    未启用 celery_enabled 或 task 未知时返回 False（仅告警，不抛异常），
    且未启用时绝不连接 broker（本地无 Redis 也不报错/挂起）。
    """
    payload = dict(payload or {})
    if task in ("build", "build_text"):
        task_fn = build_task
        if task == "build_text":
            task_kwargs = {
                "text": str(payload.get("text") or ""),
                "doc_id": str(payload.get("docId") or ""),
                "title": str(payload.get("title") or ""),
                "llm": _flag(payload.get("llm")),
            }
        else:
            task_kwargs = {
                "entities": payload.get("entities") or [],
                "relations": payload.get("relations") or [],
                "doc_id": str(payload.get("docId") or ""),
            }
    elif task == "merge":
        task_fn = merge_task
        task_kwargs = {
            "entities": payload.get("entities") or [],
            "relations": payload.get("relations") or [],
            "doc_id": str(payload.get("docId") or ""),
        }
    elif task == "deprecate_doc":
        task_fn = deprecate_doc_task
        task_kwargs = {"doc_id": str(payload.get("docId") or "")}
    elif task == "export":
        task_fn = export_task
        task_kwargs = {"format": str(payload.get("format") or "jsonl")}
    else:
        logger.warning("未知 Celery job task：%s（job_id=%s），跳过投递", task, job_id)
        return False
    if not Settings().celery_enabled:
        return False
    task_fn.apply_async(args=[graph_id], kwargs={**task_kwargs, "job_id": job_id})
    return True


def worker_main(argv: list[str] | None = None) -> None:
    """启动 worker（供 `graph-engine serve worker` 调用）。"""
    celery_app.worker_main(argv or ["worker", "-l", "info"])
