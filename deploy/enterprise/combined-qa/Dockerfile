# Local disposable qualification image only. Never deploy this image.
FROM deerflow-p3-backend-qa:r35-20261012-goalhistory1
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY uv.lock /qa/uv.lock
COPY locked_requirements.py /qa/locked_requirements.py
RUN /app/backend/.venv/bin/python /qa/locked_requirements.py /qa/uv.lock /qa/requirements.txt \
    && uv pip install --python /app/backend/.venv/bin/python --no-deps --require-hashes -r /qa/requirements.txt \
    && /app/backend/.venv/bin/python /qa/locked_requirements.py /qa/uv.lock --check
