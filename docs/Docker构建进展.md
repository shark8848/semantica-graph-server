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
| `scripts/build_docker.sh` | 构建脚本（`--no-cache` / `--pull` 可选，IMAGE_TAG 可覆盖，默认 `graph-engine:<pyproject 版本>`，输出镜像体积） |
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
