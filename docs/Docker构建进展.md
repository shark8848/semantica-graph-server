# Docker 统一镜像（HAProxy 反向代理）构建进展

> 记录时间：2026-08-26。状态：**已完成**——多阶段构建改造完成，镜像已成功构建，
> `scripts/docker_smoke.sh` 6 项冒烟与 `pytest tests` 全部通过。

## 已完成

参照 `/home/open-ikc` 的单镜像模式（引擎 + HAProxy 同容器，HAProxy 为对外唯一入口），
为 semantica-graph-server 生成了以下文件：

| 文件 | 说明 |
| --- | --- |
| `Dockerfile` | **多阶段构建**：`builder`（安装引擎全部依赖，依赖层仅随 `requirements.txt` 失效）+ `runtime`（`python:3.12-slim` 仅复制安装产物 + HAProxy + gettext）；非 root（uid 1000）；`EXPOSE 8080 50051 8404`；含 `HEALTHCHECK` 与 OCI labels |
| `requirements.txt` | 引擎运行时依赖（与 `pyproject.toml` 同步）；builder 先装依赖层，代码变更不重复下载 torch 等大依赖 |
| `docker/haproxy.cfg` | HAProxy 配置模板：HTTP `:8080` → `127.0.0.1:18010`（`/health` 探测）、gRPC `:50051` → `127.0.0.1:50051`（TCP 透传）、stats `:8404` |
| `docker/entrypoint.sh` | 入口脚本：envsubst 自动渲染引擎端口 + stats 凭据 → 拉起 http/grpc（仅回环，可选 worker）→ 就绪探测 fail-fast → 前台 HAProxy，TERM/INT 转发 |
| `docker/.env.example` | 生产环境变量模板 |
| `docker-compose.yml` | 单服务栈：`18180→8080`、`18151→50051`、`8406→8404`；卷 `engine_data`/`engine_logs`；healthcheck 经 HAProxy |
| `scripts/build_docker.sh` | 构建脚本（`--no-cache` / `--pull` 可选，IMAGE_TAG 可覆盖，默认 `graph-engine:<pyproject 版本>`，输出镜像体积）；**2026-09-23 起默认 `docker save \| gzip` 导出 `docker/images/<tag>.tar.gz`**（`--no-save` 只构建，见文末「导出离线包补齐」） |
| `scripts/docker_smoke.sh` | 冒烟：/health、HTTP create+stat、gRPC 经 HAProxy、stats 凭据、回环隔离、非 root |
| `.dockerignore` | 排除 .venv/tests/data/logs/docker/images（97M tar）等 |
| `docs/Docker部署与HAProxy.md` | 部署文档（拓扑、构建、启动、环境变量、验证示例） |
| `README.md` | 新增 “Docker 部署” 小节 |

## 已验证（2026-08-26）

- `docker build` 成功（多阶段；builder 内 pip `--timeout 300 --retries 15`，克服 PyPI 网络抖动）。
- `scripts/docker_smoke.sh --force-build` 通过：/health、HTTP create+stat、gRPC 经 HAProxy、
  stats 默认凭据 200 / 错误 401、引擎 18010/50051 回环隔离、容器 uid 1000 非 root。
- `.venv/bin/python -m pytest tests -q`：12 passed（在沙箱外、与 docker 冒烟同环境执行；
  沙箱内 bwrap 与 anyio 线程唤醒存在竞态，`test_http_app` 偶发卡死，属沙箱环境问题，非仓库缺陷）。

## 注意事项

- 镜像体积数 GB：semantica 0.6.5 依赖 torch/transformers/opencv/spacy 等，属固有代价；
  如需精简可后续评估 `--no-deps` + 最小依赖集。
- 多阶段构建后，`pip install .` 与运行时共用同一依赖集合，镜像内仍保留 pip 便于排障。

## 关键设计（自动配置）

- 对外入口只有 HAProxy；引擎 HTTP/gRPC 强制监听 `127.0.0.1`（entrypoint 导出 `GRAPH_ENGINE_HTTP_HOST/GRPC_HOST=127.0.0.1`）。
- 无需手工改 haproxy.cfg：`GRAPH_ENGINE_HTTP_PORT` / `GRAPH_ENGINE_GRPC_PORT` / `HAPROXY_STATS_USER` /
  `HAPROXY_STATS_PASSWORD` 由 entrypoint 用 envsubst 渲染进配置；compose 端口映射同理可用环境变量覆盖。
- Celery worker 默认关闭（`GRAPH_ENGINE_CELERY_ENABLED=0`），MCP/CLI 为非网络协议不代理。

## 复验与修复（2026-09-15）

环境：Docker 29.6.2 / Compose v5.3.1 / 宿主本地 Redis `0.0.0.0:6379`（带密码），
镜像 `graph-engine:0.1.0`（`docker images` 显示 11.3 GB；`docker image inspect .Size` 为 3.65 GB，
两者计量口径不同），全量构建（依赖层重新下载 torch/nvidia 等）约 13–15 分钟。

- `bash scripts/build_docker.sh` ✅ 构建成功；`docker compose up -d` ✅ 容器 `graph-engine-engine-1` `(healthy)`。
- `bash scripts/docker_smoke.sh` ✅ **PASS：单镜像栈全部冒烟通过（HTTP 18180 / gRPC 18151 / stats 8406 /
  Celery / MCP / CLI）**，8 项断言全绿。
- 独立端到端验证（未走冒烟脚本）✅：同步建图（2 实体 + 1 关系 → `nodeCount=2, edgeCount=1`）、
  异步建图 `async=true` → job 轮询至 `success`、gRPC `Stat` 经 HAProxy（`nodeCount=3`）、
  stats 默认凭据 200 / 错误凭据 401、引擎 18010/50051 回环隔离、容器 uid/gid 1000、CLI `stat`、MCP 握手。

### 本轮实测修复

| 问题 | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| 镜像缺 `redis`（redis-py） | 容器内 Celery worker 启动即崩（`AttributeError: 'NoneType' object has no attribute 'Redis'`，来自 `kombu/transport/redis.py`），`async=true` 建图永久 `pending`，但容器仍显示 `(healthy)` | `requirements.txt` / `pyproject.toml` 只写 `celery>=5.3`，未带 `[redis]` extra；开发 venv 已装 `redis 8.1.0` 掩盖了缺口 | 两者同步改为 `celery[redis]>=5.3`（`redis-py` 进镜像） |
| 冒烟脚本宿主侧 Redis 预检地址 | `host.docker.internal` 预检报 `Connection closed by server`，冒烟在步骤 0 即失败 | 该别名是**容器内**语义；本机 DNS 把它解析到 `192.168.137.50`（非本机 Redis） | `scripts/docker_smoke.sh` 预检时归一化为 `127.0.0.1`（容器内仍用别名） |
| 镜像缺 `ikc-sdk-lib` | 容器启动 `ModuleNotFoundError: No module named 'ikc_sdk'`（旧镜像 2026-09-09 构建） | builder 只按 `requirements.txt` 装包，`pyproject.toml` 里的 `ikc-sdk-lib==0.7.0` 未同步到前者 | `requirements.txt` 补 `ikc-sdk-lib==0.7.0`（本轮重建后复验通过） |

### 复验发现的运维坑（已写入 `docs/本地Docker部署手册.md`）

- **健康检查不等于业务可用**：`HEALTHCHECK` 只探 `/health`，worker 崩溃时容器依旧 `(healthy)`——
  本轮问题正是如此，必须跑 `scripts/docker_smoke.sh` 才能覆盖 Celery 端到端。
- 镜像基于 `python:3.12-slim`，**没有 `ps` / `netstat`**：排查用 `docker top <容器>`，或用容器内 python 扫
  `/proc/*/cmdline`（冒烟脚本步骤 7a 即此法）。
- 同一 `kbId` 重复 `POST /api/v1/graph/graphs` 返回 **409**（唯一约束，非故障）。
- CLI 命令为**顶层结构**：`graph-engine create|stat|...`，不存在 `graph-engine graph stat`（会打印 usage 报错）。

## 导出离线包补齐（2026-09-23）

- 背景：兄弟仓 `ikc-core-service` / `ikc-open-platform` 的 `scripts/build_docker.sh` 默认「构建 + `docker save`」
  出离线包，本仓此前只有构建（导出要照手册第 3 节手工敲）；ikc-demo 的 `scripts/start-stack.sh` 也只是把镜像
  按 `ikc-graph-engine:0.1.0` 就地补别名，不带包。
- 改动（**只动脚本与文档，未改 `Dockerfile` / 镜像内容**）：
  - `scripts/build_docker.sh`：默认构建后 `docker save "$IMAGE_TAG" | gzip > docker/images/<tag>.tar.gz`
    （文件名由 tag 推导，`:`/`/` → `_`；`ikc-graph-engine:0.1.0` → `ikc-graph-engine_0.1.0.tar.gz`）；
    新增 `--no-save`（只构建），头部注释同步用法 / 环境变量 / 产物 / 手册章节指引。
  - `.gitignore`：离线包扩展名一并排除（`docker/images/*.tar.gz`、`*.tgz`），避免 GB 级产物误入库。
  - 文档同步：`docs/本地Docker部署手册.md`（§0 TL;DR、§2.2、§3 导出、§9 升级回滚、§12 差异表）、
    `docs/Docker部署与HAProxy.md`（§2 构建、§3 升级）、`README.md`、`AGENTS.md`。
- 实测（本机 2026-09-23，镜像 `ikc-graph-engine:0.1.0` = `sha256:b0b3c1d11b7b`，11.3 GB）：
  - 导出产物 `docker/images/ikc-graph-engine_0.1.0.tar.gz` **3.4 GB**（`docker save | gzip` 约 2 分钟）。
  - `tar -tzf` 结构正常（OCI 布局 `blobs/sha256/*`）、`gzip -t` 整包读取通过（约 20 s）。
  - `--no-save` 跳过导出；未知参数打印 usage 且 `exit=1`；产物被 `.gitignore` 命中（`git status` 无新文件）。
- 验证方式说明：为免把**已验证镜像**的 tag 挪到新构建上（`requirements.txt` 里的 `fastapi`/`uvicorn`/`pydantic`/
  `grpcio`/`protobuf`/`celery` 都是 `>=`，重建会重新解析、有漂移风险），本次**未重跑 `docker build`**——
  用 PATH 上的 `docker build` 桩跳过构建、只跑脚本的导出路径，因此 `scripts/docker_smoke.sh` 也未复跑
  （`Dockerfile` 与镜像内容未变）。需要「构建 + 导出」全量复验时：`bash scripts/build_docker.sh`
  （依赖层未失效时约分钟级）→ `bash scripts/docker_smoke.sh`。

## 2026-09-25 — 构建脚本缺省 tag 对齐 `ikc-*`

- 背景：ikc-demo 全栈要求镜像名一律 `ikc-*`（此前靠 `start-stack.sh` 补别名 / 构建时显式
  `IMAGE_TAG=ikc-graph-engine:0.1.0` 覆盖）。
- 改动：`scripts/build_docker.sh` 与 `scripts/docker_smoke.sh` 的缺省 `IMAGE_TAG` → `ikc-graph-engine:<版本>`；
  `docker-compose.yml` 的 `image:` 同步（`build → compose up` 同口径）；`docs/本地Docker部署手册.md` 的
  镜像名 / 产物名 / `docker compose config --images` 期望值同步；本文件更早内容与 `docs/进展-*.md`
  属历史记录，按原样保留。
- 产物名随之变为 `docker/images/ikc-graph-engine_0.1.0.tar.gz`（本次只改脚本与文档，未重跑构建）。
- 要原生名可显式覆盖：`IMAGE_TAG=graph-engine:0.1.0 bash scripts/build_docker.sh`。
- 验证：两个脚本 `bash -n` 通过；`rg` 复核缺省 tag 与 compose `image:` 一致。
