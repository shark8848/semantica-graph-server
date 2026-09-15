from __future__ import annotations

import logging

from ._bootstrap import async_client_from_env, client_from_env
from ._version import __version__
from .async_client import AsyncGraphClient, AsyncGraphEngineClient, AsyncJobsClient
from .client import GraphClient, GraphEngineClient, JobsClient
from .envelope import Envelope
from .errors import (
    GraphEngineAPIError,
    GraphEngineBusinessError,
    GraphEngineConflictError,
    GraphEngineConnectionError,
    GraphEngineError,
    GraphEngineHTTPStatusError,
    GraphEngineNotFoundError,
    GraphEngineProtocolError,
    GraphEngineSystemError,
    GraphEngineTimeoutError,
    GraphEngineTransportError,
    GraphEngineValidationError,
    exception_from_code,
)
from .headers import CallerIdentity, identity_defaults
from .models.graph import (
    AnalyticsData,
    BuildResult,
    DeleteResult,
    DeprecateResult,
    Entity,
    EntityListData,
    ExportResult,
    GraphListData,
    GraphMeta,
    GraphPath,
    IndexStatusData,
    JobData,
    JobListData,
    NeighborData,
    PathListData,
    Relation,
    RelationListData,
    SchemaCoverage,
    SearchData,
    SearchHit,
    SemanticaMeta,
    SparqlResult,
    StatResult,
    TypeCount,
)
from .trace import generate_trace_id

__all__ = [
    "GraphEngineClient",
    "AsyncGraphEngineClient",
    "GraphClient",
    "AsyncGraphClient",
    "JobsClient",
    "AsyncJobsClient",
    "client_from_env",
    "async_client_from_env",
    "Envelope",
    "CallerIdentity",
    "identity_defaults",
    "GraphMeta",
    "GraphListData",
    "DeleteResult",
    "SemanticaMeta",
    "BuildResult",
    "DeprecateResult",
    "TypeCount",
    "SchemaCoverage",
    "StatResult",
    "Entity",
    "EntityListData",
    "Relation",
    "RelationListData",
    "NeighborData",
    "GraphPath",
    "PathListData",
    "AnalyticsData",
    "ExportResult",
    "SparqlResult",
    "SearchHit",
    "SearchData",
    "IndexStatusData",
    "JobData",
    "JobListData",
    "generate_trace_id",
    "GraphEngineError",
    "GraphEngineTransportError",
    "GraphEngineConnectionError",
    "GraphEngineTimeoutError",
    "GraphEngineProtocolError",
    "GraphEngineHTTPStatusError",
    "GraphEngineAPIError",
    "GraphEngineValidationError",
    "GraphEngineNotFoundError",
    "GraphEngineConflictError",
    "GraphEngineSystemError",
    "GraphEngineBusinessError",
    "exception_from_code",
    "__version__",
]


def set_log_level(level: int | str) -> None:
    """设置 SDK 日志级别（默认 WARNING）。"""
    logging.getLogger("semantica_graph_sdk").setLevel(level)
