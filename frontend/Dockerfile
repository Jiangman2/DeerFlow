# Frontend Dockerfile
# Supports two targets:
#   --target dev   — install deps only, run `pnpm dev` at container start
#   --target prod  — full build baked in, run `pnpm start` at container start (default if no --target is specified)

ARG PNPM_STORE_PATH=/root/.local/share/pnpm/store
ARG NEXT_PUBLIC_ALLOW_PUBLIC_MODELS=true

# ── Base: shared setup ────────────────────────────────────────────────────────
FROM node:22-alpine AS base
ARG PNPM_STORE_PATH
RUN corepack enable && corepack install -g pnpm@10.26.2
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
RUN pnpm config set fetch-retries 5 && pnpm config set fetch-retry-mintimeout 20000 && pnpm config set fetch-retry-maxtimeout 120000
WORKDIR /app
COPY frontend ./frontend

# ── Dev: install only, CMD is overridden by docker-compose ───────────────────
FROM base AS dev
RUN --mount=type=cache,target=${PNPM_STORE_PATH} cd /app/frontend && pnpm install --frozen-lockfile --prefer-offline
EXPOSE 3000

# ── Builder: install + compile Next.js ───────────────────────────────────────
FROM base AS builder
ARG NEXT_PUBLIC_ALLOW_PUBLIC_MODELS
ARG BETTER_AUTH_SECRET=build-time-secret
ARG DEERFLOW_PROXY_SECRET=build-time-proxy-secret
RUN --mount=type=cache,target=${PNPM_STORE_PATH} cd /app/frontend && pnpm install --frozen-lockfile --prefer-offline
# Skip env validation — runtime vars are injected by nginx/container
RUN cd /app/frontend && SKIP_ENV_VALIDATION=1 NEXT_PUBLIC_ALLOW_PUBLIC_MODELS=${NEXT_PUBLIC_ALLOW_PUBLIC_MODELS} BETTER_AUTH_SECRET=${BETTER_AUTH_SECRET} DEERFLOW_PROXY_SECRET=${DEERFLOW_PROXY_SECRET} pnpm build

# ── Prod: minimal runtime with pre-built output ───────────────────────────────
FROM node:22-alpine AS prod
ARG PNPM_STORE_PATH
RUN corepack enable && corepack install -g pnpm@10.26.2
RUN pnpm config set store-dir ${PNPM_STORE_PATH}
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
COPY --from=builder /app/frontend/public ./public
COPY --from=builder /app/frontend/.next/static ./.next/static
COPY --from=builder /app/frontend/.next/standalone ./
EXPOSE 3000
CMD ["node", "server.js"]
