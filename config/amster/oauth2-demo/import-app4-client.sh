#!/usr/bin/env bash
set -euo pipefail

: "${AM_URL:?AM_URL is required}"
AM_ADMIN_PWD="${AM_ADMIN_PWD:-${AM_ADMIN_PASSWORD:-}}"
: "${AM_ADMIN_PWD:?AM_ADMIN_PWD or AM_ADMIN_PASSWORD is required}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTITY_DIR="${SCRIPT_DIR}/entities"
CLIENT_ID="${CLIENT_ID:-${APP4_CLIENT_ID:-demo-pkce-app}}"
CLIENT_TEMPLATE="${ENTITY_DIR}/demo-pkce-app.oauth2.app.json"
REALM_PATH="${DEMO_REALM_PATH:-/alpha}"
CA_CERT="${CA_CERT:-${SCRIPT_DIR}/../../../secrets/tls/ca/jrsz-root-ca.cert.pem}"
APP4_BASE_URL="${APP4_BASE_URL:-https://app4.jrsz.org}"
APP4_REDIRECT_URI="${APP4_REDIRECT_URI:-${APP4_BASE_URL}/callback}"

if [[ ! -f "${CLIENT_TEMPLATE}" ]]; then
  echo "Missing client definition template: ${CLIENT_TEMPLATE}" >&2
  exit 1
fi

if [[ ! -f "${CA_CERT}" ]]; then
  echo "Missing CA certificate: ${CA_CERT}" >&2
  exit 1
fi

export NODE_EXTRA_CA_CERTS="${CA_CERT}"

work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

client_file="${work_dir}/${CLIENT_ID}.oauth2.app.json"
sed \
  -e "s|@@CLIENT_ID@@|${CLIENT_ID}|g" \
  -e "s|@@APP4_REDIRECT_URI@@|${APP4_REDIRECT_URI}|g" \
  "${CLIENT_TEMPLATE}" \
  > "${client_file}"

frodo oauth client import \
  -m classic \
  --no-cache \
  -f "${client_file}" \
  -i "${CLIENT_ID}" \
  "${AM_URL}" \
  "${REALM_PATH}" \
  amadmin \
  "${AM_ADMIN_PWD}"

echo "Imported OAuth2 client ${CLIENT_ID} into realm ${REALM_PATH}"
