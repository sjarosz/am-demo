#!/usr/bin/env bash
#
# /bravo OIDC -> horizon AIC JWT-bearer bootstrap.
#
# Provisions, idempotently and over REST, everything needed for a user to log
# into the local AM /bravo realm and receive an id_token signed by a dedicated
# RSA key whose public half is registered in the horizon AIC instance:
#
#   1. /bravo realm + a cloned OAuth2/OIDC provider (RS256 id_tokens)
#   2. a realm FileSystemSecretStore (PLAIN) holding the keystore passwords
#   3. a realm KeyStoreSecretStore pointing at the dedicated JCEKS keystore
#   4. a mapping am.services.oauth2.oidc.signing.RSA -> the new key alias, so
#      /bravo id_tokens are signed by the new cert (NOT AM's default key, and
#      WITHOUT affecting /alpha which keeps the global default key)
#   5. a dedicated public PKCE OIDC client
#   6. the demo user in /bravo
#
# The signing keystore (secrets/oidc-signing/bravo-oidc.jceks) is produced by
# scripts/generate-oidc-signing-key.sh and mounted into this container at
# /run/secrets/oidc. It is copied into the shared am-home volume so the AM
# container can read it. Public-key export for horizon is a separate step:
# scripts/export_horizon_jwk.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AM_BASE_URL="${AM_SERVER_URL:-${AM_URL:-}}"
AM_ADMIN_PWD="${AM_ADMIN_PWD:-${AM_ADMIN_PASSWORD:-}}"
AM_CFG_DIR="${AM_CFG_DIR:-/home/forgerock/openam}"

BRAVO_REALM="${HORIZON_OIDC_REALM:-bravo}"
CLIENT_ID="${HORIZON_OIDC_CLIENT_ID:-horizon-oidc-app}"
REDIRECT_URI="${HORIZON_OIDC_REDIRECT_URI:-https://app6.jrsz.org/callback}"

# Dedicated signing material (see scripts/generate-oidc-signing-key.sh).
OIDC_KEYSTORE_SRC="${OIDC_KEYSTORE_SRC:-/run/secrets/oidc/bravo-oidc.jceks}"
OIDC_KEYSTORE_DEST="${AM_CFG_DIR}/security/keystores/bravo-oidc.jceks"
OIDC_SECRET_DIR="${AM_CFG_DIR}/security/secrets/bravo-oidc"
OIDC_SIGNING_ALIAS="${OIDC_SIGNING_ALIAS:-bravo-oidc-rsa}"
OIDC_KEYSTORE_PASSWORD="${OIDC_KEYSTORE_PASSWORD:-changeit}"
STORE_PASS_LABEL="bravo.oidc.keystore.storepass"
ENTRY_PASS_LABEL="bravo.oidc.keystore.entrypass"
KEYSTORE_STORE_ID="${KEYSTORE_STORE_ID:-bravo-oidc}"
PASSWORD_STORE_ID="${PASSWORD_STORE_ID:-bravo-oidc-passwords}"

PROVIDER_TEMPLATE="${PROVIDER_TEMPLATE:-/opt/amster-config/oauth-oidc.service.json}"
CLIENT_TEMPLATE="${SCRIPT_DIR}/entities/horizon-oidc-app.oauth2.app.json"

DEMO_USER_NAME="${DEMO_USER_NAME:-demo-user}"
DEMO_USER_PASSWORD="${DEMO_USER_PASSWORD:-}"

: "${AM_BASE_URL:?AM_SERVER_URL or AM_URL is required}"
: "${AM_ADMIN_PWD:?AM_ADMIN_PWD or AM_ADMIN_PASSWORD is required}"

require_bin() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required binary: $1" >&2; exit 1; }
}
require_bin curl
require_bin python3

work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

auth_body="${work_dir}/authenticate.json"
provider_body="${work_dir}/bravo-oauth-oidc.json"
client_body="${work_dir}/bravo-client.json"
fs_store_body="${work_dir}/fs-store.json"
ks_store_body="${work_dir}/ks-store.json"
mapping_body="${work_dir}/mapping.json"
user_body="${work_dir}/demo-user.json"

curl_common=(--silent --show-error --insecure)

# --- session -----------------------------------------------------------------
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
  local url="$1"; shift
  curl "${curl_common[@]}" \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=1.0, protocol=1.0" \
    "$@" "${url}"
}

resource_exists() {
  local url="$1" version="${2:-1.0}" status
  status="$(curl "${curl_common[@]}" -o /dev/null -w '%{http_code}' \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=${version}, protocol=1.0" \
    "${url}")"
  [[ "${status}" == "200" ]]
}

# PUT a config resource (create or replace). Fails loudly with the response body.
put_resource() {
  local url="$1" body_file="$2" version="${3:-1.0}" match_header="If-None-Match: *"
  if resource_exists "${url}" "${version}"; then
    match_header="If-Match: *"
  fi
  local resp="${work_dir}/put-resp.json" status
  status="$(curl "${curl_common[@]}" -o "${resp}" -w '%{http_code}' \
    -H "iPlanetDirectoryPro: ${token_id}" \
    -H "Accept-API-Version: resource=${version}, protocol=1.0" \
    -H "Content-Type: application/json" \
    -H "${match_header}" \
    -X PUT \
    --data-binary "@${body_file}" \
    "${url}")"
  if [[ "${status}" != "200" && "${status}" != "201" ]]; then
    echo "PUT ${url} failed (HTTP ${status})" >&2
    cat "${resp}" >&2
    exit 1
  fi
}

realm_exists() {
  local realm_name="$1" response
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

# --- render bodies -----------------------------------------------------------
python3 - "$PROVIDER_TEMPLATE" "$provider_body" <<'PY'
import json, sys
from pathlib import Path
source = json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(json.dumps(source["service"]["oauth-oidc"]))
PY

python3 - "$CLIENT_TEMPLATE" "$client_body" "$CLIENT_ID" "$REDIRECT_URI" <<'PY'
import json, sys
from pathlib import Path
raw = Path(sys.argv[1]).read_text()
raw = raw.replace("@@CLIENT_ID@@", sys.argv[3]).replace("@@REDIRECT_URI@@", sys.argv[4])
source = json.loads(raw)
Path(sys.argv[2]).write_text(json.dumps(source["application"][sys.argv[3]]))
PY

cat > "${fs_store_body}" <<EOF
{"format":"PLAIN","directory":"${OIDC_SECRET_DIR}"}
EOF

cat > "${ks_store_body}" <<EOF
{"file":"${OIDC_KEYSTORE_DEST}","storetype":"JCEKS","providerName":"SunJCE","storePassword":"${STORE_PASS_LABEL}","keyEntryPassword":"${ENTRY_PASS_LABEL}","leaseExpiryDuration":5}
EOF

cat > "${mapping_body}" <<EOF
{"secretId":"am.services.oauth2.oidc.signing.RSA","aliases":["${OIDC_SIGNING_ALIAS}"]}
EOF

# --- place keystore + password secrets into the shared am-home volume --------
if [[ ! -f "${OIDC_KEYSTORE_SRC}" ]]; then
  echo "Signing keystore not found at ${OIDC_KEYSTORE_SRC}." >&2
  echo "Run scripts/generate-oidc-signing-key.sh and ensure secrets/oidc-signing is mounted." >&2
  exit 1
fi
echo "Installing signing keystore -> ${OIDC_KEYSTORE_DEST}"
mkdir -p "$(dirname "${OIDC_KEYSTORE_DEST}")"
cp "${OIDC_KEYSTORE_SRC}" "${OIDC_KEYSTORE_DEST}"

echo "Writing PLAIN keystore-password secrets -> ${OIDC_SECRET_DIR}"
mkdir -p "${OIDC_SECRET_DIR}"
printf "%s" "${OIDC_KEYSTORE_PASSWORD}" > "${OIDC_SECRET_DIR}/${STORE_PASS_LABEL}"
printf "%s" "${OIDC_KEYSTORE_PASSWORD}" > "${OIDC_SECRET_DIR}/${ENTRY_PASS_LABEL}"
chmod 600 "${OIDC_SECRET_DIR}/${STORE_PASS_LABEL}" "${OIDC_SECRET_DIR}/${ENTRY_PASS_LABEL}" || true

# --- provision ---------------------------------------------------------------
ensure_realm "${BRAVO_REALM}"

bravo_base="${AM_BASE_URL}/json/realms/root/realms/${BRAVO_REALM}"

echo "Applying ${BRAVO_REALM} OAuth2 provider"
put_resource "${bravo_base}/realm-config/services/oauth-oidc" "${provider_body}"

echo "Applying ${BRAVO_REALM} FileSystemSecretStore (${PASSWORD_STORE_ID}, PLAIN)"
put_resource "${bravo_base}/realm-config/secrets/stores/FileSystemSecretStore/${PASSWORD_STORE_ID}" "${fs_store_body}"

echo "Applying ${BRAVO_REALM} KeyStoreSecretStore (${KEYSTORE_STORE_ID})"
put_resource "${bravo_base}/realm-config/secrets/stores/KeyStoreSecretStore/${KEYSTORE_STORE_ID}" "${ks_store_body}"

echo "Applying ${BRAVO_REALM} OIDC RSA signing mapping -> ${OIDC_SIGNING_ALIAS}"
put_resource "${bravo_base}/realm-config/secrets/stores/KeyStoreSecretStore/${KEYSTORE_STORE_ID}/mappings/am.services.oauth2.oidc.signing.RSA" "${mapping_body}"

echo "Applying ${BRAVO_REALM} OIDC client ${CLIENT_ID} (public PKCE)"
put_resource "${bravo_base}/realm-config/agents/OAuth2Client/${CLIENT_ID}" "${client_body}"

# --- demo user in /bravo -----------------------------------------------------
if [[ -n "${DEMO_USER_PASSWORD}" ]]; then
  user_url="${bravo_base}/users/${DEMO_USER_NAME}"
  if resource_exists "${user_url}" "2.0"; then
    echo "Demo user ${DEMO_USER_NAME} already exists in ${BRAVO_REALM}; syncing password"
    curl "${curl_common[@]}" -o /dev/null \
      -H "iPlanetDirectoryPro: ${token_id}" \
      -H "Accept-API-Version: resource=4.0, protocol=2.1" \
      -H "Content-Type: application/json" \
      -X PATCH \
      --data "[{\"operation\":\"replace\",\"field\":\"/userPassword\",\"value\":\"${DEMO_USER_PASSWORD}\"}]" \
      "${user_url}"
  else
    echo "Creating demo user ${DEMO_USER_NAME} in ${BRAVO_REALM}"
    python3 - "$user_body" "$DEMO_USER_NAME" "$DEMO_USER_PASSWORD" <<'PY'
import json, sys
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
    put_resource "${user_url}" "${user_body}" "2.0"
  fi
else
  echo "DEMO_USER_PASSWORD not set; skipping ${BRAVO_REALM} demo user"
fi

# --- verify ------------------------------------------------------------------
echo "Verifying ${BRAVO_REALM} provisioning"
resource_exists "${bravo_base}/realm-config/services/oauth-oidc" \
  && echo "  provider: present" || echo "  provider: MISSING"
resource_exists "${bravo_base}/realm-config/secrets/stores/KeyStoreSecretStore/${KEYSTORE_STORE_ID}" \
  && echo "  keystore secret store: present" || echo "  keystore secret store: MISSING"
resource_exists "${bravo_base}/realm-config/agents/OAuth2Client/${CLIENT_ID}" \
  && echo "  client ${CLIENT_ID}: present" || echo "  client ${CLIENT_ID}: MISSING"

echo "oidc-horizon bootstrap complete"
echo "Next: scripts/export_horizon_jwk.sh  (publish the public JWK for horizon)"
