# semantica-graph-sdk

Semantica Graph Engine **独立承载服务**（图引擎作为独立进程/容器对外提供）的应用集成 SDK。**v0.1.0**，
依赖 `httpx`（传输）与 `ikc-sdk-lib==0.7.0`（图谱资产 DTO 的形状单一来源：`EntityView`/`RelationView`/
`GraphPageResult`/`EngineJobView` 等），与主包 `graph-engine`（服务端）解耦、不 import 服务端模块。

DTO 经 sdk 模型校验后序列化：分页壳含 `totalPages`；作业对象 `status` 为引擎本地态，`taskStatus` 为
映射后的外部态（`SUCCEEDED`/`FAILED`…，未知态 fail-closed=FAILED）；`JobData.finished` 按外部态终态判定。

设计文档：`docs/独立承载服务与SDK集成设计.md`（仓库根目录）。

## 安装

```bash
pip install semantica-graph-sdk
```

本地开发调试可 `pip install sdk/python`，测试依赖 `pytest`（`pip install "sdk/python[dev]"`）。

## 快速开始（同步）

```python
from semantica_graph_sdk import GraphEngineClient

client = GraphEngineClient(
    base_url="http://127.0.0.1:18010",   # 独立承载服务地址（本地 serve / Docker 入口 18180）
    token="<TOKEN>",                      # 可选；服务端启用鉴权/网关注入时使用
)

# 1. 创建图谱（graphSchema 校验由服务端执行）
graph = client.graphs.create(
    kbId="kb_demo",
    name="产品知识图谱",
    graphSchema={
        "entityTypes": [{"type": "person"}, {"type": "org"}],
        "relationTypes": [{"type": "works_at", "sourceTypes": ["person"], "targetTypes": ["org"]}],
    },
)

# 2. 建图：记录建图（稳定 ID + 证据 + 置信度）或文本建图
client.graphs.build(
    graph.graphId,
    docId="doc_1",
    entities=[{"name": "Alice", "type": "person"}, {"name": "Acme", "type": "org"}],
    relations=[{"type": "works_at", "sourceEntityId": "ent_a", "targetEntityId": "ent_b"}],
)
built = client.graphs.build(graph.graphId, docId="doc_2", title="手册", text="「Alice」加入「Acme」")
print(built.entityCount, built.semantica.semantica)

# 3. 查询 / 分析 / 导出 / SPARQL / 语义检索
stat = client.graphs.stat(graph.graphId)
nodes = client.graphs.nodes(graph.graphId, entityType="person", pageSize=50)
edges = client.graphs.edges(graph.graphId, relationType="works_at")
neighbors = client.graphs.neighbors(graph.graphId, entityId=nodes.items[0].entityId, depth=1)
paths = client.graphs.paths(graph.graphId, sourceEntityId="ent_a", targetEntityId="ent_b", maxDepth=3)
analytics = client.graphs.analytics(graph.graphId)
export = client.graphs.export(graph.graphId, format="turtle")
sparql = client.graphs.sparql(graph.graphId, query="SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 5")
search = client.graphs.search(graph.graphId, query="Alice 在哪工作", topK=5)
print(stat.schemaCoverage.overall, len(sparql.bindings), search.semantica)

# 4. 异步任务（async_=True 登记 job；Celery 未启用时可手动执行或轮询）
job = client.graphs.build(graph.graphId, text="「异步」", async_=True)
job = client.jobs.run(job.jobId)        # 或 client.jobs.wait(job.jobId)
print(job.status, job.result)

client.close()
```

## 异步客户端

```python
import asyncio
from semantica_graph_sdk import AsyncGraphEngineClient

async def main():
    async with AsyncGraphEngineClient(base_url="http://127.0.0.1:18010") as client:
        graph = await client.graphs.create(kbId="kb_async", name="异步示例")
        stat = await client.graphs.stat(graph.graphId)
        print(stat.nodeCount)

asyncio.run(main())
```

同步与异步客户端共享同一套模型与错误映射；`request`/`raw`/`fetch_openapi`/`health` 低层调用与重试语义一致。

## 环境变量引导

```bash
export SEMANTICA_GRAPH_BASE_URL=http://127.0.0.1:18010
export SEMANTICA_GRAPH_TOKEN=<token>          # 可选
export SEMANTICA_GRAPH_USER_ID=u1             # 可选，身份头 X-User-Id，并作为 ownerId 默认值
export SEMANTICA_GRAPH_TENANT_ID=t1           # 可选，身份头 X-Tenant-Id，并作为 tenantId 默认值
export SEMANTICA_GRAPH_ROLES=km_admin,viewer  # 可选，身份头 X-User-Roles
```

```python
from semantica_graph_sdk import client_from_env, async_client_from_env

client = client_from_env()   # 等价：GraphEngineClient(base_url=os.environ["SEMANTICA_GRAPH_BASE_URL"], ...)
```

`CallerIdentity` 的 `tenant_id`/`user_id` 会在请求缺省时补齐 `tenantId`/`ownerId`
（图引擎的租户/归属是请求参数），显式入参始终优先。

## 领域方法总览

- `client.graphs`：`create / list / get / delete / build / merge / deprecate_doc / stat / nodes / edges /
  neighbors / paths / analytics / export / sparql / search / index_status`
- `client.jobs`：`run / get / list / wait`
- 低层：`request(method, path, ...)` / `raw(...)`（不抛业务异常）/ `fetch_openapi()` / `health()`

## 异常层级

```
GraphEngineError
├── GraphEngineTransportError         # 传输层（含 traceId）
│   ├── GraphEngineConnectionError
│   ├── GraphEngineTimeoutError
│   ├── GraphEngineProtocolError      # 响应不符统一壳协议
│   └── GraphEngineHTTPStatusError    # 非 2xx 且非统一壳
└── GraphEngineAPIError               # 业务异常（errCode/errMsg/traceId）
    ├── GraphEngineValidationError    # 200001 参数非法
    ├── GraphEngineNotFoundError      # 200404 资源不存在
    ├── GraphEngineConflictError      # 200409 冲突
    ├── GraphEngineSystemError        # 200500 系统错误
    └── GraphEngineBusinessError      # 其他错误码兜底
```

## 联调冒烟

先启动独立承载服务：

```bash
graph-engine serve http                    # 默认 18010
# 或 docker compose up -d                 # 单镜像栈入口 18180（HTTP）/ 18151（gRPC）
```

```bash
# 同步全链路：健康检查 → 建图 → 查询/分析/导出/检索 → 异步任务 → 清理
python sdk/python/examples/quickstart.py

# 异步客户端示例
python sdk/python/examples/async_quickstart.py
```

## 自测

```bash
cd sdk/python && PYTHONPATH=. python -m pytest tests -q
```

> 测试基于 `httpx.MockTransport`，无需起真实服务。
