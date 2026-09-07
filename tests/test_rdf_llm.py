"""RDF/SPARQL 视图与 LLM 建图增强测试：service 层 + 适配层门控/合并语义。"""

from __future__ import annotations

import json

import pytest

from graph_engine.application.service import GraphEngineService
from graph_engine.adapters import llm as llm_adapter
from graph_engine.errors import InvalidParamsError, NotFoundError
from graph_engine.persistence.sqlite_store import SqliteGraphStore

SCHEMA = {
    "entityTypes": [{"type": "person"}, {"type": "org"}, {"type": "concept"}],
    "relationTypes": [{"type": "works_at", "sourceTypes": ["person"], "targetTypes": ["org"]}],
}


@pytest.fixture()
def store(tmp_path):
    return SqliteGraphStore(str(tmp_path / "test.db"))


@pytest.fixture()
def svc(store):
    return GraphEngineService(store)


def _create(svc):
    return svc.create_graph(name="RDF 测试图", kb_id="kb_rdf_1", schema=SCHEMA)


def _build_working_graph(svc):
    gid = _create(svc)["graphId"]
    svc.build_from_records(
        gid,
        entities=[
            {"name": "Alice", "type": "person", "docId": "doc1", "properties": {"age": 30}},
            {"name": "Acme", "type": "org", "docId": "doc1"},
        ],
    )
    store = svc.store
    alice = next(e for e in store.list_entities(gid) if e.name == "Alice")
    acme = next(e for e in store.list_entities(gid) if e.name == "Acme")
    svc.build_from_records(
        gid,
        relations=[
            {
                "type": "works_at",
                "sourceEntityId": alice.entity_id,
                "targetEntityId": acme.entity_id,
                "docId": "doc1",
            }
        ],
    )
    return gid


# ---------- RDF 导出 ----------


def test_export_rdf_formats(svc):
    gid = _build_working_graph(svc)
    for fmt, marker in (
        ("turtle", "<urn:ge:entity:"),
        ("ttl", "<urn:ge:entity:"),
        ("nt", " <urn:ge:kg#entityType> "),
        ("nq", "<urn:ge:relation:"),
        ("rdfxml", "urn:ge:kg#Entity"),
    ):
        out = svc.export(gid, format=fmt)
        assert out["graphId"] == gid
        assert out["format"] == ("turtle" if fmt == "ttl" else fmt)
        assert out["total"] == 3
        assert marker in out["content"]
    with pytest.raises(InvalidParamsError) as exc:
        svc.export(gid, format="csv")
    assert exc.value.code == "200001"


def test_export_rdf_missing_graph(svc):
    with pytest.raises(NotFoundError):
        svc.export("graph_nope", format="turtle")


# ---------- SPARQL ----------


def test_sparql_select_ask_construct(svc):
    gid = _build_working_graph(svc)
    entities = svc.sparql(gid, query="SELECT ?e WHERE { ?e a <urn:ge:kg#Entity> }")
    assert entities["variables"] == ["e"]
    assert len(entities["bindings"]) == 2
    assert entities["bindings"][0]["e"]["type"] == "uri"

    relations = svc.sparql(
        gid,
        query="SELECT ?src ?dst WHERE { ?r <urn:ge:kg#source> ?src ; <urn:ge:kg#target> ?dst }",
    )
    assert len(relations["bindings"]) == 1
    assert relations["bindings"][0]["src"]["value"].startswith("urn:ge:entity:")

    ask = svc.sparql(
        gid,
        query="ASK { ?e <urn:ge:kg#name> \"Alice\" }",
    )
    assert ask["metadata"].get("boolean") is True

    construct = svc.sparql(gid, query="CONSTRUCT { ?s a <urn:ge:kg#Entity> } WHERE { ?s a <urn:ge:kg#Entity> }")
    assert construct["metadata"].get("result_format") == "construct"
    assert len(construct.get("triples", [])) >= 2


def test_sparql_limit_and_errors(svc):
    gid = _build_working_graph(svc)
    limited = svc.sparql(gid, query="SELECT ?e WHERE { ?e a <urn:ge:kg#Entity> }", limit=1)
    assert len(limited["bindings"]) == 1
    with pytest.raises(InvalidParamsError) as exc:
        svc.sparql(gid, query="   ")
    assert exc.value.field == "query"
    with pytest.raises(InvalidParamsError) as exc:
        svc.sparql(gid, query="SELECT nope")
    assert exc.value.field == "query"
    with pytest.raises(NotFoundError):
        svc.sparql("graph_nope", query="SELECT ?e WHERE { ?s ?p ?o }")


# ---------- LLM 建图增强 ----------


class _FakeProvider:
    def is_available(self) -> bool:
        return True


class _FakeLLMExtraction:
    """不触发网络：provider 可用，enhance_entities 把候选类型改写为 org（证明增强生效）。"""

    def __init__(self, *args, **kwargs) -> None:
        self.provider = _FakeProvider()

    def enhance_entities(self, text: str, entities: list) -> list:
        from dataclasses import replace

        return [replace(entity, label="org", confidence=0.99) for entity in entities]


def test_llm_gated_off_by_default(svc, monkeypatch):
    monkeypatch.delenv("GRAPH_ENGINE_LLM_ENHANCE", raising=False)
    monkeypatch.delenv("GRAPH_ENGINE_LLM_PROVIDER", raising=False)
    gid = _create(svc)["graphId"]
    result = svc.build_from_text(gid, text="甲说「Acme」很强。", doc_id="d1")
    assert "llm" not in result
    result_on = svc.build_from_text(gid, text="甲说「Acme」很强。", doc_id="d2", llm=True)
    assert result_on["llm"]["enabled"] is False
    assert "未配置" in result_on["llm"]["reason"]


def test_llm_enhance_merges_entities(svc, monkeypatch):
    import semantica.semantic_extract as sem_extract

    monkeypatch.setattr(sem_extract, "LLMExtraction", _FakeLLMExtraction)
    monkeypatch.setenv("GRAPH_ENGINE_LLM_PROVIDER", "openai")
    gid = _create(svc)["graphId"]
    result = svc.build_from_text(
        gid,
        text="Alice 加入「Acme」公司。",
        doc_id="doc1",
        title="Alice 入职记录",
        llm=True,
    )
    assert result["llm"]["enabled"] is True
    assert result["llm"]["located"] >= 1
    entities = svc.store.list_entities(gid)
    by_name = {e.name: e for e in entities}
    # 引号候选被 LLM 改写为 org、置信度更新；标题实体不在正文中定位，
    # 保持规则默认类型（schema 首项 person）与原始置信度
    assert by_name["Acme"].entity_type == "org"
    assert by_name["Acme"].confidence == pytest.approx(0.99)
    assert by_name["Alice 入职记录"].entity_type == "person"
    assert by_name["Alice 入职记录"].confidence == pytest.approx(1.0)


def test_llm_adapter_degrades_when_span_missing(monkeypatch):
    monkeypatch.setenv("GRAPH_ENGINE_LLM_PROVIDER", "openai")
    candidates = [{"name": "标题词", "type": "concept", "docId": "d1"}]
    enhanced, meta = llm_adapter.enhance_text_entities("正文没有任何候选词。", candidates)
    assert meta["enabled"] is True
    assert meta["located"] == 0
    assert enhanced == candidates


# ---------- 五面接口：RDF/SPARQL ----------


def test_sparql_http_export_and_query(svc):
    from fastapi.testclient import TestClient

    from graph_engine.interfaces.http_app import create_app

    client = TestClient(create_app(svc))
    resp = client.post(
        "/api/v1/graph/graphs",
        json={"name": "HTTP RDF 图", "kbId": "kb_rdf_http", "graphSchema": SCHEMA},
    )
    assert resp.json()["errCode"] == "0"
    gid = resp.json()["data"]["graphId"]
    client.post(
        f"/api/v1/graph/graphs/{gid}/build",
        json={"entities": [{"name": "Alice", "type": "person"}, {"name": "Acme", "type": "org"}], "docId": "d1"},
    )
    assert client.get(f"/api/v1/graph/graphs/{gid}/export?format=turtle").json()["data"]["format"] == "turtle"
    out = client.post(f"/api/v1/graph/graphs/{gid}/sparql", json={"query": "SELECT ?e WHERE { ?e a <urn:ge:kg#Entity> }"})
    assert out.json()["errCode"] == "0"
    assert len(out.json()["data"]["bindings"]) == 2
    missing = client.post("/api/v1/graph/graphs/nope/sparql", json={"query": "SELECT ?e WHERE { ?s ?p ?o }"})
    assert missing.json()["errCode"] == "200404"


def test_sparql_grpc(svc):
    from graph_engine.interfaces.grpc_server import build_grpc_server, grpc_client

    gid = _build_working_graph(svc)
    server = build_grpc_server(svc)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        client = grpc_client("127.0.0.1", port)
        resp = client("Sparql", {"graphId": gid, "query": "SELECT ?r WHERE { ?r a <urn:ge:kg#Relation> }"})
        assert resp["errCode"] == "0"
        assert len(resp["data"]["bindings"]) == 1
    finally:
        server.stop(0)


def test_sparql_mcp(svc):
    from graph_engine.interfaces.mcp_server import _handle_request, _tool_handlers

    gid = _build_working_graph(svc)
    handlers = _tool_handlers(svc)
    resp = _handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "graph_sparql",
                "arguments": {"graphId": gid, "query": "SELECT ?e WHERE { ?e a <urn:ge:kg#Entity> }"},
            },
        },
        handlers,
    )
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert len(payload["bindings"]) == 2
    assert payload["query"].startswith("SELECT")


def test_sparql_cli(svc):
    from typer.testing import CliRunner

    from graph_engine.interfaces import cli

    cli._service = svc
    gid = _build_working_graph(svc)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["sparql", gid, "--query", "SELECT ?s WHERE { ?s a <urn:ge:kg#Relation> }"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["graphId"] == gid
    assert len(payload["bindings"]) == 1
