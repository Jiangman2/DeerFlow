# Backend Dockerfile — multi-stage build
# Stage 1 (builder): compiles native Python extensions with build-essential
# Stage 2 (dev):     retains toolchain for dev containers (uv sync at startup)
# Stage 3 (runtime): clean image without compiler toolchain for production

# Base images are overridable so restricted or air-gapped environments can use
# an approved mirror without maintaining a second Dockerfile.
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.7.20
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG DOCKER_CLI_IMAGE=docker:cli
FROM ${UV_IMAGE} AS uv-source
FROM ${DOCKER_CLI_IMAGE} AS docker-cli-source

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS builder

ARG NODE_MAJOR=22
ARG APT_MIRROR
ARG UV_INDEX_URL
# Optional extras to install (e.g. "postgres" for PostgreSQL support)
# Usage: docker build --build-arg UV_EXTRAS=postgres,discord ...
ARG UV_EXTRAS

# Optionally override apt mirror for restricted networks (e.g. APT_MIRROR=mirrors.aliyun.com)
RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# Install build tools + Node.js (build-essential needed for native Python extensions).
# The optional BuildKit secrets keep proxy credentials out of image history while
# allowing restricted enterprise builders to use their combined CA bundle.
RUN --mount=type=secret,id=enterprise_proxy,required=false \
    --mount=type=secret,id=enterprise_ca,required=false \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    apt_ca_opt=""; \
    if [ -s /run/secrets/enterprise_ca ]; then \
      apt_ca_opt="-o Acquire::https::CaInfo=/run/secrets/enterprise_ca"; \
      export SSL_CERT_FILE=/run/secrets/enterprise_ca; \
      export CURL_CA_BUNDLE=/run/secrets/enterprise_ca; \
    fi; \
    apt-get ${apt_ca_opt} update && apt-get ${apt_ca_opt} install -y \
    curl \
    build-essential \
    gnupg \
    ca-certificates \
    && if [ -s /run/secrets/enterprise_ca ]; then \
         cp /run/secrets/enterprise_ca /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
       else \
         cp /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
       fi \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv (source image overridable via UV_IMAGE build arg)
COPY --from=uv-source /uv /uvx /usr/local/bin/

# Set working directory
WORKDIR /app

# Copy backend source code
COPY backend ./backend

# Install dependencies with cache mount
# `redis` is always installed because Docker Compose defaults the stream bridge
# to Redis (DEER_FLOW_STREAM_BRIDGE_REDIS_URL); it is an optional extra elsewhere
# so slim non-Docker installs do not pull it.
# When UV_EXTRAS is set (comma- or whitespace-separated), installs optional dependencies.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=enterprise_proxy,required=false \
    --mount=type=secret,id=enterprise_ca,required=false \
    sh -c 'cd backend && \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    export SSL_CERT_FILE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
    export CURL_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
    export REQUESTS_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
    set -f; \
    extras_flags=""; \
    for extra in $(printf "%s" "$UV_EXTRAS" | tr "," " "); do \
        case "$extra" in \
            [!A-Za-z]* | *[!A-Za-z0-9_-]*) \
                echo "UV_EXTRAS entry $extra is invalid (must match [A-Za-z][A-Za-z0-9_-]*)" >&2; \
                exit 1; \
                ;; \
        esac; \
        extras_flags="$extras_flags --extra $extra"; \
    done; \
    UV_INDEX_URL=${UV_INDEX_URL:-https://pypi.org/simple} uv sync --extra redis $extras_flags'

# UTF-8 locale prevents UnicodeEncodeError on Chinese/emoji content in minimal
# containers where locale configuration may be missing and the default encoding is not UTF-8.
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONIOENCODING=utf-8
ENV SSL_CERT_FILE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV CURL_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt

# ── Stage 2: Dev ──────────────────────────────────────────────────────────────
# Retains compiler toolchain from builder so startup-time `uv sync` can build
# source distributions in development containers.
FROM builder AS dev

# Install Docker CLI (for DooD: allows starting sandbox containers via host Docker socket)
COPY --from=docker-cli-source /usr/local/bin/docker /usr/local/bin/docker

EXPOSE 8001

CMD ["sh", "-c", "cd backend && PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"]

# ── Stage 3: Runtime ──────────────────────────────────────────────────────────
# Clean image without build-essential — reduces size (~200 MB) and attack surface.
FROM ${PYTHON_IMAGE}

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONIOENCODING=utf-8
ENV SSL_CERT_FILE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV CURL_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt

COPY --from=builder /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt

# Copy Node.js runtime from builder (provides npx for MCP servers)
COPY --from=builder /usr/bin/node /usr/bin/node
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/bin/npx

# Install Docker CLI (for DooD: allows starting sandbox containers via host Docker socket)
COPY --from=docker-cli-source /usr/local/bin/docker /usr/local/bin/docker

# Install uv (source image overridable via UV_IMAGE build arg)
COPY --from=uv-source /uv /uvx /usr/local/bin/

# Set working directory
WORKDIR /app

# Copy backend with pre-built virtualenv from builder
COPY --from=builder /app/backend ./backend

# Expose Gateway API port.
EXPOSE 8001

# Default command (can be overridden in docker-compose)
CMD ["sh", "-c", "cd backend && PYTHONPATH=. uv run --no-sync uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"]
