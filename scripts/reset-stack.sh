#!/usr/bin/env bash
#
# Reliable full reset for the jrsz.org + jrsz.com lab.
#
# Why this exists:
#   The `amster-bootstrap` / `amster-bootstrap-com` services are one-shot
#   containers gated behind the Compose `bootstrap` profile. A plain
#   `docker compose down -v` does NOT target profile services, so those
#   containers linger (Exited/Created) and keep the `am-home-bootstrap`
#   volume mounted ("Resource is still in use"). The volume therefore
#   survives the wipe, leaving AM's config out of sync with a freshly
#   recreated DS config store -> AM returns HTTP 500
#   ("Configuration store is not available") and the bootstrap deadlocks
#   waiting for AM to become healthy.
#
# This script forces a clean, consistent baseline and re-bootstraps both
# stacks. Use it for "wipe and start over" or to validate a fresh clone.
#
# Usage:
#   ./scripts/reset-stack.sh            # full wipe (down -v) + rebuild + bootstrap
#   ./scripts/reset-stack.sh --keep-data  # bootstrap only, keep AM/DS volumes
#
set -euo pipefail

cd "$(dirname "$0")/.."

KEEP_DATA=false
if [[ "${1:-}" == "--keep-data" ]]; then
  KEEP_DATA=true
fi

echo "==> Removing one-shot bootstrap containers (so they cannot pin volumes)"
docker rm -f amster-bootstrap.jrsz.org amster-bootstrap.jrsz.com >/dev/null 2>&1 || true

if [[ "${KEEP_DATA}" == "false" ]]; then
  echo "==> Tearing down stack and wiping volumes (down -v, including bootstrap profile)"
  # Pass the bootstrap profile so 'down' also removes the one-shot services.
  docker compose --profile bootstrap down -v --remove-orphans

  echo "==> Verifying AM/DS volumes are gone"
  if docker volume ls --format '{{.Name}}' | grep -E 'am-standalone_(am-home-bootstrap|ds-data|ds-secrets)(-com)?$'; then
    echo "    Some volumes survived; force-removing"
    docker volume ls --format '{{.Name}}' \
      | grep -E 'am-standalone_(am-home-bootstrap|ds-data|ds-secrets)(-com)?$' \
      | xargs -r docker volume rm
  else
    echo "    clean"
  fi
fi

echo "==> Starting the full stack (detached, with build)"
docker compose up -d --build

echo "==> Waiting for both AMs to report healthy (configurator mode)"
for am in am.jrsz.org am.jrsz.com; do
  printf '    %s ' "${am}"
  for _ in $(seq 1 60); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "${am}" 2>/dev/null || echo missing)"
    if [[ "${status}" == "healthy" ]]; then
      echo "healthy"
      break
    fi
    printf '.'
    sleep 5
  done
done

echo "==> Bootstrapping jrsz.org"
docker compose --profile bootstrap up --build --abort-on-container-exit amster-bootstrap

echo "==> Bootstrapping jrsz.com"
docker compose --profile bootstrap up --build --abort-on-container-exit amster-bootstrap-com

echo "==> Cleaning up one-shot bootstrap containers (keeps future 'down -v' clean)"
docker rm -f amster-bootstrap.jrsz.org amster-bootstrap.jrsz.com >/dev/null 2>&1 || true

echo "==> Restarting gateways so SSO picks up freshly-created agents"
docker compose restart gateway gateway-com >/dev/null

echo "==> Done. Quick check:"
docker compose ps --format '{{.Name}}\t{{.Status}}' | grep -E 'am\.jrsz|ig\.jrsz' || true
echo
echo "Validate: https://ig.jrsz.org/  and  https://app6.jrsz.org/"
