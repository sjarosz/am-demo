#!/usr/bin/env bash
# Let's Encrypt certificate for the jrsz.org stack via Cloudflare DNS-01.
#
# Issues one wildcard cert (jrsz.org + *.jrsz.org) with certbot running in Docker,
# converts it into the formats AM (PKCS12) and PingGateway (PEM key+chain bundle)
# consume, and adds ISRG Root X1 to the shared lab truststore so IG, amster and the
# .com AM keep trusting am.jrsz.org after the switch.
#
# Usage:  scripts/le-cert.sh <command> [--domain <zone>] [options]
#   issue [--dry-run|--staging] [--force]   obtain / re-obtain the certificate
#   install                                 build am.p12 + gateway PEM, update truststore + ca-bundle
#   renew [--force]                         certbot renew; install + restart the stack's am/gateway if changed
#   status                                  show installed cert(s) + compose mount sources
#
# Domains: --domain (or LE_DOMAIN) selects the zone; default jrsz.org. Known stacks:
#   jrsz.org -> org stack (services am, gateway;         .env AM_TLS_DIR / GATEWAY_TLS_DIR)
#   jrsz.net -> com stack (services am-com, gateway-com; .env AM_COM_TLS_DIR / AM_COM_KEYSTORE_FILE / GATEWAY_COM_TLS_DIR)
#   other    -> set LE_SERVICES="<compose services to restart on renew>"
#
# Inputs:
#   secrets/cloudflare/<domain>.ini     dns_cloudflare_api_token = <token>   (chmod 600, gitignored)
#   .env                                LE_EMAIL, AM_KEYSTORE_PASSWORD, TRUSTSTORE_PASSWORD
# Outputs (all gitignored under secrets/):
#   secrets/letsencrypt/                certbot state (account, live/<domain>/, renewal/)
#   secrets/tls/le/<domain>/certbot/    dereferenced privkey.pem / fullchain.pem copies
#   secrets/tls/le/<domain>/am/am.p12   -> mount dir via AM_TLS_DIR (org) / AM_COM_TLS_DIR (com)
#   secrets/tls/le/<domain>/gateway/gateway.server.keypair.pem -> GATEWAY_TLS_DIR / GATEWAY_COM_TLS_DIR
#   secrets/truststores/truststore.p12  gains alias isrg-root-x1
#   secrets/tls/ca/ca-bundle.pem        JRSZ root + ISRG Root X1 (for host-side curl / smoke tests)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="${ROOT_DIR}/secrets"
LE_STATE_DIR="${SECRETS_DIR}/letsencrypt"
LE_OUT_ROOT="${SECRETS_DIR}/tls/le"
TRUSTSTORE="${SECRETS_DIR}/truststores/truststore.p12"
CA_DIR="${SECRETS_DIR}/tls/ca"
JRSZ_ROOT="${CA_DIR}/jrsz-root-ca.cert.pem"
CA_BUNDLE="${CA_DIR}/ca-bundle.pem"
ISRG_ROOT_VENDORED="${ROOT_DIR}/config/tls/isrg-root-x1.pem"
ISRG_ROOT_URL="https://letsencrypt.org/certs/isrgrootx1.pem"
ISRG_ROOT_SHA256="96:BC:EC:06:26:49:76:F3:74:60:77:9A:CF:28:C5:A7:CF:E8:A3:C0:AA:E1:1A:8F:FC:EE:05:C0:BD:DF:08:C6"

ENV_FILE="${ROOT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

LE_DOMAIN="${LE_DOMAIN:-jrsz.org}"
LE_SERVICES="${LE_SERVICES:-}"
LE_EMAIL="${LE_EMAIL:-}"
CERTBOT_IMAGE="${CERTBOT_IMAGE:-certbot/dns-cloudflare:latest}"
LE_DNS_PROPAGATION_SECONDS="${LE_DNS_PROPAGATION_SECONDS:-30}"
AM_KEYSTORE_PASSWORD="${AM_KEYSTORE_PASSWORD:-changeit}"
TRUSTSTORE_PASSWORD="${TRUSTSTORE_PASSWORD:-changeit}"

# --domain <zone> may appear anywhere on the command line; strip it before command parsing.
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) shift; LE_DOMAIN="${1:?--domain needs a value}" ;;
    --domain=*) LE_DOMAIN="${1#--domain=}" ;;
    *) ARGS+=("$1") ;;
  esac
  shift
done
set -- ${ARGS[@]+"${ARGS[@]}"}

CF_INI="${SECRETS_DIR}/cloudflare/${LE_DOMAIN}.ini"
LE_OUT_DIR="${LE_OUT_ROOT}/${LE_DOMAIN}"
LE_CERTBOT_COPY="${LE_OUT_DIR}/certbot"
LE_AM_DIR="${LE_OUT_DIR}/am"
LE_GATEWAY_DIR="${LE_OUT_DIR}/gateway"
if [[ -z "${LE_SERVICES}" ]]; then
  case "${LE_DOMAIN}" in
    jrsz.org) LE_SERVICES="am gateway" ;;
    jrsz.net) LE_SERVICES="am-com gateway-com" ;;
  esac
fi

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_bin() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required binary: $1"
}

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

require_cf_ini() {
  [[ -f "${CF_INI}" ]] || die "Missing ${CF_INI}
Create it with:  printf 'dns_cloudflare_api_token = %s\\n' '<token>' > ${CF_INI} && chmod 600 ${CF_INI}
(token needs Zone:DNS:Edit + Zone:Zone:Read on the ${LE_DOMAIN} zone)"
  [[ -d "${CF_INI}" ]] && die "${CF_INI} is a directory (Docker created it?). Remove it and create the file."
  grep -q 'dns_cloudflare_api_token' "${CF_INI}" || die "${CF_INI} must contain: dns_cloudflare_api_token = <token>"
}

ensure_image() {
  # Pull the certbot image if missing. Use a throw-away DOCKER_CONFIG without a credential
  # store: the public image needs no login, and Docker Desktop's docker-credential-desktop
  # helper can hang indefinitely on macOS keychain access, which would otherwise stall the pull.
  if ! docker image inspect "${CERTBOT_IMAGE}" >/dev/null 2>&1; then
    log "Pulling ${CERTBOT_IMAGE}"
    local tmpcfg
    tmpcfg="$(mktemp -d)"
    if [[ -d "${HOME}/.docker/contexts" ]]; then cp -R "${HOME}/.docker/contexts" "${tmpcfg}/"; fi
    local ctx
    ctx="$(docker context show 2>/dev/null || echo default)"
    printf '{"currentContext":"%s"}\n' "${ctx}" > "${tmpcfg}/config.json"
    DOCKER_CONFIG="${tmpcfg}" docker pull "${CERTBOT_IMAGE}" >/dev/null
    rm -rf "${tmpcfg}"
  fi
}

certbot_run() {
  # $@ = certbot arguments
  ensure_image
  docker run --rm \
    -v "${LE_STATE_DIR}:/etc/letsencrypt" \
    -v "${CF_INI}:/cloudflare.ini:ro" \
    "${CERTBOT_IMAGE}" "$@"
}

cmd_issue() {
  local extra=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) extra+=(--dry-run) ;;
      --staging) extra+=(--staging) ;;
      --force)   extra+=(--force-renewal) ;;
      -h|--help) usage 0 ;;
      *) die "issue: unknown option $1" ;;
    esac
    shift
  done
  require_bin docker
  require_cf_ini
  [[ -n "${LE_EMAIL}" ]] || die "LE_EMAIL is not set (put LE_EMAIL=you@example.com in .env)"
  mkdir -p "${LE_STATE_DIR}"

  log "Requesting Let's Encrypt certificate for ${LE_DOMAIN} and *.${LE_DOMAIN} (${extra[*]:-production})"
  certbot_run certonly \
    --dns-cloudflare \
    --dns-cloudflare-credentials /cloudflare.ini \
    --dns-cloudflare-propagation-seconds "${LE_DNS_PROPAGATION_SECONDS}" \
    -d "${LE_DOMAIN}" -d "*.${LE_DOMAIN}" \
    --cert-name "${LE_DOMAIN}" \
    --key-type rsa --rsa-key-size 2048 \
    --preferred-chain "ISRG Root X1" \
    --keep-until-expiring \
    --agree-tos -m "${LE_EMAIL}" --no-eff-email --non-interactive \
    ${extra[@]+"${extra[@]}"}

  if [[ " ${extra[*]:-} " == *" --dry-run "* ]]; then
    log "Dry run succeeded (no certificate written)."
  else
    log "Certificate obtained. Next: $0 install"
  fi
}

extract_live_files() {
  # Copy dereferenced live/ files out of the certbot state (root-owned symlinks) inside a container.
  mkdir -p "${LE_CERTBOT_COPY}"
  ensure_image
  docker run --rm --entrypoint sh \
    -v "${LE_STATE_DIR}:/etc/letsencrypt:ro" \
    -v "${LE_CERTBOT_COPY}:/out" \
    "${CERTBOT_IMAGE}" -c "
      set -e
      d=/etc/letsencrypt/live/${LE_DOMAIN}
      [ -f \"\$d/fullchain.pem\" ] || { echo \"no live cert at \$d (run: le-cert.sh issue)\" >&2; exit 1; }
      cp -L \"\$d/privkey.pem\" \"\$d/fullchain.pem\" \"\$d/cert.pem\" \"\$d/chain.pem\" /out/
      chmod 644 /out/*.pem
    "
}

ensure_isrg_root() {
  # Returns path to a verified ISRG Root X1 PEM.
  local pem="${ISRG_ROOT_VENDORED}"
  if [[ ! -f "${pem}" ]]; then
    log "Vendored ISRG Root X1 missing; downloading from ${ISRG_ROOT_URL}"
    mkdir -p "$(dirname "${pem}")"
    curl -fsSL "${ISRG_ROOT_URL}" -o "${pem}"
  fi
  local fp
  fp="$(openssl x509 -in "${pem}" -noout -fingerprint -sha256 | sed 's/^.*=//')"
  [[ "${fp}" == "${ISRG_ROOT_SHA256}" ]] || die "ISRG Root X1 fingerprint mismatch: ${fp}"
  printf '%s' "${pem}"
}

cmd_install() {
  require_bin docker
  require_bin openssl
  require_bin keytool
  [[ -f "${TRUSTSTORE}" ]] || die "Missing ${TRUSTSTORE} — run scripts/generate-tls.sh first"
  [[ -f "${JRSZ_ROOT}" ]] || die "Missing ${JRSZ_ROOT} — run scripts/generate-tls.sh first"

  log "Extracting live certificate files from certbot state"
  extract_live_files
  local key="${LE_CERTBOT_COPY}/privkey.pem"
  local fullchain="${LE_CERTBOT_COPY}/fullchain.pem"

  log "Building AM keystore -> ${LE_AM_DIR}/am.p12"
  mkdir -p "${LE_AM_DIR}"
  openssl pkcs12 -export \
    -name am \
    -inkey "${key}" \
    -in "${fullchain}" \
    -out "${LE_AM_DIR}/am.p12" \
    -passout "pass:${AM_KEYSTORE_PASSWORD}"
  chmod 600 "${LE_AM_DIR}/am.p12"

  log "Building PingGateway PEM bundle -> ${LE_GATEWAY_DIR}/gateway.server.keypair.pem"
  mkdir -p "${LE_GATEWAY_DIR}"
  cat "${key}" "${fullchain}" > "${LE_GATEWAY_DIR}/gateway.server.keypair.pem"
  chmod 600 "${LE_GATEWAY_DIR}/gateway.server.keypair.pem"

  local isrg
  isrg="$(ensure_isrg_root)"
  if keytool -list -keystore "${TRUSTSTORE}" -storepass "${TRUSTSTORE_PASSWORD}" -storetype PKCS12 -alias isrg-root-x1 >/dev/null 2>&1; then
    log "Truststore already contains isrg-root-x1"
  else
    log "Importing ISRG Root X1 into ${TRUSTSTORE}"
    keytool -importcert -noprompt -trustcacerts \
      -alias isrg-root-x1 -file "${isrg}" \
      -keystore "${TRUSTSTORE}" -storepass "${TRUSTSTORE_PASSWORD}" -storetype PKCS12 >/dev/null
  fi

  log "Writing CA bundle -> ${CA_BUNDLE}"
  cat "${JRSZ_ROOT}" "${isrg}" > "${CA_BUNDLE}"

  local sans notafter
  sans="$(openssl x509 -in "${fullchain}" -noout -ext subjectAltName | tail -n +2 | tr -d ' ')"
  notafter="$(openssl x509 -in "${fullchain}" -noout -enddate | sed 's/^notAfter=//')"
  log ""
  log "Installed: ${sans}  (expires ${notafter})"
  log ""
  case "${LE_DOMAIN}" in
    jrsz.org)
      log "To activate (first time), set in .env:"
      log "  AM_TLS_DIR=./secrets/tls/le/jrsz.org/am"
      log "  GATEWAY_TLS_DIR=./secrets/tls/le/jrsz.org/gateway"
      log "then recreate the org containers and restart the other AM so it reloads the truststore:"
      log "  docker compose up -d am gateway && docker compose restart am-com" ;;
    jrsz.net)
      log "To activate (first time), set in .env:"
      log "  AM_COM_TLS_DIR=./secrets/tls/le/jrsz.net/am"
      log "  AM_COM_KEYSTORE_FILE=/run/secrets/tls/am.p12"
      log "  GATEWAY_COM_TLS_DIR=./secrets/tls/le/jrsz.net/gateway"
      log "then recreate the com containers and restart the other AM so it reloads the truststore:"
      log "  docker compose up -d am-com gateway-com && docker compose restart am" ;;
    *)
      log "Point the stack's AM keystore mount at ${LE_AM_DIR} (file am.p12) and the gateway mount at ${LE_GATEWAY_DIR}, then recreate those containers." ;;
  esac
  log "After a renewal:  docker compose restart ${LE_SERVICES:-<am and gateway services>}"
}

installed_fingerprint() {
  [[ -f "${LE_CERTBOT_COPY}/fullchain.pem" ]] || { printf 'none'; return; }
  openssl x509 -in "${LE_CERTBOT_COPY}/fullchain.pem" -noout -fingerprint -sha256 | sed 's/^.*=//'
}

live_fingerprint() {
  docker run --rm --entrypoint sh \
    -v "${LE_STATE_DIR}:/etc/letsencrypt:ro" \
    "${CERTBOT_IMAGE}" -c "openssl x509 -in /etc/letsencrypt/live/${LE_DOMAIN}/fullchain.pem -noout -fingerprint -sha256 2>/dev/null | sed 's/^.*=//'" || printf 'none'
}

cmd_renew() {
  local force=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force) force=1 ;;
      -h|--help) usage 0 ;;
      *) die "renew: unknown option $1" ;;
    esac
    shift
  done
  require_bin docker
  require_cf_ini
  [[ -d "${LE_STATE_DIR}/live/${LE_DOMAIN}" ]] || die "No certificate to renew — run: $0 issue"

  local before after
  before="$(installed_fingerprint)"
  log "Running certbot renew"
  if [[ ${force} -eq 1 ]]; then
    certbot_run renew --cert-name "${LE_DOMAIN}" --force-renewal --non-interactive
  else
    certbot_run renew --cert-name "${LE_DOMAIN}" --non-interactive
  fi
  after="$(live_fingerprint)"

  if [[ "${before}" == "${after}" && ${force} -eq 0 ]]; then
    log "Certificate unchanged (${after}); nothing to install."
    return 0
  fi
  log "Certificate changed; installing and restarting ${LE_SERVICES:-(no services mapped for ${LE_DOMAIN}; set LE_SERVICES)}"
  cmd_install
  if [[ -n "${LE_SERVICES}" ]]; then
    # shellcheck disable=SC2086
    (cd "${ROOT_DIR}" && docker compose restart ${LE_SERVICES})
  fi
}

cmd_status() {
  require_bin openssl
  local d found=0
  for d in "${LE_OUT_ROOT}"/*/; do
    [[ -f "${d}certbot/fullchain.pem" ]] || continue
    found=1
    log "Installed Let's Encrypt cert for $(basename "${d}") (${d}certbot/fullchain.pem):"
    openssl x509 -in "${d}certbot/fullchain.pem" -noout -subject -issuer -enddate -ext subjectAltName | sed 's/^/  /'
  done
  [[ ${found} -eq 1 ]] || log "No Let's Encrypt cert installed yet (run: $0 issue --domain <zone> && $0 install --domain <zone>)"
  log ""
  log "Compose mount sources (from .env):"
  log "  org: AM_TLS_DIR=${AM_TLS_DIR:-<unset -> ./secrets/tls/am (private CA)>}"
  log "       GATEWAY_TLS_DIR=${GATEWAY_TLS_DIR:-<unset -> ./secrets/tls/gateway (private CA)>}"
  log "  com: AM_COM_TLS_DIR=${AM_COM_TLS_DIR:-<unset -> ./secrets/tls/am-com (private CA)>}  AM_COM_KEYSTORE_FILE=${AM_COM_KEYSTORE_FILE:-<unset -> /run/secrets/tls/am-com.p12>}"
  log "       GATEWAY_COM_TLS_DIR=${GATEWAY_COM_TLS_DIR:-<unset -> ./secrets/tls/gateway-com (private CA)>}"
  if [[ -f "${TRUSTSTORE}" ]] && command -v keytool >/dev/null 2>&1; then
    log ""
    log "Truststore aliases:"
    keytool -list -keystore "${TRUSTSTORE}" -storepass "${TRUSTSTORE_PASSWORD}" -storetype PKCS12 2>/dev/null \
      | grep -i 'trustedCertEntry' | sed 's/^/  /'
  fi
}

main() {
  local cmd="${1:-}"
  [[ -n "${cmd}" ]] || usage 1
  shift
  case "${cmd}" in
    issue)   cmd_issue "$@" ;;
    install) cmd_install "$@" ;;
    renew)   cmd_renew "$@" ;;
    status)  cmd_status "$@" ;;
    -h|--help|help) usage 0 ;;
    *) die "unknown command: ${cmd} (issue|install|renew|status)" ;;
  esac
}

main "$@"
