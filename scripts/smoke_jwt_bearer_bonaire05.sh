#!/usr/bin/env bash
#
# End-to-end check of the jrsz.net /bravo -> bonaire05 (PingOne AIC) RFC 7523 JWT-bearer trust
# (docs/bonaire05-jwt-bearer.md). Same shape as horizon -> bonaire05:
#
#   1. password grant at am.jrsz.net /bravo with the "portal" client -> RS256 JWT access token
#      (aud = bonaire05 token endpoint, preferred_username = uid, scope incl. a2a:invoke)
#   2. POST that token as `assertion` (grant_type jwt-bearer) to bonaire05 with a client that has
#      the JWT Bearer grant (client_secret_post) -> bonaire05 access token for the same userName
#
# Reads .env for defaults. Env:
#   COM_AM_BASE_URL              default https://am.jrsz.net:9443/am
#   BONAIRE_OIDC_REALM           default bravo
#   BONAIRE_PORTAL_CLIENT_ID / BONAIRE_PORTAL_CLIENT_SECRET   local portal client
#   BONAIRE_DEMO_USER / BONAIRE_DEMO_USER_PASSWORD           identity present in BOTH tenants
#   BONAIRE_PORTAL_SCOPES        default "openid profile email a2a:invoke"
#   BONAIRE_AM_URL / BONAIRE_REALM                            default bonaire05 / alpha
#   BONAIRE_JWT_CLIENT_ID / BONAIRE_JWT_CLIENT_SECRET         bonaire05 jwt-bearer client (jrsz-concierge)
#   BONAIRE_JWT_SCOPE            default a2a:invoke
#   ASSERTION                    skip step 1 and use this JWT
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
CA_CERT="${ROOT_DIR}/secrets/tls/ca/ca-bundle.pem"   # JRSZ root + ISRG Root X1 (written by scripts/le-cert.sh install)
[[ -f "${CA_CERT}" ]] || CA_CERT="${ROOT_DIR}/secrets/tls/ca/jrsz-root-ca.cert.pem"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

COM_AM_BASE_URL="${COM_AM_BASE_URL:-https://am.jrsz.net:9443/am}"
LOCAL_REALM="${BONAIRE_OIDC_REALM:-bravo}"
PORTAL_CLIENT_ID="${BONAIRE_PORTAL_CLIENT_ID:-bonaire-portal}"
PORTAL_CLIENT_SECRET="${BONAIRE_PORTAL_CLIENT_SECRET:-bonaire-portal-secret-changeit}"
PORTAL_SCOPES="${BONAIRE_PORTAL_SCOPES:-openid profile email a2a:invoke}"
USER_NAME="${BONAIRE_DEMO_USER:-acarter}"
USER_PASSWORD="${BONAIRE_DEMO_USER_PASSWORD:?BONAIRE_DEMO_USER_PASSWORD is required (.env)}"

BONAIRE_AM_URL="${BONAIRE_AM_URL:-https://openam-bonaire05.forgeblocks.com/am}"
BONAIRE_REALM="${BONAIRE_REALM:-alpha}"
JWT_CLIENT_ID="${BONAIRE_JWT_CLIENT_ID:?BONAIRE_JWT_CLIENT_ID is required (.env)}"
JWT_CLIENT_SECRET="${BONAIRE_JWT_CLIENT_SECRET:?BONAIRE_JWT_CLIENT_SECRET is required (.env)}"
JWT_SCOPE="${BONAIRE_JWT_SCOPE:-a2a:invoke}"

require_bin() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required binary: $1" >&2; exit 1; }; }
require_bin curl
require_bin python3

local_token_url="${COM_AM_BASE_URL}/oauth2/realms/root/realms/${LOCAL_REALM}/access_token"
remote_token_url="${BONAIRE_AM_URL}/oauth2/realms/root/realms/${BONAIRE_REALM}/access_token"

curl_local=(--silent --show-error)
if [[ -f "${CA_CERT}" ]]; then curl_local+=(--cacert "${CA_CERT}"); else curl_local+=(--insecure); fi

decode_claims() {  # $1 = jwt, $2 = label
  python3 - "$1" "$2" <<'PY'
import base64, json, sys
jwt, label = sys.argv[1], sys.argv[2]
parts = jwt.split(".")
def dec(p):
    p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p))
if len(parts) != 3:
    print(f"  {label}: opaque token"); sys.exit(0)
h, c = dec(parts[0]), dec(parts[1])
keep = ("iss", "sub", "subname", "aud", "preferred_username", "scope", "grant_type", "exp", "act")
print(f"  {label}: alg={h.get('alg')} kid={h.get('kid')}")
for k in keep:
    if k in c:
        print(f"    {k} = {json.dumps(c[k])}")
PY
}

if [[ -z "${ASSERTION:-}" ]]; then
  echo "1) Password grant at ${local_token_url} as ${USER_NAME} (client ${PORTAL_CLIENT_ID})"
  local_resp="$(curl "${curl_local[@]}" -X POST "${local_token_url}" \
    --user "${PORTAL_CLIENT_ID}:${PORTAL_CLIENT_SECRET}" \
    --data-urlencode 'grant_type=password' \
    --data-urlencode "username=${USER_NAME}" \
    --data-urlencode "password=${USER_PASSWORD}" \
    --data-urlencode "scope=${PORTAL_SCOPES}")"
  ASSERTION="$(python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("access_token",""))' <<<"${local_resp}")"
  if [[ -z "${ASSERTION}" ]]; then
    echo "FAIL: no access_token from ${COM_AM_BASE_URL}: ${local_resp}" >&2
    exit 1
  fi
  decode_claims "${ASSERTION}" "jrsz.net /${LOCAL_REALM} access token"
else
  echo "1) Using ASSERTION from environment"
  decode_claims "${ASSERTION}" "assertion"
fi

echo "2) RFC 7523 exchange at ${remote_token_url} (client ${JWT_CLIENT_ID}, scope ${JWT_SCOPE})"
remote_resp="$(curl --silent --show-error -X POST "${remote_token_url}" \
  --data-urlencode 'grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer' \
  --data-urlencode "assertion=${ASSERTION}" \
  --data-urlencode "client_id=${JWT_CLIENT_ID}" \
  --data-urlencode "client_secret=${JWT_CLIENT_SECRET}" \
  --data-urlencode "scope=${JWT_SCOPE}")"
remote_at="$(python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("access_token",""))' <<<"${remote_resp}")"
if [[ -z "${remote_at}" ]]; then
  echo "FAIL: bonaire05 rejected the assertion: ${remote_resp}" >&2
  exit 1
fi
decode_claims "${remote_at}" "bonaire05 access token"
echo "PASS: bonaire05 issued a token for '${USER_NAME}' from the jrsz.net /${LOCAL_REALM} assertion"
