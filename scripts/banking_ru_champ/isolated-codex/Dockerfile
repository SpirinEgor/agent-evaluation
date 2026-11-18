FROM node@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY package/vendor/aarch64-unknown-linux-musl/bin/ /opt/codex/bin/
COPY package/vendor/aarch64-unknown-linux-musl/codex-path/ /opt/codex/codex-path/
ENV PATH="/opt/codex/bin:/opt/codex/codex-path:${PATH}"
WORKDIR /workspace
CMD ["sleep", "infinity"]
