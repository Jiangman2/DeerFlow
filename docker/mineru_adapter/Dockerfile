ARG DOCREADER_IMAGE=wechatopenai/weknora-docreader@sha256:65d9a4a4fb7b2d3c065eb8eabe033d1f001cedfd039be7f2355540ecad7da57b
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
