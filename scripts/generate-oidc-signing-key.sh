#!/usr/bin/env bash
#
# Generate a dedicated RSA signing keypair for the /bravo realm's OIDC ID-token
# signing key. This key is intentionally separate from the AM TLS / default
# signing material: its public half is registered in the horizon AIC instance as
# a Trusted JWT Issuer key, so id_tokens minted in /bravo can be replayed there
# as RFC 7523 (urn:ietf:params:oauth:grant-type:jwt-bearer) assertions.
#
# Outputs under secrets/oidc-signing/ (gitignored):
#   bravo-oidc-rsa.key.pem   RSA 2048 private key (PEM)
#   bravo-oidc-rsa.cert.pem  self-signed X.509 cert (PEM, for local verify only)
#   bravo-oidc.jceks         JCEKS keystore, alias bravo-oidc-rsa (consumed by AM)
#
# The keystore is JCEKS/SunJCE to match AM's proven default keystore format. The
# store/entry password is `changeit`; the /bravo realm bootstrap provisions a
# realm FileSystemSecretStore (PLAIN) that hands AM that password.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OIDC_DIR="${ROOT_DIR}/secrets/oidc-signing"

KEY_FILE="${OIDC_DIR}/bravo-oidc-rsa.key.pem"
CERT_FILE="${OIDC_DIR}/bravo-oidc-rsa.cert.pem"
P12_FILE="${OIDC_DIR}/bravo-oidc-rsa.p12"
JCEKS_FILE="${OIDC_DIR}/bravo-oidc.jceks"

OIDC_SIGNING_ALIAS="${OIDC_SIGNING_ALIAS:-bravo-oidc-rsa}"
OIDC_SIGNING_PASSWORD="${OIDC_SIGNING_PASSWORD:-changeit}"
OIDC_SIGNING_DAYS="${OIDC_SIGNING_DAYS:-3650}"
OIDC_SIGNING_SUBJECT="${OIDC_SIGNING_SUBJECT:-/CN=bravo-oidc-signing/O=JRSZ/OU=Development/C=US}"

require_bin() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required binary: $1" >&2
    exit 1
  }
}

require_bin openssl
require_bin keytool

mkdir -p "${OIDC_DIR}"

if [[ -f "${JCEKS_FILE}" ]]; then
  echo "Reusing existing OIDC signing keystore: ${JCEKS_FILE}"
  exit 0
fi

echo "Generating /bravo OIDC signing keypair under ${OIDC_DIR}"

# RSA 2048 private key + self-signed cert (the cert is only used for local
# signature verification in the smoke test; AM publishes the public JWK itself).
openssl req \
  -x509 \
  -newkey rsa:2048 \
  -sha256 \
  -nodes \
  -days "${OIDC_SIGNING_DAYS}" \
  -subj "${OIDC_SIGNING_SUBJECT}" \
  -keyout "${KEY_FILE}" \
  -out "${CERT_FILE}"

# Pack into a PKCS12 entry, then convert to JCEKS (AM's default keystore type).
openssl pkcs12 \
  -export \
  -name "${OIDC_SIGNING_ALIAS}" \
  -inkey "${KEY_FILE}" \
  -in "${CERT_FILE}" \
  -out "${P12_FILE}" \
  -passout "pass:${OIDC_SIGNING_PASSWORD}"

rm -f "${JCEKS_FILE}"
keytool -importkeystore \
  -noprompt \
  -srckeystore "${P12_FILE}" \
  -srcstoretype PKCS12 \
  -srcstorepass "${OIDC_SIGNING_PASSWORD}" \
  -srcalias "${OIDC_SIGNING_ALIAS}" \
  -destkeystore "${JCEKS_FILE}" \
  -deststoretype JCEKS \
  -deststorepass "${OIDC_SIGNING_PASSWORD}" \
  -destkeypass "${OIDC_SIGNING_PASSWORD}" \
  -destalias "${OIDC_SIGNING_ALIAS}"

rm -f "${P12_FILE}"

echo "Created:"
echo "  private key:  ${KEY_FILE}"
echo "  certificate:  ${CERT_FILE}"
echo "  AM keystore:  ${JCEKS_FILE} (alias ${OIDC_SIGNING_ALIAS}, JCEKS)"
