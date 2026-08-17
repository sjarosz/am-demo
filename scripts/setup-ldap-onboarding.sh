#!/usr/bin/env bash
#
# Idempotent, replayable setup of "ds.jrsz.net -> RCS -> bonaire05" user onboarding
# (docs/bonaire05-ldap-onboarding.md). Safe to re-run any time; called by
# scripts/reset-stack.sh after the jrsz.net bootstrap when BOOTSTRAP_LDAP_ONBOARDING=true.
#
#   1. openicf/          from zips/openicf-zip-<ver>.zip (vendor RCS, gitignored)
#   2. secrets/rcs/client-secret  (tenant OAuth2 client secret, generated once)
#   3. demo users        config/ds/seed-users.ldif -> ds.jrsz.net (scripts/seed-ldap-users.sh)
#   4. tenant, part 1    OAuth2 client + auth mapping + connector-server registration
#   5. rcs.jrsz.net      docker compose up -d --build rcs-com (waits for the websocket)
#   6. tenant, part 2    connector, application, mapping, schedule, connector test, reconcile
#   7. --smoke           scripts/smoke_ldap_onboarding.sh (add/recon/delete/recon round trip)
#
# Usage: scripts/setup-ldap-onboarding.sh [--smoke] [--no-recon] [--skip-seed]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SMOKE=false; RECON=true; SEED=true
for a in "$@"; do
  case "$a" in
    --smoke) SMOKE=true ;;
    --no-recon) RECON=false ;;
    --skip-seed) SEED=false ;;
    *) echo "usage: $0 [--smoke] [--no-recon] [--skip-seed]" >&2; exit 2 ;;
  esac
done

say() { echo "==> [ldap-onboarding] $*"; }

# 1. vendor RCS tree
if ! ls openicf/lib/framework/connector-framework-internal-*.jar >/dev/null 2>&1; then
  ZIP="$(ls zips/openicf-zip-*.zip 2>/dev/null | sort -V | tail -1 || true)"
  if [[ -z "$ZIP" ]]; then
    echo "!! zips/openicf-zip-<version>.zip missing (RCS distribution; see docs/install-after-git-pull.md)" >&2
    exit 1
  fi
  say "unzipping $ZIP -> openicf/"
  rm -rf openicf && unzip -q "$ZIP" -d .
else
  say "openicf/ present ($(ls openicf/lib/framework/connector-framework-internal-*.jar | sed -E 's/.*internal-(.*)\.jar/\1/'))"
fi

# 2. tenant client secret
mkdir -p secrets/rcs
if [[ ! -s secrets/rcs/client-secret ]]; then
  say "generating secrets/rcs/client-secret"
  openssl rand -base64 24 | tr -d '/+=\n' > secrets/rcs/client-secret
  chmod 600 secrets/rcs/client-secret
fi

# frodo is required for every tenant-side step
if ! command -v frodo >/dev/null 2>&1; then
  echo "!! frodo CLI not found (brew install frodo-cli) - cannot provision bonaire05" >&2
  exit 1
fi

# 3. seed users (needs ds.jrsz.net healthy)
if $SEED; then
  say "waiting for ds.jrsz.net"
  for _ in $(seq 1 60); do
    [[ "$(docker inspect -f '{{.State.Health.Status}}' ds.jrsz.net 2>/dev/null || echo missing)" == "healthy" ]] && break
    sleep 5
  done
  ./scripts/seed-ldap-users.sh com | grep -v -E '^\s+(uid|#)' || true
fi

# 4. tenant part 1 (client + auth mapping + connector server), does not wait for the RCS
say "bonaire05: OAuth2 client, auth mapping, connector-server registration"
./scripts/provision_bonaire05_ldap_app.py --register-only

# 5. RCS container
say "starting rcs.jrsz.net"
docker compose up -d --build --no-deps rcs-com >/dev/null   # --no-deps: never recreate ds.jrsz.net from here
printf '    waiting for RCS websocket '
for _ in $(seq 1 36); do
  if docker logs rcs.jrsz.net 2>&1 | grep -q 'operational=true'; then echo "connected"; break; fi
  if docker logs rcs.jrsz.net 2>&1 | grep -q 'OAuth 2.0 token request failed'; then
    echo; echo "!! RCS OAuth2 token request failed - secret mismatch? re-run this script (it re-PUTs the client)" >&2; exit 1
  fi
  printf '.'; sleep 5
done

# 6. tenant part 2 (+ test + recon)
if $RECON; then
  say "bonaire05: connector, application, mapping, schedule, test, reconcile"
  ./scripts/provision_bonaire05_ldap_app.py
else
  say "bonaire05: connector, application, mapping, schedule (no recon)"
  ./scripts/provision_bonaire05_ldap_app.py --no-recon
fi

# 7. smoke
if $SMOKE; then
  say "smoke test"
  ./scripts/smoke_ldap_onboarding.sh
fi
say "done"
