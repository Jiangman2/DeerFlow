ARG SANDBOX_BASE_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox@sha256:742062f99915e5495df8d4bfeaf40a93197c87c7c47b4e2407cd2b6356df8f48
FROM ${SANDBOX_BASE_IMAGE}

USER root

ARG APT_MIRROR=

# Keep the sandbox runtime aligned with the ppt-master skill dependency list.
COPY skills/public/ppt-master/requirements.txt /tmp/ppt-master-requirements.txt

# CairoSVG needs native Cairo libraries at runtime.
RUN if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|http://archive.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" /etc/apt/sources.list; \
        sed -i "s|http://security.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" /etc/apt/sources.list; \
    fi \
    && apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libpango-1.0-0 \
        fonts-noto-cjk \
        fonts-liberation \
        fonts-wqy-zenhei \
        fontconfig \
        pandoc \
        librsvg2-bin \
        shared-mime-info \
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

# The upstream image already includes several requirements (python-pptx,
# PyMuPDF, openpyxl, Pillow, numpy, requests, beautifulsoup4, curl_cffi).
# Install the remaining ppt-master packages in cacheable groups so interrupted
# builds can resume without redownloading everything.
RUN python3 -m pip install --root-user-action=ignore --no-cache-dir \
    --timeout 60 --retries 3 --prefer-binary \
    "edge-tts>=7.2.8" "cairosvg>=2.7.0" "svglib>=1.5.0" "reportlab>=4.0.0"

RUN python3 -m pip install --root-user-action=ignore --no-cache-dir \
    --timeout 60 --retries 3 --prefer-binary \
    "mammoth>=1.6.0" "markdownify>=0.11.6" "ebooklib>=0.18" "nbconvert>=7.0.0"

RUN python3 -m pip install --root-user-action=ignore --no-cache-dir \
    --timeout 60 --retries 3 --prefer-binary --ignore-installed \
    "google-genai>=1.0.0" "openai>=1.0.0" "flask>=3.0.0" \
    && rm -rf /root/.cache/pip /tmp/pip-* /tmp/ppt-master-requirements.txt \
    && find /usr /opt -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
