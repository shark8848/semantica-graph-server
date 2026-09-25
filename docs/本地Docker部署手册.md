# Semantica Graph Engine 本地 Docker 部署手册

适用范围：在**本机（已装 Docker + Compose v2、可访问 PyPI）**构建并运行单镜像栈
（图引擎五面接口 + HAProxy 代理层同容器）；需要迁移到无构建环境/无外网机器时，
走第 2.2/3 节的「构建即导出（`docker save | gzip`）」→ 传输 → `docker load` 路径。

参考：openwiki-server `docs/deploy-offline.md`（同款「单镜像 + HAProxy 唯一入口」拓扑），
与本手册的差异见第 12 节对照表。

> **实测状态（2026-09-15）**：本手册流程已在带 Docker daemon 的宿主（Docker 29.6.2 / Compose v5.3.1 /
> 宿主本地 Redis 0.0.0.0:6379）**全量跑通**——`docker build` 成功、`docker compose up -d` 容器 `healthy`、
> `bash scripts/docker_smoke.sh` 8 项断言全绿，另独立验证了同步/异步建图、gRPC、stats 401/200、
> 回环隔离、非 root、CLI/MCP。实测数据：镜像 `docker images` 显示 **11.3 GB**（`docker image inspect .Size` 为
> 3.65 GB，两者计量口径不同），单次构建约 13–15 分钟（依赖层失效时需重新下载 torch/nvidia 依赖）。
> 体积构成分析见 `docs/镜像体积精简评估.md`，构建改造与历史记录见 `docs/Docker构建进展.md`。
> 撰写期无 daemon 时的静态核对（`bash -n`、`pytest`）见本手册早期版本与仓库 git 历史。

## 0. 一键（TL;DR，本机）

```bash
cd /home/sharkyai/semantica-graph-server
bash scripts/build_docker.sh        # 构建 ikc-graph-engine:0.1.0（多阶段；首次下载 torch 等大依赖）
                                    # 同时导出离线包 docker/images/ikc-graph-engine_0.1.0.tar.gz（--no-save 可跳过）
docker compose up -d                # 启动（HAProxy 入口 18180 HTTP / 18151 gRPC / 8406 stats）
curl -s http://127.0.0.1:18180/health
bash scripts/docker_smoke.sh        # 8 项冒烟（脚本内部会自行 compose up/down）
```

## 1. 镜像清单与容器拓扑

| 镜像:标签 | 体积（估算） | 来源 | 用途 |
| --- | --- | --- | --- |
| `ikc-graph-engine:0.1.0` | 实测 `docker images` **11.3 GB**（`image inspect .Size` 3.65 GB） | `Dockerfile` 多阶段（`builder` → `python:3.12-slim` runtime） | 单镜像内含引擎（HTTP/gRPC/Celery/MCP/CLI）+ HAProxy 代理层 + gettext(envsubst) |

容器内拓扑（引擎只监听回环，对外唯一入口为 HAProxy）：

```
client -> HAProxy(:8080 HTTP / :50051 gRPC / :8404 stats)
           -> graph-engine serve http (127.0.0.1:18010，仅回环)
           -> graph-engine serve grpc (127.0.0.1:50051，仅回环)
           -> graph-engine serve worker (Celery；broker/backend 指向宿主 Redis)
```

- 引擎 HTTP/gRPC **不直接对外**（只在容器回环监听），绕过 HAProxy 直连 18010/50051 必然失败，属预期。
- 容器以非 root 运行：`appuser`，**uid 1000**（与常见宿主机用户对齐，便于挂载 data/logs 卷）。
- 数据**一律外部挂载、不进镜像**：`/app/data`（SQLite `engine.db`）与 `/app/logs`；compose 用命名卷
  `graph-engine_engine_data` / `graph-engine_engine_logs` 持久化，删除/重建容器不丢数据。
- `MCP` 为 stdio 协议、`CLI` 为容器内本地命令，均不参与网络代理。

## 2. 本机构建

### 2.1 前置条件

```bash
docker version                                  # Docker Engine 20.10+（建议 24+）
docker compose version                          # Compose v2 插件（docker compose，非 docker-compose）
df -h /var/lib/docker                           # 预留 ≥ 20 GB（镜像数 GB + builder 缓存）
ss -ltn | grep -E ':(18180|18151|8406)\b'       # 应无输出（端口空闲）
```

- **依赖源**：构建机需可访问 PyPI。builder 阶段安装 `requirements.txt`，其中包含跨层共享模型
  SDK `ikc-sdk-lib==0.8.3`（引擎 `domain`/`application`/`interfaces` 直接 `import ikc_sdk`），
  缺该包容器会启动失败；离线构建机见 2.3。
- **宿主 Redis（Celery broker/backend）**：compose 栈**不启动任何 redis 容器**，默认连宿主本地 Redis
  （`redis://:1qaz2wsx3edc@host.docker.internal:6379/0`）。宿主 Redis 必须**监听 `0.0.0.0` 且开启密码**，
  否则容器内连不上（`ss -ltn | grep 6379` 应看到 `0.0.0.0:6379`/`*:6379`，而非只有 `127.0.0.1:6379`）。
  - 仅做 HTTP/gRPC 验证时可不依赖 Redis：把 `GRAPH_ENGINE_CELERY_ENABLED` 设为 `0`（异步任务将保持 `pending`）。

### 2.2 构建镜像

```bash
cd /home/sharkyai/semantica-graph-server
bash scripts/build_docker.sh              # 版本取自 pyproject.toml → ikc-graph-engine:0.1.0（并导出 docker/images/*.tar.gz）
bash scripts/build_docker.sh --no-save    # 只构建，不导出离线包
bash scripts/build_docker.sh --no-cache   # 需要强制重建依赖层时
bash scripts/build_docker.sh --pull       # 先拉最新 python:3.12-slim 基础镜像
IMAGE_TAG=graph-engine:test bash scripts/build_docker.sh   # 自定义 tag（compose 需同步改 image；导出名随 tag）
```

- 多阶段：`builder` 装齐引擎依赖（semantica 0.6.5 → torch/transformers/spacy/opencv 等大依赖，
  pip `--timeout 300 --retries 15` 抗网络抖动），**依赖层只随 `requirements.txt` 失效**，
  改代码不重复下载大依赖；`runtime` 只复制安装产物 + HAProxy/gettext。
- 构建完成后脚本打印镜像体积；`docker images | grep graph-engine` 可核验。
- **离线包**：构建完成后脚本自动 `docker save | gzip` 导出到 `docker/images/<tag>.tar.gz`
  （文件名由 tag 推导，`:`/`/` 换成 `_`：`ikc-graph-engine:0.1.0` → `ikc-graph-engine_0.1.0.tar.gz`）。
  数 GB 镜像 gzip 需几分钟；只重建镜像、不要包时加 `--no-save`。
- **实测**：本机单次构建约 13–15 分钟（`requirements.txt` 变更会触发依赖层重装，需重新下载
  torch/nvidia 等数 GB wheel）；仅改 `graph_engine/` 代码时依赖层命中缓存，构建在分钟级完成。
- **依赖必须写进 `requirements.txt` / `pyproject.toml`**：镜像 builder 只按这两个文件装包，
  开发 venv 里"碰巧已装"的包不会进镜像。曾因此踩坑——`celery>=5.3` 未带 `[redis]` extra，
  本地 venv 有 `redis 8.1.0` 而镜像没有，导致容器内 worker 一启动就崩、异步作业永久 `pending`
  （现已改为 `celery[redis]>=5.3`）。

### 2.3 无外网构建机（离线依赖预置，未实测）

路径 A（本地 wheel 源，推荐）：在**有外网**的机器导出依赖 wheel，再拷到构建机
（`docker/wheels/` 已加入 `.gitignore`，不会入库）：

```bash
# 有外网机器（同版本 Python 环境）
pip download -r requirements.txt -d docker/wheels     # 含 ikc-sdk-lib==0.8.3
# 拷到构建机：scp -r docker/wheels 构建机:.../semantica-graph-server/docker/
```

构建机上在 `Dockerfile` 的 builder 阶段临时追加（仅离线构建时改，勿入库）：

```dockerfile
COPY docker/wheels/ ./wheels/
RUN pip install --no-cache-dir --timeout 300 --retries 15 --find-links ./wheels --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --timeout 300 --retries 15 --find-links ./wheels -r requirements.txt
```

路径 B（内网 PyPI 镜像）：给 builder 加 `ENV PIP_INDEX_URL=<内网源>/simple`，需该源已同步
`ikc-sdk-lib==0.8.3` 与 semantica 全量依赖。

> 两条路径都必须在 builder 内完成依赖安装；`runtime` 阶段是 `COPY --from=builder`，不重新联网装包。

## 3. 导出镜像与部署包（迁移到其它机器时）

镜像包由 `scripts/build_docker.sh` **默认导出**（与 ikc-core-service / ikc-open-platform 同口径）：

```bash
cd /home/sharkyai/semantica-graph-server
bash scripts/build_docker.sh                                    # → docker/images/ikc-graph-engine_0.1.0.tar.gz
docker images | grep graph-engine                               # 核验镜像与 tag
```

只想重出包、不重建镜像时用等价的单条命令（`| gzip` 出压缩包；`-o` 出未压缩 tar）：

```bash
mkdir -p docker/images
docker save ikc-graph-engine:0.1.0 | gzip > docker/images/ikc-graph-engine_0.1.0.tar.gz
# 或：docker save -o docker/images/ikc-graph-engine_0.1.0.tar ikc-graph-engine:0.1.0
```

compose 侧文件与校验和仍手工打包：

```bash
tar czf docker/images/graph-engine-compose-0.1.0.tgz \
  docker-compose.yml docker/.env.example config/engine.example.yaml docs/本地Docker部署手册.md

cd docker/images && sha256sum ikc-graph-engine_0.1.0.tar.gz graph-engine-compose-0.1.0.tgz \
  > graph-engine-0.1.0-SHA256SUMS.txt
```

传输（scp / rsync / U 盘均可）：

```bash
scp docker/images/ikc-graph-engine_0.1.0.tar.gz \
    docker/images/graph-engine-compose-0.1.0.tgz \
    docker/images/graph-engine-0.1.0-SHA256SUMS.txt \
    root@SERVER:/opt/graph-engine/_release/
```

## 4. 目标机导入

目标机无需 `Dockerfile`、无需构建依赖、无需访问 PyPI：

```bash
mkdir -p /opt/graph-engine && cd /opt/graph-engine
tar xzf _release/graph-engine-compose-0.1.0.tgz
sha256sum -c _release/graph-engine-0.1.0-SHA256SUMS.txt

docker load -i _release/ikc-graph-engine_0.1.0.tar.gz
docker images | grep graph-engine       # 与第 1 节清单一致
docker compose config --images          # 应为 ikc-graph-engine:0.1.0
```

## 5. 配置 `.env`

`docker compose` 自动读取项目根目录 `.env`（已在 `.gitignore`，**切勿入库真实密码**）：

```bash
cd /opt/graph-engine              # 本机部署则用仓库根目录
cp docker/.env.example .env
${EDITOR:-vi} .env
```

最小可用配置（完整注释见 `docker/.env.example`）：

```bash
# 宿主端口映射（HAProxy 入口；引擎 18010/50051 只在容器回环，不对外）
HAPROXY_HTTP_PORT=18180
HAPROXY_GRPC_PORT=18151
HAPROXY_STATS_PORT=8406
# HAProxy stats 登录（生产必改；默认 admin/change-me 会输出告警）
HAPROXY_STATS_USER=admin
HAPROXY_STATS_PASSWORD=REPLACE_WITH_STRONG_PASSWORD

# 引擎
GRAPH_ENGINE_DB_PATH=data/engine.db
GRAPH_ENGINE_SEMANTICA=1          # 0 = 关闭 semantica 集成（pinecone 桩包冲突环境）
GRAPH_ENGINE_LOG_LEVEL=INFO

# Celery（compose 默认启用；broker/backend 指向宿主 Redis）
GRAPH_ENGINE_CELERY_ENABLED=1
# CELERY_BROKER_URL=redis://:密码@host.docker.internal:6379/0   # 一键覆写 broker+backend
```

- 环境变量在**容器创建时**注入：改 `.env` 后必须 `docker compose up -d --force-recreate` 才生效。
- 若把数据卷改成宿主目录（bind mount），宿主目录必须可写（容器内 uid 1000）：
  `sudo chown -R 1000:1000 /opt/graph-engine/data`，否则启动报
  `sqlite3.OperationalError: unable to open database file`。

## 6. 启动与验证（compose）

```bash
cd /opt/graph-engine
docker compose up -d --no-build     # 目标机用已 load 的镜像；本机可省略 --no-build
docker compose ps                   # 等 health 变为 healthy（容器名 graph-engine-engine-1）

# 健康检查（端到端：HAProxy -> 引擎 127.0.0.1:18010）
curl -s http://127.0.0.1:18180/health    # {"status":"ok","service":"graph-engine"}
curl -s http://127.0.0.1:18180/ready     # {"status":"ready"}

# 建图 + 统计（envelope 对齐 open-ikc：traceId/errCode/errMsg/data）
curl -s -X POST http://127.0.0.1:18180/api/v1/graph/graphs \
  -H 'Content-Type: application/json' \
  -d '{"name":"测试图","kbId":"kb_demo","graphSchema":{"entityTypes":[{"type":"person"}],"relationTypes":[]}}'

curl -s http://127.0.0.1:18180/api/v1/graph/graphs/<graphId>/stat   # nodeCount=0

# 异步建图（compose 默认启用 Celery，消费端为容器内 worker）
curl -s -X POST http://127.0.0.1:18180/api/v1/graph/graphs/<graphId>/build \
  -H 'Content-Type: application/json' \
  -d '{"async":true,"docId":"d1","entities":[{"name":"Alice","type":"person"}]}'
# → {"data":{"jobId":"job_xxx","status":"pending",...}}
curl -s http://127.0.0.1:18180/api/v1/graph/jobs/<jobId>     # status=success 即完成
curl -s 'http://127.0.0.1:18180/api/v1/graph/jobs?graphId=<graphId>&limit=20'

# gRPC（经 HAProxy TCP 透传；需本机可 import 引擎包，如仓库 .venv）
.venv/bin/python -c "
from graph_engine.interfaces.grpc_server import grpc_client
print(grpc_client('127.0.0.1', 18151)('Stat', {'graphId': '<graphId>'}))"

# HAProxy stats（账号取 HAPROXY_STATS_USER/PASSWORD）
curl -s -u admin:REPLACE_WITH_STRONG_PASSWORD -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8406/

# 容器内 CLI 与 MCP（复用同一数据卷的 SQLite）
docker exec graph-engine-engine-1 graph-engine --help
docker exec graph-engine-engine-1 graph-engine create --name cli-demo --kb-id kb_cli_demo \
  --schema '{"entityTypes":[{"type":"person"}],"relationTypes":[]}'     # 成功输出为裸 JSON（无 envelope）
docker exec graph-engine-engine-1 graph-engine stat <graphId>           # 命令为顶层结构（非 `graph stat`）
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | docker exec -i graph-engine-engine-1 timeout 15 graph-engine serve mcp
```

一键冒烟（覆盖上述大部分断言，共 8 项：`/health`、HTTP create+stat、gRPC 经 HAProxy、
stats 默认凭据 200/错误 401、引擎端口回环隔离、非 root(uid 1000)、Celery 端到端
（worker 进程 + HTTP async 建图至 success）、容器内 CLI/MCP）：

```bash
cd /home/sharkyai/semantica-graph-server
bash scripts/docker_smoke.sh                 # 镜像不存在时自动构建；--force-build 强制重建
```

- 冒烟脚本前置：宿主 Redis 可达（可用 `CELERY_BROKER_URL` 覆写）、仓库 `.venv` 可用、18180/18151/8406 空闲；
  脚本退出时会执行 `docker compose down` 清理容器（**数据卷保留**）。
- **实测（2026-09-15）**：`bash scripts/docker_smoke.sh` → `PASS：单镜像栈全部冒烟通过（HTTP 18180 /
  gRPC 18151 / stats 8406 / Celery / MCP / CLI）`。脚本宿主侧预检已把 `host.docker.internal`
  归一化为 `127.0.0.1`（该别名是容器内语义；宿主上可能被 DNS 解析到其它机器，实测本机解析到
  `192.168.137.50`，Redis 连接被对端关闭）。

访问入口：HTTP `http://<服务器IP>:18180`、gRPC `<服务器IP>:18151`、stats `http://<服务器IP>:8406/`。

## 7. 非 compose 部署（手动 `docker run`）

目标机无 compose 插件、或需要精确控制时使用。

### 7.1 前置条件与端口

- 镜像已 `docker load`（第 4 节）。容器以 uid 1000 运行，**宿主数据目录必须可写**：
  `sudo mkdir -p /opt/graph-engine/data /opt/graph-engine/logs && sudo chown -R 1000:1000 /opt/graph-engine/data /opt/graph-engine/logs`
- Linux 上容器访问宿主机服务（Redis）必须加 `--add-host host.docker.internal:host-gateway`。
- 端口（容器端口固定，宿主端口可改；冲突检查 `ss -ltnp | grep -E ':(18180|18151|8406)\b'`）：

| 宿主端口 | 容器端口 | 用途 |
| --- | --- | --- |
| `18180` | `8080` | HTTP（HAProxy → 引擎 127.0.0.1:18010） |
| `18151` | `50051` | gRPC（HAProxy TCP 透传） |
| `8406` | `8404` | HAProxy stats |

### 7.2 环境变量文件

`--env-file` 不做 `${}` 展开，需填具体值：

```bash
cat > /opt/graph-engine/.env <<'EOF'
HAPROXY_STATS_USER=admin
HAPROXY_STATS_PASSWORD=REPLACE_WITH_STRONG_PASSWORD
GRAPH_ENGINE_LOG_LEVEL=INFO
GRAPH_ENGINE_DB_PATH=data/engine.db
GRAPH_ENGINE_SEMANTICA=1
GRAPH_ENGINE_CELERY_ENABLED=1
GRAPH_ENGINE_CELERY_BROKER=redis://:1qaz2wsx3edc@host.docker.internal:6379/0
GRAPH_ENGINE_CELERY_BACKEND=redis://:1qaz2wsx3edc@host.docker.internal:6379/0
EOF
chmod 600 /opt/graph-engine/.env
```

> 注意 `docker run` 下 `GRAPH_ENGINE_CELERY_ENABLED` 的**默认值是 0**（compose 才默认 1）；
> 需要异步建图就必须显式写 `1` 并提供容器内可达的 broker/backend。

### 7.3 启动单容器（引擎 + HAProxy + worker）

```bash
docker run -d --name graph-engine --restart unless-stopped \
  --add-host host.docker.internal:host-gateway \
  -v /opt/graph-engine/data:/app/data \
  -v /opt/graph-engine/logs:/app/logs \
  --env-file /opt/graph-engine/.env \
  -p 18180:8080 -p 18151:50051 -p 8406:8404 \
  ikc-graph-engine:0.1.0
```

验证与第 6 节相同（`docker compose ps` 换成 `docker ps --filter name=graph-engine`，
日志用 `docker logs -f graph-engine`）。

### 7.4 可选：worker 独立容器（未实测）

入口脚本固定启动 http/grpc/haproxy、不接收参数。要让 worker 独立成容器，用 `--entrypoint` 覆盖：

```bash
docker run -d --name graph-engine-worker --restart unless-stopped \
  --add-host host.docker.internal:host-gateway \
  -v /opt/graph-engine/data:/app/data \
  --env-file /opt/graph-engine/.env \
  --entrypoint graph-engine ikc-graph-engine:0.1.0 serve worker
```

- 两者必须挂同一数据目录（同一 SQLite），且 broker/backend 可达；此时主容器把
  `GRAPH_ENGINE_CELERY_ENABLED` 设为 `0`，避免重复消费。
- 简单场景建议直接用 7.3 单容器（worker 随引擎在容器内启动）。

## 8. Celery worker 与异步建图语义

- `POST /api/v1/graph/graphs/{graphId}/build` 带 `"async": true` → HTTP 进程登记 job 后投递 Celery，
  立即返回 `{"jobId":"job_xxx","status":"pending"}`；worker 消费后回写状态，`GET /api/v1/graph/jobs/{jobId}`
  可轮询到 `success`（失败为 `failed`；`GET /api/v1/graph/jobs?graphId=...` 可列作业）。
- 入口脚本在启动 worker 前做**有界等待**（30 × 0.5s 探测 broker 端口）：
  - 可达 → 后台启动 worker（日志 `[info] Celery broker 可达 ...`）；
  - 超时 → 打印 `[error] Celery broker 等待超时` **并跳过 worker**，HTTP/gRPC/HAProxy 不受影响，
    容器**不会**因此退出；此时异步作业会一直停留在 `pending`，需人工排查 Redis。
- 排查：`docker logs graph-engine-engine-1 | grep -i celery`，或
  `docker top graph-engine-engine-1`（**镜像内没有 `ps`**，`ps aux` 会报 not found；需要精确匹配
  argv 时用容器自带 python 扫 `/proc/*/cmdline`，与 `scripts/docker_smoke.sh` 步骤 7a 同款）。

## 9. 升级与回滚

```bash
# 本机（改代码后）
cd /home/sharkyai/semantica-graph-server
bash scripts/build_docker.sh
docker compose up -d --build          # 避免复用同 tag 旧镜像

# 目标机（离线包路径）
docker load -i _release/ikc-graph-engine_0.1.0.tar.gz
docker compose up -d --no-build

# 回滚：保留上一个 tar，load 后用同一份 compose 重建
docker load -i _release/graph-engine_0.0.9.tar.gz
docker compose up -d --no-build
```

- 数据在命名卷（`graph-engine_engine_data`）/ 宿主目录中，重建容器不丢数据；
  **`docker compose down -v` 会删除数据卷，严禁随手使用**。
- 升级后建议复跑验证：`curl -s http://127.0.0.1:18180/health` + `docker compose ps`（healthy）。
- `.env`（端口、凭据等）变更与镜像升级独立：改 `.env` 后 `docker compose up -d --force-recreate` 即可。

## 10. 常用运维与排障

```bash
docker compose logs -f engine                  # 跟踪日志
docker compose ps                              # 状态与健康
docker compose stop / start / restart          # 停止 / 启动 / 重启
docker stats graph-engine-engine-1             # 资源占用
docker exec -it graph-engine-engine-1 sh       # 进容器排障（非 root，uid 1000）
```

数据备份 / 恢复（compose 命名卷）：

```bash
# 备份
docker run --rm -v graph-engine_engine_data:/data -v "$(pwd)":/backup alpine \
  tar czf /backup/graph-engine-data.tgz -C /data .
# 恢复（先停容器）
docker compose down
docker run --rm -v graph-engine_engine_data:/data -v "$(pwd)":/backup alpine \
  sh -c 'rm -rf /data/* && tar xzf /backup/graph-engine-data.tgz -C /data'
docker compose up -d --no-build
```

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| 容器反复重启、日志 `[error] 引擎未在 30s 内就绪` / `引擎进程退出` | 引擎自身启动失败（依赖缺失、DB 不可写等）；入口脚本 fail-fast 是有意设计 | `docker logs graph-engine-engine-1` 看引擎 traceback；常见为 `ModuleNotFoundError: ikc_sdk`（镜像未含 `ikc-sdk-lib==0.8.3`，需重建镜像） |
| `sqlite3.OperationalError: unable to open database file` | 宿主挂载目录不可写（容器 uid 1000） | `sudo chown -R 1000:1000 <数据目录>` |
| 异步作业长期 `pending` | ①容器内 worker 未启动（broker 不可达）；②镜像缺 `redis` 包（kombu redis 传输导入失败，worker 启动即崩，日志含 `'NoneType' object has no attribute 'Redis'`） | ①确认宿主 Redis 监听 `0.0.0.0` 且有密码；②确认 `requirements.txt` 为 `celery[redis]>=5.3` 并重建镜像；`docker logs ... \| grep -i celery`；也可改同步调用（不加 `async`） |
| `POST /api/v1/graph/graphs` 返回 409 | 同一 `kbId` 已存在图谱（幂等/唯一约束，非故障） | 换 `kbId`，或先 `GET /api/v1/graph/graphs` 找到已有 `graphId` 复用 |
| 容器内 `ps`/`netstat` 报 not found | `python:3.12-slim` 精简镜像不含 procps/net-tools | 用 `docker top <容器>`、`docker exec <容器> graph-engine --help`，或容器 python 读 `/proc` |
| HTTP 504 / 网关超时 | HAProxy `timeout server 60s` 掐断长耗时请求 | 改用 `"async": true` + 轮询 job；或调长 `docker/haproxy.cfg` 后重建镜像 |
| 直连 `18010` / `50051` 失败 | 引擎只监听容器回环（安全设计） | 一律经 HAProxy：`18180` / `18151` |
| stats 401 | 账号密码不匹配 | 用 `.env` 的 `HAPROXY_STATS_USER/PASSWORD`；默认 `admin/change-me` 仅试用，启动日志会告警 |
| 端口被占用 | 宿主已有服务监听 | 改 `.env` 的 `HAPROXY_*_PORT` 后 `--force-recreate` |
| 磁盘快速增长 | 镜像数 GB + 卷数据 + 构建缓存 | `docker system df`；`docker builder prune` 清构建缓存（勿误删卷） |

## 11. 与 SDK / 上层服务对接

- 应用侧集成推荐用 SDK（独立包 `semantica-graph-sdk`，已发布 PyPI 0.1.0；依赖 `httpx` + `ikc-sdk-lib`）：

```bash
pip install "semantica-graph-sdk==0.1.0"
```

```python
from semantica_graph_sdk import GraphEngineClient

with GraphEngineClient("http://<服务器IP>:18180") as client:   # 本机直连引擎可用 18010
    graph = client.graphs.create(kbId="kb_demo", name="演示图")
    client.graphs.build(graph.graphId, docId="d1", entities=[{"name": "Alice", "type": "person"}])
    print(client.graphs.stat(graph.graphId).nodeCount)
```

- 覆盖 `graphs`（CRUD/建图合并/统计/查询/路径/分析/导出/SPARQL/检索）与 `jobs`（run/get/list/wait），
  同步 + 异步双客户端，统一 envelope/错误码/traceId 语义；用法见 `sdk/python/README.md`。
- 引擎线缆形状单一来源为 `ikc-sdk-lib==0.8.3`（图谱资产 G8 / `EngineJobView` 作业视图 G3 / 23 位 traceId G2），
  上层服务（open-ikc、ikc-core-service）按同一套模型校验，避免字段漂移。

## 12. 与 openwiki-server 部署手册的差异

| 维度 | openwiki-server `docs/deploy-offline.md` | 本手册 |
| --- | --- | --- |
| 定位 | 主打**离线镜像**（build → save → 传输 → load） | **本地构建运行**为主，离线迁移作为第 3/4 节可选路径 |
| 一键脚本 | `scripts/publish-offline.sh --build/--push`、`scripts/deploy-remote.sh` | `scripts/build_docker.sh`（默认构建 + `docker save` 导出离线包，`--no-save` 跳过；第 2.2/3 节） |
| 容器名/服务名 | `openwiki-server`（compose 服务 `app`；worker 用 `--profile worker` 另起容器） | `graph-engine-engine-1`（compose 服务 `engine`；worker 随引擎在容器内启动） |
| 端口 | 18011 / 50052 / 8404 | 18180 / 18151 / 8406（容器内 8080 / 50051 / 8404） |
| 数据卷 | `openwiki-server_app_data` | `graph-engine_engine_data` / `graph-engine_engine_logs` |
| 冒烟脚本 | 无（手册内逐条 curl） | `bash scripts/docker_smoke.sh`（8 项断言，含 gRPC/回环隔离/非 root/CLI/MCP） |
| 额外依赖 | 镜像内置可选 `ikc-log-center` | 镜像内置必需 `ikc-sdk-lib==0.8.3`（构建/离线预置见 2.1/2.3） |

## 13. 相关文档

- `docs/Docker部署与HAProxy.md` — 拓扑、构建、环境变量速查（精炼版）
- `docs/Docker构建进展.md` — 镜像构建改造与历史实测记录
- `docs/镜像体积精简评估.md` — 体积构成分析与瘦身路径（torch CUDA → CPU-only）
- `docs/解决方案.md` — 五面接口与分层设计；`proto/graph/v1/graph.proto` — gRPC 权威契约
- `/home/sharkyai/openwiki-server/docs/deploy-offline.md` — 参考的离线部署手册（同款单镜像 + HAProxy 拓扑）
