#
# Semantica Graph Engine 统一镜像（图引擎 + HAProxy 代理层单镜像）：
#   runtime —— Python 3.12 + graph-engine（HTTP/gRPC/Celery/MCP/CLI）+ HAProxy 反向代理
#
# 拓扑（容器内）：client -> HAProxy(:8080 HTTP / :50051 gRPC / :8404 stats)
#                         -> graph-engine HTTP(127.0.0.1:18010) / gRPC(127.0.0.1:50051)（仅回环）
#   - 引擎服务只监听 127.0.0.1，不直接对外暴露；对外唯一入口为 HAProxy。
#   - HAProxy 配置模板 /etc/haproxy/haproxy.cfg.tmpl 由入口脚本 envsubst 渲染
#     （stats 账号/密码、引擎内部端口等自动配置，见 docker/entrypoint.sh）。
#
# 多阶段构建：
#   builder —— 安装引擎与全部依赖（semantica 0.6.5 → torch/transformers/spacy/opencv 等超大依赖）。
#              依赖层仅随 requirements.txt 失效，代码变更不重复下载大依赖；
#   runtime —— 只复制安装产物（site-packages + 控制台脚本），镜像不含编译工具链/源码。

# ---------- 阶段 1：构建并安装引擎依赖 ----------
FROM python:3.12-slim AS builder

ENV PIP_DEFAULT_TIMEOUT=300 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# 依赖层：仅 requirements.txt 变更才失效（torch 等大依赖不因代码改动重复下载）；
# 同时升级构建工具，供下一步 --no-build-isolation 使用
COPY requirements.txt ./
RUN pip install --no-cache-dir --timeout 300 --retries 15 --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --timeout 300 --retries 15 -r requirements.txt

# 引擎本体：--no-deps 只安装 graph-engine 自身（秒级完成）
COPY pyproject.toml ./
COPY graph_engine/ ./graph_engine/
RUN pip install --no-cache-dir --no-build-isolation --no-deps .

# ---------- 阶段 2：运行时 ----------
FROM python:3.12-slim AS runtime

ARG VERSION=0.1.0

# HAProxy 反向代理 + gettext（envsubst 渲染 stats 账号与引擎端口）
RUN apt-get update \
    && apt-get install -y --no-install-recommends haproxy gettext \
    && rm -rf /var/lib/apt/lists/*

# 复制 builder 的安装产物（site-packages + 控制台脚本 + pip），两阶段同基镜像兼容
COPY --from=builder /usr/local /usr/local

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# HAProxy 代理层：配置模板 + 入口脚本（渲染配置后同进程拉起引擎 + haproxy）
COPY docker/haproxy.cfg /etc/haproxy/haproxy.cfg.tmpl
COPY docker/entrypoint.sh /usr/local/bin/graph-engine-entrypoint.sh

# 非 root 运行；uid 1000 与常见宿主机用户对齐，便于挂载 data/logs 卷
RUN useradd --uid 1000 --create-home appuser \
    && mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app \
    && chmod +x /usr/local/bin/graph-engine-entrypoint.sh

USER appuser

# 仅暴露 HAProxy 入口（HTTP + gRPC + stats）；引擎端口只在容器回环内
EXPOSE 8080 50051 8404

LABEL org.opencontainers.image.title="Semantica Graph Engine" \
      org.opencontainers.image.description="Graph engine (HTTP/gRPC/Celery/MCP/CLI) + HAProxy reverse proxy" \
      org.opencontainers.image.version="${VERSION}"

# 容器健康状态：经 HAProxy 端到端探测（docker run 单容器场景；compose 另配 healthcheck）
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

ENTRYPOINT ["/usr/local/bin/graph-engine-entrypoint.sh"]
