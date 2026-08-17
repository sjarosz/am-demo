#!/usr/bin/env bash
# QA smoketests for the cross-AM OIDC social login (jrsz.org <-> jrsz.net).
#
# Drives the SocialLogin journey end to end in both directions, simulating a
# browser (cookie jar, redirect following, AM JSON callback resume), then
# confirms a federated session on the consumer side.
#
# Usage:
#   scripts/smoke_social.sh                      # both directions (verbose hops)
#   scripts/smoke_social.sh org-consumer_com-op  # one direction
#   scripts/smoke_social.sh -q                   # summary only
#
# Env:
#   SMOKE_VERIFY=1   enforce TLS verification with the lab root CA
#                    (default is unverified; the lab CA omits keyUsage, which
#                    OpenSSL 3 / Python rejects even though curl accepts it)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
CA_CERT="${ROOT_DIR}/secrets/tls/ca/ca-bundle.pem"   # JRSZ root + ISRG Root X1 (written by scripts/le-cert.sh install)
[[ -f "${CA_CERT}" ]] || CA_CERT="${ROOT_DIR}/secrets/tls/ca/jrsz-root-ca.cert.pem"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

: "${AM_ADMIN_PASSWORD:?AM_ADMIN_PASSWORD is required (source .env)}"
: "${DEMO_USER_PASSWORD:?DEMO_USER_PASSWORD is required (source .env)}"

export ORG_AM_BASE_URL COM_AM_BASE_URL
export AM_REALM_PATH DEMO_USER_NAME DEMO_USER_PASSWORD AM_ADMIN_PASSWORD
export AM_ADMIN_USER="${AM_ADMIN_USER:-amadmin}"

if [[ -f "${CA_CERT}" ]]; then
  export SMOKE_CA_CERT="${CA_CERT}"
fi

exec python3 "${ROOT_DIR}/scripts/smoke_social.py" "$@"
