# Docker 部署与 HAProxy 代理层

> 单镜像（`graph-engine:0.1.0`）同时包含图引擎（HTTP / gRPC / Celery / MCP / CLI）与 HAProxy 代理层；
> HAProxy 为对外唯一入口，反向代理内部 HTTP 与 gRPC 服务。
> 配套脚本：`scripts/build_docker.sh`（构建）、`scripts/docker_smoke.sh`（冒烟）、`docker-compose.yml`（编排）、`docker/.env.example`（生产模板）。

## 1. 拓扑与端口

```
宿主机 client
  │  http://127.0.0.1:18180（HTTP）   127.0.0.1:18151（gRPC）   127.0.0.1:8406（stats）
  ▼
┌──────────────────────────────────────────────────────────────┐
│  容器 graph-engine-engine-1                                    │
│  HAProxy(:8080 HTTP / :50051 gRPC / :8404 stats)              │
│     │  反向代理（HTTP /health 探测；gRPC TCP 透传）            │
│     ▼                                                          │
│  graph-engine serve http(127.0.0.1:18010 仅回环)              │
│  graph-engine serve grpc(127.0.0.1:50051 仅回环)              │
│  graph-engine serve worker（Celery broker/backend → redis:6379）│
└──────────────────────────────────────────────────────────────┘
             │ engine 依赖 redis（compose 内部网络，不发布端口）
             ▼
   容器 graph-engine-redis-1（redis:7-alpine，AOF 持久化卷 redis_data）
```

- **引擎服务不直接暴露**：HTTP/gRPC 只监听容器回环 `127.0.0.1`，容器网络内/外部均无法直连；唯一对外入口为 HAProxy。
- 对外端口（compose 映射）：`18180`（HTTP → 容器 `8080`）、`18151`（gRPC → 容器 `50051`）、`8406`（HAProxy stats UI）。
- MCP 为 stdio 协议、CLI 为本地命令，不参与网络代理；compose 栈额外启动 `redis:7-alpine`
  作为 Celery broker/backend（仅 compose 内部网络可达，不发布宿主端口），worker 默认随启。
- 异步语义：`POST .../build` 带 `async=true` 返回 `jobId`（pending），HTTP 进程投递 Celery，
  worker 消费后回写 job 状态；`GET /api/v1/graph/jobs/{jobId}` 可轮询到 `success`。
- 容器内入口用高位端口，避免非 root（uid 1000）绑定特权端口对运行时内核参数的依赖。

## 2. 构建镜像

```bash
bash scripts/build_docker.sh              # 构建 graph-engine:0.1.0（版本取自 pyproject.toml）
bash scripts/build_docker.sh --no-cache   # docker build --no-cache
```

- 多阶段构建：`builder` 阶段安装引擎与全部依赖（semantica 0.6.5 等从 PyPI 拉取，pip `--timeout 300 --retries 15`
  抗网络抖动；依赖层仅随 `requirements.txt` 失效，代码变更不重复下载 torch 等大依赖），
  `runtime` 阶段（`python:3.12-slim`）只复制安装产物 + HAProxy + gettext（envsubst）。
- 镜像内文件：HAProxy 配置模板 `/etc/haproxy/haproxy.cfg.tmpl`、入口脚本 `/usr/local/bin/graph-engine-entrypoint.sh`
  （启动时 envsubst 渲染引擎端口与 stats 凭据，同进程拉起引擎 + haproxy，就绪探测 fail-fast，TERM/INT 转发优雅停机）；
  镜像自带 `HEALTHCHECK`（经 HAProxy 探测 `/health`）。
- `.dockerignore` 已排除 `.venv/`、`tests/`、`data/`、`logs/`、`docker/images/`（大镜像 tar）等，构建上下文保持精简。

## 3. 启动 / 停止 / 升级

```bash
docker compose up -d                # 启动（后台）
docker compose ps                   # 查看状态（health）
docker compose logs -f engine       # 查看日志
docker compose down                 # 停止并清理容器/网络（数据卷保留）
```

- 首次启动后等待健康：`docker ps` 中 `graph-engine-engine-1` 显示 `(healthy)` 即就绪。
- 升级旧镜像：先 `bash scripts/build_docker.sh`，再 `docker compose up -d --build`（避免复用同 tag 旧镜像）。
- 冒烟验证：`bash scripts/docker_smoke.sh`（8 项断言：`/health`、HTTP create+stat、gRPC 经 HAProxy 调用、
  stats 默认凭据 200/错误 401、引擎端口回环隔离、非 root 运行、Celery 端到端（worker 进程 + HTTP async
  建图至 success）、容器内 CLI/MCP 冒烟；`--force-build` 可强制重建）。

## 4. 环境变量（自动配置）

`docker compose` 读取当前 shell 或根目录 `.env`；生产模板见 `docker/.env.example`。
入口脚本会自动渲染 HAProxy 配置，因此**无需手工改 haproxy.cfg**，只需设置环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GRAPH_ENGINE_DB_PATH` | `data/engine.db` | SQLite 路径（挂载卷 `engine_data` 持久化） |
| `GRAPH_ENGINE_SEMANTICA` | `1` | 设为 `0` 关闭 semantica 集成（pinecone 冲突环境） |
| `GRAPH_ENGINE_LOG_LEVEL` | `INFO` | 引擎日志级别 |
| `GRAPH_ENGINE_HTTP_PORT` | `18010` | 引擎 HTTP 内部端口（仅回环；改后 HAProxy 自动跟随） |
| `GRAPH_ENGINE_GRPC_PORT` | `50051` | 引擎 gRPC 内部端口（仅回环；改后 HAProxy 自动跟随） |
| `GRAPH_ENGINE_CELERY_ENABLED` | compose `1` / docker run `0` | `1` 时容器内随启 Celery worker（compose 默认启用，内置 redis） |
| `GRAPH_ENGINE_CELERY_BROKER` / `..._BACKEND` | compose `redis://redis:6379/0` | Celery broker/backend；接外部 Redis 时写容器内可达地址 |
| `HAPROXY_HTTP_PORT` | `18180` | HAProxy 对外 HTTP 端口（映射容器 `8080`） |
| `HAPROXY_GRPC_PORT` | `18151` | HAProxy 对外 gRPC 端口（映射容器 `50051`） |
| `HAPROXY_STATS_PORT` | `8406` | HAProxy stats 端口 |
| `HAPROXY_STATS_USER` / `HAPROXY_STATS_PASSWORD` | `admin` / `change-me` | stats UI 登录账号（生产必改；默认凭据启动会输出告警） |

> 环境变量在容器创建时注入，修改后必须 `docker compose up -d --force-recreate` 重建容器才生效。

## 5. 验证示例

```bash
# HTTP（经 HAProxy）
curl -s http://127.0.0.1:18180/health
curl -s -X POST http://127.0.0.1:18180/api/v1/graph/graphs \
  -H 'Content-Type: application/json' \
  -d '{"name":"测试图","kbId":"kb_demo","graphSchema":{"entityTypes":[{"type":"person"}],"relationTypes":[]}}'

# HTTP async 建图 → 轮询 job（compose 栈默认启用 Celery + 内置 Redis）
curl -s -X POST http://127.0.0.1:18180/api/v1/graph/graphs/graph_xxx/build \
  -H 'Content-Type: application/json' \
  -d '{"async":true,"docId":"d1","entities":[{"name":"Alice","type":"person"}]}'
# → {"data":{"jobId":"job_xxx","status":"pending",...}}；worker 消费后：
curl -s http://127.0.0.1:18180/api/v1/graph/jobs/job_xxx   # status=success 即完成

# gRPC（经 HAProxy TCP 透传）
.venv/bin/python -c "
from graph_engine.interfaces.grpc_server import grpc_client
print(grpc_client('127.0.0.1', 18151)('Stat', {'graphId': 'graph_xxx'}))"

# stats UI
# http://127.0.0.1:8406/（账号 HAPROXY_STATS_USER / HAPROXY_STATS_PASSWORD）
```
