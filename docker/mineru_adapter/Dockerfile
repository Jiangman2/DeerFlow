ARG DOCREADER_IMAGE=wechatopenai/weknora-docreader@sha256:59e26f98f17c296c5f4bd216404558f51d31991b580cfef1d204903a01e4b46b
FROM ${DOCREADER_IMAGE}

COPY requirements-anydoc.txt /tmp/requirements-anydoc.txt
RUN /app/.venv/bin/python -m pip install \
      --no-cache-dir \
      --only-binary=:all: \
      --require-hashes \
      -r /tmp/requirements-anydoc.txt \
    && rm -f /tmp/requirements-anydoc.txt

COPY app.py /app/deerflow_mineru_adapter.py
COPY anydoc_worker.py /app/anydoc_worker.py
COPY docreader_worker.py /app/docreader_worker.py

EXPOSE 50052

ENTRYPOINT ["/app/.venv/bin/python", "/app/deerflow_mineru_adapter.py"]
