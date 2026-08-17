#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
CA_CERT="${ROOT_DIR}/secrets/tls/ca/ca-bundle.pem"   # JRSZ root + ISRG Root X1 (written by scripts/le-cert.sh install)
[[ -f "${CA_CERT}" ]] || CA_CERT="${ROOT_DIR}/secrets/tls/ca/jrsz-root-ca.cert.pem"
APP_BASE_URL="${APP_BASE_URL:-https://app4.jrsz.org}"
APP_LOGIN_URL="${APP_BASE_URL}/login"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

: "${AM_ADMIN_PASSWORD:?AM_ADMIN_PASSWORD is required}"
: "${DEMO_USER_NAME:?DEMO_USER_NAME is required}"
: "${DEMO_USER_PASSWORD:?DEMO_USER_PASSWORD is required}"
AM_REALM_PATH="${AM_REALM_PATH:-realms/root/realms/alpha}"

work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

cookie_jar="${work_dir}/cookies.txt"
login_headers="${work_dir}/login.headers"
auth_headers="${work_dir}/auth.headers"
app_home="${work_dir}/app-home.html"
auth_body="${work_dir}/auth.json"

curl_common=(
  --silent
  --show-error
  --fail
  --cacert "${CA_CERT}"
  --resolve "am.jrsz.org:8443:127.0.0.1"
  --resolve "app4.jrsz.org:443:127.0.0.1"
  -c "${cookie_jar}"
  -b "${cookie_jar}"
)

echo "Starting app4 PKCE login flow"
curl "${curl_common[@]}" \
  -D "${login_headers}" \
  -o /dev/null \
  "${APP_LOGIN_URL}"

authorize_url="$(sed -n 's/^location: \(.*\)\r$/\1/ip' "${login_headers}" | tail -n 1)"
if [[ -z "${authorize_url}" ]]; then
  echo "app4 /login did not return an authorize redirect" >&2
  cat "${login_headers}" >&2
  exit 1
fi

echo "Authenticating as ${DEMO_USER_NAME} to obtain AM SSO session"
curl "${curl_common[@]}" \
  -D "${auth_headers}" \
  -H "X-OpenAM-Username: ${DEMO_USER_NAME}" \
  -H "X-OpenAM-Password: ${DEMO_USER_PASSWORD}" \
  -H "Accept-API-Version: resource=2.1, protocol=1.0" \
  -H "Content-Type: application/json" \
  -X POST \
  "${AM_URL}/json/${AM_REALM_PATH}/authenticate" \
  > "${auth_body}"

if ! rg -q 'tokenId' "${auth_body}"; then
  echo "AM authentication did not return tokenId" >&2
  cat "${auth_body}" >&2
  exit 1
fi

echo "Driving authorize redirect through AM back to app4 callback"
curl "${curl_common[@]}" \
  --location \
  -o "${app_home}" \
  "${authorize_url}"

assert_page_contains() {
  local pattern="$1"
  local description="$2"
  if ! rg -q "${pattern}" "${app_home}"; then
    echo "Final app4 page missing ${description}" >&2
    sed -n '1,220p' "${app_home}" >&2
    exit 1
  fi
}

assert_page_contains 'Hello World via Authorization Code \+ PKCE' 'success title'
assert_page_contains '&quot;access_token&quot;' 'access token'
assert_page_contains '&quot;id_token&quot;' 'id token'
assert_page_contains '&quot;scope&quot;' 'token scope'

echo "app4 OIDC flow completed successfully"
