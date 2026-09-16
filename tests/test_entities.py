"""规则实体候选抽取（markdown 结构性术语）与「结构性术语驱动关系落地」用例。

覆盖：四类标记（引号/标题/粗体/行内代码）的抽取与顺序、归一化去重、长度与空白过滤、
上限截断，以及 `build_from_text` 在**无引号词**的普通 markdown 上仍能产出实体与关系
（回归：关系抽取只让能在正文定位的实体参与共现，候选不足时边恒为 0）。
"""

from __future__ import annotations

import pytest

from graph_engine.adapters.entities import MAX_CANDIDATES, candidate_terms
from graph_engine.application.service import GraphEngineService
from graph_engine.persistence.sqlite_store import SqliteGraphStore

SCHEMA = {"entityTypes": [{"type": "concept"}], "relationTypes": [{"type": "related_to"}]}

MARKDOWN = """# 平台概览

介绍 `ikc-open-platform` 与 `ikc-sdk-lib`。

- **业务能力**：知识库、文档两类域，模型来自 `ikc_sdk.core`。
- 「接入向导」用于创建。

`这段 含空白` 不作为术语；**这句强调也 不作为术语**。
"""


@pytest.fixture()
def svc(tmp_path):
    return GraphEngineService(SqliteGraphStore(str(tmp_path / "test.db")))


def _create(svc, schema=SCHEMA, kb_id="kb_ent_1"):
    return svc.create_graph(name="实体候选图", kb_id=kb_id, schema=schema)["graphId"]


# ---------- ① 候选抽取口径 ----------


def test_candidates_cover_four_markers_in_document_order():
    terms = candidate_terms(MARKDOWN)
    names = [item["name"] for item in terms]

    assert names == [
        "平台概览",
        "ikc-open-platform",
        "ikc-sdk-lib",
        "业务能力",
        "ikc_sdk.core",
        "接入向导",
    ]
    assert {item["name"]: item["marker"] for item in terms} == {
        "平台概览": "heading",
        "ikc-open-platform": "code",
        "ikc-sdk-lib": "code",
        "业务能力": "bold",
        "ikc_sdk.core": "code",
        "接入向导": "quote",
    }


def test_candidates_dedupe_by_normalized_name():
    # 大小写/标点差异归一到同一术语（与实体稳定 ID 同一口径）：只保留首次出现
    terms = candidate_terms("`Acme` 与 **acme**、`A-C-M-E` 是同一术语。")
    assert [item["name"] for item in terms] == ["Acme"]


def test_candidates_drop_blank_long_and_space_borne_marks():
    long_name = "x" * 41
    # 超长、以及行内代码/粗体中的含空白内容一律丢弃；(c) 归一化后非空 → 保留
    terms = candidate_terms(f"`{long_name}` 与 `a b` 与 **(c)**")
    assert [item["name"] for item in terms] == ["(c)"]
    # 空标记（无正文）不成候选
    assert candidate_terms("`` 与 「」 与 **  **") == []


def test_candidates_are_capped_for_determinism():
    text = " ".join(f"`term{i:03d}`" for i in range(MAX_CANDIDATES + 20))
    terms = candidate_terms(text)
    assert len(terms) == MAX_CANDIDATES
    assert terms[0]["name"] == "term000"


# ---------- ② build_from_text：结构性术语 → 实体 + 关系 ----------


def test_build_from_text_lands_entities_and_edges_without_quotes(svc):
    gid = _create(svc)
    result = svc.build_from_text(gid, text=MARKDOWN, doc_id="doc1", title="平台概览")

    # 标题 + 5 个正文术语（`ikc_sdk.core` 是「业务能力」句内第二个术语）
    assert result["entityCount"] == 6
    assert result["relationCount"] > 0, "结构性术语必须能在同句共现中产边"
    assert svc.stat(gid)["edgeCount"] == result["relationCount"] == svc.stat(gid)["edgeCount"]
    assert svc.stat(gid)["edgeCount"] > 0

    titles = {item["name"] for item in svc.list_nodes(gid)["items"]}
    assert {"ikc-open-platform", "ikc-sdk-lib", "ikc_sdk.core", "业务能力"} <= titles

    # 端点稳定 ID + 证据：`业务能力` 与同句的 `ikc-sdk-lib` / `ikc_sdk.core` 各有边
    edges = svc.list_edges(gid)["items"]
    assert all(item["sourceEntityId"].startswith("ent_") for item in edges)
    assert all(item["evidence"] for item in edges)
    assert all(item["type"] == "related_to" for item in edges)
    assert all(item["properties"]["methods"] == ["rule_cooccurrence"] for item in edges)


def test_build_from_text_keeps_quote_only_behavior(svc):
    """回归：原「引号词」口径不变（无结构性标记时实体数 = 标题 + 引号词）。"""
    gid = _create(svc, kb_id="kb_ent_2")
    result = svc.build_from_text(gid, text="介绍「Alice」与「Acme」。", title="文档标题")
    assert result["entityCount"] == 3
    assert result["relationCount"] == 1


def test_candidate_types_all_follow_schema_first_entity_type(svc):
    gid = _create(svc, kb_id="kb_ent_3")
    svc.build_from_text(gid, text="`alpha` 与 `beta`", doc_id="d1", title="t")
    assert {item["type"] for item in svc.list_nodes(gid)["items"]} == {"concept"}
