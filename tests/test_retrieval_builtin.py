"""内置词法检索后端（`builtin-lexical`）单测：缺省可用 / 打分与排序 / 写后立即生效 / 三面可达。

背景：`/search` 此前恒为 `semantica:false` 降级（向量链被 pinecone 桩卡死）。本文件钉住
「引擎自己就能给的图检索」——不依赖网络、模型与外部索引。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from graph_engine.adapters.retrieval_builtin import (
    BACKEND_NAME,
    BuiltinLexicalBackend,
    normalize_query,
)
from graph_engine.application.service import GraphEngineService
from graph_engine.persistence.sqlite_store import SqliteGraphStore

SCHEMA = {
    "entityTypes": [{"type": "org"}, {"type": "person"}, {"type": "concept"}],
    "relationTypes": [{"type": "operates", "sourceTypes": ["org"], "targetTypes": ["concept"]}],
}

ENTITIES = [
    {
        "entityId": "ent_telecom",
        "type": "org",
        "name": "中国电信",
        "normalizedName": "中国电信",
        "aliases": ["China Telecom", "电信集团"],
        "docId": "doc_a",
        "confidence": 0.9,
        "evidence": [{"docId": "doc_a", "snippet": "中国电信是基础电信运营商"}],
    },
    {
        "entityId": "ent_cloud",
        "type": "concept",
        "name": "天翼云",
        "normalizedName": "天翼云",
        "aliases": ["CTyun"],
        "docId": "doc_a",
        "confidence": 0.8,
        "evidence": [{"docId": "doc_a", "snippet": "中国电信旗下的云计算品牌"}],
    },
    {
        "entityId": "ent_unrelated",
        "type": "person",
        "name": "张三",
        "normalizedName": "张三",
        "docId": "doc_b",
        "confidence": 0.7,
        "evidence": [{"docId": "doc_b", "snippet": "无关记录"}],
    },
]

RELATIONS = [
    {
        "relationId": "rel_operates",
        "type": "operates",
        "sourceEntityId": "ent_telecom",
        "targetEntityId": "ent_cloud",
        "docId": "doc_a",
        "confidence": 0.8,
        "evidence": [{"docId": "doc_a", "snippet": "中国电信运营天翼云"}],
    }
]


@pytest.fixture()
def svc(tmp_path):
    return GraphEngineService(SqliteGraphStore(str(tmp_path / "builtin.db")))


def _built(svc, kb_id="kb_builtin"):
    gid = svc.create_graph(name="内置检索图", kb_id=kb_id, schema=SCHEMA)["graphId"]
    svc.build_from_records(gid, entities=ENTITIES, relations=RELATIONS, doc_id="doc_a")
    return gid


# ---------- 缺省即真实可用（不是降级） ----------


def test_default_service_searches_without_any_backend_injection(svc):
    gid = _built(svc)
    result = svc.semantic_search(gid, query="中国电信", top_k=10)
    assert result["semantica"] is True
    assert result["backend"] == BACKEND_NAME
    assert result["mode"] == "lexical"
    assert result["total"] >= 1
    names = [hit["name"] for hit in result["hits"]]
    assert "中国电信" in names
    top = result["hits"][0]
    assert top["name"] == "中国电信"  # 名称精确命中排第一
    assert top["kind"] == "entity"
    assert top["type"] == "org"
    assert top["docId"] == "doc_a"
    assert 0.0 < top["score"] <= 1.0
    assert top["evidence"][0]["docId"] == "doc_a"


def test_search_matches_alias_type_and_relation_endpoints(svc):
    gid = _built(svc)
    by_alias = svc.semantic_search(gid, query="China Telecom", top_k=5)
    assert [hit["name"] for hit in by_alias["hits"]][:1] == ["中国电信"]

    by_type = svc.semantic_search(gid, query="org", top_k=5)
    assert "中国电信" in [hit["name"] for hit in by_type["hits"]]

    by_endpoint = svc.semantic_search(gid, query="天翼云", top_k=5)
    kinds = {hit["kind"] for hit in by_endpoint["hits"]}
    assert "entity" in kinds and "relation" in kinds  # 端点命中 → 关系也被召回


def test_score_is_monotonic_and_deterministic(svc):
    gid = _built(svc)
    hits = svc.semantic_search(gid, query="中国电信", top_k=10)["hits"]
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)
    again = svc.semantic_search(gid, query="中国电信", top_k=10)["hits"]
    assert [hit["id"] for hit in again] == [hit["id"] for hit in hits]


def test_empty_query_and_unrelated_query_return_no_hits(svc):
    gid = _built(svc)
    assert svc.semantic_search(gid, query="   ", top_k=5)["hits"] == []
    assert svc.semantic_search(gid, query="完全不相关的词xyz", top_k=5)["hits"] == []


# ---------- 写后立即生效（现算打分，没有索引维护窗口） ----------


def test_index_follows_writes_without_maintenance(svc):
    gid = _built(svc)
    assert svc.semantic_search(gid, query="新公司", top_k=5)["hits"] == []
    svc.build_from_records(
        gid,
        entities=[{"entityId": "ent_new", "type": "org", "name": "新公司", "docId": "doc_c"}],
        relations=[],
        doc_id="doc_c",
    )
    assert [hit["name"] for hit in svc.semantic_search(gid, query="新公司", top_k=5)["hits"]] == ["新公司"]

    # 废弃该文档后不再召回（列表默认只取 active）
    svc.deprecate_doc(gid, doc_id="doc_c")
    assert svc.semantic_search(gid, query="新公司", top_k=5)["hits"] == []


def test_index_status_reports_builtin_backend(svc):
    gid = _built(svc)
    status = svc.index_status(gid)
    assert status["graphId"] == gid
    assert status["semantica"] is True
    assert status["available"] is True
    assert status["backend"] == BACKEND_NAME
    assert status["mode"] == "lexical"


def test_retrieval_backend_none_still_degrades(tmp_path, monkeypatch):
    """`retrieval_backend="none"` 保留旧口径（供只跑结构面 / 压测环境）。"""
    from graph_engine.adapters import retrieval as retrieval_module

    monkeypatch.setattr(retrieval_module, "retrieval_available", lambda: False)
    svc = GraphEngineService(
        SqliteGraphStore(str(tmp_path / "off.db")), retrieval_backend="none"
    )
    gid = _built(svc)
    assert svc.semantic_search(gid, query="中国电信", top_k=5)["hits"] == []


# ---------- 归一化与 HTTP / MCP 面 ----------


def test_normalize_query_strips_noise_and_case():
    assert normalize_query("  China  Telecom ") == "chinatelecom"
    assert normalize_query("中国电信（集团）") == "中国电信集团"
    assert normalize_query(None) == ""


def test_http_search_and_index_status(tmp_path):
    svc = GraphEngineService(SqliteGraphStore(str(tmp_path / "http.db")))
    gid = _built(svc)
    from graph_engine.interfaces.http_app import create_app

    http = TestClient(create_app(svc))
    resp = http.get(f"/api/v1/graph/graphs/{gid}/search", params={"query": "中国电信", "topK": 5})
    body = resp.json()
    assert body["errCode"] == "0"  # 引擎信封：OK = "0"（平台北向才归一成 "000000"）
    assert body["data"]["backend"] == BACKEND_NAME
    assert body["data"]["hits"][0]["name"] == "中国电信"

    status = http.get(f"/api/v1/graph/graphs/{gid}/index-status").json()["data"]
    assert status["available"] is True and status["backend"] == BACKEND_NAME


def test_mcp_graph_search_returns_builtin_hits(tmp_path):
    from graph_engine.interfaces.mcp_server import _handle_request, _tool_handlers

    svc = GraphEngineService(SqliteGraphStore(str(tmp_path / "mcp.db")))
    gid = _built(svc)
    handlers = _tool_handlers(svc)
    resp = _handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "graph_search", "arguments": {"graphId": gid, "query": "天翼云"}},
        },
        handlers,
    )
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["backend"] == BACKEND_NAME
    assert payload["total"] >= 1
    assert payload["hits"][0]["name"] in {"天翼云", "中国电信"}
