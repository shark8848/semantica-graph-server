#!/usr/bin/env bash
set -euo pipefail

# 构建 Semantica Graph Engine Docker 镜像（图引擎 + HAProxy 代理层同镜像，多阶段）。
#
# 用法：
#   bash scripts/build_docker.sh                # docker build
#   bash scripts/build_docker.sh --no-cache     # docker build --no-cache
#   bash scripts/build_docker.sh --pull         # 先拉取最新基础镜像
#
# 环境变量：
#   IMAGE_TAG   镜像名:标签（默认 graph-engine:<版本>，版本取自 pyproject.toml）

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

NO_CACHE=0
PULL=0
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE=1 ;;
    --pull) PULL=1 ;;
    *)
      echo "[usage] 未知参数: $arg（支持 --no-cache、--pull）" >&2
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
IMAGE_TAG="${IMAGE_TAG:-graph-engine:${VERSION:-0.1.0}}"

build_args=(--build-arg "VERSION=${VERSION:-0.1.0}" --progress=plain)
[[ "$NO_CACHE" == "1" ]] && build_args+=(--no-cache)
[[ "$PULL" == "1" ]] && build_args+=(--pull)

echo "[docker] 构建镜像 $IMAGE_TAG（引擎 + HAProxy 代理层同镜像，多阶段）..."
docker build "${build_args[@]}" -t "$IMAGE_TAG" .

SIZE_DISPLAY="$(docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | awk -v t="$IMAGE_TAG" '$1 == t {print $2; exit}')"
echo "[docker] 构建完成: $IMAGE_TAG（docker images 显示大小：${SIZE_DISPLAY:-未知}）"
echo "[docker] 启动（HAProxy 入口 http://127.0.0.1:18180，gRPC 127.0.0.1:18151）：docker compose up -d"
