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

## 委派执行契约（PROJ-SEMANTICA-0002：qoderclicn / jscode 常态委派）

- 看板剩余任务中的**简单/轻量实现类任务默认委派给本机 agent 执行**，候选执行方：
  - **qoderclicn**（QoderCN CLI，入口 `/home/sharkyai/.local/bin/qoderclicn`，非交互 `-p/--print`）；
  - **jscode**（入口 `/home/sharkyai/.nvm/versions/node/v24.20.0/bin/jscode`，`jscode run "<任务说明>" --dir <repo>`）。
  两者任一可用即委派；可并行时以 `--worktree <name>` 隔离，或拆分不重叠写文件范围。
  Codex 会话负责编排、评审、复验、提交与看板同步（git 写操作、GitHub Projects 同步默认不委托）。
- **委派范围只限三类**：写代码、写文档、单元测试。**以下任务一律不委派**：
  - 复杂构建/重活（Docker 镜像构建、`docker compose up`、重量级编译/打包、CI 类全流程）；
  - 系统级/网络级操作（pip 安装卸载、依赖升级、端口/服务管理、sudo、下载大文件）；
  - 部署与生产变更、需要外部凭据/机密的操作、不可逆数据操作。
  上述任务留在 Codex 会话内执行（逐条申请人工批准），或输出为"待人工执行"清单。
- 委派调用范式（非交互、单仓库直接执行）：
  `qoderclicn -p "<任务说明>" --cwd /home/sharkyai/semantica-graph-server`
  或 `jscode run "<任务说明>" --dir /home/sharkyai/semantica-graph-server`
  （权限模式按需选 `accept_edits`/`auto`（qoderclicn）或默认非危险模式（jscode，禁止默认启用
  `--dangerously-skip-permissions`）；即便高权限也只允许执行任务说明内的命令，禁止说明外的
  docker/构建/网络/系统命令）
  - 任务说明必须包含：可写文件白名单、禁止改动清单、禁止行为（git 写操作/删除数据/泄露凭据）、
    验收命令与通过标准、完成后报告格式（改动文件清单 + 关键验证输出）。
  - 需工作树隔离的并行任务用 `--worktree <name>` 起独立 worktree，完成后由 Codex 会话合并评审；
    单一 `--cwd` 任务必须限定不重叠的写文件范围。
  - 单元测试委派只允许跑仓库自带验证（如 `.venv/bin/python -m pytest tests -q`、`bash -n`）；
    禁止在委派任务里附带 Docker/pip/网络验证。
  - 涉密操作（`/tmp/gh_token` 等）禁止委托。
- 委派结果回收规则：Codex 会话必须复验（`pytest tests -q`、`bash -n`、必要时 Docker 冒烟），
  再更新 `docs/project-board.tsv` 与文档，按 Conventional Commits 提交并推送 `origin/main`。
