#!/usr/bin/env bash
# Provision the cross-domain SAML 2.0 federation (jrsz.org <-> jrsz.net) from the
# canon artifacts in this directory. Invoked by the amster bootstrap container
# (docker/amster/docker-entrypoint.sh) once per stack; idempotent.
#
# The side (org/com) is auto-detected from AM_COOKIE_DOMAIN, and the script
# targets the local AM via AM_SERVER_URL. See provision.py for details.
set -euo pipefail

AM_BASE_URL="${AM_SERVER_URL:-${AM_URL:-}}"
AM_ADMIN_PWD="${AM_ADMIN_PWD:-${AM_ADMIN_PASSWORD:-}}"

: "${AM_BASE_URL:?AM_SERVER_URL or AM_URL is required}"
: "${AM_ADMIN_PWD:?AM_ADMIN_PWD or AM_ADMIN_PASSWORD is required}"

command -v python3 >/dev/null 2>&1 || { echo "Missing required binary: python3" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export AM_SERVER_URL="${AM_BASE_URL}"
export AM_ADMIN_PASSWORD="${AM_ADMIN_PWD}"

echo "Provisioning SAML federation (${AM_BASE_URL})"
python3 "${SCRIPT_DIR}/provision.py"
