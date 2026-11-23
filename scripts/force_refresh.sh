#!/usr/bin/env bash
# Force the 5-minute sync to run right now, instead of waiting for the next cycle.
#
#   ./scripts/force_refresh.sh                    # all clients
#   ./scripts/force_refresh.sh "Acme Corp"     # one client (name as in list-clients)
#   ./scripts/force_refresh.sh --all-details      # also fetch every exact pattern version now
#
# Runs inside the running app container, so it uses the same database and keys.
# If the scheduler is mid-sync, this waits for it to finish and then runs.
set -euo pipefail
cd "$(dirname "$0")/.."

args=()
for a in "$@"; do
  case "$a" in
    --all-details) args+=(--all-details) ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) args+=(--name "$a") ;;
  esac
done

if ! docker compose ps --status running --services 2>/dev/null | grep -qx app; then
  echo "The app container isn't running. Start it first: docker compose up -d" >&2
  exit 1
fi

echo "Syncing now (this can take a minute or two)..."
docker compose exec -T app python -m app.cli poll "${args[@]}"
