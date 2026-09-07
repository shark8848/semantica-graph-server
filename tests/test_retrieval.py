"""语义检索骨架单测：降级确定性 / fake backend 命中 / NotFound / MCP 工具注册。

不依赖网络、真实模型与真实 semantica 检索链；检索链不可用场景以注入方式模拟
（真实导入 semantica.vector_store 会先拉起重依赖且在此 venv 中必然失败）。
"""

from __future__ import annotations

import json

import pytest

from graph_engine.adapters.retrieval import RetrievalAdapter, record_to_doc
from graph_engine.application.service import GraphEngineService
from graph_engine.domain.models import EntityRecord, RelationRecord
from graph_engine.errors import NotFoundError
from graph_engine.persistence.sqlite_store import SqliteGraphStore

SCHEMA = {
    "entityTypes": [{"type": "person"}, {"type": "org"}, {"type": "concept"}],
    "relationTypes": [{"type": "works_at", "sourceTypes": ["person"], "targetTypes": ["org"]}],
}


@pytest.fixture()
def store(tmp_path):
    return SqliteGraphStore(str(tmp_path / "retrieval.db"))


@pytest.fixture()
def svc(store):
    return GraphEngineService(store)


def _create(svc):
    return svc.create_graph(name="检索测试图", kb_id="kb_retrieval", schema=SCHEMA)["graphId"]


class _FakeBackend:
    """确定性 fake 检索后端：query 返回固定原始命中（稳定 ID + metadata 契约字段）。"""

    def __init__(self) -> None:
        self.upserted: list[tuple[str, list[dict]]] = []
        self.deleted: list[tuple[str, list[str]]] = []

    def upsert(self, graph_id_value: str, records: list[dict]) -> int:
        self.upserted.append((graph_id_value, records))
        return len(records)

    def query(self, graph_id_value: str, text: str, top_k: int) -> list[dict]:
        return [
            {
                "id": "ent_retrieval_alice",
                "score": 0.93,
                "metadata": {
                    "id": "ent_retrieval_alice",
                    "kind": "entity",
                    "type": "person",
                    "docId": "doc1",
                    "name": "Alice",
                    "confidence": 0.9,
                    "evidence": [{"docId": "doc1"}],
                },
            }
        ]

    def delete(self, graph_id_value: str, record_ids: list[str]) -> int:
        self.deleted.append((graph_id_value, record_ids))
        return len(record_ids)


# ---------- 检索链不可用（桩包场景）→ 确定降级 ----------


def test_semantic_search_degraded_when_chain_unavailable(svc, monkeypatch):
    """检索链不可用时 degrade 不抛错：semantica:false + hits:[]。"""
    from graph_engine.adapters import retrieval as retrieval_module

    monkeypatch.setattr(retrieval_module, "retrieval_available", lambda: False)
    gid = _create(svc)
    result = svc.semantic_search(gid, query="Alice 相关的记录", top_k=5)
    assert result["graphId"] == gid
    assert result["query"] == "Alice 相关的记录"
    assert result["topK"] == 5
    assert result["semantica"] is False
    assert result["total"] == 0
    assert result["hits"] == []
    assert result["reason"]

    status = svc.index_status(gid)
    assert status["graphId"] == gid
    assert status["semantica"] is False
    assert status["reason"]


def test_retrieval_available_guards_bare_exception(monkeypatch):
    """桩包抛裸 Exception（非 ImportError/OSError）同样判为检索链不可用，不崩溃。"""
    import builtins

    from graph_engine.adapters import retrieval as retrieval_module

    def raiser(name, *args, **kwargs):
        raise Exception("The official Pinecone python package has been renamed...")

    monkeypatch.setattr(retrieval_module, "_chain_available", None)
    monkeypatch.setattr(builtins, "__import__", raiser)
    assert retrieval_module.retrieval_available() is False


# ---------- 注入 fake backend → 命中返回 ----------


def test_semantic_search_with_fake_backend_returns_stable_hits(tmp_path):
    backend = _FakeBackend()
    store = SqliteGraphStore(str(tmp_path / "retrieval-fake.db"))
    svc = GraphEngineService(store, retrieval=RetrievalAdapter(backend=backend))
    gid = _create(svc)

    result = svc.semantic_search(gid, query="谁是 Alice 的雇主？", top_k=5)
    assert result["semantica"] is True
    assert result["total"] == 1
    hit = result["hits"][0]
    assert hit["id"] == "ent_retrieval_alice"  # 稳定 ID 原样透出
    assert hit["kind"] == "entity"
    assert hit["type"] == "person"
    assert hit["docId"] == "doc1"
    assert hit["name"] == "Alice"
    assert 0.0 <= hit["score"] <= 1.0
    assert hit["confidence"] == 0.9
    assert hit["evidence"] == [{"docId": "doc1"}]

    # 后端 upsert/delete 契约可用（废弃/迁移语义由调用方控制，本骨架不自动触发）
    assert backend.upsert(gid, [{"id": "x"}]) == 1
    assert backend.delete(gid, ["x"]) == 1


def test_record_to_doc_metadata_contract():
    """待索引条目的 id 锚定稳定 ID，metadata 携带 type/docId/confidence/evidence。"""
    entity = EntityRecord(
        entity_id="ent_alice_1",
        graph_id="graph_1",
        entity_type="person",
        name="Alice",
        normalized_name="alice",
        doc_id="doc1",
        confidence=0.9,
        evidence=[{"docId": "doc1"}],
    )
    doc = record_to_doc(entity)
    assert doc["id"] == "ent_alice_1"
    assert doc["kind"] == "entity"
    assert doc["metadata"]["id"] == "ent_alice_1"
    assert doc["metadata"]["type"] == "person"
    assert doc["metadata"]["docId"] == "doc1"
    assert doc["metadata"]["confidence"] == 0.9
    assert doc["metadata"]["evidence"] == [{"docId": "doc1"}]

    relation = RelationRecord(
        relation_id="rel_works_1",
        graph_id="graph_1",
        relation_type="works_at",
        source_entity_id="ent_alice_1",
        target_entity_id="ent_acme_1",
        doc_id="doc2",
        confidence=0.8,
        evidence=[{"docId": "doc2"}],
    )
    doc = record_to_doc(relation)
    assert doc["id"] == "rel_works_1"
    assert doc["kind"] == "relation"
    assert doc["metadata"]["type"] == "works_at"
    assert doc["metadata"]["docId"] == "doc2"


# ---------- 图谱不存在 → NotFoundError ----------


def test_semantic_search_unknown_graph_raises_not_found(svc):
    with pytest.raises(NotFoundError) as exc:
        svc.semantic_search("graph_nope", query="查询")
    assert exc.value.code == "200404"
    assert exc.value.field == "graphId"

    with pytest.raises(NotFoundError):
        svc.index_status("graph_nope")


# ---------- MCP：工具注册与 degrade 响应 ----------


def test_mcp_retrieval_tools_and_degrade(monkeypatch, tmp_path):
    from graph_engine.adapters import retrieval as retrieval_module
    from graph_engine.interfaces.mcp_server import TOOLS, _handle_request, _tool_handlers

    monkeypatch.setattr(retrieval_module, "retrieval_available", lambda: False)
    store = SqliteGraphStore(str(tmp_path / "retrieval-mcp.db"))
    svc = GraphEngineService(store)
    gid = _create(svc)

    names = {t["name"] for t in TOOLS}
    assert len(TOOLS) == 18
    assert "graph_search" in names
    assert "graph_index_status" in names
    assert "graph_sparql" in names

    handlers = _tool_handlers(svc)
    listed = _handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, handlers)
    listed_names = {t["name"] for t in listed["result"]["tools"]}
    assert "graph_search" in listed_names and "graph_index_status" in listed_names

    resp = _handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "graph_search", "arguments": {"graphId": gid, "query": "Alice 相关"}}},
        handlers,
    )
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["semantica"] is False
    assert payload["hits"] == []
    assert payload["graphId"] == gid

    resp = _handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "graph_index_status", "arguments": {"graphId": gid}}},
        handlers,
    )
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["semantica"] is False

    # 图谱不存在 → GraphEngineError 按 jsonrpc 错误映射
    resp = _handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "graph_search", "arguments": {"graphId": "graph_nope", "query": "查询"}}},
        handlers,
    )
    assert resp["error"]["code"] == -32000
    assert resp["error"]["data"]["code"] == "200404"
