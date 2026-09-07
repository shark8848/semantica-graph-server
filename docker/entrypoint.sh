#!/bin/sh
set -eu

# 单镜像入口：先启动引擎内部服务（HTTP/gRPC 仅监听回环 127.0.0.1，不对外），
# 再前台运行 HAProxy 作为唯一对外入口（:8080 HTTP / :50051 gRPC / :8404 stats）。

# ---------- 自动配置：默认参数（全部可用环境变量覆盖） ----------
# 必须 export 供 envsubst（独立进程）读取；否则模板变量不会被替换
export GRAPH_ENGINE_HTTP_PORT="${GRAPH_ENGINE_HTTP_PORT:-18010}"
export GRAPH_ENGINE_GRPC_PORT="${GRAPH_ENGINE_GRPC_PORT:-50051}"
export HAPROXY_STATS_USER="${HAPROXY_STATS_USER:-admin}"
export HAPROXY_STATS_PASSWORD="${HAPROXY_STATS_PASSWORD:-change-me}"
GRAPH_ENGINE_CELERY_ENABLED="${GRAPH_ENGINE_CELERY_ENABLED:-0}"
GRAPH_ENGINE_CELERY_BROKER="${GRAPH_ENGINE_CELERY_BROKER:-redis://localhost:6379/0}"

# 引擎服务只监听回环，避免绕过 HAProxy 直连
export GRAPH_ENGINE_HTTP_HOST=127.0.0.1
export GRAPH_ENGINE_GRPC_HOST=127.0.0.1

# 渲染 HAProxy 配置模板（引擎端口 + stats 凭据），写到 /tmp（appuser 可写）
envsubst '${GRAPH_ENGINE_HTTP_PORT} ${GRAPH_ENGINE_GRPC_PORT} ${HAPROXY_STATS_USER} ${HAPROXY_STATS_PASSWORD}' \
  < /etc/haproxy/haproxy.cfg.tmpl \
  > /tmp/haproxy.cfg

# 默认 stats 凭据告警（admin/change-me 仅限本地试用，生产必须设置 HAPROXY_STATS_PASSWORD）
if [ "${HAPROXY_STATS_USER}" = "admin" ] && [ "${HAPROXY_STATS_PASSWORD}" = "change-me" ]; then
  echo "[warn] HAProxy stats 使用默认凭据 admin/change-me，生产请设置 HAPROXY_STATS_PASSWORD" >&2
fi

# ---------- 启动内部服务（仅回环） ----------
graph-engine serve http &
HTTP_PID=$!

graph-engine serve grpc &
GRPC_PID=$!

# Celery worker（默认关闭，GRAPH_ENGINE_CELERY_ENABLED=1 时启用）：启动前先做有界等待，
# 确保 broker(redis) 可达（解析 redis://host:port，python socket 探测 30 次 × 0.5s）。
# 可达才后台启动 worker；超时打印错误并跳过 worker——HTTP/gRPC/HAProxy 不受影响，
# 容器不因缺少 broker 而整体退出（异步任务将保持 pending，便于人工排查）。
celery_broker_ready() {
  python - "${GRAPH_ENGINE_CELERY_BROKER}" <<'PY'
import socket
import sys
import time
from urllib.parse import urlsplit

url = sys.argv[1] or "redis://localhost:6379/0"
u = urlsplit(url)
host = u.hostname or "127.0.0.1"
port = u.port or 6379
for _ in range(30):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            sys.exit(0)
    except OSError:
        time.sleep(0.5)
print(f"[error] Celery broker 不可达：{host}:{port}（{url}）", file=sys.stderr)
sys.exit(1)
PY
}

CELERY_PID=""
if [ "${GRAPH_ENGINE_CELERY_ENABLED}" = "1" ]; then
  if celery_broker_ready; then
    echo "[info] Celery broker 可达（${GRAPH_ENGINE_CELERY_BROKER}），后台启动 worker"
    graph-engine serve worker &
    CELERY_PID=$!
  else
    echo "[error] Celery broker 等待超时（30 次 × 0.5s），本次不启动 worker" >&2
  fi
fi

# ---------- 等待引擎就绪（HTTP /health + gRPC TCP，最多 60 次 × 0.5s） ----------
READY=0
for _ in $(seq 1 60); do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${GRAPH_ENGINE_HTTP_PORT}/health', timeout=1)" >/dev/null 2>&1 \
     && python -c "import socket; s=socket.create_connection(('127.0.0.1', ${GRAPH_ENGINE_GRPC_PORT}), timeout=1); s.close()" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "$HTTP_PID" 2>/dev/null || ! kill -0 "$GRPC_PID" 2>/dev/null; then
    echo "[error] 引擎进程退出（http=$HTTP_PID grpc=$GRPC_PID），容器终止以便重启" >&2
    exit 1
  fi
  sleep 0.5
done
if [ "$READY" != "1" ]; then
  echo "[error] 引擎未在 30s 内就绪，容器终止以便重启" >&2
  kill "$HTTP_PID" "$GRPC_PID" 2>/dev/null || true
  exit 1
fi

# ---------- 前台运行 HAProxy（容器退出时同步清理引擎进程） ----------
/usr/sbin/haproxy -f /tmp/haproxy.cfg &
HAPROXY_PID=$!

forward() {
  kill -"$1" "$HTTP_PID" "$GRPC_PID" "$HAPROXY_PID" "$CELERY_PID" 2>/dev/null || true
}
trap 'forward TERM' TERM
trap 'forward INT' INT

wait "$HAPROXY_PID"
kill "$HTTP_PID" "$GRPC_PID" "$CELERY_PID" 2>/dev/null || true
wait "$HTTP_PID" 2>/dev/null || true
wait "$GRPC_PID" 2>/dev/null || true
wait "$CELERY_PID" 2>/dev/null || true
