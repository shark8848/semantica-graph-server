"""客户端 SDK ↔ ikc-sdk-lib 绑定契约（G8/G3）：DTO 形状单一来源 + 新增契约字段。

守护点：`from_dict()` 经 sdk 模型校验、`to_dict()` 由 sdk 模型序列化且不注入 sdk 默认值；
分页壳补 `totalPages`；作业视图给出映射后的外部态 `taskStatus`（未知态 fail-closed）。
"""

from __future__ import annotations

from semantica_graph_sdk.models.graph import (
    Entity,
    EntityListData,
    ExportResult,
    GraphMeta,
    GraphPath,
    JobData,
    NeighborData,
    PathListData,
    Relation,
    RelationListData,
    StatResult,
)


def test_entity_and_relation_bind_to_sdk_models():
    entity = Entity.from_dict({"entityId": "ent_1", "name": "Alice", "unknown": "x"})
    assert entity.type == "concept" and entity.extra == {"unknown": "x"}
    dumped = entity.to_dict()
    assert dumped["entityId"] == "ent_1" and dumped["unknown"] == "x"
    assert Entity.from_dict(dumped).to_dict() == dumped

    relation = Relation.from_dict({"relationId": "rel_1", "type": "works_at"})
    assert relation.to_dict()["sourceEntityId"] == ""
    assert Relation.from_dict(relation.to_dict()).to_dict() == relation.to_dict()


def test_graph_meta_and_extra_round_trip():
    meta = GraphMeta.from_dict(
        {"graphId": "g", "graphSchema": {"entityTypes": [{"type": "person"}]}, "x": 1}
    )
    assert meta.graphSchema == {"entityTypes": [{"type": "person"}]}
    assert meta.to_dict()["x"] == 1
    # 引擎面字段集合（不含 core 视图的 schemaVersion/nodeCount/edgeCount）
    assert "nodeCount" not in meta.to_dict()


def test_paged_lists_carry_total_pages():
    nodes = EntityListData.from_dict(
        {
            "graphId": "g",
            "total": 3,
            "page": 1,
            "pageSize": 2,
            "totalPages": 2,
            "items": [{"entityId": "ent_1"}],
        }
    )
    assert (nodes.totalPages, nodes.items[0].entityId) == (2, "ent_1")
    assert nodes.to_dict()["totalPages"] == 2

    edges = RelationListData.from_dict({"graphId": "g", "items": [{"relationId": "rel_1"}]})
    assert edges.totalPages == 0 and edges.to_dict()["totalPages"] == 0


def test_stat_and_neighbors_and_paths_validate_against_sdk():
    stat = StatResult.from_dict(
        {
            "graphId": "g",
            "kbId": "kb",
            "nodeCount": 2,
            "edgeCount": 1,
            "entityTypes": [{"type": "person", "count": 2}],
            "relationTypes": [{"type": "works_at", "count": 1}],
            "schemaCoverage": {"entity": 1.0, "relation": 0.5, "overall": 0.8},
        }
    )
    assert stat.schemaCoverage.overall == 0.8
    assert stat.to_dict()["entityTypes"] == [{"type": "person", "count": 2}]

    neighbors = NeighborData.from_dict(
        {"graphId": "g", "entityId": "ent_1", "depth": 2, "center": {"entityId": "ent_1"}}
    )
    assert neighbors.center is not None and neighbors.to_dict()["depth"] == 2

    paths = PathListData.from_dict(
        {"graphId": "g", "total": 1, "items": [{"entityIds": ["ent_1"], "length": 0}]}
    )
    assert paths.items[0].length == 0
    assert GraphPath.from_dict({"entityIds": ["e"], "length": 1}).to_dict()["entityIds"] == ["e"]


def test_export_result_round_trip():
    export = ExportResult.from_dict({"graphId": "g", "format": "ttl", "total": 2, "content": "@a b c .\n"})
    assert export.format == "ttl" and export.to_dict()["content"] == "@a b c .\n"


def test_job_status_maps_to_external_task_status():
    pending = JobData.from_dict({"jobId": "j1", "status": "pending"})
    assert pending.finished is False and pending.taskStatus.value == "PENDING"

    done = JobData.from_dict({"jobId": "j1", "status": "success"})
    assert done.finished is True and done.taskStatus.value == "SUCCEEDED"

    # 未知本地态 fail-closed 为 FAILED（不误判成功）
    unknown = JobData.from_dict({"jobId": "j1", "status": "weird"})
    assert unknown.taskStatus.value == "FAILED" and unknown.finished is True
