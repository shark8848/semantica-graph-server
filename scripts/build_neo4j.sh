#!/usr/bin/env bash
set -euo pipefail

# 准备 Neo4j 镜像（图引擎的外部依赖，单独一份可离线分发的 `ikc-neo4j:<tag>`）。
#
# 上游是官方镜像、**不重编**：本脚本 = 取镜像（pull / 或复用本地）→ 打本栈命名 tag →（缺省）导出离线包。
# 与本仓 scripts/build_docker.sh 同口径：镜像名一律 `ikc-*`、产物落 docker/images/、目标机 `docker load -i`。
# 容器启停见 scripts/docker-run-neo4j.sh（`restart=unless-stopped`，宿主重启自动拉起）。
#
# 用法：
#   bash scripts/build_neo4j.sh                  # pull → tag ikc-neo4j:<ver> → 导出 docker/images/ikc-neo4j_<ver>.tar.gz
#   bash scripts/build_neo4j.sh --no-save        # 只准备本地镜像，不导出
#   bash scripts/build_neo4j.sh --no-pull        # 不联网：只用本地已有镜像（上游或已打 tag 的）
#   bash scripts/build_neo4j.sh --load <离线包>  # 目标机侧：docker load 并补齐 ikc-neo4j 名（不联网）
#
# 环境变量：
#   NEO4J_VERSION     上游 tag（缺省 5.26-community = Neo4j 5.x LTS community）
#   NEO4J_BASE_IMAGE  上游镜像全名（缺省 neo4j:${NEO4J_VERSION}；可指向内网 registry/镜像源）
#   IMAGE_TAG         本栈镜像名:标签（缺省 ikc-neo4j:${NEO4J_VERSION}）
#
# 产物：docker/images/<tag，`:`/`/` 换成 `_`>.tar.gz（GB 级产物已在 .gitignore 排除，不入库）。

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGES_DIR="docker/images"
NEO4J_VERSION="${NEO4J_VERSION:-5.26-community}"
NEO4J_BASE_IMAGE="${NEO4J_BASE_IMAGE:-neo4j:${NEO4J_VERSION}}"
IMAGE_TAG="${IMAGE_TAG:-ikc-neo4j:${NEO4J_VERSION}}"

DO_SAVE=true
DO_PULL=true
LOAD_FROM=""

usage() { awk 'NR==1{next} /^#/{print substr($0,3); started=1} started && !/^#/{exit}' "$0"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-save) DO_SAVE=false ;;
    --no-pull) DO_PULL=false ;;
    --load)
      LOAD_FROM="${2:-}"
      [[ -n "$LOAD_FROM" ]] || { echo "[usage] --load 需要一个离线包路径" >&2; exit 1; }
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[usage] 未知参数: $1（支持 --no-save / --no-pull / --load <包> / -h）" >&2; exit 1 ;;
  esac
  shift
done

if ! command -v docker >/dev/null 2>&1; then
  echo "[docker] 未找到 docker 命令，请先安装 Docker" >&2
  exit 1
fi

have_image() { docker image inspect "$1" >/dev/null 2>&1; }

SAVED_NAME="$(printf '%s' "$IMAGE_TAG" | tr ':/' '__')"
SAVED_PATH="$IMAGES_DIR/${SAVED_NAME}.tar.gz"

# ---- 目标机侧：从离线包导入（不联网）----
if [[ -n "$LOAD_FROM" ]]; then
  [[ -f "$LOAD_FROM" ]] || { echo "[load] 离线包不存在：$LOAD_FROM" >&2; exit 1; }
  echo "[load] docker load -i $LOAD_FROM ..."
  if ! out="$(docker load -i "$LOAD_FROM" 2>&1)"; then
    printf '%s\n' "$out" >&2
    echo "[load] 失败（不是镜像包 / 包损坏）" >&2
    exit 1
  fi
  printf '%s\n' "$out" | sed 's/^/[load]   /'
  if have_image "$IMAGE_TAG"; then
    echo "[load] 已就绪：$IMAGE_TAG"
  else
    img_id="$(printf '%s\n' "$out" | sed -n 's/^Loaded image ID: //p' | tail -1)"
    for cand in "$NEO4J_BASE_IMAGE" "$(basename "$LOAD_FROM" | sed -E 's/\.(tar\.gz|tgz|tar)$//' | sed -E 's/_([^_]+)$/:\1/')"; do
      [[ -n "$cand" ]] || continue
      if [[ -n "$img_id" ]]; then
        docker tag "$img_id" "$IMAGE_TAG" && { echo "[load] 无 tag 包：按期望名补 tag → $IMAGE_TAG"; break; }
      elif have_image "$cand"; then
        docker tag "$cand" "$IMAGE_TAG" && { echo "[load] $cand → $IMAGE_TAG（同一镜像 ID，只补名）"; break; }
      fi
    done
    have_image "$IMAGE_TAG" || { echo "[load] 包内没有可识别的镜像，无法补 $IMAGE_TAG" >&2; exit 1; }
  fi
  echo "[load] 下一步：bash scripts/docker-run-neo4j.sh start"
  exit 0
fi

# ---- 构建机侧：取镜像 → 打 tag ----
if [[ "$DO_PULL" == "true" ]]; then
  echo "[pull] $NEO4J_BASE_IMAGE（联网拉取；受限网络可用 NEO4J_BASE_IMAGE 指向内网 registry）"
  docker pull "$NEO4J_BASE_IMAGE"
elif ! have_image "$NEO4J_BASE_IMAGE" && ! have_image "$IMAGE_TAG"; then
  echo "[err] --no-pull 且本地既没有 $NEO4J_BASE_IMAGE 也没有 $IMAGE_TAG" >&2
  echo "      先联网跑一次（bash scripts/build_neo4j.sh）或先导入离线包（--load <包>）" >&2
  exit 1
fi

if ! have_image "$IMAGE_TAG" || [[ "$DO_PULL" == "true" ]]; then
  echo "[tag]  $NEO4J_BASE_IMAGE → $IMAGE_TAG"
  docker tag "$NEO4J_BASE_IMAGE" "$IMAGE_TAG"
fi

SIZE_DISPLAY="$(docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | awk -v t="$IMAGE_TAG" '$1 == t {print $2; exit}')"
echo "[docker] 就绪：$IMAGE_TAG（${SIZE_DISPLAY:-未知}）"

if [[ "$DO_SAVE" == "true" ]]; then
  mkdir -p "$IMAGES_DIR"
  echo "[save] docker save | gzip > $SAVED_PATH ..."
  docker save "$IMAGE_TAG" | gzip > "$SAVED_PATH"
  ls -lh "$SAVED_PATH"
else
  echo "[save] --no-save 已指定，跳过导出离线包"
fi

cat <<EOF

[docker] 完成（镜像: $IMAGE_TAG）。离线分发到目标机：
  1) scp $SAVED_PATH <目标机>:/tmp/
  2) docker load -i /tmp/${SAVED_NAME}.tar.gz
     （等价且更稳：NEO4J_VERSION=$NEO4J_VERSION bash scripts/build_neo4j.sh --load /tmp/${SAVED_NAME}.tar.gz）
  3) bash scripts/docker-run-neo4j.sh start     # 起容器（幂等；restart=unless-stopped，宿主重启自动拉起）
EOF
