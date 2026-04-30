# Backend Development Dockerfile
FROM python:3.12-slim

ARG NODE_MAJOR=22
ARG HTTP_PROXY
ARG HTTPS_PROXY

# Install system dependencies + Node.js (provides npx for MCP servers)
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    gnupg \
    ca-certificates \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI (for DooD: allows starting sandbox containers via host Docker socket)
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker

RUN pip install --no-cache-dir uv==0.7.20 -i https://mirrors.aliyun.com/pypi/simple/

# Set working directory
WORKDIR /app

# Copy frontend source code
COPY backend ./backend

ENV http_proxy=$HTTP_PROXY
ENV https_proxy=$HTTPS_PROXY

ENV UV_NO_BUILD_ISOLATION=1
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 关键：提前安装构建依赖
RUN cd backend && uv venv && uv pip install --upgrade pip setuptools wheel -i https://pypi.tuna.tsinghua.edu.cn/simple && uv pip install editables hatchling -i https://pypi.tuna.tsinghua.edu.cn/simple

RUN cd backend && uv sync

# Expose ports (gateway: 8001, langgraph: 2024)
EXPOSE 8001 2024

# Default command (can be overridden in docker-compose)
CMD ["sh", "-c", "cd backend && .venv/bin/uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"]
