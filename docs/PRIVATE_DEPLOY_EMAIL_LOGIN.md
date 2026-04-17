## 目标

- 私有化部署 DeerFlow（前端 + Gateway + LangGraph）
- 支持邮箱/密码登录
- 多用户数据隔离（threads / uploads / artifacts / suggestions / skill install 等 thread-scoped 能力）

## 组件与端口

- Frontend（Next.js）：对外暴露（建议 80/443 反代到 3000）
- Gateway（FastAPI）：仅内网访问（默认 8001）
- LangGraph（dev runtime）：仅内网访问（默认 2025）

建议对外只开放一个入口（Nginx/Ingress），不要直接暴露 8001/2025。

## 运行前提

- Node.js：需要 22.5+（使用 node:sqlite 存储 Better Auth）
- Python：按 backend 现有 uv 环境运行

## 必需环境变量

### Frontend（Next.js）

- BETTER_AUTH_SECRET：32+ 强随机字符串
- BETTER_AUTH_URL：前端对外访问的 base URL（例如 https://deerflow.example.com）
- DEERFLOW_PROXY_SECRET：与 Gateway 保持一致（用于前端代理签名）
- DEERFLOW_GATEWAY_BASE_URL：Gateway 内网地址（例如 http://127.0.0.1:8001）
- BETTER_AUTH_DB_DIR：Better Auth SQLite 存储目录（建议与 DEER_FLOW_HOME 同盘）

### Gateway（FastAPI）

- DEER_FLOW_HOME：持久化目录（线程文件 + deerflow.db）
- DEERFLOW_PROXY_SECRET：与 Frontend 保持一致（用于验签）
- LANGGRAPH_BASE_URL：LangGraph 内网地址（例如 http://127.0.0.1:2025）

## 数据落盘位置（建议）

- DEER_FLOW_HOME=/opt/deerflow/data
- BETTER_AUTH_DB_DIR=/opt/deerflow/data

最终会产生：

- /opt/deerflow/data/deerflow.db（threads ownership 等）
- /opt/deerflow/data/better-auth.sqlite（用户/会话）
- /opt/deerflow/data/threads/...（线程产物、上传、sandbox 挂载目录等）

## 启动顺序（示例）

1) 启动 LangGraph（内网 2025）

```bash
cd backend
export PYTHONPATH=.
uv run langgraph dev --no-browser --allow-blocking --port 2025 --no-reload
```

2) 启动 Gateway（内网 8001）

```bash
cd backend
export PYTHONPATH=.
export DEERFLOW_PROXY_SECRET=...
export DEER_FLOW_HOME=/opt/deerflow/data
export LANGGRAPH_BASE_URL=http://127.0.0.1:2025
uv run uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001
```

3) 启动 Frontend（对外 3000）

```bash
cd frontend
export BETTER_AUTH_SECRET=...
export BETTER_AUTH_URL=https://deerflow.example.com
export DEERFLOW_PROXY_SECRET=...
export DEERFLOW_GATEWAY_BASE_URL=http://127.0.0.1:8001
export BETTER_AUTH_DB_DIR=/opt/deerflow/data
pnpm install
pnpm run build
node .next/standalone/server.js
```

## Nginx 反代（示例）

仅示意：对外只暴露 443，转发到 3000。

```nginx
server {
  listen 80;
  server_name deerflow.example.com;
  return 301 https://$host$request_uri;
}

server {
  listen 443 ssl;
  server_name deerflow.example.com;

  location / {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

## Docker Compose（推荐私有化最短路径）

仓库已提供生产 compose：`docker/docker-compose.yaml` + `docker/nginx/nginx.conf`。

关键点：

- 必须设置 DEERFLOW_PROXY_SECRET（前端/网关一致），否则网关会拒绝所有 thread-scoped 请求
- 需要设置 BETTER_AUTH_URL（外部访问域名），否则回调/跳转会不稳定
- Better Auth 的 SQLite 默认落在容器内；compose 已把 `${DEER_FLOW_HOME}` 挂到 frontend 的 `/app/data` 并设置 `BETTER_AUTH_DB_DIR=/app/data`，保证重启不丢用户/会话
- nginx 必须把 `/api/auth/*`、`/api/langgraph/*`、`/api/threads*`、`/api/skills/install` 转发到 frontend（Next Route Handlers），否则会绕过会话代理导致 401

最小启动示例：

```bash
export PORT=2026
export DEER_FLOW_HOME=/opt/deerflow/data
export DEER_FLOW_REPO_ROOT=/opt/deerflow/DeerFlow
export DEERFLOW_PROXY_SECRET=...
export BETTER_AUTH_SECRET=...
export BETTER_AUTH_URL=https://deerflow.example.com
export DEER_FLOW_CONFIG_PATH=/opt/deerflow/DeerFlow/backend/config.yaml
export DEER_FLOW_EXTENSIONS_CONFIG_PATH=/opt/deerflow/DeerFlow/backend/extensions_config.json
export DEER_FLOW_DOCKER_SOCKET=/var/run/docker.sock

cd /opt/deerflow/DeerFlow/docker
docker compose -f docker-compose.yaml up -d --build
```

## 最小验证清单

1) 未登录访问 /workspace 自动跳转 /login
2) 在 /login 注册用户 A 并登录，新建对话
3) 使用无痕窗口注册用户 B 并登录
4) B 的 threads 列表看不到 A 的对话
5) B 直接访问 A 的 thread_id（URL、artifacts、uploads、suggestions、langgraph thread scope）返回 401/403
