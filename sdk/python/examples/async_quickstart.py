"""semantica-graph-sdk 异步客户端示例：并发建图 + 任务轮询。

前置：图引擎服务已启动（默认 127.0.0.1:18010）。
运行：python sdk/python/examples/async_quickstart.py [base_url]
"""

from __future__ import annotations

import asyncio
import sys

from semantica_graph_sdk import AsyncGraphEngineClient


async def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18010"
    async with AsyncGraphEngineClient(base_url=base_url) as client:
        graph = await client.graphs.create(kbId="kb_sdk_async", name="异步客户端示例")
        gid = graph.graphId
        print(f"[1] create -> graphId={gid}")

        await asyncio.gather(
            *[
                client.graphs.build(gid, text=f"「并发实体{i}」", title=f"并发文档{i}", docId=f"doc_{i}")
                for i in range(3)
            ]
        )
        stat = await client.graphs.stat(gid)
        print(f"[2] 并发建图 -> nodes={stat.nodeCount}")

        job = await client.graphs.build(gid, text="「异步」", title="异步任务", docId="doc_job", async_=True)
        print(f"[3] submit -> jobId={job.jobId} status={job.status}")
        job = await client.jobs.run(job.jobId)
        print(f"[4] run -> status={job.status}")
        job = await client.jobs.wait(job.jobId, interval=0.2, timeout=10)
        print(f"[5] wait -> status={job.status}")

        deleted = await client.graphs.delete(gid)
        print(f"[6] delete -> deleted={deleted.deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
