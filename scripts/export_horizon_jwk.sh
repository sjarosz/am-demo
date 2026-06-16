#!/usr/bin/env bash
#
# Export the /bravo OIDC RSA *signing* public key as a JWK Set, ready to paste
# into the horizon AIC Trusted JWT Issuer "JWK Set" field.
#
# The JWK is pulled from AM's live jwk_uri (not hand-authored) because AM's JWT
# header `kid` is a computed hash of the key, not the keystore alias -- so the
# only way to register a key whose `kid` matches the id_tokens AM issues is to
# copy exactly what AM publishes.
#
# Output: secrets/oidc-signing/bravo-oidc-rsa.jwks.json  ({"keys":[<rsa sig jwk>]})
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
CA_CERT="${ROOT_DIR}/secrets/tls/ca/jrsz-root-ca.cert.pem"
OUT_FILE="${ROOT_DIR}/secrets/oidc-signing/bravo-oidc-rsa.jwks.json"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

AM_URL="${AM_URL:-https://am.jrsz.org:8443/am}"
BRAVO_REALM="${HORIZON_OIDC_REALM:-bravo}"
JWK_URI="${AM_URL}/oauth2/realms/root/realms/${BRAVO_REALM}/connect/jwk_uri"
SIGN_CERT="${ROOT_DIR}/secrets/oidc-signing/bravo-oidc-rsa.cert.pem"

# /bravo jwk_uri publishes BOTH the realm signing key and the inherited global
# default. Identify OUR key by matching the certificate's RSA modulus, so we
# export exactly the key that signs /bravo id_tokens (not the default).
if [[ ! -f "${SIGN_CERT}" ]]; then
  echo "Signing cert ${SIGN_CERT} not found; run scripts/generate-oidc-signing-key.sh" >&2
  exit 1
fi
CERT_MODULUS_HEX="$(openssl x509 -in "${SIGN_CERT}" -noout -modulus | sed 's/Modulus=//')"

curl_common=(--silent --show-error --fail)
if [[ -f "${CA_CERT}" ]]; then
  curl_common+=(--cacert "${CA_CERT}" --resolve "am.jrsz.org:8443:127.0.0.1")
else
  curl_common+=(--insecure)
fi

echo "Fetching ${JWK_URI}"
raw_file="$(mktemp)"
trap 'rm -f "${raw_file}"' EXIT
curl "${curl_common[@]}" "${JWK_URI}" > "${raw_file}"

mkdir -p "$(dirname "${OUT_FILE}")"
python3 - "$OUT_FILE" "$raw_file" "$CERT_MODULUS_HEX" <<'PY'
import base64, json, sys
raw = json.loads(open(sys.argv[2]).read())
keys = raw.get("keys", [])
target = int(sys.argv[3], 16)  # certificate RSA modulus

def jwk_modulus(k):
    n = k["n"]
    return int.from_bytes(base64.urlsafe_b64decode(n + "=" * (-len(n) % 4)), "big")

match = [k for k in keys
         if k.get("kty") == "RSA" and "n" in k and jwk_modulus(k) == target]
if not match:
    print("No jwk_uri RSA key matches the dedicated signing certificate modulus.", file=sys.stderr)
    print("Has the /bravo signing mapping been applied? Available RSA kids: "
          + ", ".join(k.get("kid", "?") for k in keys if k.get("kty") == "RSA"), file=sys.stderr)
    sys.exit(1)
out = {"keys": match}
with open(sys.argv[1], "w") as fh:
    json.dump(out, fh, indent=2)
for k in match:
    print(f"  exported dedicated RSA key kid={k.get('kid')} alg={k.get('alg')}")
PY

echo "Wrote ${OUT_FILE}"
echo "Register this JWK Set as the horizon Trusted JWT Issuer key (see docs/horizon-jwt-bearer.md)."
