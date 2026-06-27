FROM python:3.12-slim-bookworm

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/backend

RUN set -eux; \
    apt_proxy="${HTTP_PROXY:-${http_proxy:-}}"; \
    apt_https_proxy="${HTTPS_PROXY:-${https_proxy:-$apt_proxy}}"; \
    if [ -n "$apt_proxy" ]; then \
        printf 'Acquire::http::Proxy "%s";\n' "$apt_proxy" > /etc/apt/apt.conf.d/90proxy; \
    fi; \
    if [ -n "$apt_https_proxy" ]; then \
        printf 'Acquire::https::Proxy "%s";\n' "$apt_https_proxy" >> /etc/apt/apt.conf.d/90proxy; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends build-essential ca-certificates curl gnupg; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc; \
    chmod a+r /etc/apt/keyrings/docker.asc; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli; \
    rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/90proxy

COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/app ./app
COPY backend/agent_templates ./agent_templates
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic

RUN pip install --upgrade pip \
    && pip install uv \
    && pip install -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
