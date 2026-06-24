#!/bin/sh
# Container entrypoint: make the catalog durable when replication is configured, then serve it.
#
# Replication is opt-in via the environment. The cloud deploy sets LITESTREAM_REPLICA_URL, so:
#
#   1. Restore the database from the R2 replica (a no-op on the first boot, when the replica is
#      still empty — the app then seeds from the sample Stocklist).
#   2. Hand off to Litestream, which runs the app and streams its write-ahead log to R2 for as
#      long as it lives.
#
# Restore and replicate are sequenced, never concurrent, so the two Litestream operations never
# contend for the same database file. With no replica set — a local `docker run` or CI — the image
# runs the app directly on a plain local SQLite file, with no R2 and no Litestream.
set -e
if [ -n "$LITESTREAM_REPLICA_URL" ]; then
    fishpage-restore
    exec litestream replicate -exec "fishpage"
fi
exec fishpage
