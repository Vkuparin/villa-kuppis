#!/usr/bin/env bash
# Update Villa Kuppis on the Debian home server: pull main, rebuild the image,
# restart only the villa-kuppis service and show its health.
# Home Assistant is not touched. Run as a user that may use Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose -f deploy/home/compose.yaml)

echo "1/4 Pulling main..."
git pull --ff-only origin main

echo "2/4 Building the image..."
"${compose[@]}" build villa-kuppis

echo "3/4 Restarting villa-kuppis..."
"${compose[@]}" up -d villa-kuppis

echo "4/4 Waiting for the health check..."
container=$("${compose[@]}" ps -q villa-kuppis)
for _ in $(seq 1 30); do
    status=$(docker inspect --format '{{.State.Health.Status}}' "$container")
    [ "$status" = healthy ] && break
    sleep 2
done
echo "villa-kuppis health: $status"
[ "$status" = healthy ] || { "${compose[@]}" logs --tail 30 villa-kuppis; exit 1; }
