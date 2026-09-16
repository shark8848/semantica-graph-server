# Semantica Graph Engine

把已安装的 `semantica`（0.6.5）封装为可对外完整服务的图引擎，统一提供
**HTTP / gRPC / Celery / MCP / CLI** 五类接口；open-ikc 的图谱服务能力可消费本引擎。

- 设计方案：`docs/解决方案.md`
- gRPC 权威契约：`proto/graph/v1/graph.proto`
- 配置样例：`config/engine.example.yaml`（全部可用环境变量覆盖）

## 快速开始

```bash
# 安装（复用当前 venv 已装包；RDF/SPARQL 依赖 pyoxigraph 已随 semantica extra 就绪）
.venv/bin/pip install -e . --no-build-isolation

# 启动 HTTP 服务（默认 18010）
graph-engine serve http

# CLI 走通全链路
graph-engine graph create --name 测试图 --kb-id kb_demo \
  --schema '{"entityTypes":[{"type":"person"}],"relationTypes":[]}'
graph-engine graph build graph_xxx --records '{"entities":[{"name":"Alice","type":"person","docId":"d1"}],"relations":[]}'
graph-engine graph stat graph_xxx
graph-engine graph export graph_xxx
```

## 五类接口

| 协议面 | 入口 | 说明 |
| --- | --- | --- |
| HTTP | `graph-engine serve http` | FastAPI，`/api/v1/graph/*`，envelope 对齐 open-ikc |
| gRPC | `graph-engine serve grpc` | `graph.v1.GraphService`，动态 descriptor 实现（离线无 grpc-tools 环境） |
| Celery | `graph-engine serve worker` | 任务 `graph_engine.build / merge / deprecate_doc / export`，broker 默认 redis |
| MCP | `graph-engine serve mcp` | stdio JSON-RPC，`initialize / tools/list / tools/call` |
| CLI | `graph-engine ...` | typer，退出码 0/1/6 约定同 open-ikc `ikc` |

## Python SDK

应用侧集成推荐直接用 SDK（`sdk/python`，独立打包 `semantica-graph-sdk` v0.1.0，依赖 `httpx` + `ikc-sdk-lib`；
已发布 PyPI：`pip install "semantica-graph-sdk==0.1.0"`）：

```python
from semantica_graph_sdk import GraphEngineClient

with GraphEngineClient("http://127.0.0.1:18010") as client:
    graph = client.graphs.create(kbId="kb_demo", name="演示图")
    client.graphs.build(graph.graphId, docId="d1", entities=[{"name": "Alice", "type": "person"}])
    print(client.graphs.stat(graph.graphId).nodeCount)
```

- 设计文档：`docs/独立承载服务与SDK集成设计.md`；使用说明：`sdk/python/README.md`
- 覆盖 `graphs`（CRUD/建图合并/统计/查询/路径/分析/导出/SPARQL/检索）与 `jobs`（run/get/list/wait），
  同步 + 异步双客户端，统一 envelope/错误码/traceId 语义。
- 自测：`cd sdk/python && PYTHONPATH=. python -m pytest tests -q`；联调：`python sdk/python/examples/quickstart.py`

## 核心设计

- 分层：`interfaces`（五面）→ `application.GraphEngineService`（用例）→ `domain`（稳定 ID/schema/合并规则）→ `adapters`（semantica + SQLite）。
- 线缆形状单一来源：`ikc-sdk-lib==0.7.0`（`ikc_sdk.core.models.graph` 图谱资产 / `core.api.graph.*` G 域接口 /
  `core.trace` 23 位 traceId / `EngineJobView` 作业视图与状态映射）——引擎只做「行 → 视图」映射与校验，
  分页壳含 `totalPages`，作业视图保留本地态 `status` 并给出外态 `taskStatus`（契约测试守护）。
- 五面共用同一 application 层，语义一致；`async=true` 建图登记 job 后由 Celery worker 执行。
  终态由 `celery_app._run_job` 统一回写：成功 `success` + `result`，失败 `failed` + `error`
  （异常仍向上抛）——只在成功时回写会让失败作业停在 `pending`。
- 稳定 ID 与增量合并/证据/置信度/schema 覆盖率语义**兼容 open-ikc 现有实现**，可零成本替换其进程内 `GraphStore`。
- semantica 集成采用守卫式导入：`GraphBuilder` 建图、`GraphAnalyzer` 分析、`export` 导出；`semantica.context/vector_store` 当前因 pinecone 桩包冲突不可用，引擎不依赖该链路（修复路径见 `docs/方案-原生MCP检索接入.md`：先卸载改名桩 `pinecone-client`，再装新发布名 `pinecone>=6,<7`）。

## 测试

```bash
# 首次安装测试依赖（httpx2）
.venv/bin/pip install -e '.[test]' --no-build-isolation
.venv/bin/python -m pytest tests -q
```

## Docker 部署（单镜像 + HAProxy 反向代理）

- 构建统一镜像（图引擎 + HAProxy 代理层同容器）：`bash scripts/build_docker.sh`
- 启动：`docker compose up -d`（HAProxy 对外入口 HTTP `18180` / gRPC `18151` / stats `8406`；compose 栈默认
  启用容器内 Celery worker，broker/backend 指向**宿主本地 Redis**，`async=true` 建图由 worker 异步执行，
  `GET /api/v1/graph/jobs/{id}` 轮询）
- 部署手册（本机构建 → 启动验证 → 离线镜像导出/导入 → 非 compose `docker run` → 升级回滚 → 排障）：
  `docs/本地Docker部署手册.md`；速查版：`docs/Docker部署与HAProxy.md`
- 冒烟验证：`bash scripts/docker_smoke.sh`（HTTP/gRPC/stats/回环隔离/非 root/Celery 端到端/MCP 与 CLI 八项）
