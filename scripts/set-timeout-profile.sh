#!/usr/bin/env bash
# Apply a session/token timeout profile to the app6 "timeout-test" realm and its
# OIDC RP clients (RP C / RP D). The meaningful permutation is which clock
# expires first; pick a profile and the script sets AM session idle/max plus the
# RP access/refresh/ID token lifetimes accordingly.
#
# Usage:
#   ./scripts/set-timeout-profile.sh <baseline|idle-first|max-first|app-first|token-first|race>
#
# Targets the jrsz.org stack by sourcing .env. To target jrsz.com, pre-set
# AM_URL / AM_ADMIN_PASSWORD / RP_C_CLIENT_ID / RP_D_CLIENT_ID in the environment
# (e.g. `set -a; . ./.env.com; set +a; ./scripts/set-timeout-profile.sh baseline`).
#
# Note: PingGateway warns that an AM sessionIdleRefresh.interval below one minute
# can adversely affect AM performance; keep AM_IDLE comfortably above that.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
CA_CERT="${CA_CERT:-${ROOT_DIR}/secrets/tls/ca/jrsz-root-ca.cert.pem}"

PROFILE="${1:-}"
if [[ -z "${PROFILE}" ]]; then
  echo "Usage: $0 <baseline|idle-first|max-first|app-first|token-first|race>" >&2
  exit 2
fi

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  set -a; source "${ENV_FILE}"; set +a
fi

AM_URL="${AM_URL:-https://am.jrsz.org:8443/am}"
: "${AM_ADMIN_PASSWORD:?AM_ADMIN_PASSWORD is required}"
TIMEOUT_REALM_PATH="${TIMEOUT_REALM_PATH:-realms/root/realms/timeout-test}"
RP_C_CLIENT_ID="${RP_C_CLIENT_ID:-rp-c-app}"
RP_D_CLIENT_ID="${RP_D_CLIENT_ID:-rp-d-app}"

# AM idle/max in MINUTES, token lifetimes in SECONDS.
case "${PROFILE}" in
  baseline)    AM_IDLE=6;  AM_MAX=20;  AT=300;  RT=1800;  ID=300 ;;
  idle-first)  AM_IDLE=2;  AM_MAX=20;  AT=3600; RT=86400; ID=3600 ;;
  max-first)   AM_IDLE=10; AM_MAX=4;   AT=3600; RT=86400; ID=3600 ;;
  app-first)   AM_IDLE=10; AM_MAX=30;  AT=3600; RT=86400; ID=3600 ;;
  token-first) AM_IDLE=30; AM_MAX=120; AT=60;   RT=180;   ID=60 ;;
  race)        AM_IDLE=2;  AM_MAX=20;  AT=120;  RT=120;   ID=120 ;;
  *)
    echo "Unknown profile: ${PROFILE}" >&2
    echo "Choose one of: baseline idle-first max-first app-first token-first race" >&2
    exit 2 ;;
esac

# Derive host:port from AM_URL for curl --resolve so the lab CA + hostname match.
am_authority="${AM_URL#*://}"; am_authority="${am_authority%%/*}"
am_host="${am_authority%%:*}"
am_port="${am_authority##*:}"
if [[ "${am_port}" == "${am_host}" ]]; then am_port=443; fi

curl_common=(--silent --show-error)
if [[ -f "${CA_CERT}" ]]; then
  curl_common+=(--cacert "${CA_CERT}" --resolve "${am_host}:${am_port}:127.0.0.1")
else
  curl_common+=(--insecure)
fi

work_dir="$(mktemp -d)"; trap 'rm -rf "${work_dir}"' EXIT

echo "Authenticating as amadmin to ${AM_URL}"
token_id="$(
  curl "${curl_common[@]}" \
    -H "X-OpenAM-Username: amadmin" \
    -H "X-OpenAM-Password: ${AM_ADMIN_PASSWORD}" \
    -H "Accept-API-Version: resource=2.1, protocol=1.0" \
    -H "Content-Type: application/json" \
    -X POST "${AM_URL}/json/realms/root/authenticate" \
  | sed -n 's/.*"tokenId":"\([^"]*\)".*/\1/p'
)"
if [[ -z "${token_id}" ]]; then
  echo "Failed to obtain AM admin token" >&2
  exit 1
fi

realm_base="${AM_URL}/json/${TIMEOUT_REALM_PATH}"

echo "Setting ${TIMEOUT_REALM_PATH} session service: idle=${AM_IDLE}m max=${AM_MAX}m"
printf '{"maxIdleTime":%d,"maxSessionTime":%d,"maxCachingTime":3}' "${AM_IDLE}" "${AM_MAX}" \
  > "${work_dir}/session.json"
curl "${curl_common[@]}" \
  -H "iPlanetDirectoryPro: ${token_id}" \
  -H "Accept-API-Version: resource=1.0, protocol=1.0" \
  -H "Content-Type: application/json" \
  -H "If-Match: *" \
  -X PUT --data-binary "@${work_dir}/session.json" \
  "${realm_base}/realm-config/services/session" >/dev/null

set_client_lifetimes() {
  local client_id="$1"
  local url="${realm_base}/realm-config/agents/OAuth2Client/${client_id}"
  echo "Setting ${client_id} token lifetimes: AT=${AT}s RT=${RT}s ID=${ID}s"
  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=1.0, protocol=1.0" \
    "${url}" > "${work_dir}/${client_id}.json"

  AT_TTL="${AT}" RT_TTL="${RT}" ID_TTL="${ID}" python3 - \
    "${work_dir}/${client_id}.json" "${work_dir}/${client_id}.put.json" <<'PY'
import json, os, sys
from pathlib import Path

obj = json.loads(Path(sys.argv[1]).read_text())
core = obj.setdefault("coreOAuth2ClientConfig", {})
core["accessTokenLifetime"] = {"inherited": False, "value": int(os.environ["AT_TTL"])}
core["refreshTokenLifetime"] = {"inherited": False, "value": int(os.environ["RT_TTL"])}
oidc = obj.setdefault("coreOpenIDClientConfig", {})
oidc["jwtTokenLifetime"] = {"inherited": False, "value": int(os.environ["ID_TTL"])}
# Drop server-managed read-only fields that AM rejects on PUT.
for key in ("_rev",):
    obj.pop(key, None)
Path(sys.argv[2]).write_text(json.dumps(obj))
PY

  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=1.0, protocol=1.0" \
    -H "Content-Type: application/json" \
    -H "If-Match: *" \
    -X PUT --data-binary "@${work_dir}/${client_id}.put.json" \
    "${url}" >/dev/null
}

set_client_lifetimes "${RP_C_CLIENT_ID}"
set_client_lifetimes "${RP_D_CLIENT_ID}"

cat <<EOF

Applied profile '${PROFILE}' to ${TIMEOUT_REALM_PATH}:
  AM idle (maxIdleTime)    = ${AM_IDLE} min
  AM max  (maxSessionTime) = ${AM_MAX} min
  Access token lifetime    = ${AT} s
  Refresh token lifetime   = ${RT} s
  ID token lifetime        = ${ID} s

Re-login (new AM session + fresh tokens) for the new values to take effect.
EOF
