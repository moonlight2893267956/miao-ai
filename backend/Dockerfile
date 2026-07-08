ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
ARG APT_MIRROR=http://mirrors.aliyun.com/debian
ARG APT_SECURITY_MIRROR=http://mirrors.aliyun.com/debian-security
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

FROM docker:27-cli AS docker-cli

FROM python:3.12-slim-bookworm

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy
ARG APT_MIRROR=http://mirrors.aliyun.com/debian
ARG APT_SECURITY_MIRROR=http://mirrors.aliyun.com/debian-security
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    UV_CACHE_DIR=/root/.cache/uv

WORKDIR /app/backend

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

RUN set -eux; \
    docker --version; \
    rm -f /etc/apt/sources.list.d/debian.sources; \
    printf 'deb %s bookworm main\n' "$APT_MIRROR" > /etc/apt/sources.list; \
    printf 'deb %s bookworm-updates main\n' "$APT_MIRROR" >> /etc/apt/sources.list; \
    printf 'deb %s bookworm-security main\n' "$APT_SECURITY_MIRROR" >> /etc/apt/sources.list; \
    apt_proxy="${HTTP_PROXY:-${http_proxy:-}}"; \
    apt_https_proxy="${HTTPS_PROXY:-${https_proxy:-$apt_proxy}}"; \
    if [ -n "$apt_proxy" ]; then \
        printf 'Acquire::http::Proxy "%s";\n' "$apt_proxy" > /etc/apt/apt.conf.d/90proxy; \
    fi; \
    if [ -n "$apt_https_proxy" ]; then \
        printf 'Acquire::https::Proxy "%s";\n' "$apt_https_proxy" >> /etc/apt/apt.conf.d/90proxy; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential ca-certificates curl; \
    rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/90proxy; \
    pip install --no-cache-dir --upgrade pip; \
    pip install --no-cache-dir uv

# 依赖层：仅当 pyproject.toml / uv.lock 变化时才重建（BuildKit 缓存挂载，跨 CD 持久）
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-hashes --no-dev -o /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt

# 源码层：每次 push 都会变动，但只做廉价的可编辑安装（不再重装依赖）
COPY backend/app ./app
COPY backend/agent_templates ./agent_templates
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -e . --no-deps

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
