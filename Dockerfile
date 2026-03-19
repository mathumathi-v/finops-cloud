# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

# ── Build stage ──────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build deps first for layer caching
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir build

# Copy source
COPY cli/ cli/
COPY cloud/ cloud/
COPY collectors/ collectors/
COPY cost_model/ cost_model/
COPY intelligence/ intelligence/
COPY llm/ llm/
COPY storage/ storage/
COPY scheduler/ scheduler/

# Build wheel with all optional cloud deps
RUN pip wheel --no-cache-dir --wheel-dir /wheels -e ".[gcp,azure,oci]"

# ── Runtime stage ────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="finops-agent" \
      org.opencontainers.image.description="CLI-first, multi-cloud infrastructure cost reasoning agent" \
      org.opencontainers.image.source="https://github.com/mathumathi-v/finops-cloud" \
      org.opencontainers.image.licenses="Apache-2.0"

# Non-root user for security
RUN groupadd --gid 1000 finops && \
    useradd --uid 1000 --gid finops --create-home finops

WORKDIR /app

# Install from pre-built wheels
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Copy source and install in editable mode (for the entrypoint)
COPY pyproject.toml README.md ./
COPY cli/ cli/
COPY cloud/ cloud/
COPY collectors/ collectors/
COPY cost_model/ cost_model/
COPY intelligence/ intelligence/
COPY llm/ llm/
COPY storage/ storage/
COPY scheduler/ scheduler/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN pip install --no-cache-dir -e ".[gcp,azure,oci]"

# Data directory for SQLite DB and config
RUN mkdir -p /home/finops/.finops-agent && \
    chown -R finops:finops /home/finops/.finops-agent /app && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

USER finops

# Persist the database across container restarts
VOLUME ["/home/finops/.finops-agent"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["--help"]
