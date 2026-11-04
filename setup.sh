#!/usr/bin/env bash
# Brings up Switch, Mattermost and the demo shop on this machine.
set -euo pipefail
cd "$(dirname "$0")"

for tool in docker uv openssl python3; do
  command -v "$tool" >/dev/null || { echo "$tool is required." >&2; exit 1; }
done

if [ ! -f infra/.env ]; then
  cp infra/.env.example infra/.env
  infra/gen-secrets.sh infra/.env blackout
  chmod 600 infra/.env
fi

(cd infra && docker compose up -d)

echo "Waiting for Switch and Mattermost..."
for _ in $(seq 1 100); do
  if blackout/py blackout/switch.py 2>/dev/null; then ready=1; break; fi
  sleep 3
done
[ "${ready:-}" = 1 ] || { echo "Switch didn't come up. Try: cd infra && docker compose logs" >&2; exit 1; }

python3 blackout/chaos.py reset
echo "Ready. Next: blackout/py blackout/gm.py"
