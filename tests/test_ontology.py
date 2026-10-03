"""本体面（O-21 ~ O-24）测试：域推导 / 体检 / 候选 / 导出 / 快照 / 五面入口。

覆盖用户诉求「用 semantica-graph-server 构建整套本体」的最小可验证面：
1. 域：定义归一、graphSchema 推导、结构体检（悬空 / 环）、实例体检（端点 / 必填 / 基数 / 数据类型）；
2. 候选：从图记录确定性聚合（D6 只产候选）；
3. 产物：json 导出（编译产物）+ owl/turtle/shacl 经 semantica（不可用时如实 260009 降级）；
4. 快照：按图按版本缓存 + 版本 diff；
5. 入口：HTTP 路由与构建期 `schemaCheck`。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from graph_engine import adapters
from graph_engine.adapters import ontology as ontology_adapter
from graph_engine.application.service import GraphEngineService
from graph_engine.domain import ontology as domain
from graph_engine.errors import GraphEngineError, InvalidParamsError, NotFoundError
from graph_engine.interfaces.http_app import create_app
from graph_engine.persistence.sqlite_store import SqliteGraphStore

SCHEMA = {
    "entityTypes": [
        {"type": "person", "properties": [{"name": "age", "dataType": "integer", "required": True}]},
        {"type": "org"},
    ],
    "relationTypes": [
        {"type": "works_at", "sourceTypes": ["person"], "targetTypes": ["org"], "cardinality": "1"}
    ],
}

DEFINITION = {
    "ontologyId": "ont_test_1",
    "name": "测试本体",
    "version": 3,
    "concepts": [
        {"conceptId": "con_person", "name": "人员", "normalizedName": "person", "conceptType": "class"},
        {"conceptId": "con_org", "name": "组织", "normalizedName": "org", "conceptType": "class"},
        {"conceptId": "con_unit", "name": "单位", "normalizedName": "unit", "conceptType": "class", "subClassOf": "con_org"},
    ],
    "properties": [
        {"conceptId": "con_person", "name": "age", "propertyKind": "data", "dataType": "integer", "required": True}
    ],
    "relations": [
        {
            "relationDefId": 1,
            "name": "任职于",
            "relationCode": "works_at",
            "sourceTypeIds": ["con_person"],
            "targetTypeIds": ["con_org"],
            "cardinality": "1",
        }
    ],
}


@pytest.fixture()
def store(tmp_path):
    return SqliteGraphStore(str(tmp_path / "test.db"))


@pytest.fixture()
def svc(store):
    return GraphEngineService(store)


@pytest.fixture()
def client(svc):
    return TestClient(create_app(svc))


def _build(svc):
    graph = svc.create_graph(name="本体测试图", kb_id="kb_ont_1", schema=SCHEMA)
    gid = graph["graphId"]
    # 记录路径用显式 entityId（core 侧同一口径）；稳定 ID 派生名只在文本抽取路径
    svc.build_from_records(
        gid,
        entities=[
            {"entityId": "e_zs", "name": "张三", "type": "person", "properties": {"age": 30}},
            {"entityId": "e_ls", "name": "李四", "type": "person"},
            {"entityId": "e_zz", "name": "组织部", "type": "org"},
            {"entityId": "e_cw", "name": "财务部", "type": "org"},
        ],
        relations=[
            {"sourceEntityId": "e_zs", "targetEntityId": "e_zz", "type": "works_at"},
            {"sourceEntityId": "e_zs", "targetEntityId": "e_cw", "type": "works_at"},
            {"sourceEntityId": "e_ls", "targetEntityId": "e_zz", "type": "works_at"},
        ],
    )
    return gid


# ---------- domain ----------


def test_derive_graph_schema_from_definition():
    schema = domain.derive_graph_schema(domain.normalize_definition(DEFINITION))
    types = {item["type"]: item for item in schema["entityTypes"]}
    assert set(types) == {"person", "org", "unit"}
    assert types["unit"]["subClassOf"] == "org"  # conceptId 引用解析为 normalizedName
    assert types["person"]["properties"][0]["required"] is True
    relation = schema["relationTypes"][0]
    assert relation["type"] == "works_at"
    assert relation["sourceTypes"] == ["person"] and relation["targetTypes"] == ["org"]


def test_derive_graph_schema_prefers_explicit_product():
    explicit = {"entityTypes": [{"type": "x"}], "relationTypes": []}
    definition = domain.normalize_definition(
        {"graphSchema": explicit, "concepts": DEFINITION["concepts"]}
    )
    assert domain.derive_graph_schema(definition) == explicit


def test_validate_definition_reports_dangling_and_cycle():
    payload = {
        "concepts": [
            {"conceptId": "c1", "name": "A", "normalizedName": "a", "subClassOf": "c2"},
            {"conceptId": "c2", "name": "B", "normalizedName": "b", "subClassOf": "c1"},
            {"conceptId": "c3", "name": "C", "normalizedName": "c", "subClassOf": "missing"},
        ],
        "properties": [{"conceptId": "c1", "name": "p", "propertyKind": "data"}],
        "relations": [{"name": "r", "relationCode": "r", "sourceTypeIds": ["nope"]}],
    }
    issues = domain.validate_definition(domain.normalize_definition(payload))
    codes = [item["code"] for item in issues]
    assert domain.ISSUE_CYCLE in codes
    assert domain.ISSUE_DANGLING_REF in codes
    assert domain.ISSUE_DATA_TYPE_VIOLATION in codes


def test_validate_graph_required_and_cardinality(svc):
    gid = _build(svc)
    report = svc.validate_graph_ontology(gid, payload=DEFINITION)
    counts = report["issueCounts"]
    assert counts.get(domain.ISSUE_MISSING_REQUIRED, 0) == 1  # 李四缺 age
    assert counts.get(domain.ISSUE_CARDINALITY_VIOLATION, 0) == 1  # 张三第二条 works_at
    assert counts.get(domain.ISSUE_ENDPOINT_VIOLATION, 0) == 0
    assert report["valid"] is False
    assert all(item["entityId"] or item["relationId"] for item in report["issues"])  # §11.1
    assert report["graphId"] == gid and report["kbId"] == "kb_ont_1"
    assert report["coverage"]["schemaCoverage"]["entity"] == 1.0


def test_validate_graph_endpoint_violation(svc):
    strict = {
        "entityTypes": [{"type": "person"}, {"type": "org"}],
        "relationTypes": [{"type": "works_at", "sourceTypes": ["org"], "targetTypes": ["org"]}],
    }
    graph = svc.create_graph(name="越界图", kb_id="kb_ont_3", schema=strict)
    gid = graph["graphId"]
    svc.build_from_records(
        gid,
        entities=[
            {"entityId": "e_zs", "name": "张三", "type": "person"},
            {"entityId": "e_zz", "name": "组织部", "type": "org"},
        ],
        relations=[{"sourceEntityId": "e_zs", "targetEntityId": "e_zz", "type": "works_at"}],
    )
    report = svc.validate_graph_ontology(gid, payload={"graphSchema": strict})
    assert report["issueCounts"].get(domain.ISSUE_ENDPOINT_VIOLATION) == 1


def test_candidates_aggregate(svc):
    gid = _build(svc)
    result = svc.generate_ontology_candidates(gid, max_classes=10)
    classes = {item["name"]: item for item in result["classes"]}
    assert classes["person"]["count"] == 2
    assert classes["person"]["properties"][0]["name"] == "age"
    assert classes["person"]["properties"][0]["coverage"] == 0.5
    assert classes["person"]["sampleEntityIds"]
    relation = {item["name"]: item for item in result["relations"]}["works_at"]
    assert relation["count"] == 3
    assert relation["sourceTypes"] == ["person"] and relation["targetTypes"] == ["org"]
    assert result["kbId"] == "kb_ont_1" and result["truncated"] is False


def test_candidates_truncated(svc):
    gid = _build(svc)
    result = svc.generate_ontology_candidates(gid, max_classes=1)
    assert len(result["classes"]) == 1 and result["truncated"] is True


def test_coverage_view(svc):
    gid = _build(svc)
    view = svc.ontology_coverage(gid, payload=DEFINITION)
    assert view["graphId"] == gid and view["ontologyId"] == "ont_test_1"
    assert view["schemaCoverage"]["overall"] == 1.0
    assert view["violations"]["missingRequired"] == 1
    assert view["undeclaredEntityTypes"] == []
    assert view["entityTypes"][0]["declared"] is True


def test_schema_check_written_into_build(svc):
    gid = _build(svc)
    result = svc.build_from_records(
        gid, entities=[{"entityId": "e_ww", "name": "王五", "type": "person"}], relations=[]
    )
    assert result["schemaCheck"]["missingRequired"] == 1
    assert result["schemaCheck"]["endpointViolations"] == 0


# ---------- 产物导出 / 导入 ----------


def test_export_json_uses_compiled_product(svc):
    result = svc.export_ontology({**DEFINITION, "format": "json"})
    assert result["format"] == "json" and result["contentType"] == "application/json"
    assert result["graphSchema"]["entityTypes"]


def test_export_unknown_format(svc):
    with pytest.raises(InvalidParamsError):
        svc.export_ontology({**DEFINITION, "format": "yaml"})


def test_export_owl_degraded_when_semantica_unavailable(monkeypatch, svc):
    monkeypatch.setattr(ontology_adapter, "ontology_available", lambda: False)
    with pytest.raises(GraphEngineError) as excinfo:
        svc.export_ontology({**DEFINITION, "format": "owl"})
    assert excinfo.value.code == "260009"


def test_ingest_degraded(monkeypatch, svc):
    monkeypatch.setattr(ontology_adapter, "rdflib_available", lambda: False)
    with pytest.raises(GraphEngineError) as excinfo:
        svc.ingest_ontology("<rdf:RDF/>", format="owl")
    assert excinfo.value.code == "260009"


@pytest.mark.skipif(not adapters.ontology_available(), reason="semantica.ontology 不可用")
def test_export_owl_and_shacl_real(svc):
    owl = svc.export_ontology({**DEFINITION, "format": "owl"})
    assert owl["contentType"] and "person" in owl["content"]
    shacl = svc.export_ontology({**DEFINITION, "format": "shacl"})
    assert "NodeShape" in shacl["content"]


# ---------- 快照 / 版本 ----------


def test_snapshot_roundtrip_and_diff(svc):
    gid = _build(svc)
    schema_v1 = domain.derive_graph_schema(domain.normalize_definition(DEFINITION))
    svc.put_ontology_snapshot(
        gid, ontology_id="ont_test_1", ontology_version=1, graph_schema=schema_v1
    )
    schema_v2 = {
        "entityTypes": [item for item in schema_v1["entityTypes"] if item["type"] != "unit"]
        + [{"type": "dept"}],
        "relationTypes": schema_v1["relationTypes"],
    }
    svc.put_ontology_snapshot(
        gid, ontology_id="ont_test_1", ontology_version=2, graph_schema=schema_v2
    )

    listed = svc.list_ontology_snapshots(gid)
    assert listed["total"] == 2 and listed["items"][0]["ontologyVersion"] == 2
    diff = svc.ontology_version_diff(gid, from_version=1, to_version=2)
    assert diff["diff"]["entityTypes"]["added"] == ["dept"]
    assert diff["diff"]["entityTypes"]["removed"] == ["unit"]


def test_snapshot_requires_version(svc):
    gid = _build(svc)
    with pytest.raises(InvalidParamsError):
        svc.put_ontology_snapshot(gid, ontology_version=0, graph_schema={"entityTypes": []})


def test_ontology_version_diff_missing(svc):
    gid = _build(svc)
    with pytest.raises(NotFoundError):
        svc.ontology_version_diff(gid, from_version=9, to_version=10)


def test_delete_graph_removes_snapshots(svc):
    gid = _build(svc)
    svc.put_ontology_snapshot(gid, ontology_version=1, graph_schema={"entityTypes": []})
    svc.delete_graph(gid)
    assert svc.list_graphs()["total"] == 0
    svc.create_graph(graph_id_value=gid, name="重建", kb_id="kb_ont_1")
    assert svc.list_ontology_snapshots(gid)["total"] == 0


# ---------- HTTP 面 ----------


def test_http_ontology_routes(client, svc):
    gid = _build(svc)
    created = client.post(
        "/api/v1/graph/ontology/candidates", json={"graphId": gid, "maxClasses": 10}
    ).json()
    assert created["errCode"] == "0" and created["data"]["classes"]

    validated = client.post(
        "/api/v1/graph/ontology/validate-graph", json={"graphId": gid, **DEFINITION}
    ).json()
    assert validated["errCode"] == "0"
    assert validated["data"]["issueCounts"]["missing_required"] == 1

    exported = client.post(
        "/api/v1/graph/ontology/export", json={**DEFINITION, "format": "json"}
    ).json()
    assert exported["errCode"] == "0" and exported["data"]["graphSchema"]

    snapshot = client.post(
        "/api/v1/graph/ontology/snapshots",
        json={"graphId": gid, "ontologyId": "ont_test_1", "ontologyVersion": 1, "graphSchema": SCHEMA},
    ).json()
    assert snapshot["errCode"] == "0"
    listed = client.get("/api/v1/graph/ontology/snapshots", params={"graphId": gid}).json()
    assert listed["data"]["total"] == 1

    coverage = client.post(
        "/api/v1/graph/ontology/coverage", json={"graphId": gid, **DEFINITION}
    ).json()
    assert coverage["errCode"] == "0" and coverage["data"]["schemaCoverage"]

    definition_validation = client.post("/api/v1/graph/ontology/validate", json=DEFINITION).json()
    assert definition_validation["errCode"] == "0"
    assert definition_validation["data"]["valid"] is True


def test_http_ontology_unavailable_maps_501(monkeypatch, client, svc):
    gid = _build(svc)
    monkeypatch.setattr(ontology_adapter, "ontology_available", lambda: False)
    response = client.post(
        "/api/v1/graph/ontology/export", json={**DEFINITION, "format": "turtle"}
    )
    assert response.status_code == 501
    assert response.json()["errCode"] == "260009"


def test_http_ontology_unknown_format_400(client):
    response = client.post("/api/v1/graph/ontology/export", json={"format": "yaml"})
    assert response.status_code == 400
    assert response.json()["errCode"] == "200001"


def test_candidates_async_submits_job(client, svc):
    gid = _build(svc)
    body = client.post(
        "/api/v1/graph/ontology/candidates", json={"graphId": gid, "async": True}
    ).json()
    assert body["errCode"] == "0"
    job = body["data"]
    assert job["jobId"] and job["status"] == "pending"
    assert job["task"] == "ontology_candidates"
    # Celery 未启用时只登记 pending；run_job 兜底同步执行（G-07 轮询同源）
    done = svc.run_job(job["jobId"])
    assert done["status"] == "success"
    assert done["result"]["classes"]


def test_candidates_async_missing_graph_404(client):
    response = client.post(
        "/api/v1/graph/ontology/candidates", json={"graphId": "graph_missing", "async": True}
    )
    assert response.status_code == 404
