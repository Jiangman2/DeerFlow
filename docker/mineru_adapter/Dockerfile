ARG DOCREADER_IMAGE=wechatopenai/weknora-docreader@sha256:65d9a4a4fb7b2d3c065eb8eabe033d1f001cedfd039be7f2355540ecad7da57b
FROM ${DOCREADER_IMAGE}

COPY app.py /app/deerflow_mineru_adapter.py

EXPOSE 50052

ENTRYPOINT ["/app/.venv/bin/python", "/app/deerflow_mineru_adapter.py"]
