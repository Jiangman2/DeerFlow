# Frontend Dockerfile
# Supports two targets:
#   --target dev   — install deps only, run `pnpm dev` at container start
#   --target prod  — full build baked in, run `pnpm start` at container start (default if no --target is specified)

ARG PNPM_STORE_PATH=/root/.local/share/pnpm/store
ARG NPM_REGISTRY
ARG NODE_IMAGE=node:22-alpine

# ── Base: shared setup ────────────────────────────────────────────────────────
FROM ${NODE_IMAGE} AS base
ARG PNPM_STORE_PATH
ARG NPM_REGISTRY
# Configure corepack registry before installing pnpm so the download itself
# succeeds in restricted networks (COREPACK_NPM_REGISTRY controls where
# corepack fetches package managers from).
RUN --mount=type=secret,id=enterprise_proxy,required=false \
    --mount=type=secret,id=enterprise_ca,required=false \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    if [ -s /run/secrets/enterprise_ca ]; then \
      cp /run/secrets/enterprise_ca /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
    else \
      cp /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
    fi; \
    export NODE_EXTRA_CA_CERTS=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
    if [ -n "${NPM_REGISTRY}" ]; then \
      export COREPACK_NPM_REGISTRY="${NPM_REGISTRY}"; \
    fi && \
    corepack enable && corepack install -g pnpm@10.26.2
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
# Optionally override npm registry for restricted networks (e.g. NPM_REGISTRY=https://registry.npmmirror.com)
RUN if [ -n "${NPM_REGISTRY}" ]; then pnpm config set registry "${NPM_REGISTRY}"; fi
WORKDIR /app
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./frontend/

# ── Dev: install only, CMD is overridden by docker-compose ───────────────────
FROM base AS dev
RUN --mount=type=secret,id=enterprise_proxy,required=false \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    cd /app/frontend && pnpm install --frozen-lockfile
COPY frontend ./frontend
EXPOSE 3000

# ── Builder: install + compile Next.js ───────────────────────────────────────
FROM base AS builder
RUN --mount=type=secret,id=enterprise_proxy,required=false \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    cd /app/frontend && pnpm install --frozen-lockfile
COPY frontend ./frontend
# Skip env validation — runtime vars are injected by nginx/container
# App version stamp for the About page. Nightly CI passes
# APP_VERSION=<base>-nightly.<date>-<sha>; release/local builds leave it empty
# and the frontend falls back to package.json's version.
ARG APP_VERSION=""
ENV NEXT_PUBLIC_APP_VERSION=$APP_VERSION
ARG NEXT_PUBLIC_STATIC_WEBSITE_ONLY=""
ARG DEER_FLOW_INTERNAL_GATEWAY_BASE_URL="http://127.0.0.1:8001"
ARG NEXT_CONFIG_BUILD_OUTPUT="standalone"
ENV NEXT_PUBLIC_STATIC_WEBSITE_ONLY=$NEXT_PUBLIC_STATIC_WEBSITE_ONLY
ENV DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=$DEER_FLOW_INTERNAL_GATEWAY_BASE_URL
ENV NEXT_CONFIG_BUILD_OUTPUT=$NEXT_CONFIG_BUILD_OUTPUT
RUN cd /app/frontend && SKIP_ENV_VALIDATION=1 pnpm build

# ── Prod: minimal runtime with pre-built output ───────────────────────────────
FROM ${NODE_IMAGE} AS prod
ARG PNPM_STORE_PATH
ARG NPM_REGISTRY
RUN --mount=type=secret,id=enterprise_proxy,required=false \
    --mount=type=secret,id=enterprise_ca,required=false \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    if [ -s /run/secrets/enterprise_ca ]; then \
      cp /run/secrets/enterprise_ca /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
    else \
      cp /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
    fi; \
    export NODE_EXTRA_CA_CERTS=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
    if [ -n "${NPM_REGISTRY}" ]; then \
      export COREPACK_NPM_REGISTRY="${NPM_REGISTRY}"; \
    fi && \
    corepack enable && corepack install -g pnpm@10.26.2
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
RUN if [ -n "${NPM_REGISTRY}" ]; then pnpm config set registry "${NPM_REGISTRY}"; fi
WORKDIR /app
COPY --from=builder /app/frontend ./frontend
EXPOSE 3000
CMD ["sh", "-c", "cd /app/frontend && pnpm start"]
