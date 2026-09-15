# Semantica Graph Engine 独立承载服务与 SDK 集成设计（semantica-graph-sdk）

> 版本：1.0.0（文档版本；SDK 包版本 0.1.0）
> 状态：已实现并自测通过（`sdk/python`，`pytest` 52 passed）
> 发布形态：SDK 位于本仓库 `sdk/python/`，独立打包（dist 名 `semantica-graph-sdk`，import 名
> `semantica_graph_sdk`，**仅依赖 `httpx`**），与主包 `graph-engine`（服务端）解耦，可按需独立发版。
> 适用范围：面向外部应用的集成客户端 SDK；图引擎作为**独立承载服务**（独立进程/容器）运行——
> 本地 `graph-engine serve http`（默认 HTTP 18010）或 Docker 单镜像栈（HAProxy 入口 18180）。
> 参考实现：`/home/sharkyai/openwiki-server/sdk/python`（`openwiki-server-sdk` v0.1.0），
> 沿用其**同协议解耦、同 envelope、同 traceId/认证头约定、同异常层级与重试语义**的设计口径。

## 1. 背景与目标

图引擎以五类接口（HTTP / gRPC / Celery / MCP / CLI）对外提供图谱能力：图谱 CRUD、建图与增量合并
（build/merge/deprecate-doc）、查询与结构分析（stat/nodes/edges/neighbors/paths/analytics）、导出与
RDF/SPARQL 视图、语义检索、异步任务（jobs）。应用侧直接调 HTTP 需要自行处理统一响应壳、traceId、
错误码映射、重试与超时，且要手工拼装 camelCase 字段，成本高且易错。本 SDK 的目标：

1. 把全部图谱能力封装为类型安全、可读的 Python 调用，屏蔽 HTTP 细节。
2. 内置 23 位数字 traceId 生成/复用、可选 Bearer 认证、身份头透传
   （`X-User-Id` / `X-Tenant-Id` / `X-User-Roles`），并把身份映射为图谱归属默认值（`tenantId`/`ownerId`）。
3. 统一错误码 → 异常层级映射，业务失败（`errCode != 0`）与传输失败可区分捕获。
4. 与服务端实现完全解耦：SDK 只依赖对外 HTTP 协议（`/api/v1/graph/*` + `/health` + `/openapi.json`），
   不 import `graph_engine` 任何内部代码。
5. 提供同步（`GraphEngineClient`）与异步（`AsyncGraphEngineClient`）双客户端，共享同一套模型。

## 2. 边界与冲突隔离

| 维度 | 约定 |
| --- | --- |
| 写范围 | 仅 `sdk/python/`、本文档、README/看板等文档；**不修改** `graph_engine/`（除 HTTP 面补齐检索端点）、`proto/` |
| 依赖 | 仅第三方 `httpx`；不引入 fastapi/pydantic/typer，不 import 服务端内部模块 |
| 命名 | dist 名 `semantica-graph-sdk`，import 名 `semantica_graph_sdk`，与服务端包 `graph_engine` 区分 |
| 耦合面 | 仅 HTTP 协议：路径 `/api/v1/graph/*`、统一响应壳（`{traceId,errCode,errMsg,data}`）、
  错误码（`0 / 200001 / 200404 / 200409 / 200500`）、Header 约定（`X-Trace-Id`） |
| 运行时契约 | 可选诊断方法 `fetch_openapi()` / `health()`，以 FastAPI 自动生成的 `/openapi.json` 与 `/health`
  为运行时接口目录与存活自检 |
| 部署拓扑 | SDK 面向「独立承载服务」：本地 `serve http`（18010）或 Docker 单镜像栈（HAProxy 18180），
  与消费方进程完全解耦 |

## 3. 包结构与命名

```
sdk/python/
  pyproject.toml          # 独立打包：semantica-graph-sdk，核心依赖 httpx；可选 dev(pytest)
  README.md               # SDK 使用说明（安装 / 快速开始 / 环境变量 / 联调冒烟）
  semantica_graph_sdk/
    __init__.py           # 导出 GraphEngineClient / AsyncGraphEngineClient / 异常 / 模型
    _version.py           # SDK 版本号（0.1.0）
    _bootstrap.py         # client_from_env / async_client_from_env：环境变量 → 客户端
    client.py             # 主客户端 + graphs/jobs 域子客户端 + raw 逃生口 + fetch_openapi/health
    async_client.py       # 异步客户端（httpx.AsyncClient），领域方法同同步客户端
    transport.py          # 同步 HTTP 传输：超时、重试、统一壳解析、身份默认值补齐
    transport_async.py    # 异步 HTTP 传输：与同步共享超时/重试/解析语义
    envelope.py           # 统一响应壳解析（errCode/errMsg/data/traceId，成功码 "0"）
    errors.py             # 异常层级 + 错误码映射表（200001/200404/200409/200500）
    trace.py              # 23 位数字 traceId 生成/复用
    headers.py            # 认证头 + 身份头构建（CallerIdentity）+ identity_defaults
    models/
      graph.py            # 图谱数据模型（GraphMeta/StatResult/BuildResult/Entity/Relation/
                          #   NeighborData/PathListData/SparqlResult/SearchData/JobData/...）
  examples/
    quickstart.py         # 同步全链路冒烟：建图 → 合并/废弃 → 查询/分析/导出/检索 → 异步 job → 清理
    async_quickstart.py   # 异步客户端示例（并发建图 + 任务轮询）
  tests/                  # SDK 自测（httpx.MockTransport，无需起服务）
```

命名约定（与 openwiki-server-sdk / open-ikc SDK 一致）：

- 方法按领域子客户端组织：`client.graphs.create/list/get/delete/build/merge/deprecate_doc/stat/nodes/
  edges/neighbors/paths/analytics/export/sparql/search/index_status`、`client.jobs.run/get/list/wait`；
  方法名蛇形，请求参数与响应字段**沿用服务端 API 的 camelCase**（`graphId`、`kbId`、`entityType`、
  `sourceEntityId`…），与接口 1:1 对应。
- 数据模型用 `dataclass` + `from_dict()` 构造；未知字段收进 `extra: dict` 透传，不阻断解析。
- 每个模型带 `to_dict()`，方便日志与二次加工。

## 4. 核心对象模型

### 4.1 客户端配置

```python
client = GraphEngineClient(
    base_url="http://127.0.0.1:18010",   # 独立承载服务地址（必填）
    token=None,                           # 可选；服务端/网关启用 Bearer 鉴权时填写
    timeout=(5.0, 60.0),                  # (connect, read) 秒；或单值；默认 5/60/60/5
    max_retries=2,                        # 幂等请求的重试次数（指数退避 + 抖动）
    identity=CallerIdentity(user_id="u1", tenant_id="t1", roles=["km_admin"]),
    extra_headers=None,                   # 附加请求头（优先级最高）
    trace_id=None,                        # 固定 traceId（不传则每请求生成 23 位数字）
    http_client=None,                     # 复用外部 httpx.Client（如 MockTransport 单测）
)
```

### 4.2 身份上下文（CallerIdentity）

| 字段 | 请求头 | 图谱归属默认值 | 说明 |
| --- | --- | --- | --- |
| `user_id` | `X-User-Id` | `ownerId` | 调用方用户；创建/列表图谱的归属过滤 |
| `tenant_id` | `X-Tenant-Id` | `tenantId` | 租户隔离；所有查询按 namespace 隔离 |
| `roles` | `X-User-Roles`（逗号连接） | — | 角色列表，供网关/服务端鉴权 |

图引擎的租户/归属是**请求参数**而非请求头，因此 SDK 在请求缺省时按身份补齐 `tenantId`/`ownerId`
（POST 补 body、GET 补 query），显式入参始终优先；请求头同时透传身份，便于网关/审计链路对齐。

### 4.3 数据模型

| 模型 | 对应接口 | 关键字段 |
| --- | --- | --- |
| `GraphMeta` | `POST/GET/DELETE /graphs` | `graphId / name / kbId / tenantId / ownerId / graphSchema / status / createdAt / updatedAt` |
| `GraphListData` | `GET /graphs` | `total / items[]` |
| `DeleteResult` | `DELETE /graphs/{id}` | `graphId / deleted` |
| `BuildResult` | `POST .../build`、`.../merge` | `entityCount / relationCount / semantica(SemanticaMeta) / llm / deprecated` |
| `DeprecateResult` | `POST .../deprecate-doc` | `graphId / docId / deprecated` |
| `StatResult` | `GET .../stat` | `nodeCount / edgeCount / entityTypes[] / relationTypes[] / schemaCoverage` |
| `Entity` / `EntityListData` | `GET .../nodes`、neighbors | `entityId / type / name / properties / aliases / evidence / confidence / status` |
| `Relation` / `RelationListData` | `GET .../edges`、neighbors | `relationId / type / sourceEntityId / targetEntityId / properties / evidence / confidence` |
| `NeighborData` | `GET .../neighbors` | `center / nodes[] / edges[] / depth` |
| `GraphPath` / `PathListData` | `GET .../paths` | `entityIds[] / nodeNames[] / length` |
| `AnalyticsData` | `GET .../analytics` | `analysis`（semantica GraphAnalyzer 结果，原样透传） |
| `ExportResult` | `GET .../export` | `format / total / content`（jsonl/json/turtle/nt/nq/rdfxml） |
| `SparqlResult` | `POST .../sparql` | `success / bindings[] / variables[] / triples[] / metadata / rowLimit / truncated`，附 `boolean` 属性（ASK） |
| `SearchHit` / `SearchData` | `GET .../search` | `id / kind / type / name / docId / score / confidence / evidence`；`semantica/total/reason/hits[]` |
| `IndexStatusData` | `GET .../index-status` | `semantica / backend / reason` |
| `JobData` / `JobListData` | `POST .../build?async`、`/jobs/*` | `jobId / graphId / task / status / payload / result / error`，附 `finished` 属性 |

## 5. 方法总览

### 5.1 `client.graphs`

| 方法 | HTTP | 说明 |
| --- | --- | --- |
| `create(*, graphId="", name="", kbId="", tenantId="", ownerId="", graphSchema=None)` | `POST /api/v1/graph/graphs` | 创建图谱（`graphSchema` 校验由服务端执行） |
| `list(*, tenantId="", ownerId="")` | `GET /api/v1/graph/graphs` | 图谱列表（身份可补齐过滤条件） |
| `get(graphId)` / `delete(graphId)` | `GET` / `DELETE /graphs/{id}` | 元信息 / 级联删除 |
| `build(graphId, *, text="", title="", docId="", llm=None, entities=None, relations=None, async_=False)` | `POST /graphs/{id}/build` | 文本或记录建图；`async_=True` 返回 `JobData` |
| `merge(graphId, *, entities=None, relations=None, docId="")` | `POST /graphs/{id}/merge` | 增量合并（对齐 open-ikc `build_from_doc`） |
| `deprecate_doc(graphId, *, docId)` | `POST /graphs/{id}/deprecate-doc` | 按 docId 增量废弃 |
| `stat(graphId)` | `GET /graphs/{id}/stat` | 统计 + schema 覆盖率 |
| `nodes(...)` / `edges(...)` | `GET /graphs/{id}/nodes` / `/edges` | 分页查询（pageSize 服务端上限 200） |
| `neighbors(graphId, *, entityId, depth=1)` | `GET /graphs/{id}/neighbors` | 邻域（depth 1/2） |
| `paths(graphId, *, sourceEntityId, targetEntityId, maxDepth=5)` | `GET /graphs/{id}/paths` | 最短路径 |
| `analytics(graphId)` | `GET /graphs/{id}/analytics` | 图结构分析（semantica 守卫式） |
| `export(graphId, *, format="jsonl")` | `GET /graphs/{id}/export` | 导出；RDF 格式需服务端 pyoxigraph |
| `sparql(graphId, *, query, limit=0)` | `POST /graphs/{id}/sparql` | SPARQL（白名单 + 超时/行数护栏） |
| `search(graphId, *, query, topK=10)` | `GET /graphs/{id}/search` | 语义检索（不可用时确定降级） |
| `index_status(graphId)` | `GET /graphs/{id}/index-status` | 检索/向量索引可用状态 |

### 5.2 `client.jobs`

| 方法 | HTTP | 说明 |
| --- | --- | --- |
| `run(jobId)` | `POST /api/v1/graph/jobs/{id}/run` | 同步执行已登记任务（broker 不可达时的确定性回退） |
| `get(jobId)` | `GET /api/v1/graph/jobs/{id}` | 任务状态/结果 |
| `list(*, graphId="", limit=20)` | `GET /api/v1/graph/jobs` | 任务列表 |
| `wait(jobId, *, interval=0.5, timeout=60)` | 轮询 `get` | 直到 `success`/`failed` 或超时（超时返回最后一次观测，不抛异常） |

### 5.3 低层逃生口

- `request(method, path, *, path_params, params, body)`：返回 `Envelope`，业务错误抛异常。
- `raw(...)`：返回原始 `Envelope`，**不抛业务异常**（需要自行判断 `err_code`）。
- `fetch_openapi()` / `health()`：运行时接口目录与存活自检（裸 JSON，非统一壳）。

## 6. 错误码与异常层级

```
GraphEngineError
├── GraphEngineTransportError            # 传输层（含 trace_id）
│   ├── GraphEngineConnectionError       # 连接失败/重试耗尽
│   ├── GraphEngineTimeoutError          # 超时（connect/read）
│   ├── GraphEngineProtocolError         # 响应不是合法 JSON / 缺 errCode
│   └── GraphEngineHTTPStatusError       # 非 2xx 且无法解析统一壳（含 status_code/body）
└── GraphEngineAPIError                  # 业务异常（err_code/err_msg/trace_id）
    ├── GraphEngineValidationError       # 200001 参数非法
    ├── GraphEngineNotFoundError         # 200404 不存在
    ├── GraphEngineConflictError         # 200409 冲突
    ├── GraphEngineSystemError           # 200500 内部错误
    └── GraphEngineBusinessError         # 其他错误码兜底
```

- HTTP 4xx/5xx 但响应体是统一壳 → 按 `errCode` 映射业务异常（与 200 响应一致）。
- 另附 `exception_from_code(err_code, err_msg, trace_id)` 供调用方按需手工映射。

## 7. 传输语义

| 维度 | 约定 |
| --- | --- |
| traceId | 每请求生成 23 位纯数字（13 位毫秒时间戳 + 10 位随机），经 `X-Trace-Id`/`X-Request-Id` 下发；
  可传 `trace_id=` 固定复用；业务异常与传输异常均携带 `trace_id` |
| 重试 | `GET/HEAD/OPTIONS` 幂等可重试；`POST` 仅当 body 显式含 `reqId` 时重试；`502/503/504` 触发重试；
  指数退避 `min(0.5*2^n, 4s)` + 抖动，默认最多 2 次 |
| 超时 | 默认 `connect=5s / read=60s / write=60s / pool=5s`；可用单值或 `(connect, read)` 覆写 |
| 连接 | 同步 `httpx.Client`、异步 `httpx.AsyncClient`；支持外部注入（连接池复用、MockTransport 单测） |
| 关闭 | `close()` / 上下文管理器（异步为 `async with`）；外部注入的 client 不被 SDK 关闭 |

## 8. 与服务端接口的对应关系

SDK ↔ HTTP 一一对应（见 §5）。为使命中面完整，服务端 HTTP 面本次补齐两个端点（此前仅有
MCP/application 层能力）：

- `GET /api/v1/graph/graphs/{graph_id}/search?query=&topK=` → `GraphEngineService.semantic_search`
- `GET /api/v1/graph/graphs/{graph_id}/index-status` → `GraphEngineService.index_status`

两者沿用 `_trace` + `_handle` 风格与统一响应壳；图谱不存在时返回 `errCode=200404`（HTTP 404），
检索链不可用时返回确定降级结果（`semantica=false`、`hits=[]`、`reason` 说明），不报错。

## 9. 发布与版本

- 版本：SDK `0.1.0`，与服务端 `graph-engine 0.1.0` 独立演进（SDK 语义版本随 HTTP 契约变化）。
- 打包：`sdk/python/pyproject.toml`（setuptools，`packages.find include = ["semantica_graph_sdk*"]`）。
- 安装：`pip install sdk/python`（仓库内）；后续如需对外发布，走 PyPI（与 openwiki-server-sdk 同流程）。
- 兼容性：新增模型字段向后兼容（未知字段进 `extra`）；错误码新增时映射为 `GraphEngineBusinessError`，
  不破坏既有捕获分支。

## 10. 自测与联调冒烟

```bash
# SDK 自测（httpx.MockTransport，无需起服务）
cd sdk/python && PYTHONPATH=. python -m pytest tests -q     # 52 passed

# 服务端全量回归
.venv/bin/python -m pytest tests -q

# 联调冒烟（需先启动独立承载服务：graph-engine serve http / docker compose up -d）
python sdk/python/examples/quickstart.py            # 同步全链路
python sdk/python/examples/async_quickstart.py      # 异步客户端
```

## 11. 与 openwiki-server-sdk 的差异

| 维度 | openwiki-server-sdk | semantica-graph-sdk |
| --- | --- | --- |
| 资源域 | `wikis` / `jobs` | `graphs` / `jobs` |
| 鉴权 | 服务端 Bearer 鉴权（token 必需场景更多） | 默认无鉴权（引擎仅监听 127.0.0.1，经 HAProxy 暴露），`token` 可选 |
| 身份用途 | 仅透传身份头 | 透传身份头 **+** 补齐 `tenantId`/`ownerId` 请求参数 |
| 异步任务 | `jobs.run/get/list` | `jobs.run/get/list` **+** `jobs.wait`（轮询至终态） |
| 自检 | `fetch_openapi()` | `fetch_openapi()` **+** `health()` |

## 12. 遗留与后续增强

1. `sparql/search` 依赖服务端 `pyoxigraph` 与检索后端；缺失时服务端返回确定降级/参数错误，SDK 已覆盖。
2. 异步任务失败自动回写/重试仍是服务端既有增强项（当前 worker 异常时 job 可能保持 pending）。
3. 如需 gRPC/MCP 面客户端，可按同一模型与异常映射扩展（当前 SDK 聚焦 HTTP 协议面，与 openwiki-server-sdk 口径一致）。
