"""存储后端选择与 Neo4j 实现回归。

工厂用例（无需图库）：显式 db_path / sqlite / 未知后端 / 驱动缺失 / 图库不可达 → 一律 SQLite 兜底。
Neo4j 用例需一个**可达**的图库，取 `GRAPH_ENGINE_NEO4J_*`（缺省 `bolt://127.0.0.1:7687`，口令为空
即认证失败 → 自动 skip）。本地跑法（栈里的 ikc-neo4j）：

    GRAPH_ENGINE_NEO4J_URI=bolt://127.0.0.1:7687 \
    GRAPH_ENGINE_NEO4J_PASSWORD=<data/stack/neo4j.env 里的口令> \
        .venv/bin/python -m pytest tests/test_store_backend.py -q
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from graph_engine import persistence
from graph_engine.application.service import GraphEngineService
from graph_engine.domain.ids import entity_id
from graph_engine.persistence import SqliteGraphStore, create_store, neo4j_available
from graph_engine.persistence.neo4j_store import Neo4jGraphStore

SCHEMA = {
    "entityTypes": [{"type": "person"}, {"type": "org"}],
    "relationTypes": [{"type": "works_at", "sourceTypes": ["person"], "targetTypes": ["org"]}],
}


# ---------- 工厂：后端选择与降级（无需图库） ----------


def test_explicit_db_path_always_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_ENGINE_STORE_BACKEND", "neo4j")
    store = create_store(str(tmp_path / "explicit.db"))
    assert isinstance(store, SqliteGraphStore)


def test_backend_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_ENGINE_STORE_BACKEND", "sqlite")
    monkeypatch.setenv("GRAPH_ENGINE_DATA_DIR", str(tmp_path))
    assert isinstance(create_store(), SqliteGraphStore)


def test_unknown_backend_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_ENGINE_STORE_BACKEND", "falkordb")
    monkeypatch.setenv("GRAPH_ENGINE_DATA_DIR", str(tmp_path))
    assert isinstance(create_store(), SqliteGraphStore)


def test_driver_missing_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_ENGINE_STORE_BACKEND", "neo4j")
    monkeypatch.setenv("GRAPH_ENGINE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(persistence, "neo4j_available", lambda: False)
    assert isinstance(create_store(), SqliteGraphStore)


def test_unreachable_neo4j_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPH_ENGINE_STORE_BACKEND", "neo4j")
    monkeypatch.setenv("GRAPH_ENGINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GRAPH_ENGINE_NEO4J_URI", "bolt://127.0.0.1:9")  # 保留端口，必拒
    assert isinstance(create_store(), SqliteGraphStore)


# ---------- Neo4j：真实图库回归 ----------


@pytest.fixture()
def neo4j():
    if not neo4j_available():
        pytest.skip("neo4j 驱动未安装")
    params = {
        "uri": os.environ.get("GRAPH_ENGINE_NEO4J_URI", "bolt://127.0.0.1:7687"),
        "user": os.environ.get("GRAPH_ENGINE_NEO4J_USER", "neo4j"),
        "password": os.environ.get("GRAPH_ENGINE_NEO4J_PASSWORD", ""),
        "database": os.environ.get("GRAPH_ENGINE_NEO4J_DATABASE", "neo4j"),
    }
    try:
        store = Neo4jGraphStore(**params, connection_timeout=3.0)
    except Exception as exc:  # 未起图库 / 口令不对 → skip（不误报失败）
        pytest.skip(f"Neo4j 不可达（{params['uri']}）：{exc}")
    created: list[str] = []
    yield store, created
    for graph_id in created:
        try:
            store.delete_graph(graph_id)
        except Exception:
            pass
    store.close()


def _new_graph(store: Neo4jGraphStore, created: list[str], svc: GraphEngineService) -> str:
    gid = f"graph_test_{uuid.uuid4().hex[:12]}"
    svc.create_graph(graph_id_value=gid, name="neo4j 测试图", kb_id="kb_test", schema=SCHEMA)
    created.append(gid)
    return gid


def test_neo4j_graph_metadata(neo4j):
    store, created = neo4j
    svc = GraphEngineService(store)
    gid = _new_graph(store, created, svc)
    assert svc.get_graph(gid)["graphId"] == gid
    assert gid in {item["graphId"] for item in svc.list_graphs()["items"]}
    svc.delete_graph(gid)
    with pytest.raises(Exception):
        svc.get_graph(gid)


def test_neo4j_build_merge_queries(neo4j):
    store, created = neo4j
    svc = GraphEngineService(store)
    gid = _new_graph(store, created, svc)
    alice = entity_id(gid, "person", "alice")
    acme = entity_id(gid, "org", "acme")
    result = svc.build_from_records(
        gid,
        entities=[
            {"name": "Alice", "type": "person", "docId": "doc1", "properties": {"age": 30}},
            {"name": "Acme", "type": "org", "docId": "doc1"},
        ],
        relations=[{"type": "works_at", "source": alice, "target": acme, "docId": "doc1"}],
        doc_id="doc1",
    )
    assert (result["entityCount"], result["relationCount"]) == (2, 1)
    stat = svc.stat(gid)
    assert stat["nodeCount"] == 2 and stat["edgeCount"] == 1

    # 合并语义：别名并集 / 证据追加 / 置信度取大（与 SQLite 同口径）
    svc.build_from_records(
        gid,
        entities=[{"name": "Alice", "type": "person", "docId": "doc2", "aliases": ["A. Lee"]}],
        doc_id="doc2",
    )
    node = svc.list_nodes(gid, name="Alice")["items"][0]
    assert "A. Lee" in node["aliases"] and len(node["evidence"]) == 2

    nb = svc.neighbors(gid, entity_id_value=alice, depth=1)
    assert {item["entityId"] for item in nb["nodes"]} == {alice, acme}
    assert len(nb["edges"]) == 1
    paths = svc.paths(gid, source_entity_id=alice, target_entity_id=acme)
    assert paths["total"] == 1 and paths["items"][0]["length"] == 1

    lines = [json.loads(line) for line in svc.export(gid, format="jsonl")["content"].splitlines()]
    assert len(lines) == 3  # 2 实体 + 1 关系

    # 增量废弃：doc3 专属实体，废弃后不出现在活跃列表
    svc.build_from_records(
        gid, entities=[{"name": "Carol", "type": "person", "docId": "doc3"}], doc_id="doc3"
    )
    assert svc.deprecate_doc(gid, doc_id="doc3")["deprecated"] == 1
    assert svc.list_nodes(gid, name="Carol")["total"] == 0


def test_neo4j_jobs(neo4j):
    store, _created = neo4j
    job_id = f"job_test_{uuid.uuid4().hex[:12]}"
    gid = f"graph_test_{uuid.uuid4().hex[:12]}"
    store.create_job(job_id, gid, "build", {"entities": []})
    assert store.get_job(job_id)["status"] == "pending"
    store.update_job(job_id, status="success", result={"entityCount": 0})
    stored = store.get_job(job_id)
    assert stored["status"] == "success" and stored["result"] == {"entityCount": 0}
    assert job_id in {item["jobId"] for item in store.list_jobs(graph_id=gid)}
