"""跨仓契约测试：图谱线缆形状/分页/作业状态/traceId 的单一来源 = ikc-sdk-lib（G8/G3/G2）。

守护点（引擎侧「接线消费 sdk 模型」）：
1. domain 三个资产（GraphMeta/EntityRecord/RelationRecord）的 `to_dict()` 通过 sdk 模型校验，
   且键集合不发生漂移（不注入 sdk 默认值）；
2. `list_nodes` / `list_edges` 输出含 `totalPages`（G-02/G-03 契约字段，pageSize≤200）；
3. `stat` 输出通过 sdk `GraphStatResponse` 校验；
4. 作业视图保留引擎本地态 `status`，并给出映射后的外部态 `taskStatus`（未知态 fail-closed=FAILED）；
5. traceId 为 23 位纯数字（`ikc_sdk.core.trace` 算法单一来源）。
"""

from __future__ import annotations

import pytest

from graph_engine.application.service import GraphEngineService
from graph_engine.persistence.sqlite_store import SqliteGraphStore
from graph_engine.protocol import new_trace_id
from ikc_sdk.core.api.graph.edges import GraphEdgesResponse
from ikc_sdk.core.api.graph.nodes import GraphNodesResponse
from ikc_sdk.core.api.graph.stat import GraphStatResponse
from ikc_sdk.core.models.graph import EntityView, GraphMeta, RelationView
from ikc_sdk.core.models.task import EngineJobView

SCHEMA = {
    "entityTypes": [{"type": "person"}, {"type": "org"}],
    "relationTypes": [{"type": "works_at", "sourceTypes": ["person"], "targetTypes": ["org"]}],
}


@pytest.fixture()
def store(tmp_path):
    return SqliteGraphStore(str(tmp_path / "contract.db"))


@pytest.fixture()
def svc(store):
    return GraphEngineService(store)


def test_graph_meta_wire_shape_matches_sdk(svc):
    """graph 视图键集合 = sdk GraphMeta 的引擎面字段（无 schemaVersion/nodeCount/edgeCount）。"""
    created = svc.create_graph(name="契约图", kb_id="kb_contract", schema=SCHEMA)
    meta = svc.get_graph(created["graphId"])
    GraphMeta.model_validate(meta)  # 形状校验（sdk 为单一来源）
    assert set(meta) == {
        "graphId",
        "name",
        "kbId",
        "tenantId",
        "ownerId",
        "graphSchema",
        "status",
        "createdAt",
        "updatedAt",
    }


def test_entity_and_relation_payloads_match_sdk(svc):
    """实体/关系线缆形状 = sdk EntityView / RelationView（含证据扩展键透传）。"""
    gid = svc.create_graph(name="契约图", kb_id="kb_contract", schema=SCHEMA)["graphId"]
    svc.build_from_records(
        gid,
        entities=[{"name": "Alice", "type": "person", "confidence": 0.9}],
        relations=[],
        doc_id="d1",
    )
    node = svc.list_nodes(gid)["items"][0]
    EntityView.model_validate(node)
    assert node["evidence"] == [{"docId": "d1"}]
    assert node["normalizedName"] == "alice"

    svc.build_from_records(
        gid,
        entities=[{"name": "Acme", "type": "org"}],
        relations=[{"type": "works_at", "source": node["entityId"], "target": "Acme"}],
        doc_id="d1",
    )
    edge = svc.list_edges(gid)["items"][0]
    RelationView.model_validate(edge)
    assert edge["sourceEntityId"] == node["entityId"] and edge["type"] == "works_at"


def test_pagination_includes_total_pages(svc):
    """G-02/G-03 分页壳含 totalPages；空图谱为 0。"""
    gid = svc.create_graph(name="契约图", kb_id="kb_contract", schema=SCHEMA)["graphId"]
    empty = svc.list_nodes(gid)
    assert empty["totalPages"] == 0 and empty["total"] == 0
    GraphNodesResponse.model_validate(empty)

    svc.build_from_records(
        gid,
        entities=[
            {"name": "Alice", "type": "person"},
            {"name": "Bob", "type": "person"},
            {"name": "Acme", "type": "org"},
        ],
        relations=[],
        doc_id="d1",
    )
    first = svc.list_nodes(gid, page=1, page_size=2)
    assert (first["total"], first["page"], first["pageSize"], first["totalPages"]) == (3, 1, 2, 2)
    assert len(first["items"]) == 2
    second = svc.list_nodes(gid, page=2, page_size=2)
    assert (second["totalPages"], len(second["items"])) == (2, 1)
    # pageSize 上限 200 与 sdk 口径一致
    assert svc.list_nodes(gid, page_size=1000)["pageSize"] == 200
    GraphEdgesResponse.model_validate(svc.list_edges(gid))


def test_stat_matches_sdk(svc):
    gid = svc.create_graph(name="契约图", kb_id="kb_contract", schema=SCHEMA)["graphId"]
    svc.build_from_records(
        gid, entities=[{"name": "Alice", "type": "person"}], relations=[], doc_id="d1"
    )
    stat = svc.stat(gid)
    GraphStatResponse.model_validate(stat)
    assert stat["schemaCoverage"] == {"entity": 1.0, "relation": 1.0, "overall": 1.0}


def test_job_view_keeps_local_status_and_maps_external(svc, store):
    """本地态保留在 status，外部态在 taskStatus（G3）；未知态 fail-closed 为 FAILED。"""
    gid = svc.create_graph(name="契约图", kb_id="kb_contract", schema=SCHEMA)["graphId"]
    job = svc.submit_job("build", gid, {"entities": [], "relations": []})
    EngineJobView.model_validate(job)
    assert job["status"] == "pending" and job["taskStatus"] == "PENDING"

    svc.run_job(job["jobId"])
    done = svc.get_job(job["jobId"])
    assert done["status"] == "success" and done["taskStatus"] == "SUCCEEDED"

    store.update_job(job["jobId"], status="weird-status")
    unknown = svc.get_job(job["jobId"])
    assert unknown["status"] == "weird-status" and unknown["taskStatus"] == "FAILED"
    assert svc.list_jobs(graph_id_value=gid)["items"][0]["taskStatus"] == "FAILED"


def test_engine_generated_trace_id_is_23_digits():
    trace_id = new_trace_id()
    assert len(trace_id) == 23 and trace_id.isdigit()
