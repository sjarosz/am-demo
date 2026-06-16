#!/usr/bin/env bash
set -euo pipefail

AM_BASE_URL="${AM_SERVER_URL:-${AM_URL:-}}"
AM_ADMIN_PWD="${AM_ADMIN_PWD:-${AM_ADMIN_PASSWORD:-}}"
DEMO_USER_NAME="${DEMO_USER_NAME:-demo-user}"
DEMO_USER_PASSWORD="${DEMO_USER_PASSWORD:-}"

: "${AM_BASE_URL:?AM_SERVER_URL or AM_URL is required}"
: "${AM_ADMIN_PWD:?AM_ADMIN_PWD or AM_ADMIN_PASSWORD is required}"
: "${DEMO_USER_PASSWORD:?DEMO_USER_PASSWORD is required}"

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
user_body="${work_dir}/demo-user.json"

python3 - "$user_body" "$DEMO_USER_NAME" "$DEMO_USER_PASSWORD" <<'PY'
import json
import sys
from pathlib import Path

username, password = sys.argv[2], sys.argv[3]
Path(sys.argv[1]).write_text(json.dumps({
    "userName": username,
    "givenName": "Demo",
    "sn": "User",
    "mail": f"{username}@jrsz.org",
    "userPassword": password,
    "inetUserStatus": "Active",
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

user_url="${AM_BASE_URL}/json/realms/root/realms/alpha/users/${DEMO_USER_NAME}"
status="$(
  curl "${curl_common[@]}" \
    -o /dev/null \
    -w '%{http_code}' \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=2.0, protocol=1.0" \
    "${user_url}"
)"

if [[ "${status}" == "200" ]]; then
  echo "Demo user ${DEMO_USER_NAME} already exists in /alpha; syncing password"
  sync_status="$(
    curl "${curl_common[@]}" \
      -o /dev/null \
      -w '%{http_code}' \
      -H "iPlanetDirectoryPro: ${token_id}" \
      -H "Accept-API-Version: resource=4.0, protocol=2.1" \
      -H "Content-Type: application/json" \
      -X PATCH \
      --data "[{\"operation\":\"replace\",\"field\":\"/userPassword\",\"value\":\"${DEMO_USER_PASSWORD}\"}]" \
      "${user_url}"
  )"
  if [[ "${sync_status}" != "200" ]]; then
    echo "Failed to sync demo user password (HTTP ${sync_status})" >&2
    exit 1
  fi
  echo "Demo user password sync complete"
  exit 0
fi

echo "Creating demo user ${DEMO_USER_NAME} in /alpha"
create_status="$(
  curl "${curl_common[@]}" \
    -o "${work_dir}/create-resp.json" \
    -w '%{http_code}' \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=2.0, protocol=1.0" \
    -H "Content-Type: application/json" \
    -H "If-None-Match: *" \
    -X PUT \
    --data-binary "@${user_body}" \
    "${user_url}"
)"

if [[ "${create_status}" != "200" && "${create_status}" != "201" ]]; then
  echo "Failed to create demo user ${DEMO_USER_NAME} (HTTP ${create_status})" >&2
  cat "${work_dir}/create-resp.json" >&2
  exit 1
fi

echo "Demo user bootstrap complete"
