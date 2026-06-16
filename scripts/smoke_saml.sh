#!/usr/bin/env bash
# QA smoketests for the cross-domain SAML flows (jrsz.org <-> jrsz.com).
#
# Drives all four IDP/SP-init permutations end to end over the SAML HTTP-POST
# binding, simulating a browser (cookie jar, redirect following, auto-submit of
# the self-posting SAML forms), then confirms a federated demo-user session on
# the SP side.
#
# Usage:
#   scripts/smoke_saml.sh                 # run all four flows (verbose hops)
#   scripts/smoke_saml.sh idp             # only IDP-init flows
#   scripts/smoke_saml.sh sp              # only SP-init flows
#   scripts/smoke_saml.sh org-idp_com-sp_sp-init   # one specific flow
#   scripts/smoke_saml.sh -q              # summary only
#
# Env:
#   SMOKE_VERIFY=1   enforce TLS verification with the lab root CA
#                    (default is unverified; the lab CA omits keyUsage, which
#                    OpenSSL 3 / Python rejects even though curl accepts it)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
CA_CERT="${ROOT_DIR}/secrets/tls/ca/jrsz-root-ca.cert.pem"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

: "${AM_ADMIN_PASSWORD:?AM_ADMIN_PASSWORD is required (source .env)}"
: "${DEMO_USER_PASSWORD:?DEMO_USER_PASSWORD is required (source .env)}"

export ORG_AM_BASE_URL COM_AM_BASE_URL ORG_ENTITY_ID COM_ENTITY_ID
export ORG_IDP_METAALIAS ORG_SP_METAALIAS COM_IDP_METAALIAS COM_SP_METAALIAS
export AM_REALM_PATH DEMO_USER_NAME DEMO_USER_PASSWORD AM_ADMIN_PASSWORD APP7_BASE_URL
export AM_ADMIN_USER="${AM_ADMIN_USER:-amadmin}"

if [[ -f "${CA_CERT}" ]]; then
  export SMOKE_CA_CERT="${CA_CERT}"
fi

exec python3 "${ROOT_DIR}/scripts/smoke_saml.py" "$@"
