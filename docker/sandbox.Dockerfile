FROM enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest

# Base image has distutils-installed blinker; pre-install with ignore flag
# to avoid "Cannot uninstall blinker 1.4" when bringing in Flask>=3.
RUN python -m pip install --no-cache-dir --ignore-installed "blinker>=1.9.0"

# Install CJK fonts for SVG/PDF preview so Chinese text does not render as tofu.
RUN set -eux; \
    if command -v apt-get >/dev/null 2>&1; then \
        apt-get update; \
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            fontconfig \
            fonts-noto-cjk \
            fonts-wqy-zenhei \
            fonts-wqy-microhei; \
        rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache \
            fontconfig \
            font-noto-cjk; \
    elif command -v yum >/dev/null 2>&1; then \
        yum install -y \
            fontconfig \
            google-noto-sans-cjk-fonts; \
        yum clean all; \
    fi; \
    fc-cache -f -v || true

RUN python -m pip install --no-cache-dir \
    # svg_to_pptx
    "python-pptx>=0.6.21" \
    "edge-tts>=7.2.8" \
    svglib \
    "reportlab>=4.0.0" \
    "cairosvg>=2.7.1" \
    # source_to_md
    "PyMuPDF>=1.23.0" \
    "mammoth>=1.6.0" \
    "markdownify>=0.11.6" \
    "ebooklib>=0.18" \
    "nbconvert>=7.0.0" \
    "openpyxl>=3.1.0" \
    # image processing
    "Pillow>=9.0.0" \
    "numpy>=1.20.0" \
    # web_to_md
    "requests>=2.31.0" \
    "beautifulsoup4>=4.12.0" \
    "curl_cffi>=0.7.0" \
    # image_gen
    "google-genai>=1.0.0" \
    "openai>=1.0.0" \
    # svg_editor
    "flask>=3.0.0"
