"""文本建图关系抽取与 build 响应记录回传用例。

覆盖：规则共现抽取（端点稳定 ID / 证据 / 置信）、graphSchema 关系类型约束、
单实体与跨句不产关系、semantica 增强失败的确定降级、build 响应 `entities`/`relations`
可被 `EntityRecord.from_dict` / `RelationRecord.from_dict` 反向解析。
"""

from __future__ import annotations

import pytest

from graph_engine.adapters import relations as relations_adapter
from graph_engine.application.service import GraphEngineService
from graph_engine.domain.ids import entity_id, normalize_name, relation_id
from graph_engine.domain.models import EntityRecord, RelationRecord
from graph_engine.persistence.sqlite_store import SqliteGraphStore

SCHEMA = {"entityTypes": [{"type": "concept"}], "relationTypes": [{"type": "related_to"}]}

#: 两个句子共 4 个可定位实体（含正文出现的标题实体），句内两两共现 → 4 条边
TEXT = "「接入向导」用于创建「知识库」。演示用户通过「接入向导」查看「检索结果」。"
TITLE = "演示用户"


@pytest.fixture()
def svc(tmp_path):
    return GraphEngineService(SqliteGraphStore(str(tmp_path / "test.db")))


def _create(svc, schema=SCHEMA, kb_id="kb_rel_1"):
    return svc.create_graph(name="关系测试图", kb_id=kb_id, schema=schema)["graphId"]


# ---------- ① 规则关系抽取 ----------


def test_rule_relations_use_stable_ids_and_evidence(svc):
    gid = _create(svc)
    result = svc.build_from_text(gid, text=TEXT, doc_id="doc1", title=TITLE)

    assert result["entityCount"] == 4  # 标题 + 三个引号候选（重复候选去重）
    assert result["relationCount"] == 4
    assert svc.list_edges(gid)["total"] == 4
    assert svc.stat(gid)["edgeCount"] == 4

    expected = {
        entity_id(gid, "concept", normalize_name("接入向导")),
        entity_id(gid, "concept", normalize_name("知识库")),
    }
    edge = next(
        item for item in result["relations"] if {item["sourceEntityId"], item["targetEntityId"]} == expected
    )
    # 方向取原文先出现者；端点与关系 ID 均为稳定键派生
    assert edge["sourceEntityId"] == entity_id(gid, "concept", normalize_name("接入向导"))
    assert edge["targetEntityId"] == entity_id(gid, "concept", normalize_name("知识库"))
    assert edge["type"] == "related_to"
    assert edge["docId"] == "doc1"
    assert edge["confidence"] == pytest.approx(1.0)
    assert edge["properties"] == {"cooccurrence": 1, "methods": ["rule_cooccurrence"]}
    assert edge["evidence"][0]["docId"] == "doc1"
    assert "知识库" in edge["evidence"][0]["snippet"]
    assert edge["relationId"] == relation_id(
        gid, "related_to", edge["sourceEntityId"], edge["targetEntityId"]
    )
    # 同一对跨句重复共现只产一条边：接入向导/知识库仅在首句共现 → cooccurrence=1
    assert svc.list_edges(gid, relation_type="related_to")["total"] == 4


@pytest.mark.parametrize(
    ("relation_types", "expected"),
    [
        ([{"type": "related_to"}], "related_to"),
        ([{"type": "works_at"}], "works_at"),
        ([{"type": "works_at"}, {"type": "关联关系"}], "关联关系"),
        ([{"type": "works_at"}, {"type": "co_occurs_with"}], "works_at"),
    ],
)
def test_relation_type_follows_schema(svc, relation_types, expected):
    """schema 声明了 relationTypes 时只用声明的类型（优先 related/关联 者，其次首个）。"""
    schema = {"entityTypes": [{"type": "concept"}], "relationTypes": relation_types}
    gid = _create(svc, schema=schema, kb_id=f"kb_rel_{expected}")
    result = svc.build_from_text(gid, text=TEXT, doc_id="doc1", title=TITLE)
    assert result["relationCount"] >= 1
    assert {item["type"] for item in result["relations"]} == {expected}


def test_single_entity_produces_no_relation(svc):
    gid = _create(svc, kb_id="kb_rel_single")
    result = svc.build_from_text(gid, text="「接入向导」是唯一的实体。", doc_id="doc1")
    assert result["entityCount"] == 2  # 未命名文档（标题占位）+ 引号候选
    assert result["relationCount"] == 0
    assert result["relations"] == []
    assert svc.stat(gid)["edgeCount"] == 0


def test_no_relation_across_sentences(svc):
    """跨句共现不产关系（规则口径按句切分，避免远距离噪声边）。"""
    gid = _create(svc, kb_id="kb_rel_sentence")
    result = svc.build_from_text(gid, text="「接入向导」用于创建。演示用户查看「知识库」。", doc_id="doc1")
    assert result["entityCount"] == 3  # 标题占位 + 两个引号候选
    assert result["relationCount"] == 0


def test_llm_enhance_failure_degrades_to_rules(svc, monkeypatch):
    monkeypatch.delenv("GRAPH_ENGINE_LLM_PROVIDER", raising=False)

    def _boom(method="cooccurrence"):
        raise RuntimeError("semantica boom")

    monkeypatch.setattr(relations_adapter, "_relation_extractor", _boom)
    gid = _create(svc, kb_id="kb_rel_llm")
    result = svc.build_from_text(gid, text=TEXT, doc_id="doc1", title=TITLE, llm=True)
    assert result["relationCount"] == 4
    assert {item["properties"]["methods"][0] for item in result["relations"]} == {"rule_cooccurrence"}


def test_adapter_defaults_and_guards():
    records = relations_adapter.extract_text_relations(
        "「甲」与「乙」共事。", [{"name": "甲"}, {"name": "乙"}]
    )
    assert len(records) == 1
    record = records[0]
    assert record["type"] == "related_to"
    assert record["sourceEntityId"] == entity_id("", "concept", normalize_name("甲"))
    assert record["targetEntityId"] == entity_id("", "concept", normalize_name("乙"))
    assert record["evidence"][0] == {"docId": "", "snippet": "「甲」与「乙」共事"}
    assert relations_adapter.relation_type_for(None) == "related_to"
    # 空文本 / 少于两个可定位实体 / 全部实体不可定位 → 空列表，不抛错
    assert relations_adapter.extract_text_relations("", [{"name": "甲"}, {"name": "乙"}]) == []
    assert relations_adapter.extract_text_relations("「甲」", [{"name": "甲"}, {"name": "乙"}]) == []
    assert relations_adapter.extract_text_relations("正文。", [{"name": "标题词"}]) == []


# ---------- ② build 响应回传已决策记录 ----------


def test_build_response_carries_decided_records(svc):
    gid = _create(svc, kb_id="kb_rel_records")
    alice = entity_id(gid, "concept", normalize_name("Alice"))
    bob = entity_id(gid, "concept", normalize_name("Bob"))
    result = svc.build_from_records(
        gid,
        entities=[
            {"name": "Alice", "type": "concept", "docId": "doc1"},
            {"name": "Bob", "type": "concept", "docId": "doc1"},
        ],
        relations=[{"type": "related_to", "source": alice, "target": bob, "docId": "doc1"}],
        doc_id="doc1",
    )

    assert {"graphId", "entityCount", "relationCount", "semantica"} <= set(result)
    assert result["entities"] and result["relations"]

    entities = [EntityRecord.from_dict(item, graph_id=gid) for item in result["entities"]]
    relations = [RelationRecord.from_dict(item, graph_id=gid) for item in result["relations"]]
    assert {item.entity_id for item in entities} == {alice, bob}
    assert relations[0].source_entity_id == alice
    assert relations[0].target_entity_id == bob
    assert relations[0].relation_type == "related_to"
    # 响应记录与本地落库结果一致（core 侧落库不产生第二套派生）
    assert {item.entity_id for item in entities} == {
        item.entity_id for item in svc.store.list_entities(gid)
    }
    assert {item.relation_id for item in relations} == {
        item.relation_id for item in svc.store.list_relations(gid)
    }
    # merge 走同一链路，同样回传记录
    merged = svc.merge_records(
        gid, entities=[{"name": "Alice", "type": "concept", "docId": "doc2"}], doc_id="doc2"
    )
    assert [item["name"] for item in merged["entities"]] == ["Alice"]
    assert merged["relations"] == []
