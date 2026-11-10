# Build the generated bundle, never the repository root (see development.md).
ARG EXEC_IMAGE=execute
FROM python:3.12-slim AS execute
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock .
RUN pip install --no-deps --require-hashes -r requirements.lock
COPY vendor/platform-sdk-contract /tmp/platform-sdk-contract
RUN pip install --no-deps /tmp/platform-sdk-contract \
    && rm -rf /tmp/platform-sdk-contract
ENV TAU2_DATA_DIR=/app/data
ENV BANKING_PAYLOAD_STORE=/app/private_payloads
COPY private_payloads/ private_payloads/
# The platform overrides PYTHONPATH for its mounted harness. Author packages
# live beside the fixed entrypoints, as in the platform reference benchmark.
COPY execute/src/ ./
COPY execute/data/ data/
COPY platform_benchmark.py .

FROM ${EXEC_IMAGE} AS evaluate
COPY evaluate/src/ ./
COPY evaluate/data/ data/
COPY platform_tasks.py platform_evaluator.py .
