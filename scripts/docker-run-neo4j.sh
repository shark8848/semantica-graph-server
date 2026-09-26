#!/usr/bin/env bash
set -euo pipefail

# Neo4j 容器启停（图引擎的外部依赖；镜像准备/离线分发见 scripts/build_neo4j.sh）。
#
# 用法：
#   bash scripts/docker-run-neo4j.sh start     # 起容器（幂等：在跑 [skip] / 镜像变过 [recreate] / 缺镜像自动 load 离线包）
#   bash scripts/docker-run-neo4j.sh stop      # 停并删容器（命名卷保留）
#   bash scripts/docker-run-neo4j.sh restart   # = stop + start（改端口/内存/密码后用）
#   bash scripts/docker-run-neo4j.sh status    # 状态 + 探活 + 连接信息
#   bash scripts/docker-run-neo4j.sh logs [n]  # 跟日志（缺省末尾 100 行）
#
# 环境变量（可写在仓库根 .env，脚本自动读取；命令行/环境变量优先）：
#   IMAGE_TAG         镜像:标签（缺省 ikc-neo4j:${NEO4J_VERSION}）
#   NEO4J_VERSION     上游版本（缺省 5.26-community）
#   NEO4J_CONTAINER   容器名（缺省 neo4j）
#   NEO4J_PASSWORD    初始密码（缺省读 .env → 已存在容器的 NEO4J_AUTH → 都没有则随机生成并写入 .env，权限 600）
#   NEO4J_BIND        宿主发布绑定（缺省 0.0.0.0：引擎容器需经 host.docker.internal 访问，与宿主 redis 同口径；
#                     只本机用可设 127.0.0.1）
#   NEO4J_HTTP_PORT / NEO4J_BOLT_PORT   宿主端口（缺省 7474 / 7687）
#   NEO4J_HEAP / NEO4J_PAGECACHE        内存（缺省 512m / 512m）
#   NEO4J_WAIT        起后等待就绪的秒数（缺省 90）
#
# 说明：
#   - 容器 `--restart unless-stopped`：**宿主重启后自动拉起**（这就是「自动启动」）；`stop` 之后不会被拉起。
#   - 数据 / 日志 / 插件 / 导入落命名卷 `neo4j-data` / `neo4j-logs` / `neo4j-plugins` / `neo4j-import`。
#   - 同栈容器接 Neo4j 用 `bolt://host.docker.internal:<NEO4J_BOLT_PORT>`（宿主发布端口，与 redis 同口径）；
#     宿主机直连用 `bolt://127.0.0.1:<NEO4J_BOLT_PORT>`、浏览器 `http://127.0.0.1:<NEO4J_HTTP_PORT>`。
#   - 引擎（graph_engine）当前**没有** Neo4j 适配器（图数据仍落 SQLite `GRAPH_ENGINE_DB_PATH`）：
#     本脚本只把 Neo4j 起好备用，接进引擎数据面属另一次代码改动。

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
ENV_FILE="$ROOT_DIR/.env"

have_image() { docker image inspect "$1" >/dev/null 2>&1; }

env_val() { # env_val <KEY> → .env 里最后一个取值（剥掉包裹引号）
  [[ -f "$ENV_FILE" ]] || return 0
  local v
  v="$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -1)"
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  printf '%s' "$v"
}

pick() { # pick <变量名> <缺省> → 环境变量 → .env → 缺省
  local name="$1" def="${2:-}" v
  v="${!name:-}"
  [[ -n "$v" ]] || v="$(env_val "$name")"
  printf '%s' "${v:-$def}"
}

usage() { awk 'NR==1{next} /^#/{print substr($0,3); started=1} started && !/^#/{exit}' "$0"; }

NEO4J_VERSION="$(pick NEO4J_VERSION 5.26-community)"
IMAGE_TAG="$(pick IMAGE_TAG "ikc-neo4j:${NEO4J_VERSION}")"
CONTAINER="$(pick NEO4J_CONTAINER neo4j)"
BIND="$(pick NEO4J_BIND 0.0.0.0)"
HTTP_PORT="$(pick NEO4J_HTTP_PORT 7474)"
BOLT_PORT="$(pick NEO4J_BOLT_PORT 7687)"
HEAP="$(pick NEO4J_HEAP 512m)"
PAGECACHE="$(pick NEO4J_PAGECACHE 512m)"
WAIT_SECS="$(pick NEO4J_WAIT 90)"

pw_from_container() { # 已存在容器的 NEO4J_AUTH=neo4j/<pw>
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" 2>/dev/null \
    | sed -n 's/^NEO4J_AUTH=neo4j\///p' | tail -1 || true
}
PW="$(pick NEO4J_PASSWORD '')"
[[ -n "$PW" ]] || PW="$(pw_from_container)"

ensure_password() {
  [[ -n "$PW" ]] && return 0
  PW="$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  ( umask 077; printf '\n# Neo4j（scripts/docker-run-neo4j.sh 首次启动自动生成，勿删：删了与已有数据卷密码不一致）\nNEO4J_PASSWORD=%s\n' "$PW" >> "$ENV_FILE" )
  chmod 600 "$ENV_FILE"
  echo "[init] $ENV_FILE 无 NEO4J_PASSWORD → 已生成并写入（权限 600）" >&2
  echo "[init] Neo4j 认证：neo4j / $PW" >&2
}

ensure_image() {
  have_image "$IMAGE_TAG" && return 0
  local pkg="" f
  shopt -s nullglob
  for f in "$ROOT_DIR"/docker/images/*neo4j*.tar.gz "$ROOT_DIR"/docker/images/*neo4j*.tar; do pkg="$f"; break; done
  shopt -u nullglob
  if [[ -n "$pkg" ]]; then
    echo "[load] 缺镜像 $IMAGE_TAG → 自动导入离线包 $(basename "$pkg")"
    IMAGE_TAG="$IMAGE_TAG" bash "$ROOT_DIR/scripts/build_neo4j.sh" --load "$pkg" >/dev/null
  fi
  have_image "$IMAGE_TAG" || {
    echo "[err] 缺镜像 $IMAGE_TAG" >&2
    echo "      先 bash scripts/build_neo4j.sh（联网）或 bash scripts/build_neo4j.sh --load <离线包>" >&2
    exit 1
  }
}

run_container() {
  docker run -d --name "$CONTAINER" --restart unless-stopped \
    --add-host host.docker.internal:host-gateway \
    -e "NEO4J_AUTH=neo4j/${PW}" \
    -e "NEO4J_server_default__listen__address=0.0.0.0" \
    -e "NEO4J_server_memory_heap_initial__size=${HEAP}" \
    -e "NEO4J_server_memory_heap_max__size=${HEAP}" \
    -e "NEO4J_server_memory_pagecache_size=${PAGECACHE}" \
    -v neo4j-data:/data \
    -v neo4j-logs:/logs \
    -v neo4j-plugins:/plugins \
    -v neo4j-import:/var/lib/neo4j/import \
    -p "${BIND:+${BIND}:}${HTTP_PORT}:7474" \
    -p "${BIND:+${BIND}:}${BOLT_PORT}:7687" \
    "$IMAGE_TAG" >/dev/null
}

wait_ready() {
  local i=0
  while ((i < WAIT_SECS)); do
    if docker exec "$CONTAINER" cypher-shell -u neo4j -p "$PW" 'RETURN 1;' >/dev/null 2>&1; then return 0; fi
    if curl -fsS -m 2 "http://127.0.0.1:${HTTP_PORT}/" >/dev/null 2>&1; then return 0; fi
    sleep 1
    i=$((i + 1))
  done
  return 1
}

print_info() {
  echo "   Bolt : bolt://127.0.0.1:${BOLT_PORT}（同栈容器：bolt://host.docker.internal:${BOLT_PORT}）"
  echo "   HTTP : http://127.0.0.1:${HTTP_PORT}（Neo4j Browser）"
  echo "   认证 : neo4j / ${PW:0:4}****（完整值见 $ENV_FILE 或 NEO4J_PASSWORD）"
  echo "   数据 : 命名卷 neo4j-data / neo4j-logs / neo4j-plugins / neo4j-import"
  echo "   日志 : bash scripts/docker-run-neo4j.sh logs"
}

cmd_start() {
  ensure_password
  ensure_image
  local state="" cid="" tid="" action="created"
  state="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || true)"
  if [[ -z "$state" ]]; then
    echo "[create] 新建容器 $CONTAINER（镜像 $IMAGE_TAG，绑定 ${BIND:-0.0.0.0}，端口 ${HTTP_PORT}/7474 ${BOLT_PORT}/7687）"
    run_container
    action="created"
  elif [[ "$state" != "running" ]]; then
    echo "[start]  容器 $CONTAINER 处于 $state → docker start"
    docker start "$CONTAINER" >/dev/null
    action="started"
  else
    cid="$(docker inspect -f '{{.Image}}' "$CONTAINER")"
    tid="$(docker image inspect -f '{{.Id}}' "$IMAGE_TAG")"
    if [[ "$cid" != "$tid" ]]; then
      echo "[recreate] 容器引用的镜像与 $IMAGE_TAG 不一致（镜像重建过 / 换 tag）→ 就地重建"
      docker rm -f "$CONTAINER" >/dev/null
      run_container
      action="recreated"
    else
      echo "[skip]   $CONTAINER 已在跑且镜像一致（改端口 / 内存 / 密码请用 restart）"
      action="skip"
    fi
  fi
  if wait_ready; then
    echo "[ok]     $CONTAINER（$action）已就绪"
  else
    echo "[warn]   $CONTAINER（$action）已起，但 ${WAIT_SECS}s 内未探活成功——看 bash scripts/docker-run-neo4j.sh logs" >&2
  fi
  print_info
}

cmd_stop() {
  if [[ -z "$(docker inspect -f '{{.Id}}' "$CONTAINER" 2>/dev/null || true)" ]]; then
    echo "[skip] 容器 $CONTAINER 不存在"
    return 0
  fi
  docker rm -f "$CONTAINER" >/dev/null
  echo "[stop] 已停并删除容器 $CONTAINER（命名卷保留：neo4j-data / neo4j-logs / neo4j-plugins / neo4j-import）"
}

cmd_status() {
  if [[ -z "$(docker inspect -f '{{.Id}}' "$CONTAINER" 2>/dev/null || true)" ]]; then
    echo "[status] 容器 $CONTAINER 不存在（bash scripts/docker-run-neo4j.sh start）"
    return 0
  fi
  docker ps -a --filter "name=^/${CONTAINER}$" --format '  {{.Names}}\t{{.Status}}\t{{.Image}}'
  if [[ -n "$PW" ]] && docker exec "$CONTAINER" cypher-shell -u neo4j -p "$PW" 'RETURN 1;' >/dev/null 2>&1; then
    echo "  [ok] cypher-shell 认证成功（bolt 通）"
  elif curl -fsS -m 2 "http://127.0.0.1:${HTTP_PORT}/" >/dev/null 2>&1; then
    echo "  [warn] HTTP 通，但没能用当前密码跑通 cypher-shell（密码可能不是 .env 里那把）"
  else
    echo "  [err] 探活失败（docker logs $CONTAINER）"
  fi
  print_info
}

case "${1:-}" in
  start)   cmd_start ;;
  stop)    cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status)  cmd_status ;;
  logs)    shift; docker logs -f --tail "${1:-100}" "$CONTAINER" ;;
  -h|--help|"") usage; [[ -n "${1:-}" ]] || exit 1 ;;
  *) echo "[usage] 未知子命令: $1（start / stop / restart / status / logs / -h）" >&2; exit 1 ;;
esac
