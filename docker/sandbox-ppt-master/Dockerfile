FROM enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0

USER root

ARG APT_MIRROR=

# Keep the sandbox runtime aligned with the ppt-master skill dependency list.
COPY --from=ppt-master-skill requirements.txt /tmp/ppt-master-requirements.txt

# CairoSVG needs native Cairo libraries at runtime.
RUN if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|http://archive.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" /etc/apt/sources.list; \
        sed -i "s|http://security.ubuntu.com/ubuntu|http://${APT_MIRROR}/ubuntu|g" /etc/apt/sources.list; \
    fi \
    && apt-get update -o Acquire::Retries=5 \
    && apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
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

# Install the complete upstream dependency list instead of relying on packages
# that happen to be present in the base image. Playwright is an upstream
# runtime requirement of visual_review.py and extract_svg_pictures.py, but is
# not yet declared in the upstream requirements.txt.
RUN python3 -m pip install --no-cache-dir \
    --timeout 60 --retries 3 --prefer-binary --ignore-installed \
    -r /tmp/ppt-master-requirements.txt \
    "playwright>=1.50.0" "pydantic>=2.12.5,<3.0.0" \
    && python3 -m playwright install chromium \
    && rm -rf /root/.cache/pip /tmp/pip-* /tmp/ppt-master-requirements.txt \
    && find /usr /opt -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# The base image contains Pydantic 2.12.4. Reinstall after the upstream batch
# because pip's --ignore-installed mode can leave that older distribution
# visible even when a newer wheel was selected by the resolver.
RUN python3 -m pip install --no-cache-dir \
    --upgrade --force-reinstall "pydantic>=2.12.5,<3.0.0" \
    && python3 -c "import pydantic; from packaging.version import Version; assert Version(pydantic.__version__) >= Version('2.12.5'), pydantic.__version__"

ENV PYTHONDONTWRITEBYTECODE=1
