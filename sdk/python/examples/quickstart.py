"""semantica-graph-sdk 同步全链路冒烟：建图 → 建图/合并 → 查询/分析/导出/检索 → 异步 job → 清理。

前置：图引擎服务已启动（默认 127.0.0.1:18010；Docker 单镜像栈入口 18180）。
运行：python sdk/python/examples/quickstart.py [base_url]
"""

from __future__ import annotations

import sys
from pathlib import Path

from semantica_graph_sdk import GraphEngineClient, GraphEngineError

ROOT = Path(__file__).resolve().parents[3]  # sdk/python/examples -> 仓库根

SCHEMA = {
    "entityTypes": [{"type": "person"}, {"type": "org"}, {"type": "concept"}],
    "relationTypes": [
        {"type": "works_at", "sourceTypes": ["person"], "targetTypes": ["org"]},
        {"type": "related_to"},
    ],
}


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18010"
    suffix = sys.argv[2] if len(sys.argv) > 2 else "sdk"
    with GraphEngineClient(base_url=base_url) as client:
        # 0. 存活探测
        print(f"[0] health -> {client.health()}")

        # 1. 创建图谱（graphId 由 graphSchema + kbId 校验后生成/派生）
        graph = client.graphs.create(
            kbId=f"kb_sdk_{suffix}",
            name="SDK 联调冒烟图",
            graphSchema=SCHEMA,
        )
        gid = graph.graphId
        print(f"[1] create -> graphId={gid} status={graph.status}")

        # 2. 记录建图（稳定 ID + 证据 + 置信度由服务端语义保证）
        built = client.graphs.build(
            gid,
            docId="doc_1",
            entities=[
                {"name": "Alice", "type": "person", "docId": "doc_1", "confidence": 0.9},
                {"name": "Acme", "type": "org", "docId": "doc_1"},
            ],
            relations=[
                {"type": "works_at", "sourceEntityId": "ent_alice", "targetEntityId": "ent_acme"},
            ],
        )
        print(f"[2] build(records) -> entityCount={built.entityCount} relationCount={built.relationCount}")

        # 3. 文本建图（标题 + 「引号词」候选实体；llm=True 时尝试 semantica LLM 增强）
        text_doc = (ROOT / "docs" / "解决方案.md").read_text(encoding="utf-8")[:2000]
        text_built = client.graphs.build(gid, text=text_doc, title="解决方案摘要", docId="doc_2", llm=False)
        print(f"[3] build(text) -> entityCount={text_built.entityCount} semantica={text_built.semantica.semantica}")

        # 4. 增量合并 + 按文档废弃
        merged = client.graphs.merge(
            gid,
            docId="doc_2",
            entities=[{"name": "Bob", "type": "person", "docId": "doc_2"}],
        )
        print(f"[4] merge -> entityCount={merged.entityCount} deprecated={merged.deprecated}")
        deprecated = client.graphs.deprecate_doc(gid, docId="doc_1")
        print(f"[5] deprecate-doc -> deprecated={deprecated.deprecated}")

        # 5. 查询：统计 / 节点 / 边 / 邻域 / 路径 / 分析
        stat = client.graphs.stat(gid)
        print(f"[6] stat -> nodes={stat.nodeCount} edges={stat.edgeCount} coverage={stat.schemaCoverage.overall}")

        nodes = client.graphs.nodes(gid, pageSize=5)
        print(f"[7] nodes -> total={nodes.total} first={nodes.items[0].name if nodes.items else '-'}")

        edges = client.graphs.edges(gid, pageSize=5)
        print(f"[8] edges -> total={edges.total}")

        if nodes.items:
            neighbors = client.graphs.neighbors(gid, entityId=nodes.items[0].entityId, depth=1)
            print(f"[9] neighbors -> nodes={len(neighbors.nodes)} edges={len(neighbors.edges)}")
            if len(nodes.items) > 1:
                paths = client.graphs.paths(
                    gid,
                    sourceEntityId=nodes.items[0].entityId,
                    targetEntityId=nodes.items[1].entityId,
                )
                print(f"[10] paths -> total={paths.total}")

        analytics = client.graphs.analytics(gid)
        print(f"[11] analytics -> keys={sorted(analytics.analysis)[:6]}")

        # 6. 导出与 RDF/SPARQL（RDF 能力缺失时服务端返回 200001，按业务错误捕获）
        export = client.graphs.export(gid, format="jsonl")
        print(f"[12] export(jsonl) -> total={export.total} bytes={len(export.content)}")
        try:
            sparql = client.graphs.sparql(gid, query="SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 5")
            print(f"[13] sparql -> rows={len(sparql.bindings)} truncated={sparql.truncated}")
        except GraphEngineError as exc:
            print(f"[13] sparql -> 不可用（{exc}）")

        # 7. 语义检索与索引状态（检索链不可用时为确定降级结果）
        search = client.graphs.search(gid, query="Alice", topK=5)
        print(f"[14] search -> semantica={search.semantica} total={search.total}")
        print(f"[15] index-status -> {client.graphs.index_status(gid).to_dict()}")

        # 8. 异步任务：登记 → 手动执行 → 轮询
        job = client.graphs.build(gid, text="「异步」建图", title="异步示例", docId="doc_async", async_=True)
        print(f"[16] submit async job -> jobId={job.jobId} status={job.status}")
        job = client.jobs.run(job.jobId)
        print(f"[17] run job -> status={job.status} task={job.task}")

        # 9. 运行时自检 + 清理
        catalog = client.fetch_openapi()
        print(f"[18] openapi -> title={catalog.get('info', {}).get('title')} paths={len(catalog.get('paths', {}))}")
        deleted = client.graphs.delete(gid)
        print(f"[19] delete -> graphId={deleted.graphId} deleted={deleted.deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
