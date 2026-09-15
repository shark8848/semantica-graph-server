from __future__ import annotations

from semantica_graph_sdk.models.graph import (
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
    SearchData,
    SearchHit,
    SparqlResult,
    StatResult,
)


def test_graph_meta_round_trip_and_extra():
    meta = GraphMeta.from_dict(
        {
            "graphId": "graph_1",
            "name": "演示图",
            "kbId": "kb_1",
            "tenantId": "t1",
            "ownerId": "u1",
            "graphSchema": {"entityTypes": [{"type": "person"}]},
            "status": "active",
            "createdAt": "2026-09-15T00:00:00Z",
            "updatedAt": "2026-09-15T00:00:01Z",
            "unknownField": 7,
        }
    )
    assert meta.graphId == "graph_1"
    assert meta.graphSchema["entityTypes"][0]["type"] == "person"
    assert meta.extra == {"unknownField": 7}
    assert meta.to_dict()["unknownField"] == 7
    assert GraphMeta.from_dict({}).graphId == ""


def test_graph_list_delete_and_deprecate():
    data = GraphListData.from_dict({"total": 1, "items": [{"graphId": "graph_1"}]})
    assert data.total == 1 and data.items[0].graphId == "graph_1"
    assert data.to_dict()["items"][0]["graphId"] == "graph_1"

    deleted = DeleteResult.from_dict({"graphId": "graph_1", "deleted": True})
    assert deleted.deleted is True

    deprecated = DeprecateResult.from_dict({"graphId": "graph_1", "docId": "doc_1", "deprecated": 3})
    assert deprecated.deprecated == 3


def test_build_result_with_semantica_llm_and_deprecated():
    result = BuildResult.from_dict(
        {
            "graphId": "graph_1",
            "entityCount": 2,
            "relationCount": 1,
            "semantica": {
                "semantica": True,
                "entities": 2,
                "relationships": 1,
                "metadata": {"version": "0.6.5"},
                "provider": "noop",
            },
            "llm": {"enabled": True, "candidates": 3},
            "deprecated": 2,
        }
    )
    assert (result.entityCount, result.relationCount) == (2, 1)
    assert result.semantica.semantica is True
    assert result.semantica.entities == 2
    assert result.semantica.metadata["version"] == "0.6.5"
    assert result.semantica.extra == {"provider": "noop"}
    assert result.llm["candidates"] == 3
    assert result.deprecated == 2
    assert result.to_dict()["deprecated"] == 2


def test_build_result_degraded_semantica():
    result = BuildResult.from_dict({"graphId": "g", "semantica": {"semantica": False, "error": "boom"}})
    assert result.semantica.semantica is False
    assert result.semantica.error == "boom"
    assert "error" in result.semantica.to_dict()
    assert "deprecated" not in result.to_dict()


def test_stat_result():
    stat = StatResult.from_dict(
        {
            "graphId": "graph_1",
            "kbId": "kb_1",
            "nodeCount": 4,
            "edgeCount": 3,
            "entityTypes": [{"type": "person", "count": 2}],
            "relationTypes": [{"type": "works_at", "count": 3}],
            "schemaCoverage": {"entity": 1.0, "relation": 0.5, "overall": 0.8},
        }
    )
    assert stat.entityTypes[0].type == "person"
    assert stat.relationTypes[0].count == 3
    assert stat.schemaCoverage.relation == 0.5
    assert stat.to_dict()["schemaCoverage"]["overall"] == 0.8


def test_entity_and_relation_models():
    entity = Entity.from_dict(
        {
            "entityId": "ent_1",
            "graphId": "graph_1",
            "docId": "doc_1",
            "type": "person",
            "name": "Alice",
            "normalizedName": "alice",
            "properties": {"age": 30},
            "aliases": ["A."],
            "evidence": [{"docId": "doc_1"}],
            "confidence": 0.9,
            "unknown": "x",
        }
    )
    assert entity.type == "person" and entity.confidence == 0.9
    assert entity.evidence == [{"docId": "doc_1"}]
    assert entity.extra == {"unknown": "x"}
    assert entity.to_dict()["properties"] == {"age": 30}

    relation = Relation.from_dict(
        {
            "relationId": "rel_1",
            "type": "works_at",
            "sourceEntityId": "ent_1",
            "targetEntityId": "ent_2",
            "evidence": ["bad-item", {"docId": "doc_1"}],
        }
    )
    assert relation.type == "works_at"
    assert relation.evidence == [{"docId": "doc_1"}]
    assert relation.to_dict()["sourceEntityId"] == "ent_1"


def test_entity_and_relation_lists():
    nodes = EntityListData.from_dict(
        {"graphId": "g", "total": 1, "page": 2, "pageSize": 5, "items": [{"entityId": "ent_1"}]}
    )
    assert (nodes.total, nodes.page, nodes.pageSize) == (1, 2, 5)
    assert nodes.items[0].entityId == "ent_1"

    edges = RelationListData.from_dict({"graphId": "g", "items": [{"relationId": "rel_1"}]})
    assert edges.items[0].relationId == "rel_1"
    assert edges.to_dict()["items"][0]["relationId"] == "rel_1"


def test_neighbor_data_and_paths():
    neighbors = NeighborData.from_dict(
        {
            "graphId": "g",
            "entityId": "ent_1",
            "depth": 2,
            "center": {"entityId": "ent_1"},
            "nodes": [{"entityId": "ent_1"}, {"entityId": "ent_2"}],
            "edges": [{"relationId": "rel_1"}],
        }
    )
    assert neighbors.depth == 2
    assert neighbors.center is not None and neighbors.center.entityId == "ent_1"
    assert len(neighbors.nodes) == 2
    assert neighbors.to_dict()["center"]["entityId"] == "ent_1"

    empty = NeighborData.from_dict({"graphId": "g", "center": None})
    assert empty.center is None and empty.to_dict()["center"] is None

    paths = PathListData.from_dict(
        {
            "graphId": "g",
            "sourceEntityId": "ent_1",
            "targetEntityId": "ent_2",
            "total": 1,
            "items": [{"entityIds": ["ent_1", "ent_2"], "nodeNames": ["Alice", "Acme"], "length": 1}],
        }
    )
    assert paths.total == 1
    assert paths.items[0].nodeNames == ["Alice", "Acme"]
    assert paths.items[0].to_dict()["length"] == 1


def test_analytics_and_export():
    analytics = AnalyticsData.from_dict({"graphId": "g", "analysis": {"node_count": 2}})
    assert analytics.analysis["node_count"] == 2
    assert analytics.to_dict()["analysis"] == {"node_count": 2}

    export = ExportResult.from_dict({"graphId": "g", "format": "jsonl", "total": 3, "content": "{}\n"})
    assert (export.format, export.total, export.content) == ("jsonl", 3, "{}\n")


def test_sparql_result_boolean_and_truncation():
    select = SparqlResult.from_dict(
        {
            "graphId": "g",
            "query": "SELECT ?s WHERE { ?s ?p ?o }",
            "success": True,
            "bindings": [{"s": {"value": "x"}}],
            "variables": ["s"],
            "rowLimit": 100,
            "truncated": True,
        }
    )
    assert select.variables == ["s"]
    assert select.rowLimit == 100 and select.truncated is True
    assert select.boolean is None
    assert select.to_dict()["bindings"][0]["s"]["value"] == "x"

    ask = SparqlResult.from_dict({"graphId": "g", "metadata": {"boolean": True}})
    assert ask.boolean is True


def test_search_and_index_status_models():
    search = SearchData.from_dict(
        {
            "graphId": "g",
            "query": "Alice",
            "topK": 5,
            "semantica": False,
            "total": 0,
            "reason": "semantica 检索链不可用",
            "hits": [],
        }
    )
    assert search.semantica is False and search.reason.startswith("semantica")
    assert search.hits == []

    hit = SearchHit.from_dict(
        {"id": "ent_1", "kind": "entity", "type": "person", "name": "Alice", "score": 0.42}
    )
    assert hit.id == "ent_1" and hit.score == 0.42
    assert hit.to_dict()["kind"] == "entity"

    status = IndexStatusData.from_dict({"graphId": "g", "semantica": True, "backend": "FakeStore"})
    assert status.semantica is True and status.backend == "FakeStore"
    assert status.to_dict()["backend"] == "FakeStore"
    assert "reason" not in status.to_dict()


def test_job_models():
    job = JobData.from_dict(
        {
            "jobId": "job_1",
            "graphId": "g",
            "task": "build_text",
            "status": "success",
            "payload": {"text": "..."},
            "result": {"entityCount": 1},
            "error": None,
        }
    )
    assert job.finished is True
    assert job.result["entityCount"] == 1
    assert job.to_dict()["task"] == "build_text"

    pending = JobData.from_dict({"jobId": "job_2", "status": "pending"})
    assert pending.finished is False

    jobs = JobListData.from_dict({"total": 1, "items": [{"jobId": "job_1"}]})
    assert jobs.total == 1 and jobs.items[0].jobId == "job_1"
    assert jobs.to_dict()["items"][0]["jobId"] == "job_1"


def test_graph_path_defaults():
    path = GraphPath.from_dict({})
    assert path.entityIds == [] and path.length == 0
