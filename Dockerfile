# Multi-stage build. The builder compiles the application wheel; the final image installs only
# that wheel, so the running container carries no source tree, no tests, and no dev dependencies —
# just the app and its runtime dependencies.

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder
WORKDIR /src
COPY . .
RUN uv build --wheel -o /dist

FROM python:3.14-slim AS runtime

# The database and watched-folder directories live under a writable working tree rather than
# site-packages. The ephemeral disk starts blank on every boot; durability comes from restoring
# the database from R2 before serving (see the entrypoint), not from the disk surviving. The
# database's parent directory must exist before the app opens it, so create it at build time.
WORKDIR /app
RUN mkdir -p /app/data/incoming /app/data/processed
ENV FISHPAGE_DB=/app/data/fishpage.db \
    INCOMING_DIR=/app/data/incoming \
    PROCESSED_DIR=/app/data/processed \
    HOST=0.0.0.0 \
    PORT=8080

# Litestream supervises the app and streams the SQLite WAL to R2. Copy its static binary from the
# published image rather than fetching a release tarball at build time.
COPY --from=litestream/litestream:0.3.13 /usr/local/bin/litestream /usr/local/bin/litestream
COPY litestream.yml /etc/litestream.yml
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

EXPOSE 8080
ENTRYPOINT ["docker-entrypoint.sh"]
