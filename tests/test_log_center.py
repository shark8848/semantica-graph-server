"""日志接入测试：JSON 格式（含 trace_id）+ IKC Log Center 配置解析/降级。"""

from __future__ import annotations

import json
import logging

from graph_engine.config import LogCenterSettings, Settings
from graph_engine.logging_setup import (
    JsonFormatter,
    configure_logging,
    set_trace_id,
)


def _record(message: str = "http.request path=/health") -> logging.LogRecord:
    return logging.LogRecord(
        name="graph_engine.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_json_formatter_includes_app_and_trace_id() -> None:
    set_trace_id("12345678901234567890123")
    try:
        payload = json.loads(JsonFormatter().format(_record()))
    finally:
        set_trace_id(None)

    assert payload["app"] == "graph-engine"
    assert payload["logger"] == "graph_engine.access"
    assert payload["trace_id"] == "12345678901234567890123"
    assert payload["level"] == "INFO"


def test_json_formatter_omits_trace_id_when_unset() -> None:
    set_trace_id(None)
    assert "trace_id" not in json.loads(JsonFormatter().format(_record()))


def test_log_center_settings_disabled_by_default(monkeypatch) -> None:
    for key in (
        "GRAPH_ENGINE_LOG_CENTER_ENABLED",
        "GRAPH_ENGINE_LOG_CENTER_URL",
        "GRAPH_ENGINE_LOG_CENTER_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = LogCenterSettings.from_env()
    assert cfg.enabled is False
    assert cfg.url is None


def test_log_center_settings_from_env(monkeypatch) -> None:
    monkeypatch.setenv("GRAPH_ENGINE_LOG_CENTER_ENABLED", "1")
    monkeypatch.setenv("GRAPH_ENGINE_LOG_CENTER_URL", "http://host.docker.internal:9315")
    cfg = Settings().log_center
    assert cfg.enabled is True
    assert cfg.url == "http://host.docker.internal:9315"


def test_configure_logging_without_sdk_degrades(monkeypatch) -> None:
    """SDK 缺失时只告警，不抛异常（日志故障不阻断业务）。"""
    monkeypatch.setenv("GRAPH_ENGINE_LOG_CENTER_ENABLED", "0")
    configure_logging(level="INFO", log_center=LogCenterSettings.from_env(), force=True)
    assert logging.getLogger().handlers, "configure_logging 应至少装配 stdout handler"
