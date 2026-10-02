"""结构化 JSON 日志 + IKC Log Center 远程投递（HTTP POST {url}/ingest）。

与 openwiki-server 同款口径：根 logger 只保留一个 stdout handler（JSON 或纯文本），
``log_center.enabled`` 时再挂一个日志中心的异步 HTTP handler（best-effort，故障不阻断业务）。

traceId 经 ``trace_id_var`` 绑定（HTTP 入口按契约头归一后写入），随每条日志投递，
供日志中心按 trace 串链。
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import LogCenterSettings

APP_NAME = "graph-engine"

trace_id_var: ContextVar[str | None] = ContextVar("graph_trace_id", default=None)

_configured = False


def set_trace_id(trace_id: str | None) -> None:
    """绑定当前上下文 traceId（空值清除），供日志中心串链。"""
    trace_id_var.set(trace_id or None)


class JsonFormatter(logging.Formatter):
    """单行 JSON：ts/level/logger/message/trace_id/app（+ exc_info/extra_fields）。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).isoformat()
        payload: dict[str, Any] = {
            "ts": ts,
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "app": APP_NAME,
            "message": record.getMessage(),
        }
        trace_id = trace_id_var.get()
        if trace_id:
            payload["trace_id"] = trace_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    log_center: LogCenterSettings | None = None,
    *,
    force: bool = False,
) -> None:
    """配置根 logger；启用时挂载 IKC Log Center 投递 handler（幂等，``force`` 供 fork 后重建）。"""
    global _configured
    if _configured and not force:
        logging.getLogger().setLevel(level.upper())
        return

    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    # uvicorn 自带 access 日志（与引擎 access 日志重复）；引擎侧统一投递自己的 access 日志
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _configured = True

    if log_center is not None and log_center.enabled:
        attach_log_center_handlers(log_center)


def attach_log_center_handlers(config: LogCenterSettings) -> None:
    """尽力而为地挂载 IKC Log Center 远程投递（HTTP POST {url}/ingest）。"""
    if not config.url:
        logging.getLogger(__name__).warning("log_center.enabled 为 true 但未配置 url；远程投递关闭")
        return
    try:
        from log_center_sdk.handlers import HttpLogHandler
    except ImportError:
        logging.getLogger(__name__).warning(
            "未安装 ikc-log-center（pip install ikc-log-center）；远程日志投递关闭"
        )
        return
    try:
        handler = HttpLogHandler(
            endpoint=config.url,
            timeout=config.timeout_seconds,
            queue_size=config.queue_size,
            batch_size=config.batch_size,
            token=config.token or "",
        )
        handler.setFormatter(JsonFormatter())
        logging.getLogger().addHandler(handler)
        logging.getLogger(__name__).info("log center HTTP 投递已挂载（app=%s）", APP_NAME)
    except Exception:
        logging.getLogger(__name__).exception("挂载 log center handler 失败")


def attach_celery_fork_hook(app: Any) -> bool:
    """给 Celery 应用注册 prefork 重建钩子（worker 专用）。

    prefork 子进程只继承 handler 对象、不继承后台投递线程，日志会静默丢失；故在
    ``worker_process_init`` 重建 handler（含投递线程）。注册失败返回 False，不抛异常。
    """
    try:
        from celery.signals import worker_process_init
    except Exception:  # pragma: no cover - 未安装 celery
        return False

    def _on_worker_fork(**_kwargs: object) -> None:
        from .config import Settings

        try:
            settings = Settings()
            configure_logging(
                level=settings.log_level, log_center=settings.log_center, force=True
            )
        except Exception:  # noqa: BLE001 - 子进程日志重建失败不得阻断 worker
            pass

    try:
        # weak=False 是硬要求：Celery 信号默认弱引用，局部函数会被 GC，钩子静默失效
        worker_process_init.connect(_on_worker_fork, weak=False)
    except Exception:  # pragma: no cover
        return False
    return True


__all__ = [
    "APP_NAME",
    "JsonFormatter",
    "attach_celery_fork_hook",
    "attach_log_center_handlers",
    "configure_logging",
    "set_trace_id",
    "trace_id_var",
]
