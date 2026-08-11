ARG SANDBOX_BASE_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0
FROM ${SANDBOX_BASE_IMAGE}

USER root

ARG APT_MIRROR=

# CairoSVG needs native Cairo libraries at runtime.
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
    if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|http://archive.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" /etc/apt/sources.list; \
        sed -i "s|http://security.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" /etc/apt/sources.list; \
    fi \
    && apt-get ${apt_ca_opt} update -o Acquire::Retries=5 \
    && apt-get ${apt_ca_opt} install -y --no-install-recommends -o Acquire::Retries=5 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libpango-1.0-0 \
        fonts-noto-cjk \
        fonts-liberation \
        fonts-wqy-zenhei \
        fontconfig \
        pandoc \
        ffmpeg \
        librsvg2-bin \
        libreoffice-core \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        shared-mime-info \
    && if [ -s /run/secrets/enterprise_ca ]; then \
         cp /run/secrets/enterprise_ca /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
       else \
         cp /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/deerflow-enterprise-ca-bundle.crt; \
       fi \
    && printf '%s\n' \
        '<?xml version="1.0"?>' \
        '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">' \
        '<fontconfig>' \
        '  <alias><family>Source Han Sans</family><prefer><family>Noto Sans CJK SC</family></prefer></alias>' \
        '  <alias><family>Source Han Sans CN</family><prefer><family>Noto Sans CJK SC</family></prefer></alias>' \
        '  <alias><family>Source Han Sans SC</family><prefer><family>Noto Sans CJK SC</family></prefer></alias>' \
        '  <alias><family>Source Han Sans Regular</family><prefer><family>Noto Sans CJK SC</family></prefer></alias>' \
        '  <alias><family>Source Han Sans Bold</family><prefer><family>Noto Sans CJK SC</family></prefer></alias>' \
        '  <alias><family>Microsoft YaHei</family><prefer><family>Noto Sans CJK SC</family></prefer></alias>' \
        '  <alias><family>SimHei</family><prefer><family>Noto Sans CJK SC</family></prefer></alias>' \
        '  <alias><family>SimSun</family><prefer><family>Noto Serif CJK SC</family></prefer></alias>' \
        '  <alias><family>KaiTi</family><prefer><family>Noto Serif CJK SC</family></prefer></alias>' \
        '  <alias><family>PingFang SC</family><prefer><family>Noto Sans CJK SC</family></prefer></alias>' \
        '</fontconfig>' \
        > /etc/fonts/local.conf \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

ENV SSL_CERT_FILE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV CURL_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt

# Keep the sandbox runtime aligned with the ppt-master skill dependency list.
# Place this after the large OS layer so skill-only requirement changes do not
# invalidate LibreOffice, Pandoc, fonts, and other native dependencies.
COPY --from=ppt-master-skill requirements.txt /tmp/ppt-master-requirements.txt

# Install the complete upstream dependency list instead of relying on packages
# that happen to be present in the base image. Playwright is an upstream
# runtime requirement of visual_review.py and extract_svg_pictures.py, but is
# not yet declared in the upstream requirements.txt.
RUN --mount=type=secret,id=enterprise_proxy,required=false \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    python3 -m pip install --no-cache-dir \
    --timeout 60 --retries 3 --prefer-binary --ignore-installed \
    -r /tmp/ppt-master-requirements.txt \
    "playwright>=1.50.0" "pydantic>=2.12.5,<3.0.0" \
    && python3 -m playwright install chromium \
    && rm -rf /root/.cache/pip /tmp/pip-* /tmp/ppt-master-requirements.txt \
    && find /usr /opt -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# Keep optional production hardening in trailing layers so an update does not
# invalidate the large office/browser dependency layers above.
RUN --mount=type=secret,id=enterprise_proxy,required=false \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    python3 -m pip install --no-cache-dir --timeout 60 --retries 3 \
        "CairoSVG==2.7.1" \
        "duckdb>=1.3,<2" \
        "PyYAML>=6,<7" \
    && python3 -c "import cairosvg, duckdb, pydantic, yaml; from packaging.version import Version; assert Version(pydantic.__version__) >= Version('2.12.5'), pydantic.__version__; assert duckdb.sql('select 1').fetchone() == (1,); assert yaml.safe_load('ready: true')['ready']; data = cairosvg.svg2png(bytestring=b'<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"2\" height=\"2\"><rect width=\"2\" height=\"2\"/></svg>'); assert data.startswith(b'\\x89PNG')"

ARG WEBSOCAT_VERSION=1.14.1
ARG WEBSOCAT_SHA256=711a69576a2ff473fb01a90ffafb571c2ed019e55479d7ae71b12c2eadeb7011

RUN --mount=type=secret,id=enterprise_proxy,required=false \
    --mount=type=secret,id=enterprise_ca,required=false \
    set -a; \
    if [ -s /run/secrets/enterprise_proxy ]; then . /run/secrets/enterprise_proxy; fi; \
    set +a; \
    if [ -s /run/secrets/enterprise_ca ]; then \
        export SSL_CERT_FILE=/run/secrets/enterprise_ca; \
        export CURL_CA_BUNDLE=/run/secrets/enterprise_ca; \
    fi; \
    curl --fail --location --show-error --silent \
        --retry 5 --retry-all-errors --connect-timeout 20 --max-time 300 \
        "https://github.com/vi/websocat/releases/download/v${WEBSOCAT_VERSION}/websocat.aarch64-unknown-linux-musl" \
        --output /tmp/websocat \
    && printf '%s  %s\n' "${WEBSOCAT_SHA256}" /tmp/websocat | sha256sum -c - \
    && install -o root -g root -m 0755 /tmp/websocat /usr/local/bin/websocat \
    && rm -f /tmp/websocat \
    && websocat --version | grep -F "websocat ${WEBSOCAT_VERSION}"

# The AIO image has an outer authentication Nginx in front of the Python
# command service. Its stock 60-second proxy timeout can report a false 504
# while a bounded long command (for example, batch image generation) is still
# running and persisting output. Keep both authenticated and unauthenticated
# upstream locations aligned with the command service; the DeerFlow client
# still enforces its per-command hard timeout. Keep this in a trailing layer so
# transport-only changes do not invalidate the large Office/browser layers.
RUN outer_nginx=/opt/gem/nginx-server-with-auth.conf; \
    upstream_count="$(grep -cF 'proxy_pass http://127.0.0.1:${AUTH_BACKEND_PORT};' "${outer_nginx}")"; \
    test "${upstream_count}" -eq 2; \
    sed -i '/proxy_pass http:\/\/127\.0\.0\.1:${AUTH_BACKEND_PORT};/a\        proxy_connect_timeout 5s;\
        proxy_read_timeout 86400s;\
        proxy_send_timeout 86400s;' "${outer_nginx}"; \
    test "$(grep -cF 'proxy_read_timeout 86400s;' "${outer_nginx}")" -eq 2

ENV PYTHONDONTWRITEBYTECODE=1

# The same immutable image runs both containers in a provisioned Sandbox Pod:
# the stock AIO command/browser container and this lightweight external Agent
# host. Keeping Codex, Office/Chromium, fonts, ppt-master dependencies, CA trust,
# and the host service in one image prevents host/runtime drift between them.
COPY docker/sandbox-ppt-master/agent_host.py /opt/ppt-master-agent-host/agent_host.py
RUN chmod 0755 /opt/ppt-master-agent-host/agent_host.py \
    && python3 -m py_compile /opt/ppt-master-agent-host/agent_host.py
