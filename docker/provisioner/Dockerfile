ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

ARG APT_MIRROR
ARG PIP_INDEX_URL

# Optionally override apt mirror for restricted networks (e.g. APT_MIRROR=mirrors.aliyun.com)
RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# Install system dependencies. Enterprise proxy/CA inputs are BuildKit secrets
# so credentials are never persisted in an image layer.
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
    apt-get ${apt_ca_opt} update && apt-get ${apt_ca_opt} install -y --no-install-recommends \
    ca-certificates \
    curl \
    && if [ -s /run/secrets/enterprise_ca ]; then \
         cp /run/secrets/enterprise_ca /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
       else \
         cp /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
       fi \
    && rm -rf /var/lib/apt/lists/*

ENV SSL_CERT_FILE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV CURL_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt

# Install Python dependencies
RUN --mount=type=secret,id=enterprise_proxy,required=false \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    pip install --no-cache-dir \
    ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} \
    fastapi \
    "uvicorn[standard]" \
    kubernetes

WORKDIR /app
COPY app.py .

EXPOSE 8002

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
