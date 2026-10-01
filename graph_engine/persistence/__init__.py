"""持久化层：存储实现（SQLite / Neo4j）与后端选择工厂。

缺省后端 = **Neo4j**（`GRAPH_ENGINE_STORE_BACKEND`，见 `graph_engine/config.py`）：
`create_store()` 按配置构造 `Neo4jGraphStore`；驱动缺失 / 连接失败时**告警降级 SQLite**
（`SqliteGraphStore`），引擎整体仍可用（与仓库既有的守卫式降级一致）。

显式给 `db_path` 时一律走 SQLite —— CLI `--db-path` 与既有测试/单机口径不变。
"""

from __future__ import annotations

import logging

from .neo4j_store import Neo4jGraphStore, neo4j_available
from .sqlite_store import SqliteGraphStore, create_store as create_sqlite_store

logger = logging.getLogger("graph_engine.persistence")

# 两种实现的公共类型别名（接口/语义一致，见 neo4j_store 模块注释）
GraphStore = SqliteGraphStore | Neo4jGraphStore

__all__ = ["GraphStore", "Neo4jGraphStore", "SqliteGraphStore", "create_store", "neo4j_available"]


def create_store(db_path: str | None = None) -> GraphStore:
    """构造存储：显式 `db_path` → SQLite；否则按 `GRAPH_ENGINE_STORE_BACKEND`（缺省 neo4j）。"""
    from ..config import Settings

    settings = Settings()
    backend = "" if db_path else (settings.store_backend or "").strip().lower()

    if db_path or backend in ("", "sqlite"):
        return create_sqlite_store(db_path)

    if backend != "neo4j":
        logger.error("未知存储后端 GRAPH_ENGINE_STORE_BACKEND=%s → 降级 SQLite", backend)
        return create_sqlite_store(None)

    if not neo4j_available():
        logger.warning(
            "Neo4j 驱动不可用（pip install -r requirements-neo4j.txt 或 '.[neo4j]'）→ 降级 SQLite"
        )
        return create_sqlite_store(None)
    try:
        return Neo4jGraphStore(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password,
            database=settings.neo4j_database,
        )
    except Exception as exc:  # 连接失败 / 认证失败 / 库名非法
        logger.warning("Neo4j 不可达（%s）→ 降级 SQLite：%s", settings.neo4j_uri, exc)
        return create_sqlite_store(None)
