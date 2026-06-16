#!/usr/bin/env bash
# Re-export the live SAML federation config from both AM stacks into the canon
# artifacts under config/amster/saml/ (hosted JSON + standard metadata XML + COT).
#
# Use this after intentionally changing the federation config in the AM console
# and wanting that state to become the new source of truth. Re-run
# scripts/smoke_saml.sh and commit the updated artifacts afterwards.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

: "${AM_ADMIN_PASSWORD:?AM_ADMIN_PASSWORD is required (source .env)}"

export AM_ADMIN_PASSWORD
export ORG_AM_BASE_URL COM_AM_BASE_URL ORG_ENTITY_ID COM_ENTITY_ID
export AM_ADMIN_USER="${AM_ADMIN_USER:-amadmin}"
export SAML_CANON_DIR="${ROOT_DIR}/config/amster/saml"

exec python3 "${ROOT_DIR}/scripts/export_saml_canon.py" "$@"
