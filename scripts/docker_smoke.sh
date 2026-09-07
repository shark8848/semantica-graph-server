#!/usr/bin/env bash
set -euo pipefail

# Docker 单镜像栈冒烟验证：
#   1) 构建镜像（若 IMAGE_TAG 已存在则跳过构建，可用 --force-build 强制）
#   2) 起容器（HAProxy 对外 18180 HTTP / 18151 gRPC / 8406 stats）
#   3) 断言：/health、HTTP create+stat、gRPC 经 HAProxy 调用、
#      stats 默认凭据 200 / 错误凭据 401、
#      引擎 18010/50051 仅回环（容器 IP 连接被拒）、容器非 root 运行、
#      Celery 端到端（真实 Redis：worker 进程 + HTTP async 建图至 success）、
#      容器内 CLI / MCP 冒烟
# 用法：bash scripts/docker_smoke.sh [--force-build]

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

FORCE_BUILD=0
[[ "${1:-}" == "--force-build" ]] && FORCE_BUILD=1

VERSION="$(sed -n 's/^version = "\([0-9][0-9.]*\)".*/\1/p' pyproject.toml | head -1)"
IMAGE_TAG="${IMAGE_TAG:-graph-engine:${VERSION:-0.1.0}}"
HTTP_PORT="${HAPROXY_HTTP_PORT:-18180}"
GRPC_PORT="${HAPROXY_GRPC_PORT:-18151}"
STATS_PORT="${HAPROXY_STATS_PORT:-8406}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[smoke] 未找到 docker 命令" >&2
  exit 1
fi

if [[ "$FORCE_BUILD" == "1" ]] || ! docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
  echo "[smoke] 构建镜像 $IMAGE_TAG ..."
  bash scripts/build_docker.sh
fi

cleanup() {
  docker compose down >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[smoke] 启动容器 ..."
docker compose up -d >/dev/null

# 等待 HAProxy HTTP 入口就绪（最多 60s）
for _ in $(seq 1 60); do
  if curl -fsS -m 3 "http://127.0.0.1:${HTTP_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

fail() {
  echo "[smoke] FAIL: $1" >&2
  exit 1
}

echo "[smoke] 1. /health 经 HAProxy"
curl -fsS -m 5 "http://127.0.0.1:${HTTP_PORT}/health" >/dev/null || fail "/health 未就绪"

echo "[smoke] 2. HTTP create + stat（经 HAProxy）"
SUFFIX=$(date +%s)
CREATE=$(curl -s -m 15 -X POST "http://127.0.0.1:${HTTP_PORT}/api/v1/graph/graphs" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"docker-smoke-${SUFFIX}\",\"kbId\":\"kb_docker_smoke_${SUFFIX}\",\"graphSchema\":{\"entityTypes\":[{\"type\":\"person\"}],\"relationTypes\":[]}}")
echo "$CREATE" | grep -q '"errCode":"0"' || fail "create 失败: $CREATE"
GID=$(echo "$CREATE" | sed -n 's/.*"graphId":"\([^"]*\)".*/\1/p')
STAT=$(curl -s -m 15 "http://127.0.0.1:${HTTP_PORT}/api/v1/graph/graphs/${GID}/stat")
echo "$STAT" | grep -q '"nodeCount":0' || fail "stat 异常: $STAT"

echo "[smoke] 3. gRPC 经 HAProxy 调用（${GRPC_PORT}）"
.venv/bin/python - "$GRPC_PORT" "$SUFFIX" <<'PY' || fail "gRPC 经 HAProxy 调用失败"
import sys
from graph_engine.interfaces.grpc_server import grpc_client

port = int(sys.argv[1])
suffix = sys.argv[2]
client = grpc_client("127.0.0.1", port)
resp = client("CreateGraph", {"name": f"grpc-smoke-{suffix}", "kbId": f"kb_grpc_smoke_{suffix}", "graphSchema": {"entityTypes": [{"type": "person"}], "relationTypes": []}})
assert resp["errCode"] == "0", resp
PY

echo "[smoke] 4. stats 默认凭据 200 / 错误凭据 401"
C1=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -u "admin:change-me" "http://127.0.0.1:${STATS_PORT}/")
[[ "$C1" == "200" ]] || fail "stats 默认凭据应 200，实际 $C1"
C2=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -u "admin:wrong" "http://127.0.0.1:${STATS_PORT}/")
[[ "$C2" == "401" ]] || fail "stats 错误凭据应 401，实际 $C2"

echo "[smoke] 5. 引擎 18010/50051 仅回环（容器 IP 直连引擎 HTTP 应被拒，引擎 socket 仅在 127.0.0.1）"
CID=$(docker compose ps -q engine)
docker exec "$CID" python -c '
import os, re, socket, subprocess, sys

ip = subprocess.check_output(["hostname", "-i"]).decode().strip()

# 1) HAProxy 不暴露引擎 HTTP 端口 18010：容器 IP 直连必须被拒
s = socket.socket()
try:
    s.settimeout(1)
    s.connect((ip, 18010))
except (ConnectionRefusedError, OSError):
    pass
else:
    print("FAIL: 容器 IP 可连引擎 HTTP 18010")
    sys.exit(1)
finally:
    s.close()

# 2) 引擎进程（serve http/grpc）的监听 socket 必须仅在回环
#    （127.0.0.1 / ::1 / ::ffff:127.0.0.1）；50051 上容器 IP 可连属 HAProxy 对外入口，不在此列
listen = {}
for f in ("/proc/net/tcp", "/proc/net/tcp6"):
    for line in open(f).read().splitlines()[1:]:
        p = line.split()
        if p[3] == "0A":
            listen[p[9]] = p[1]
LOOPBACK = ("0100007F", "00000000000000000000000000000001", "0000000000000000FFFF00000100007F")
bad = []
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace").replace(chr(0), " ").strip()
    except OSError:
        continue
    if not re.search(r"graph-engine serve (http|grpc)", cmd):
        continue
    for fd in os.listdir(f"/proc/{pid}/fd"):
        try:
            link = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        m = re.match(r"socket:\[(\d+)\]", link)
        if m and m.group(1) in listen and listen[m.group(1)].split(":")[0] not in LOOPBACK:
            bad.append(f"pid={pid} {cmd} -> {listen[m.group(1)]}")
if bad:
    print("FAIL: 引擎存在非回环监听:", bad)
    sys.exit(1)
sys.exit(0)
' || fail "回环隔离失效"

echo "[smoke] 6. 容器非 root 运行"
docker exec "$CID" sh -c 'test "$(id -u)" = "1000"' || fail "容器应以 uid 1000 运行"

echo "[smoke] 7. Celery 端到端（真实 Redis：worker 进程 + HTTP async 建图）"

# 7a) engine 容器内应存在 `graph-engine serve worker` 进程（worker 由 entrypoint 在
#     broker 可达后启动；celery 可能改写进程标题，故按 argv 特征匹配）
docker exec "$CID" python -c '
import os, re, sys

def cmdline(pid):
    try:
        return open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace").replace(chr(0), " ").strip()
    except OSError:
        return ""

found = []
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    cmd = cmdline(pid)
    if not cmd:
        continue
    if re.search(r"graph-engine serve worker|graph-engine: worker", cmd) or ("graph-engine" in cmd and " worker" in cmd):
        found.append((pid, cmd))
if not found:
    print("FAIL: 未发现 celery worker 进程", file=sys.stderr)
    sys.exit(1)
for pid, cmd in found:
    print(f"worker pid={pid} cmd={cmd}")
sys.exit(0)
' || fail "engine 容器内未发现 graph-engine serve worker 进程"

# 7b) 经 HAProxy HTTP：建图（schema 含 person）→ async=true 建图 → 轮询 job 至 success
ASYNC_SUFFIX=$(date +%s)
CREATE_ASYNC=$(curl -s -m 15 -X POST "http://127.0.0.1:${HTTP_PORT}/api/v1/graph/graphs" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"docker-smoke-async-${ASYNC_SUFFIX}\",\"kbId\":\"kb_docker_smoke_async_${ASYNC_SUFFIX}\",\"graphSchema\":{\"entityTypes\":[{\"type\":\"person\"}],\"relationTypes\":[]}}")
echo "$CREATE_ASYNC" | grep -q '"errCode":"0"' || fail "async 建图前置 create 失败: $CREATE_ASYNC"
ASYNC_GID=$(echo "$CREATE_ASYNC" | sed -n 's/.*"graphId":"\([^"]*\)".*/\1/p')
BUILD_ASYNC=$(curl -s -m 15 -X POST "http://127.0.0.1:${HTTP_PORT}/api/v1/graph/graphs/${ASYNC_GID}/build" \
  -H "Content-Type: application/json" \
  -d "{\"async\":true,\"docId\":\"doc-async-${ASYNC_SUFFIX}\",\"entities\":[{\"name\":\"Alice\",\"type\":\"person\"}]}")
echo "$BUILD_ASYNC" | grep -q '"errCode":"0"' || fail "async 建图提交失败: $BUILD_ASYNC"
echo "$BUILD_ASYNC" | grep -q '"status":"pending"' || fail "async 建图未返回 pending 状态: $BUILD_ASYNC"
ASYNC_JOB=$(echo "$BUILD_ASYNC" | sed -n 's/.*"jobId":"\([^"]*\)".*/\1/p')
[[ -n "$ASYNC_JOB" ]] || fail "async 建图未返回 jobId: $BUILD_ASYNC"

ASYNC_STATUS=""
for _ in $(seq 1 60); do
  JOB=$(curl -s -m 5 "http://127.0.0.1:${HTTP_PORT}/api/v1/graph/jobs/${ASYNC_JOB}")
  if echo "$JOB" | grep -q '"errCode":"0"' && echo "$JOB" | grep -q '"status":"success"'; then
    ASYNC_STATUS="success"
    break
  fi
  if echo "$JOB" | grep -q '"status":"failed"'; then
    fail "异步建图任务失败: $JOB"
  fi
  sleep 1
done
[[ "$ASYNC_STATUS" == "success" ]] || fail "轮询 60s 后 job(${ASYNC_JOB}) 仍未 success"

# 7c) 任务完成后 stat nodeCount 应符合预期（Alice 1 个 person 节点）
STAT_ASYNC=$(curl -s -m 15 "http://127.0.0.1:${HTTP_PORT}/api/v1/graph/graphs/${ASYNC_GID}/stat")
echo "$STAT_ASYNC" | grep -q '"nodeCount":1' || fail "async 建图后 stat 异常: $STAT_ASYNC"

echo "[smoke] 8. 镜像内 CLI + MCP 冒烟（docker exec，appuser 非 root）"

# 8a) CLI：--help 退出码 0；create + stat 走真实引擎（与 HTTP 同容器共享
#     engine_data 卷的 SQLite）。CLI 成功输出为裸 JSON（无 envelope），
#     断言 create 返回 graphId、stat 返回 nodeCount=0。
docker exec "$CID" graph-engine --help >/dev/null || fail "graph-engine --help 退出码非 0"
CLI_SUFFIX=$(date +%s)
CLI_CREATE=$(docker exec "$CID" graph-engine create --name "cli-smoke-${CLI_SUFFIX}" --kb-id "kb_cli_smoke_${CLI_SUFFIX}" \
  --schema '{"entityTypes":[{"type":"person"}],"relationTypes":[]}')
echo "$CLI_CREATE" | grep -Eq '"graphId"[[:space:]]*:' || fail "graph-engine create 输出缺少 graphId: $CLI_CREATE"
CLI_GID=$(echo "$CLI_CREATE" | sed -n 's/.*"graphId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[[ -n "$CLI_GID" ]] || fail "graph-engine create 未返回 graphId: $CLI_CREATE"
CLI_STAT=$(docker exec "$CID" graph-engine stat "$CLI_GID")
echo "$CLI_STAT" | grep -Eq '"nodeCount"[[:space:]]*:[[:space:]]*0' || fail "graph-engine stat 异常: $CLI_STAT"

# 8b) MCP：stdio JSON-RPC 握手（initialize + tools/list），stdin EOF 后进程退出；
#     输出应为每行一条 JSON 响应，含对应 id 与 result（timeout 15 兜底）
MCP_OUT=$(printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"docker-smoke","version":"1.0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | docker exec -i "$CID" bash -lc 'timeout 15 graph-engine serve mcp') || fail "MCP stdio 握手失败或超时"
echo "$MCP_OUT" | grep -q '"id": 1' || fail "MCP initialize 缺少 id=1 响应: $MCP_OUT"
echo "$MCP_OUT" | grep -q '"serverInfo"' || fail "MCP initialize 缺少 result.serverInfo: $MCP_OUT"
echo "$MCP_OUT" | grep -q '"id": 2' || fail "MCP tools/list 缺少 id=2 响应: $MCP_OUT"
echo "$MCP_OUT" | grep -q '"graph_create"' || fail "MCP tools/list 缺少工具列表: $MCP_OUT"

echo "[smoke] PASS：单镜像栈全部冒烟通过（HTTP ${HTTP_PORT} / gRPC ${GRPC_PORT} / stats ${STATS_PORT} / Celery / MCP / CLI）"
