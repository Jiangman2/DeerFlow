FROM enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest

USER root

# Keep the sandbox runtime aligned with the ppt-master skill dependency list.
COPY skills/public/ppt-master/requirements.txt /tmp/ppt-master-requirements.txt

# CairoSVG needs native Cairo libraries at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libpango-1.0-0 \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# The upstream image already includes several requirements (python-pptx,
# PyMuPDF, openpyxl, Pillow, numpy, requests, beautifulsoup4, curl_cffi).
# Install the remaining ppt-master packages in cacheable groups so interrupted
# builds can resume without redownloading everything.
RUN python3 -m pip install --root-user-action=ignore \
    --timeout 60 --retries 3 --prefer-binary \
    "edge-tts>=7.2.8" "cairosvg>=2.7.0" "svglib>=1.5.0" "reportlab>=4.0.0"

RUN python3 -m pip install --root-user-action=ignore \
    --timeout 60 --retries 3 --prefer-binary \
    "mammoth>=1.6.0" "markdownify>=0.11.6" "ebooklib>=0.18" "nbconvert>=7.0.0"

RUN python3 -m pip install --root-user-action=ignore \
    --timeout 60 --retries 3 --prefer-binary --ignore-installed \
    "google-genai>=1.0.0" "openai>=1.0.0" "flask>=3.0.0" \
    && rm -f /tmp/ppt-master-requirements.txt
