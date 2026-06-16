#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AM_BASE_URL="${AM_SERVER_URL:-${AM_URL:-}}"
AM_ADMIN_PWD="${AM_ADMIN_PWD:-${AM_ADMIN_PASSWORD:-}}"
LOGIN_WIDGET_BASE_URL="${LOGIN_WIDGET_BASE_URL:-https://app5.jrsz.org}"
LOGIN_WIDGET_CLIENT_ID="${LOGIN_WIDGET_CLIENT_ID:-sdkPublicClient}"
LOGIN_WIDGET_JOURNEY="${LOGIN_WIDGET_JOURNEY:-sdkUsernamePasswordJourney}"
CORS_CONFIG_NAME="${LOGIN_WIDGET_CORS_CONFIG_NAME:-LoginWidget}"
CLIENT_TEMPLATE="${SCRIPT_DIR}/../oauth2-demo/entities/demo-pkce-app.oauth2.app.json"

PAGE_NODE_ID="f47ac10b-58cc-4372-a567-0e02b2c3d479"
USERNAME_NODE_ID="6ba7b810-9dad-11d1-80b4-00c04fd430c8"
PASSWORD_NODE_ID="6ba7b811-9dad-11d1-80b4-00c04fd430c8"
DATASTORE_NODE_ID="6ba7b812-9dad-11d1-80b4-00c04fd430c8"
SUCCESS_NODE_ID="70e691a5-1e33-4ac3-a356-e7b6d60d92e0"
FAILURE_NODE_ID="e301438c-0bd0-429c-ab0c-66126501069a"

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
client_body="${work_dir}/sdk-public-client.json"
tree_body="${work_dir}/sdk-username-password-journey.json"
cors_body="${work_dir}/login-widget-cors.json"

python3 - "$CLIENT_TEMPLATE" "$client_body" "$LOGIN_WIDGET_CLIENT_ID" "$LOGIN_WIDGET_BASE_URL" "$LOGIN_WIDGET_JOURNEY" <<'PY'
import json
import sys
from pathlib import Path

template_path, out_path, client_id, base_url, journey = sys.argv[1:6]
base_url = base_url.rstrip("/")
raw = Path(template_path).read_text()
raw = raw.replace("@@CLIENT_ID@@", client_id).replace("@@APP4_REDIRECT_URI@@", f"{base_url}/callback.html")
source = json.loads(raw)
client = source["application"][client_id]
client["coreOAuth2ClientConfig"]["clientName"]["value"] = ["Login Widget Public Client"]
client["coreOAuth2ClientConfig"]["redirectionUris"]["value"] = [
    f"{base_url}/callback.html",
    f"{base_url}/",
]
client["coreOAuth2ClientConfig"]["scopes"]["value"] = ["openid", "profile", "email", "address"]
client["advancedOAuth2ClientConfig"]["treeName"]["value"] = "[Empty]"
client["advancedOAuth2ClientConfig"]["isConsentImplied"]["value"] = True
client["overrideOAuth2ClientConfig"]["clientsCanSkipConsent"] = True
Path(out_path).write_text(json.dumps(client))
PY

python3 - "$tree_body" "$LOGIN_WIDGET_JOURNEY" "$PAGE_NODE_ID" "$DATASTORE_NODE_ID" "$SUCCESS_NODE_ID" "$FAILURE_NODE_ID" <<'PY'
import json
import sys
from pathlib import Path

out_path, journey, page_id, datastore_id, success_id, failure_id = sys.argv[1:7]
Path(out_path).write_text(json.dumps({
    "entryNodeId": page_id,
    "enabled": True,
    "nodes": {
        page_id: {
            "displayName": "Page Node",
            "nodeType": "PageNode",
            "version": "1.0",
            "connections": {"outcome": datastore_id},
        },
        datastore_id: {
            "displayName": "Data Store Decision",
            "nodeType": "DataStoreDecisionNode",
            "version": "1.0",
            "connections": {
                "true": success_id,
                "false": failure_id,
            },
        },
    },
}))
PY

python3 - "$cors_body" "$LOGIN_WIDGET_BASE_URL" "$CORS_CONFIG_NAME" <<'PY'
import json
import sys
from pathlib import Path

base_url = sys.argv[2].rstrip("/")
Path(sys.argv[1]).write_text(json.dumps({
    "_id": sys.argv[3],
    "enabled": True,
    "acceptedOrigins": [base_url],
    "acceptedMethods": ["GET", "POST"],
    "acceptedHeaders": [
        "accept-api-version",
        "x-requested-with",
        "content-type",
        "authorization",
        "if-match",
        "x-requested-platform",
        "iPlanetDirectoryPro",
    ],
    "maxAge": 600,
    "allowCredentials": True,
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
  local resource_version="${3:-1.0}"
  local match_header="If-None-Match: *"

  if resource_exists "${url}" "${resource_version}"; then
    match_header="If-Match: *"
  fi

  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=${resource_version}, protocol=1.0" \
    -H "Content-Type: application/json" \
    -H "${match_header}" \
    -X PUT \
    --data-binary "@${body_file}" \
    "${url}" \
    >/dev/null
}

ensure_auth_node() {
  local node_type="$1"
  local node_id="$2"
  local body="$3"
  upsert_resource \
    "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/authentication/authenticationtrees/nodes/${node_type}/${node_id}" \
    <(printf '%s' "${body}") \
    "1.0"
}

echo "Applying Login Widget authentication nodes"
ensure_auth_node "UsernameCollectorNode" "${USERNAME_NODE_ID}" \
  '{"_id":"'"${USERNAME_NODE_ID}"'","_type":{"_id":"UsernameCollectorNode","name":"Username Collector"}}'
ensure_auth_node "PasswordCollectorNode" "${PASSWORD_NODE_ID}" \
  '{"_id":"'"${PASSWORD_NODE_ID}"'","_type":{"_id":"PasswordCollectorNode","name":"Password Collector"}}'
ensure_auth_node "PageNode" "${PAGE_NODE_ID}" \
  '{"_id":"'"${PAGE_NODE_ID}"'","_type":{"_id":"PageNode","name":"Page Node"},"nodes":[{"_id":"'"${USERNAME_NODE_ID}"'","nodeType":"UsernameCollectorNode","displayName":"Username Collector"},{"_id":"'"${PASSWORD_NODE_ID}"'","nodeType":"PasswordCollectorNode","displayName":"Password Collector"}],"stage":"UsernamePassword","pageHeader":{},"pageDescription":{}}'
ensure_auth_node "DataStoreDecisionNode" "${DATASTORE_NODE_ID}" \
  '{"_id":"'"${DATASTORE_NODE_ID}"'","_type":{"_id":"DataStoreDecisionNode","name":"Data Store Decision"}}'

echo "Applying Login Widget journey ${LOGIN_WIDGET_JOURNEY}"
upsert_resource \
  "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/authentication/authenticationtrees/trees/${LOGIN_WIDGET_JOURNEY}" \
  "${tree_body}"

echo "Applying Login Widget OAuth2 client ${LOGIN_WIDGET_CLIENT_ID}"
upsert_resource \
  "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/agents/OAuth2Client/${LOGIN_WIDGET_CLIENT_ID}" \
  "${client_body}"

echo "Applying Login Widget CORS configuration ${CORS_CONFIG_NAME}"
upsert_resource \
  "${AM_BASE_URL}/json/global-config/services/CorsService/configuration/${CORS_CONFIG_NAME}" \
  "${cors_body}"

echo "Verifying Login Widget AM configuration"
resource_exists "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/authentication/authenticationtrees/trees/${LOGIN_WIDGET_JOURNEY}"
resource_exists "${AM_BASE_URL}/json/realms/root/realms/alpha/realm-config/agents/OAuth2Client/${LOGIN_WIDGET_CLIENT_ID}"
resource_exists "${AM_BASE_URL}/json/global-config/services/CorsService/configuration/${CORS_CONFIG_NAME}"

echo "Login Widget AM bootstrap complete"
