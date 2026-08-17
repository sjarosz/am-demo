#!/usr/bin/env bash
# /bravo -> bonaire05 (PingOne AIC) RFC 7523 JWT-bearer IdP bootstrap for the jrsz.net stack.
#
# 1. installs the dedicated signing keystore (secrets/oidc-signing-net/bravo-oidc.jceks,
#    mounted at /run/secrets/oidc) + PLAIN password secrets into the shared am-home volume
# 2. runs provision.py (realm, provider, script, secret stores/mappings, portal client, users)
# See provision.py header and docs/bonaire05-jwt-bearer.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AM_CFG_DIR="${AM_CFG_DIR:-/home/forgerock/openam}"
OIDC_KEYSTORE_SRC="${OIDC_KEYSTORE_SRC:-/run/secrets/oidc/bravo-oidc.jceks}"
OIDC_KEYSTORE_DEST="${AM_CFG_DIR}/security/keystores/bravo-oidc.jceks"
OIDC_SECRET_DIR="${AM_CFG_DIR}/security/secrets/bravo-oidc"
OIDC_KEYSTORE_PASSWORD="${OIDC_KEYSTORE_PASSWORD:-changeit}"

command -v python3 >/dev/null 2>&1 || { echo "Missing required binary: python3" >&2; exit 1; }
: "${AM_SERVER_URL:?AM_SERVER_URL is required}"
: "${AM_ADMIN_PASSWORD:?AM_ADMIN_PASSWORD is required}"

if [[ ! -f "${OIDC_KEYSTORE_SRC}" ]]; then
  echo "Signing keystore not found at ${OIDC_KEYSTORE_SRC}." >&2
  echo "Run scripts/generate-tls.sh (or generate-oidc-signing-key.sh with OIDC_DIR=secrets/oidc-signing-net)" >&2
  echo "and make sure compose mounts secrets/oidc-signing-net at /run/secrets/oidc for this bootstrap." >&2
  exit 1
fi
echo "Installing signing keystore -> ${OIDC_KEYSTORE_DEST}"
mkdir -p "$(dirname "${OIDC_KEYSTORE_DEST}")" "${OIDC_SECRET_DIR}"
cp "${OIDC_KEYSTORE_SRC}" "${OIDC_KEYSTORE_DEST}"
printf "%s" "${OIDC_KEYSTORE_PASSWORD}" > "${OIDC_SECRET_DIR}/bravo.oidc.keystore.storepass"
printf "%s" "${OIDC_KEYSTORE_PASSWORD}" > "${OIDC_SECRET_DIR}/bravo.oidc.keystore.entrypass"
chmod 600 "${OIDC_SECRET_DIR}"/bravo.oidc.keystore.* || true

echo "Provisioning /bravo as JWT-bearer IdP for bonaire05 (${AM_SERVER_URL})"
python3 "${SCRIPT_DIR}/provision.py"
