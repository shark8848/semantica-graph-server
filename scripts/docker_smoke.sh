#!/usr/bin/env bash
set -euo pipefail

# Docker 单镜像栈冒烟验证：
#   1) 构建镜像（若 IMAGE_TAG 已存在则跳过构建，可用 --force-build 强制）
#   2) 起容器（HAProxy 对外 18180 HTTP / 18151 gRPC / 8406 stats）
#   3) 断言：/health、HTTP create+stat、gRPC 经 HAProxy 调用、
#      stats 默认凭据 200 / 错误凭据 401、
#      引擎 18010/50051 仅回环（容器 IP 连接被拒）、容器非 root 运行
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

echo "[smoke] PASS：单镜像栈全部冒烟通过（HTTP ${HTTP_PORT} / gRPC ${GRPC_PORT} / stats ${STATS_PORT}）"
