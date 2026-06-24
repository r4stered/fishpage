# Multi-stage build. The builder compiles the application wheel; the final image installs only
# that wheel, so the running container carries no source tree, no tests, and no dev dependencies —
# just the app and its runtime dependencies.

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder
WORKDIR /src
COPY . .
RUN uv build --wheel -o /dist

FROM python:3.14-slim AS runtime

# The catalog is rebuilt from the packaged sample Stocklist on boot at this stage, so the database
# and watched-folder directories live under a writable working tree rather than site-packages. The
# database's parent directory must exist before the app opens it, so create it at build time.
WORKDIR /app
RUN mkdir -p /app/data/incoming /app/data/processed
ENV FISHPAGE_DB=/app/data/fishpage.db \
    INCOMING_DIR=/app/data/incoming \
    PROCESSED_DIR=/app/data/processed \
    HOST=0.0.0.0 \
    PORT=8080

COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

EXPOSE 8080
CMD ["fishpage"]
