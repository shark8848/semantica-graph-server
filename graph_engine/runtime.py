"""运行时单例：存储与服务全局共享（HTTP/gRPC/Celery/MCP/CLI 复用同一实例）。

存储经 `persistence.create_store` 按后端构造（缺省 Neo4j，不可用降级 SQLite）；
显式 `db_path` 时固定 SQLite（CLI `--db-path` 与既有测试口径）。
"""

from __future__ import annotations

import threading

from .application.service import GraphEngineService
from .config import Settings
from .persistence import GraphStore, create_store

_lock = threading.RLock()
_store: GraphStore | None = None
_service: GraphEngineService | None = None


def get_settings() -> Settings:
    return Settings()


def get_store(db_path: str | None = None) -> GraphStore:
    global _store
    with _lock:
        if _store is None:
            _store = create_store(db_path)
        return _store


def get_service(db_path: str | None = None) -> GraphEngineService:
    global _service
    with _lock:
        if _service is None:
            settings = get_settings()
            _service = GraphEngineService(
                get_store(db_path),
                semantica_enabled=settings.semantica_enabled,
                retrieval_backend=settings.retrieval_backend,
            )
        return _service


def reset_runtime() -> None:
    global _store, _service
    with _lock:
        _store = None
        _service = None
