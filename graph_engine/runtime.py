"""运行时单例：存储与服务全局共享（HTTP/gRPC/Celery/MCP/CLI 复用同一实例）。"""

from __future__ import annotations

import threading
from typing import Any

from .application.service import GraphEngineService
from .config import Settings
from .persistence.sqlite_store import SqliteGraphStore

_lock = threading.RLock()
_store: SqliteGraphStore | None = None
_service: GraphEngineService | None = None


def get_settings() -> Settings:
    return Settings()


def get_store(db_path: str | None = None) -> SqliteGraphStore:
    global _store
    with _lock:
        if _store is None:
            _store = SqliteGraphStore(db_path or get_settings().resolved_db_path)
        return _store


def get_service(db_path: str | None = None) -> GraphEngineService:
    global _service
    with _lock:
        if _service is None:
            settings = get_settings()
            _service = GraphEngineService(
                get_store(db_path), semantica_enabled=settings.semantica_enabled
            )
        return _service


def reset_runtime() -> None:
    global _store, _service
    with _lock:
        _store = None
        _service = None
