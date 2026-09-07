# Semantica Graph Engine

把已安装的 `semantica`（0.6.5）封装为可对外完整服务的图引擎，统一提供
**HTTP / gRPC / Celery / MCP / CLI** 五类接口；open-ikc 的图谱服务能力可消费本引擎。

- 设计方案：`docs/解决方案.md`
- gRPC 权威契约：`proto/graph/v1/graph.proto`
- 配置样例：`config/engine.example.yaml`（全部可用环境变量覆盖）

## 快速开始

```bash
# 安装（复用当前 venv，无新增外部依赖）
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

## 核心设计

- 分层：`interfaces`（五面）→ `application.GraphEngineService`（用例）→ `domain`（稳定 ID/schema/合并规则）→ `adapters`（semantica + SQLite）。
- 五面共用同一 application 层，语义一致；`async=true` 建图登记 job 后由 Celery worker 执行。
- 稳定 ID 与增量合并/证据/置信度/schema 覆盖率语义**兼容 open-ikc 现有实现**，可零成本替换其进程内 `GraphStore`。
- semantica 集成采用守卫式导入：`GraphBuilder` 建图、`GraphAnalyzer` 分析、`export` 导出；`semantica.context/vector_store` 当前因 pinecone 包冲突不可用，引擎不依赖该链路（修复：`pip install "pinecone>=5"` 后可切换 semantica 原生 MCP/检索能力）。

## 测试

```bash
# 首次安装测试依赖（httpx2）
.venv/bin/pip install -e '.[test]' --no-build-isolation
.venv/bin/python -m pytest tests -q
```

## Docker 部署（单镜像 + HAProxy 反向代理）

- 构建统一镜像（图引擎 + HAProxy 代理层同容器）：`bash scripts/build_docker.sh`
- 启动：`docker compose up -d`（HAProxy 对外入口 HTTP `18180` / gRPC `18151` / stats `8406`）
- 详细说明：`docs/Docker部署与HAProxy.md`；冒烟验证：`bash scripts/docker_smoke.sh`
