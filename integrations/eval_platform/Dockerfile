# Build the generated bundle, never the repository root (see development.md).
ARG EXEC_IMAGE
FROM python:3.12-slim AS execute
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock .
RUN pip install --no-deps --require-hashes -r requirements.lock
COPY vendor/platform-sdk-contract /tmp/platform-sdk-contract
RUN pip install --no-deps /tmp/platform-sdk-contract \
    && rm -rf /tmp/platform-sdk-contract
ENV PYTHONPATH=/app/src TAU2_DATA_DIR=/app/data
COPY execute/src/ src/
COPY execute/data/ data/
COPY platform_benchmark.py .

FROM ${EXEC_IMAGE} AS evaluate
COPY evaluate/src/ src/
COPY evaluate/data/ data/
COPY platform_tasks.py platform_evaluator.py .
