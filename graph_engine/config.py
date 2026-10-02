from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool) -> bool:
    """读取布尔环境变量：1/true/yes（不区分大小写）视为 True，其余为 False。"""
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes")


def _env_float(key: str, default: str) -> float:
    """读取浮点环境变量：非法值回退默认。"""
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(key: str, default: str) -> int:
    """读取整数环境变量：非法值回退默认。"""
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return int(default)


@dataclass(frozen=True)
class LogCenterSettings:
    """IKC Log Center 远程日志投递配置（HTTP POST {url}/ingest）。"""

    enabled: bool = False
    url: str | None = None
    token: str | None = None
    timeout_seconds: float = 2.0
    queue_size: int = 1000
    batch_size: int = 50
    module_name: str = "graph-engine"

    @classmethod
    def from_env(cls) -> "LogCenterSettings":
        return cls(
            enabled=_env_bool("GRAPH_ENGINE_LOG_CENTER_ENABLED", False),
            url=_env("GRAPH_ENGINE_LOG_CENTER_URL", "") or None,
            token=_env("GRAPH_ENGINE_LOG_CENTER_TOKEN", "") or None,
            timeout_seconds=_env_float("GRAPH_ENGINE_LOG_CENTER_TIMEOUT", "2"),
            queue_size=_env_int("GRAPH_ENGINE_LOG_CENTER_QUEUE_SIZE", "1000"),
            batch_size=_env_int("GRAPH_ENGINE_LOG_CENTER_BATCH_SIZE", "50"),
        )


@dataclass(frozen=True)
class Settings:
    """引擎配置：环境变量优先，缺省使用内置默认值。"""

    data_dir: str = field(default_factory=lambda: _env("GRAPH_ENGINE_DATA_DIR", "data"))
    db_path: str = field(default_factory=lambda: _env("GRAPH_ENGINE_DB_PATH", ""))
    http_host: str = field(default_factory=lambda: _env("GRAPH_ENGINE_HTTP_HOST", "0.0.0.0"))
    http_port: int = field(default_factory=lambda: int(_env("GRAPH_ENGINE_HTTP_PORT", "18010")))
    grpc_host: str = field(default_factory=lambda: _env("GRAPH_ENGINE_GRPC_HOST", "0.0.0.0"))
    grpc_port: int = field(default_factory=lambda: int(_env("GRAPH_ENGINE_GRPC_PORT", "50051")))
    celery_broker: str = field(default_factory=lambda: _env("GRAPH_ENGINE_CELERY_BROKER", "redis://localhost:6379/0"))
    celery_backend: str = field(default_factory=lambda: _env("GRAPH_ENGINE_CELERY_BACKEND", "redis://localhost:6379/0"))
    celery_enabled: bool = field(default_factory=lambda: _env_bool("GRAPH_ENGINE_CELERY_ENABLED", False))
    mcp_transport: str = field(default_factory=lambda: _env("GRAPH_ENGINE_MCP_TRANSPORT", "stdio"))
    log_level: str = field(default_factory=lambda: _env("GRAPH_ENGINE_LOG_LEVEL", "INFO"))
    log_center: LogCenterSettings = field(default_factory=LogCenterSettings.from_env)
    # SPARQL/RDF 视图护栏：单次查询超时（秒，0 关闭）与单次查询返回行数上限
    sparql_timeout: float = field(default_factory=lambda: _env_float("GRAPH_ENGINE_SPARQL_TIMEOUT", "10"))
    sparql_max_rows: int = field(default_factory=lambda: _env_int("GRAPH_ENGINE_SPARQL_MAX_ROWS", "5000"))
    semantica_enabled: bool = field(
        default_factory=lambda: os.environ.get("GRAPH_ENGINE_SEMANTICA", "1") != "0"
    )
    # 图存储后端：缺省 neo4j（外部图库 Bolt）；不可用时由持久化工厂告警降级 SQLite
    store_backend: str = field(default_factory=lambda: _env("GRAPH_ENGINE_STORE_BACKEND", "neo4j"))
    neo4j_uri: str = field(
        default_factory=lambda: _env("GRAPH_ENGINE_NEO4J_URI", "bolt://localhost:7687")
    )
    neo4j_user: str = field(default_factory=lambda: _env("GRAPH_ENGINE_NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("GRAPH_ENGINE_NEO4J_PASSWORD", ""))
    neo4j_database: str = field(
        default_factory=lambda: _env("GRAPH_ENGINE_NEO4J_DATABASE", "neo4j")
    )

    @property
    def resolved_db_path(self) -> str:
        if self.db_path:
            return self.db_path
        data_dir = Path(self.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        return str(data_dir / "engine.db")
