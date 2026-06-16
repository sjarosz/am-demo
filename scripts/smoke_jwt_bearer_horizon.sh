#!/usr/bin/env bash
#
# Optional end-to-end check: take a /bravo id_token and exchange it at the
# horizon AIC token endpoint using the RFC 7523 JWT-bearer grant.
#
# Prerequisites (configured BY YOU on horizon -- see docs/horizon-jwt-bearer.md):
#   - a Trusted JWT Issuer registered with the /bravo issuer + exported JWK Set
#   - an OAuth2 client with the JWT Bearer grant enabled
#
# Required env:
#   HORIZON_CLIENT_ID, HORIZON_CLIENT_SECRET   horizon OAuth2 client (Basic auth)
# Optional env:
#   HORIZON_AM_URL   default https://openam-horizon.forgeblocks.com/am
#   HORIZON_REALM    default alpha
#   HORIZON_SCOPE    default openid
#   ID_TOKEN         a /bravo id_token; if unset, this script obtains one
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${HORIZON_CLIENT_ID:?HORIZON_CLIENT_ID is required}"
: "${HORIZON_CLIENT_SECRET:?HORIZON_CLIENT_SECRET is required}"
HORIZON_AM_URL="${HORIZON_AM_URL:-https://openam-horizon.forgeblocks.com/am}"
HORIZON_REALM="${HORIZON_REALM:-alpha}"
HORIZON_SCOPE="${HORIZON_SCOPE:-openid}"
token_url="${HORIZON_AM_URL}/oauth2/realms/root/realms/${HORIZON_REALM}/access_token"

require_bin() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required binary: $1" >&2; exit 1; }; }
require_bin curl
require_bin python3

if [[ -z "${ID_TOKEN:-}" ]]; then
  echo "ID_TOKEN not provided; obtaining one via smoke_oidc_bravo.sh is manual."
  echo "Run scripts/smoke_oidc_bravo.sh, copy the id_token, and re-run with ID_TOKEN set." >&2
  exit 2
fi

echo "Exchanging /bravo id_token at ${token_url}"
resp="$(curl --silent --show-error \
  --user "${HORIZON_CLIENT_ID}:${HORIZON_CLIENT_SECRET}" \
  --data 'grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer' \
  --data-urlencode "assertion=${ID_TOKEN}" \
  --data-urlencode "scope=${HORIZON_SCOPE}" \
  "${token_url}")"

echo "${resp}" | python3 -m json.tool || echo "${resp}"

if echo "${resp}" | grep -q '"access_token"'; then
  echo "PASS: horizon issued an access token via JWT bearer"
else
  echo "FAIL: no access_token in horizon response" >&2
  exit 1
fi
