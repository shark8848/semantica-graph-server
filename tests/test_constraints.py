"""P4 抽取约束（人工修正沉淀）测试：画像解析/合并、候选收敛、记录收敛、按图沉淀。

覆盖用户诉求「同一错误不再复发」的最小可验证面：
1. 改名（别名表）→ 抽取结果收敛到规范名且保留别名；
2. 锁类型 / schema 白名单 → 类型不漂移；
3. 黑名单 → 人工软删的行不再进图（关系端点级联）；
4. few-shot 样例 → 展开为类型 / 关系类型约束；
5. 按图沉淀 → 后续不带 constraints 的构建同样生效（HTTP 端点读写一致）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from graph_engine.application.service import GraphEngineService
from graph_engine.domain.constraints import ExtractionConstraints, relation_pair_key
from graph_engine.domain.ids import normalize_name
from graph_engine.persistence.sqlite_store import SqliteGraphStore

SCHEMA = {
    "entityTypes": [{"type": "person"}, {"type": "org"}, {"type": "concept"}],
    "relationTypes": [{"type": "related_to"}, {"type": "reports_to"}],
}

#: 一份带「改名 / 锁类型 / 黑名单 / 样例」的画像（人工修正后的沉淀形态）
PROFILE = {
    "entityRenames": {"alicechen": "Alice Chen", "alice": "Alice Chen"},
    "entityTypes": {"alicechen": "person", "垃圾节点": "org"},
    "entityBlacklist": ["废弃段落"],
    "relationTypes": {"Alice Chen|Acme": "reports_to"},
    "typeWhitelist": ["person", "org", "concept"],
    "examples": [
        {
            "entities": [{"name": "Beta", "type": "org"}],
            "relations": [{"source": "Beta", "target": "Alice Chen", "type": "reports_to"}],
        }
    ],
}


def test_parse_and_normalize_profile():
    profile = ExtractionConstraints.from_dict(PROFILE)
    assert profile.entity_renames[normalize_name("AliceChen")] == "Alice Chen"
    assert profile.relation_types[relation_pair_key("Acme", "Alice Chen")] == "reports_to"
    # few-shot 样例展开：实体样例并入类型表、关系样例并入关系类型表
    assert profile.entity_types[normalize_name("Beta")] == "org"
    assert profile.relation_types[relation_pair_key("Beta", "Alice Chen")] == "reports_to"
    assert profile.type_whitelist == ("person", "org", "concept")
    assert ExtractionConstraints.from_dict("{不是 JSON").is_empty()
    assert ExtractionConstraints.from_dict(None).is_empty()


def test_merge_is_last_write_wins_with_unions():
    base = ExtractionConstraints.from_dict(
        {"entityTypes": {"a": "person"}, "entityBlacklist": ["x"], "typeWhitelist": ["person"]}
    )
    nxt = ExtractionConstraints.from_dict(
        {"entityTypes": {"a": "org", "b": "concept"}, "entityBlacklist": ["y"]}
    )
    merged = base.merged(nxt)
    assert merged.entity_types == {"a": "org", "b": "concept"}
    assert merged.entity_blacklist == frozenset({"x", "y"})
    assert merged.type_whitelist == ("person",)  # 非空才覆盖
    assert merged.merged({}) is merged


def test_apply_candidates_rename_type_blacklist_and_dedupe():
    profile = ExtractionConstraints.from_dict(PROFILE)
    entities = [
        {"name": "Alice", "type": "org", "evidence": [{"docId": "d1"}]},
        {"name": "Alice Chen", "type": "concept", "confidence": 0.9},
        {"name": "废弃段落", "type": "concept"},
        {"name": "Other", "type": "unknown-type"},
    ]
    out, stats = profile.apply_candidates(entities, default_type="concept")
    names = [item["name"] for item in out]
    # 「Alice」→「Alice Chen」并与同名候选收敛为一行，原名进别名
    assert names == ["Alice Chen", "Other"]
    assert out[0]["type"] == "person"
    assert out[0]["aliases"] == ["Alice"]
    assert len(out[0]["evidence"]) == 1 and out[0]["confidence"] == 0.9
    # 黑名单与白名单回落
    assert "废弃段落" not in names
    assert out[1]["type"] == "concept"
    assert stats["renamed"] == 1 and stats["dropped"] == 1 and stats["deduped"] == 1
    assert stats["typeFallbacks"] == 1


def test_apply_records_drops_relations_of_blacklisted_entities():
    profile = ExtractionConstraints.from_dict({"entityBlacklist": ["垃圾"]})
    entities = [{"entityId": "ent_1", "name": "保留"}, {"entityId": "ent_2", "name": "垃圾"}]
    relations = [
        {"relationId": "rel_1", "sourceEntityId": "ent_1", "targetEntityId": "ent_2"},
        {"relationId": "rel_2", "sourceEntityId": "ent_1", "targetEntityId": "ent_1"},
    ]
    kept_e, kept_r, stats = profile.apply_records(entities, relations)
    assert [item["entityId"] for item in kept_e] == ["ent_1"]
    assert [item["relationId"] for item in kept_r] == ["rel_2"]
    assert stats["dropped"] == 2


def test_service_persists_profile_and_applies_on_next_build(tmp_path):
    store = SqliteGraphStore(str(tmp_path / "p4.db"))
    svc = GraphEngineService(store)
    svc.create_graph(name="P4", kb_id="kb_p4", schema=SCHEMA)
    gid = store.list_graphs()[0].graph_id

    first = svc.build_from_text(
        gid,
        text="「Alice」与「Acme」合作，「废弃段落」也在。",
        doc_id="d1",
        constraints=PROFILE,
    )
    assert first["constraints"]["renames"] == 2
    assert {item["name"] for item in first["entities"]} >= {"Alice Chen", "Acme"}
    assert "废弃段落" not in {item["name"] for item in first["entities"]}
    # 关系类型按人工确认的端点对落定（不是 schema 的 related_to）
    pairs = {
        (item["sourceEntityId"], item["targetEntityId"]): item["type"]
        for item in first["relations"]
    }
    alice = next(item["entityId"] for item in first["entities"] if item["name"] == "Alice Chen")
    acme = next(item["entityId"] for item in first["entities"] if item["name"] == "Acme")
    assert pairs.get((alice, acme)) == "reports_to"
    stored = svc.get_constraints(gid)
    assert stored["entityRenames"][normalize_name("Alice")] == "Alice Chen"
    assert stored["entityTypes"][normalize_name("AliceChen")] == "person"

    # 第二次构建**不带** constraints：画像已沉淀，同样生效
    second = svc.build_from_text(gid, text="「Alice」与「Beta」。", doc_id="d1")
    names = {item["name"] for item in second["entities"]}
    assert "Alice Chen" in names and "Alice" not in names


def test_http_constraints_roundtrip_and_build_payload(tmp_path):
    store = SqliteGraphStore(str(tmp_path / "p4http.db"))
    svc = GraphEngineService(store)
    from graph_engine.interfaces.http_app import create_app

    http = TestClient(create_app(svc))
    resp = http.post("/api/v1/graph/graphs", json={"name": "P4-HTTP", "kbId": "kb_p4_http", "graphSchema": SCHEMA})
    gid = resp.json()["data"]["graphId"]

    resp = http.put(f"/api/v1/graph/graphs/{gid}/constraints", json=PROFILE)
    assert resp.status_code == 200
    assert resp.json()["data"]["entityRenames"][normalize_name("AliceChen")] == "Alice Chen"

    resp = http.get(f"/api/v1/graph/graphs/{gid}/constraints")
    assert resp.json()["data"]["entityBlacklist"] == ["废弃段落"]

    # 构建载荷里的 constraints 与沉淀画像合并；返回体给出收敛统计
    resp = http.post(
        f"/api/v1/graph/graphs/{gid}/build",
        json={
            "text": "「Alice」与「Acme」。",
            "docId": "d1",
            "constraints": {"entityBlacklist": ["Acme"]},
        },
    )
    body = resp.json()
    assert body["errCode"] == "0"
    assert body["data"]["constraints"]["applied"]["dropped"] == 0  # 候选阶段已收敛
    names = {item["name"] for item in body["data"]["entities"]}
    assert "Alice Chen" in names and "Acme" not in names
    # 合并后的画像包含新黑名单，且原画像不丢
    stored = http.get(f"/api/v1/graph/graphs/{gid}/constraints").json()["data"]
    assert set(stored["entityBlacklist"]) == {"acme", "废弃段落"}  # 画像键一律归一化


def test_record_path_only_blacklist_applies(tmp_path):
    store = SqliteGraphStore(str(tmp_path / "p4rec.db"))
    svc = GraphEngineService(store)
    svc.create_graph(name="P4-Rec", kb_id="kb_p4_rec", schema=SCHEMA)
    gid = store.list_graphs()[0].graph_id
    result = svc.build_from_records(
        gid,
        entities=[
            {"name": "保留", "type": "person", "entityId": "ent_keep"},
            {"name": "垃圾", "type": "concept", "entityId": "ent_junk"},
        ],
        relations=[{"relationId": "rel_1", "sourceEntityId": "ent_keep", "targetEntityId": "ent_junk"}],
        doc_id="d1",
        constraints={"entityBlacklist": ["垃圾"]},
    )
    assert [item["name"] for item in result["entities"]] == ["保留"]
    assert result["relations"] == []
    assert result["constraints"]["applied"]["dropped"] == 2
