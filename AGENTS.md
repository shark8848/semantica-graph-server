# Repository Guidelines

Semantica Graph Engine：把已安装的 `semantica`（0.6.5）封装为对外完整服务的图引擎，统一提供
**HTTP / gRPC / Celery / MCP / CLI** 五类接口；open-ikc 的图谱服务能力可消费本引擎。
权威设计文档：`docs/解决方案.md`；gRPC 契约：`proto/graph/v1/graph.proto`。

## Project Structure & Module Organization

- `docs/解决方案.md` — 设计方案（五面接口、分层、稳定 ID/合并/证据/置信度语义）。
- `proto/graph/v1/graph.proto` — gRPC 权威契约；`graph_engine/interfaces/grpc_server.py` 为运行时动态 descriptor 实现。
- `graph_engine/interfaces/` — HTTP / gRPC / Celery / MCP / CLI 五面；共用 `application.GraphEngineService`。
- `graph_engine/domain/` — 稳定 ID / schema / 合并规则（与 open-ikc 语义兼容）。
- `graph_engine/adapters/` — semantica 集成（守卫式导入）+ SQLite 持久化。
- `docker/` + `Dockerfile` — 单镜像部署（图引擎 + HAProxy 代理层）；`scripts/build_docker.sh`（构建）、`scripts/docker_smoke.sh`（冒烟）。
- 新增代码按此分层放置，并保持 `docs/解决方案.md` 与接口 envelope 语义同步。

## Build, Test, and Development Commands

- 安装：`.venv/bin/pip install -e . --no-build-isolation`
- 测试：`.venv/bin/pip install -e '.[test]' --no-build-isolation` 后 `.venv/bin/python -m pytest tests -q`
- 本地启动：`graph-engine serve http`（默认 18010）/ `serve grpc` / `serve worker` / `serve mcp`
- Docker：`bash scripts/build_docker.sh` → `docker compose up -d`（对外 HTTP 18180 / gRPC 18151 / stats 8406）→ `bash scripts/docker_smoke.sh`

## Coding Style & Naming Conventions

- Python：PEP 8、4 空格缩进、`snake_case`、类型注解、`from __future__ import annotations`。
- 设计/文档用中文；代码标识符与提交信息沿用现有中文风格（Conventional Commits：`feat:`/`fix:`/`docs:`/`chore:`）。
- 不引入未配置的格式化/静态检查工具；改动保持最小并与现有风格一致。

## Testing Guidelines

- 全量回归：`.venv/bin/python -m pytest tests -q`（12 用例，覆盖 domain/存储/application/五面接口）。
- Docker 改动必须跑 `bash scripts/docker_smoke.sh`（/health、HTTP create+stat、gRPC 经 HAProxy、stats 鉴权、回环隔离、非 root）。

## GitHub Projects Sync (PROJ-SEMANTICA-0001)

- Target board: https://github.com/users/shark8848/projects/5（用户级 Projects v2，标题 "@shark8848's semantica-graph-server project"）。
- Auth: Classic PAT（`project` scope）写入 `/tmp/gh_token`（chmod 600）；**禁止入库**；
  fine-grained PAT 无法访问用户级 Projects v2。
- 条目清单（契约的一部分）：`docs/project-board.tsv`，格式 `标题<TAB>状态<TAB>优先级`；
  状态 ∈ `未开始|进行中|已完成`（看板选项为英文，按别名映射 Backlog / In progress / Done），
  优先级 ∈ `P0|P1|P2`（可空）。
- 同步：`bash scripts/sync-github-projects.sh docs/project-board.tsv`（按标题幂等 upsert，缺则创建、存在只更新）。
- 流程：改 TSV → 同步 → 将 TSV 与脚本/契约一起提交。

## Push / Remote Contract

- `origin`：`git@github.com:shark8848/semantica-graph-server.git`（已验证 SSH 22/443 均连通）。
- 直接推 `main`（direct-push，无 PR），与 `/home/open-ikc`、`/home/ontolith` 工作流一致。
- 推送前用 `git remote -v` 核对远端，勿改回内部镜像地址。
