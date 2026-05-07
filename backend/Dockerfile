# Backend Development Dockerfile
FROM python:3.12-slim

ARG NODE_MAJOR=22
ARG HTTP_PROXY
ARG HTTPS_PROXY

# Install system dependencies + Node.js (provides npx for MCP servers)
RUN set -eux; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' > /etc/apt/apt.conf.d/99network-retries; \
    retry() { \
      n=0; \
      until [ "$n" -ge 5 ]; do \
        "$@" && break; \
        n=$((n + 1)); \
        echo "Command failed, retry ${n}/5: $*"; \
        sleep $((n * 5)); \
      done; \
      [ "$n" -lt 5 ]; \
    }; \
    retry apt-get update; \
    retry apt-get install -y --no-install-recommends --fix-missing \
      curl \
      build-essential \
      pkg-config \
      python3-dev \
      libcairo2-dev \
      libffi-dev \
      libjpeg-dev \
      zlib1g-dev \
      gnupg \
      ca-certificates; \
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o /etc/apt/keyrings/nodesource.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list; \
    retry apt-get update; \
    retry apt-get install -y --no-install-recommends nodejs; \
    rm -rf /var/lib/apt/lists/*

# Install Docker CLI (for DooD: allows starting sandbox containers via host Docker socket)
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker

RUN pip install --no-cache-dir uv==0.7.20 -i https://mirrors.aliyun.com/pypi/simple/

# Set working directory
WORKDIR /app

# Copy frontend source code
COPY backend ./backend
COPY skills/public/ppt-master/requirements.txt /app/skills/public/ppt-master/requirements.txt
RUN rm -rf /app/backend/config.yaml

ENV http_proxy=$HTTP_PROXY
ENV https_proxy=$HTTPS_PROXY

ENV UV_NO_BUILD_ISOLATION=1
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 关键：提前安装构建依赖
RUN cd backend && uv venv && uv pip install --upgrade pip setuptools wheel -i https://pypi.tuna.tsinghua.edu.cn/simple && uv pip install editables hatchling -i https://pypi.tuna.tsinghua.edu.cn/simple

RUN cd backend && uv sync

# Install ppt-master runtime dependencies into backend venv so
# gateway/langgraph containers can run ppt scripts without manual pip install.
# Keep svglib enabled; Cairo toolchain is provided above for pycairo/rlpycairo.
RUN cd backend \
    && uv pip install --python .venv/bin/python meson-python meson ninja -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && uv pip install --python .venv/bin/python -r /app/skills/public/ppt-master/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# Expose ports (gateway: 8001, langgraph: 2024)
EXPOSE 8001 2024

# Default command (can be overridden in docker-compose)
CMD ["sh", "-c", "cd backend && .venv/bin/uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"]
