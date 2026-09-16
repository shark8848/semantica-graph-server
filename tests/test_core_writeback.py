"""core 回写适配层用例：开关、契约字段、HTTP 形状（路径/鉴权头/载荷）、错误降级。

用本机 `http.server` 起桩（零新依赖），断言**实际发出的请求**与 core `§2.5` 契约一致——
这正是 G-05（`format=ttl` 跨仓漂移）那类问题的拦阻点。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from graph_engine.adapters import core_writeback


class _Stub:
    def __init__(self, *, err_code: str = "000000") -> None:
        self.requests: list[dict] = []
        self.err_code = err_code


@pytest.fixture()
def stub(monkeypatch) -> _Stub:
    state = _Stub()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server 协议命名
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8")
            state.requests.append(
                {
                    "path": self.path,
                    "token": self.headers.get("X-Internal-Token"),
                    "json": json.loads(raw),
                }
            )
            body = json.dumps(
                {
                    "errCode": state.err_code,
                    "errMsg": "" if state.err_code == "000000" else "回写被拒",
                    "data": {"added": 1, "nodeCount": 1} if state.err_code == "000000" else {},
                    "traceId": "17570000000000000000001",
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # 静音
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("IKC_CORE_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("IKC_CORE_ADMIN_TOKEN", "internal-admin-token")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


_ENTITY = {
    "entityId": "ent_1",
    "graphId": "graph_1",
    "docId": "doc_1",
    "type": "concept",
    "name": "知识图谱",
    "normalizedName": "知识图谱",
    "properties": {"lang": "zh"},
    "aliases": ["KG"],
    "evidence": [{"docId": "doc_1"}],
    "confidence": 0.8,
    "status": "active",
    "createdAt": "2026-09-16T00:00:00Z",
    "updatedAt": "2026-09-16T00:00:00Z",
}
_RELATION = {
    "relationId": "rel_1",
    "graphId": "graph_1",
    "docId": "doc_1",
    "type": "related_to",
    "sourceEntityId": "ent_1",
    "targetEntityId": "ent_2",
    "properties": {},
    "evidence": [{"docId": "doc_1"}],
    "confidence": 0.5,
    "status": "active",
    "createdAt": "2026-09-16T00:00:00Z",
    "updatedAt": "2026-09-16T00:00:00Z",
}


def test_disabled_without_base_url(monkeypatch) -> None:
    monkeypatch.delenv("IKC_CORE_BASE_URL", raising=False)
    assert core_writeback.writeback_enabled() is False
    assert core_writeback.write_assets("kb_1", doc_id="doc_1", entities=[_ENTITY]) is None


def test_disabled_by_switch(monkeypatch, stub) -> None:
    monkeypatch.setenv("IKC_CORE_WRITEBACK", "0")
    assert core_writeback.writeback_enabled() is False
    assert core_writeback.write_assets("kb_1", doc_id="doc_1", entities=[_ENTITY]) is None
    assert stub.requests == []


def test_write_assets_contract_and_payload(stub) -> None:
    result = core_writeback.write_assets(
        "kb_1",
        doc_id="doc_1",
        entities=[_ENTITY],
        relations=[_RELATION],
        task_id="task-1",
        config={"mode": "records"},
    )
    assert result is not None and result["ok"] is True and result["added"] == 1
    sent = stub.requests[0]
    assert sent["path"] == "/internal/graph/build"
    assert sent["token"] == "internal-admin-token"
    body = sent["json"]
    assert set(body) == {
        "kbId",
        "docId",
        "entities",
        "relations",
        "engine",
        "engineVersion",
        "taskId",
        "config",
    }
    assert body["engine"] == "semantica-graph-server" and body["engineVersion"]
    entity = body["entities"][0]
    assert set(entity) <= set(core_writeback._ENTITY_FIELDS)
    assert {"type", "name", "evidence"} <= set(entity)
    # 本地字段不外投（entityId/normalizedName/status/时间戳由 core 侧决定）
    assert "entityId" not in entity and "normalizedName" not in entity
    relation = body["relations"][0]
    assert set(relation) <= set(core_writeback._RELATION_FIELDS)
    assert relation["sourceEntityId"] == "ent_1" and relation["targetEntityId"] == "ent_2"


def test_empty_records_with_doc_id_still_posts(stub) -> None:
    """空记录 + docId 合法：core 侧按同口径做 doc 级废弃。"""
    result = core_writeback.write_assets("kb_1", doc_id="doc_1", entities=[], relations=[])
    assert result is not None and result["ok"] is True
    body = stub.requests[0]["json"]
    assert body["entities"] == [] and body["relations"] == [] and body["docId"] == "doc_1"


def test_no_kb_or_no_doc_no_request(stub) -> None:
    assert core_writeback.write_assets("", doc_id="doc_1", entities=[_ENTITY]) is None
    assert core_writeback.write_assets("kb_1", entities=[], relations=[]) is None
    assert stub.requests == []


def test_error_code_degrades_without_raising(stub) -> None:
    stub.err_code = "100001"
    result = core_writeback.write_assets("kb_1", doc_id="doc_1", entities=[_ENTITY])
    assert result is not None and result["ok"] is False
    assert "100001" in result["error"]


def test_unreachable_core_degrades(monkeypatch) -> None:
    monkeypatch.setenv("IKC_CORE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("IKC_CORE_WRITEBACK_TIMEOUT", "1")
    result = core_writeback.write_assets("kb_1", doc_id="doc_1", entities=[_ENTITY])
    assert result is not None and result["ok"] is False
    assert result["endpoint"] == "graph/build"


def test_engine_version_matches_pyproject() -> None:
    """版本漂移护栏：回写审计里的 engineVersion 必须等于本仓 pyproject 声明。"""
    import tomllib
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert core_writeback.ENGINE_VERSION == declared["project"]["version"]
