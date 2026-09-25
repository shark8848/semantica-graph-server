#!/usr/bin/env bash
set -euo pipefail

# 构建 Semantica Graph Engine Docker 镜像（图引擎 + HAProxy 代理层同镜像，多阶段），
# 并导出离线部署包（`docker save | gzip` → docker/images/）。
#
# 与兄弟仓同口径（ikc-core-service / ikc-open-platform 默认导出；openwiki-server 用 --export）：
#   默认 = 构建 + 导出；`--no-save` 只构建。导出文件名由镜像 tag 推导
#   （`ikc-graph-engine:0.1.0` → `docker/images/ikc-graph-engine_0.1.0.tar.gz`）。
#
# 用法：
#   bash scripts/build_docker.sh                # docker build + 导出离线包到 docker/images/
#   bash scripts/build_docker.sh --no-save      # 只构建，不导出
#   bash scripts/build_docker.sh --no-cache     # docker build --no-cache
#   bash scripts/build_docker.sh --pull         # 先拉取最新基础镜像
#
# 环境变量：
#   IMAGE_TAG   镜像名:标签（默认 ikc-graph-engine:<版本>，版本取自 pyproject.toml；
#               ikc-* 是本栈口径，覆盖示例：IMAGE_TAG=graph-engine:0.1.0 bash scripts/build_docker.sh）
#
# 产物：docker/images/<image_tag，`:`/`/` 换成 `_`>.tar.gz；目标机 `docker load -i` 即可
#       （传输 / 导入 / 回滚见 docs/本地Docker部署手册.md 第 3、4、9 节）。

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGES_DIR="docker/images"
NO_CACHE=0
PULL=0
NO_SAVE=0
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE=1 ;;
    --pull) PULL=1 ;;
    --no-save) NO_SAVE=1 ;;
    *)
      echo "[usage] 未知参数: $arg（支持 --no-cache、--pull、--no-save）" >&2
      exit 1
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "[docker] 未找到 docker 命令，请先安装 Docker" >&2
  exit 1
fi

# 版本取自 pyproject.toml（如 0.1.0）
VERSION="$(sed -n 's/^version = "\([0-9][0-9.]*\)".*/\1/p' pyproject.toml | head -1)"
IMAGE_TAG="${IMAGE_TAG:-ikc-graph-engine:${VERSION:-0.1.0}}"

build_args=(--build-arg "VERSION=${VERSION:-0.1.0}" --progress=plain)
[[ "$NO_CACHE" == "1" ]] && build_args+=(--no-cache)
[[ "$PULL" == "1" ]] && build_args+=(--pull)

echo "[docker] 构建镜像 $IMAGE_TAG（引擎 + HAProxy 代理层同镜像，多阶段）..."
docker build "${build_args[@]}" -t "$IMAGE_TAG" .

SIZE_DISPLAY="$(docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | awk -v t="$IMAGE_TAG" '$1 == t {print $2; exit}')"
echo "[docker] 构建完成: $IMAGE_TAG（docker images 显示大小：${SIZE_DISPLAY:-未知}）"

if [[ "$NO_SAVE" == "1" ]]; then
  echo "[docker] --no-save 已指定，跳过导出离线包"
else
  mkdir -p "$IMAGES_DIR"
  # 文件名由镜像 tag 推导（:/ -> _）：ikc-graph-engine:0.1.0 -> ikc-graph-engine_0.1.0.tar.gz
  SAVED_NAME="$(printf '%s' "$IMAGE_TAG" | tr ':/' '__')"
  SAVED_PATH="$IMAGES_DIR/${SAVED_NAME}.tar.gz"
  echo "[docker] 导出离线包到 $SAVED_PATH（镜像数 GB，gzip 需要几分钟）..."
  docker save "$IMAGE_TAG" | gzip > "$SAVED_PATH"
  ls -lh "$SAVED_PATH"
  echo "[docker] 导出完成: $SAVED_PATH"
  echo "[docker] 目标机导入：docker load -i $SAVED_PATH"
fi

echo "[docker] 启动（HAProxy 入口 http://127.0.0.1:18180，gRPC 127.0.0.1:18151）：docker compose up -d"
