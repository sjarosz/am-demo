#!/usr/bin/env bash
# Idempotently repair the cross-domain SAML federation config (jrsz.org <-> jrsz.com)
# so the four browser POST-SSO flows work end to end. See the docstring in
# scripts/repair_saml_federation.py for the exact settings this corrects.
#
# Run this after (re)provisioning the SAML entities, then verify with:
#   scripts/smoke_saml.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

: "${AM_ADMIN_PASSWORD:?AM_ADMIN_PASSWORD is required (source .env)}"
: "${DEMO_USER_PASSWORD:?DEMO_USER_PASSWORD is required (source .env)}"

export AM_ADMIN_PASSWORD DEMO_USER_PASSWORD DEMO_USER_NAME AM_REALM_PATH
export ORG_AM_BASE_URL COM_AM_BASE_URL ORG_ENTITY_ID COM_ENTITY_ID APP7_BASE_URL
export AM_ADMIN_USER="${AM_ADMIN_USER:-amadmin}"

exec python3 "${ROOT_DIR}/scripts/repair_saml_federation.py" "$@"
