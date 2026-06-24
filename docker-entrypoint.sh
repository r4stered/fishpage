#!/bin/sh
# Container entrypoint: open the public ingress, make the catalog durable, then serve it.
#
# Both cloud pieces are opt-in via the environment, so a local `docker run` or CI runs the app
# directly on a plain local SQLite file with no tunnel and no replication:
#
#   CLOUDFLARE_TUNNEL_TOKEN  Run a Cloudflare Tunnel as the only public ingress. The Machine has no
#                            public origin, so cloudflared dials out to Cloudflare's edge — which
#                            enforces the login + allowlist — and forwards requests to the local
#                            app. Run in the background; tini (PID 1) forwards the stop signal to it
#                            as well as to the app, so on a deploy it deregisters its tunnel
#                            connection rather than being killed mid-flight. The app stays the main
#                            long-running process, so its exit is what stops the container.
#
#   LITESTREAM_REPLICA_URL   Restore the database from the R2 replica (a no-op on the first boot,
#                            when the replica is still empty — the app then seeds from the sample
#                            Stocklist), then hand off to Litestream, which runs the app and streams
#                            its write-ahead log to R2 for as long as it lives.
#
# Restore and replicate are sequenced, never concurrent, so the two Litestream operations never
# contend for the same database file.
set -e

if [ -n "$CLOUDFLARE_TUNNEL_TOKEN" ]; then
    cloudflared tunnel --no-autoupdate run --token "$CLOUDFLARE_TUNNEL_TOKEN" &
fi

if [ -n "$LITESTREAM_REPLICA_URL" ]; then
    fishpage-restore
    exec litestream replicate -exec "fishpage"
fi
exec fishpage
