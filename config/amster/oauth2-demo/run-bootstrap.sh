#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AM_BASE_URL="${AM_SERVER_URL:-${AM_URL:-}}"
AM_ADMIN_PWD="${AM_ADMIN_PWD:-${AM_ADMIN_PASSWORD:-}}"
APP4_BASE_URL="${APP4_BASE_URL:-https://app4.jrsz.org}"
APP4_CLIENT_ID="${APP4_CLIENT_ID:-demo-pkce-app}"
APP4_REDIRECT_URI="${APP4_REDIRECT_URI:-${APP4_BASE_URL}/callback}"
IG_AGENT_ID="${IG_AGENT_ID:-ig_agent_alpha}"
IG_AGENT_PASSWORD="${IG_AGENT_PASSWORD:-igagent-changeit-01}"
IG_BASE_URL="${IG_BASE_URL:-https://ig.jrsz.org}"
# Space-separated public app base URLs added to the alpha Validation Service
# validGotoDestinations. Defaults target the jrsz.org gateway (port 443).
PUBLIC_APP_BASE_URLS="${PUBLIC_APP_BASE_URLS:-https://app1.jrsz.org https://app2.jrsz.org https://app3.jrsz.org https://app4.jrsz.org https://app5.jrsz.org https://app6.jrsz.org}"
PROVIDER_TEMPLATE="/opt/amster-config/oauth-oidc.service.json"
CLIENT_TEMPLATE="${SCRIPT_DIR}/entities/demo-pkce-app.oauth2.app.json"

# app6 session-timeout / logout / OIDC SLO test console: dedicated short-timeout
# realm plus two OIDC RPs (RP C confidential, RP D public PKCE) that register
# back-channel logout URIs, and the IG agent used by AmServiceTimeout/Cached.
TIMEOUT_REALM_NAME="${TIMEOUT_REALM_NAME:-timeout-test}"
APP6_BASE_URL="${APP6_BASE_URL:-https://app6.jrsz.org}"
APP6_BASE_URL="${APP6_BASE_URL%/}"
RP_C_CLIENT_ID="${RP_C_CLIENT_ID:-rp-c-app}"
RP_C_CLIENT_SECRET="${RP_C_CLIENT_SECRET:-rp-c-secret-changeit}"
RP_D_CLIENT_ID="${RP_D_CLIENT_ID:-rp-d-app}"
RP_C_TEMPLATE="${SCRIPT_DIR}/entities/rp-c-app.oauth2.app.json"
RP_D_TEMPLATE="${SCRIPT_DIR}/entities/rp-d-app.oauth2.app.json"
# Short non-production defaults (AM in minutes, tokens in seconds).
AM_IDLE_MINUTES="${AM_IDLE_MINUTES:-6}"
AM_MAX_MINUTES="${AM_MAX_MINUTES:-20}"
AT_TTL_SECONDS="${AT_TTL_SECONDS:-300}"
RT_TTL_SECONDS="${RT_TTL_SECONDS:-1800}"
ID_TTL_SECONDS="${ID_TTL_SECONDS:-300}"
# app6 SSO session storage for the timeout-test realm: "true" => client-side
# (stateless JWT) sessions, "false" => server-side (CTS) sessions. Both are valid
# app6 test modes; the dashboard's "AM type" chip reflects whichever is in use.
TIMEOUT_REALM_STATELESS="${TIMEOUT_REALM_STATELESS:-true}"
# Realm-local test user for the timeout-test realm (so the SSO session is created
# IN that realm, exercising its session type). Set TIMEOUT_TEST_USER empty to skip.
TIMEOUT_TEST_USER="${TIMEOUT_TEST_USER:-tt-user}"
TIMEOUT_TEST_USER_PASSWORD="${TIMEOUT_TEST_USER_PASSWORD:-St@telessTest-2026-xyz}"

: "${AM_BASE_URL:?AM_SERVER_URL or AM_URL is required}"
: "${AM_ADMIN_PWD:?AM_ADMIN_PWD or AM_ADMIN_PASSWORD is required}"

require_bin() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required binary: $1" >&2
    exit 1
  }
}

require_bin curl
require_bin python3

work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

auth_body="${work_dir}/authenticate.json"
root_provider_body="${work_dir}/root-oauth-oidc.json"
root_client_body="${work_dir}/root-demo-pkce-app.json"
live_root_provider_body="${work_dir}/live-root-oauth-oidc.json"
live_root_client_body="${work_dir}/live-root-demo-pkce-app.json"
alpha_ig_agent_body="${work_dir}/alpha-ig-agent.json"
alpha_validation_body="${work_dir}/alpha-validation.json"
rp_c_body="${work_dir}/timeout-rp-c.json"
rp_d_body="${work_dir}/timeout-rp-d.json"
timeout_session_body="${work_dir}/timeout-session.json"

python3 - "$PROVIDER_TEMPLATE" "$root_provider_body" <<'PY'
import json
import sys
from pathlib import Path

source = json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(json.dumps(source["service"]["oauth-oidc"]))
PY

python3 - "$CLIENT_TEMPLATE" "$root_client_body" "$APP4_CLIENT_ID" "$APP4_REDIRECT_URI" <<'PY'
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text()
raw = raw.replace("@@CLIENT_ID@@", sys.argv[3]).replace("@@APP4_REDIRECT_URI@@", sys.argv[4])
source = json.loads(raw)
Path(sys.argv[2]).write_text(json.dumps(source["application"][sys.argv[3]]))
PY

python3 - "$alpha_ig_agent_body" "$IG_AGENT_PASSWORD" <<'PY'
import json
import sys
from pathlib import Path

body = {
    "userpassword": sys.argv[2],
    "status": {"inherited": False, "value": "Active"},
    "igCdssoRedirectUrls": {"inherited": False, "value": []},
    "igCdssoLoginUrlTemplate": {"inherited": False, "value": ""},
}
Path(sys.argv[1]).write_text(json.dumps(body))
PY

python3 - "$alpha_validation_body" "$IG_BASE_URL" "$PUBLIC_APP_BASE_URLS" <<'PY'
import json
import sys
from pathlib import Path

ig_base = sys.argv[2].rstrip("/")
public_hosts = [h for h in sys.argv[3].split() if h]
valid_gotos = [f"{ig_base}/*", f"{ig_base}/*?*"]
for base in public_hosts:
    base = base.rstrip("/")
    valid_gotos.extend([f"{base}/*", f"{base}/*?*"])

Path(sys.argv[1]).write_text(json.dumps({"validGotoDestinations": valid_gotos}))
PY

render_rp_client() {
  local template="$1" out="$2" client_id="$3" secret="$4"
  python3 - "$template" "$out" "$APP6_BASE_URL" "$client_id" "$secret" \
    "$AT_TTL_SECONDS" "$RT_TTL_SECONDS" "$ID_TTL_SECONDS" <<'PY'
import json
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text()
base = sys.argv[3].rstrip("/")
client_id = sys.argv[4]
# /rp/c/... or /rp/d/... derived from the client id suffix (rp-c-app -> c).
short = client_id.split("-")[1] if "-" in client_id else client_id
raw = raw.replace("@@REDIRECT_URI@@", f"{base}/rp/{short}/callback")
raw = raw.replace("@@POST_LOGOUT_URI@@", f"{base}/rp/{short}/post-logout")
raw = raw.replace("@@BACKCHANNEL_URI@@", f"{base}/rp/{short}/backchannel")
raw = raw.replace("@@CLIENT_SECRET@@", sys.argv[5])
raw = raw.replace("@@AT_TTL@@", sys.argv[6])
raw = raw.replace("@@RT_TTL@@", sys.argv[7])
raw = raw.replace("@@ID_TTL@@", sys.argv[8])
obj = json.loads(raw)
Path(sys.argv[2]).write_text(json.dumps(obj))
PY
}

render_rp_client "$RP_C_TEMPLATE" "$rp_c_body" "$RP_C_CLIENT_ID" "$RP_C_CLIENT_SECRET"
render_rp_client "$RP_D_TEMPLATE" "$rp_d_body" "$RP_D_CLIENT_ID" ""

python3 - "$timeout_session_body" "$AM_IDLE_MINUTES" "$AM_MAX_MINUTES" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "maxIdleTime": int(sys.argv[2]),
    "maxSessionTime": int(sys.argv[3]),
    "maxCachingTime": 3,
}))
PY

curl_common=(
  --silent
  --show-error
  --insecure
)

authenticate() {
  curl "${curl_common[@]}" \
    -H "X-OpenAM-Username: amadmin" \
    -H "X-OpenAM-Password: ${AM_ADMIN_PWD}" \
    -H "Accept-API-Version: resource=2.1, protocol=1.0" \
    -H "Content-Type: application/json" \
    -X POST \
    "${AM_BASE_URL}/json/realms/root/authenticate" \
    > "${auth_body}"

  sed -n 's/.*"tokenId":"\([^"]*\)".*/\1/p' "${auth_body}"
}

token_id="$(authenticate)"
if [[ -z "${token_id}" ]]; then
  echo "Failed to obtain AM admin session token" >&2
  cat "${auth_body}" >&2
  exit 1
fi

am_get() {
  local url="$1"
  shift
  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=1.0, protocol=1.0" \
    "$@" \
    "${url}"
}

service_get() {
  local url="$1"
  shift
  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=0.0, protocol=1.0" \
    "$@" \
    "${url}"
}

realm_exists() {
  local realm_name="$1"
  local response
  response="$(am_get "${AM_BASE_URL}/json/global-config/realms?_queryFilter=true")"
  [[ "${response}" == *"\"name\":\"${realm_name}\""* ]]
}

ensure_realm() {
  local realm_name="$1"
  if realm_exists "${realm_name}"; then
    echo "Realm ${realm_name} already exists"
    return 0
  fi

  echo "Creating realm ${realm_name}"
  am_get "${AM_BASE_URL}/json/global-config/realms?_action=create" \
    -H "Content-Type: application/json" \
    -X POST \
    --data "{\"parentPath\":\"/\",\"name\":\"${realm_name}\",\"active\":true,\"aliases\":[]}" \
    >/dev/null
}

resource_exists() {
  local url="$1"
  local resource_version="${2:-1.0}"
  local status
  status="$(
    curl "${curl_common[@]}" \
      -o /dev/null \
      -w '%{http_code}' \
      -H "iPlanetDirectoryPro: ${token_id}" \
      -H "Accept-API-Version: resource=${resource_version}, protocol=1.0" \
      "${url}"
  )"
  [[ "${status}" == "200" ]]
}

upsert_resource() {
  local url="$1"
  local body_file="$2"
  local match_header="If-None-Match: *"

  if resource_exists "${url}"; then
    match_header="If-Match: *"
  fi

  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=1.0, protocol=1.0" \
    -H "Content-Type: application/json" \
    -H "${match_header}" \
    -X PUT \
    --data-binary "@${body_file}" \
    "${url}" \
    >/dev/null
}

upsert_validation_service() {
  local url="$1"
  local body_file="$2"

  if resource_exists "${url}" "0.0"; then
    curl "${curl_common[@]}" \
      -H "iPlanetDirectoryPro: ${token_id}" \
      -H "Accept-API-Version: resource=0.0, protocol=1.0" \
      -H "Content-Type: application/json" \
      -H "If-Match: *" \
      -X PUT \
      --data-binary "@${body_file}" \
      "${url}" \
      >/dev/null
    return 0
  fi

  service_get "${url}?_action=create" \
    -H "Content-Type: application/json" \
    -X POST \
    --data-binary "@${body_file}" \
    >/dev/null
}

ensure_realm "alpha"
ensure_realm "bravo"
ensure_realm "${TIMEOUT_REALM_NAME}"

echo "Applying root OAuth2 provider"
upsert_resource "${AM_BASE_URL}/json/realms/root/realm-config/services/oauth-oidc" "${root_provider_body}"

echo "Applying root app4 client"
upsert_resource "${AM_BASE_URL}/json/realms/root/realm-config/agents/OAuth2Client/${APP4_CLIENT_ID}" "${root_client_body}"

echo "Applying alpha OAuth2 provider"
upsert_resource "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/services/oauth-oidc" "${root_provider_body}"

echo "Applying alpha app4 client"
upsert_resource "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/agents/OAuth2Client/${APP4_CLIENT_ID}" "${root_client_body}"

echo "Applying alpha PingGateway agent"
upsert_resource "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/agents/IdentityGatewayAgent/${IG_AGENT_ID}" "${alpha_ig_agent_body}"

echo "Applying alpha Validation Service"
upsert_validation_service "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/services/validation" "${alpha_validation_body}"

timeout_realm_base="${AM_BASE_URL}/json/realms/root/realms/${TIMEOUT_REALM_NAME}"

echo "Applying ${TIMEOUT_REALM_NAME} OAuth2 provider"
upsert_resource "${timeout_realm_base}/realm-config/services/oauth-oidc" "${root_provider_body}"

echo "Applying ${TIMEOUT_REALM_NAME} PingGateway agent (${IG_AGENT_ID})"
upsert_resource "${timeout_realm_base}/realm-config/agents/IdentityGatewayAgent/${IG_AGENT_ID}" "${alpha_ig_agent_body}"

echo "Applying ${TIMEOUT_REALM_NAME} OIDC client ${RP_C_CLIENT_ID} (confidential, back-channel logout)"
upsert_resource "${timeout_realm_base}/realm-config/agents/OAuth2Client/${RP_C_CLIENT_ID}" "${rp_c_body}"

echo "Applying ${TIMEOUT_REALM_NAME} OIDC client ${RP_D_CLIENT_ID} (public PKCE, back-channel logout)"
upsert_resource "${timeout_realm_base}/realm-config/agents/OAuth2Client/${RP_D_CLIENT_ID}" "${rp_d_body}"

echo "Applying ${TIMEOUT_REALM_NAME} Validation Service"
upsert_validation_service "${timeout_realm_base}/realm-config/services/validation" "${alpha_validation_body}"

echo "Applying ${TIMEOUT_REALM_NAME} short Session settings (idle=${AM_IDLE_MINUTES}m, max=${AM_MAX_MINUTES}m)"
upsert_resource "${timeout_realm_base}/realm-config/services/session" "${timeout_session_body}"

# SSO session storage type for the realm (client-side JWT vs server-side CTS).
# AM exposes this as general.statelessSessionsEnabled on the realm authentication
# config (NOT the session service). The auth config always exists, so PUT with
# If-Match: *. protocol=2.1 is required for this resource.
if [[ "${TIMEOUT_REALM_STATELESS}" == "true" || "${TIMEOUT_REALM_STATELESS}" == "false" ]]; then
  echo "Applying ${TIMEOUT_REALM_NAME} SSO session type (statelessSessionsEnabled=${TIMEOUT_REALM_STATELESS})"
  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=1.0, protocol=2.1" \
    -H "Content-Type: application/json" \
    -H "If-Match: *" \
    -X PUT \
    --data "{\"general\":{\"statelessSessionsEnabled\":${TIMEOUT_REALM_STATELESS}}}" \
    "${timeout_realm_base}/realm-config/authentication" \
    >/dev/null
fi

# Seed a realm-local user so logins create the SSO session IN timeout-test
# (exercising that realm's session type rather than reusing another realm's).
ensure_timeout_user() {
  local user="$1" pwd="$2"
  local status
  status="$(
    curl "${curl_common[@]}" \
      -o /dev/null -w '%{http_code}' \
      -H "iPlanetDirectoryPro: ${token_id}" \
      -H "Accept-API-Version: resource=4.0, protocol=2.1" \
      "${timeout_realm_base}/users/${user}"
  )"
  if [[ "${status}" == "200" ]]; then
    echo "Test user ${user} already exists in ${TIMEOUT_REALM_NAME}"
    return 0
  fi
  echo "Creating test user ${user} in ${TIMEOUT_REALM_NAME}"
  local user_body="${work_dir}/timeout-user.json"
  python3 - "$user" "$pwd" "$user_body" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[3]).write_text(json.dumps({
    "username": sys.argv[1],
    "userPassword": sys.argv[2],
    "givenName": "TT",
    "sn": "User",
    "cn": "TT User",
    "mail": sys.argv[1] + "@example.com",
}))
PY
  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=4.0, protocol=2.1" \
    -H "Content-Type: application/json" \
    -X POST \
    --data-binary "@${user_body}" \
    "${timeout_realm_base}/users/?_action=create" \
    >/dev/null
}

if [[ -n "${TIMEOUT_TEST_USER}" ]]; then
  ensure_timeout_user "${TIMEOUT_TEST_USER}" "${TIMEOUT_TEST_USER_PASSWORD}"
fi

echo "Verifying realms and clones"
realm_exists "alpha"
realm_exists "bravo"
realm_exists "${TIMEOUT_REALM_NAME}"
resource_exists "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/services/oauth-oidc"
resource_exists "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/agents/OAuth2Client/${APP4_CLIENT_ID}"
resource_exists "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/agents/IdentityGatewayAgent/${IG_AGENT_ID}"
resource_exists "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/services/validation" "0.0"
resource_exists "${timeout_realm_base}/realm-config/services/oauth-oidc"
resource_exists "${timeout_realm_base}/realm-config/agents/OAuth2Client/${RP_C_CLIENT_ID}"
resource_exists "${timeout_realm_base}/realm-config/agents/OAuth2Client/${RP_D_CLIENT_ID}"
resource_exists "${timeout_realm_base}/realm-config/agents/IdentityGatewayAgent/${IG_AGENT_ID}"

echo "Verifying ${TIMEOUT_REALM_NAME} SSO session type and test user"
curl "${curl_common[@]}" \
  -H "iPlanetDirectoryPro: ${token_id}" \
  -H "Accept-API-Version: resource=1.0, protocol=2.1" \
  "${timeout_realm_base}/realm-config/authentication" \
  | grep -o '"statelessSessionsEnabled":[a-z]*' || true
if [[ -n "${TIMEOUT_TEST_USER}" ]]; then
  if resource_exists "${timeout_realm_base}/users/${TIMEOUT_TEST_USER}" "4.0"; then
    echo "  test user ${TIMEOUT_TEST_USER}: present"
  else
    echo "  test user ${TIMEOUT_TEST_USER}: MISSING"
  fi
fi
