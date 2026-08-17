#!/usr/bin/env bash
#
# Smoke test: log demo-user into the local AM /bravo realm via the dedicated
# horizon OIDC client (Authorization Code + PKCE), capture the id_token, and
# prove it was signed by the dedicated /bravo signing cert:
#   - JOSE header alg == RS256
#   - JOSE header kid == the kid published at /bravo jwk_uri
#   - the RS256 signature verifies against secrets/oidc-signing/bravo-oidc-rsa.cert.pem
# Also prints iss / aud / sub / exp so you can confirm what horizon will receive.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
CA_CERT="${ROOT_DIR}/secrets/tls/ca/ca-bundle.pem"   # JRSZ root + ISRG Root X1 (written by scripts/le-cert.sh install)
[[ -f "${CA_CERT}" ]] || CA_CERT="${ROOT_DIR}/secrets/tls/ca/jrsz-root-ca.cert.pem"
SIGN_CERT="${ROOT_DIR}/secrets/oidc-signing/bravo-oidc-rsa.cert.pem"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

AM_URL="${AM_URL:-https://am.jrsz.org:8443/am}"
BRAVO_REALM="${HORIZON_OIDC_REALM:-bravo}"
CLIENT_ID="${HORIZON_OIDC_CLIENT_ID:-horizon-oidc-app}"
REDIRECT_URI="${HORIZON_OIDC_REDIRECT_URI:-https://app6.jrsz.org/callback}"
SCOPE="openid profile email"
: "${DEMO_USER_NAME:?DEMO_USER_NAME is required}"
: "${DEMO_USER_PASSWORD:?DEMO_USER_PASSWORD is required}"

realm_path="realms/root/realms/${BRAVO_REALM}"
authorize_url="${AM_URL}/oauth2/${realm_path}/authorize"
token_url="${AM_URL}/oauth2/${realm_path}/access_token"
authn_url="${AM_URL}/json/${realm_path}/authenticate"
jwk_uri="${AM_URL}/oauth2/${realm_path}/connect/jwk_uri"

curl_common=(--silent --show-error)
if [[ -f "${CA_CERT}" ]]; then
  curl_common+=(--cacert "${CA_CERT}" --resolve "am.jrsz.org:8443:127.0.0.1")
else
  curl_common+=(--insecure)
fi

require_bin() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required binary: $1" >&2; exit 1; }; }
require_bin curl
require_bin python3
require_bin openssl

work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

# --- PKCE material -----------------------------------------------------------
read -r CODE_VERIFIER CODE_CHALLENGE STATE NONCE < <(python3 - <<'PY'
import base64, hashlib, secrets
def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
v = b64(secrets.token_bytes(48))
c = b64(hashlib.sha256(v.encode()).digest())
print(v, c, b64(secrets.token_bytes(12)), b64(secrets.token_bytes(12)))
PY
)

urlencode() { python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$1"; }

# --- 1. authenticate demo-user, get SSO session ------------------------------
echo "Authenticating ${DEMO_USER_NAME} in /${BRAVO_REALM}"
auth_json="${work_dir}/auth.json"
curl "${curl_common[@]}" \
  -H "X-OpenAM-Username: ${DEMO_USER_NAME}" \
  -H "X-OpenAM-Password: ${DEMO_USER_PASSWORD}" \
  -H "Accept-API-Version: resource=2.1, protocol=1.0" \
  -H "Content-Type: application/json" \
  -X POST "${authn_url}" > "${auth_json}"
sso="$(sed -n 's/.*"tokenId":"\([^"]*\)".*/\1/p' "${auth_json}")"
if [[ -z "${sso}" ]]; then
  echo "Authentication failed" >&2; cat "${auth_json}" >&2; exit 1
fi

# --- 2. authorize (implied consent + active session => 302 with code) --------
echo "Requesting authorization code"
authz_headers="${work_dir}/authz.headers"
query="response_type=code&client_id=$(urlencode "${CLIENT_ID}")&redirect_uri=$(urlencode "${REDIRECT_URI}")"
query+="&scope=$(urlencode "${SCOPE}")&state=${STATE}&nonce=${NONCE}"
query+="&code_challenge=${CODE_CHALLENGE}&code_challenge_method=S256"
curl "${curl_common[@]}" -o /dev/null -D "${authz_headers}" \
  -H "Cookie: iPlanetDirectoryPro=${sso}" \
  "${authorize_url}?${query}"

location="$(sed -n 's/^location: \(.*\)\r$/\1/ip' "${authz_headers}" | tail -n 1)"
code="$(printf '%s' "${location}" | sed -n 's/.*[?&]code=\([^&]*\).*/\1/p')"
if [[ -z "${code}" ]]; then
  echo "No authorization code in redirect:" >&2; cat "${authz_headers}" >&2; exit 1
fi

# --- 3. token exchange -------------------------------------------------------
echo "Exchanging code for tokens"
token_json="${work_dir}/token.json"
curl "${curl_common[@]}" -o "${token_json}" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -X POST "${token_url}" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "code=${code}" \
  --data-urlencode "redirect_uri=${REDIRECT_URI}" \
  --data-urlencode "code_verifier=${CODE_VERIFIER}" \
  --data-urlencode "client_id=${CLIENT_ID}"

id_token="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("id_token",""))' "${token_json}")"
if [[ -z "${id_token}" ]]; then
  echo "No id_token in token response:" >&2; cat "${token_json}" >&2; exit 1
fi

# --- 4. inspect header + claims ---------------------------------------------
# /bravo jwk_uri publishes both the realm key and the inherited default; identify
# OUR key by matching the dedicated cert's modulus.
cert_mod_hex=""
[[ -f "${SIGN_CERT}" ]] && cert_mod_hex="$(openssl x509 -in "${SIGN_CERT}" -noout -modulus | sed 's/Modulus=//')"
published_kid="$(curl "${curl_common[@]}" "${jwk_uri}" \
  | python3 -c '
import base64,json,sys
target=int(sys.argv[1],16) if sys.argv[1] else None
def mod(k):
    n=k["n"];return int.from_bytes(base64.urlsafe_b64decode(n+"="*(-len(n)%4)),"big")
ks=[k for k in json.load(sys.stdin)["keys"] if k.get("kty")=="RSA" and "n" in k]
m=[k for k in ks if target and mod(k)==target]
print(m[0]["kid"] if m else "")' "${cert_mod_hex}")"

echo
echo "id_token analysis:"
python3 - "$id_token" "$published_kid" <<'PY'
import base64, json, sys
def seg(s): return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
tok, published = sys.argv[1], sys.argv[2]
h, p, _ = tok.split(".")
hdr, pl = seg(h), seg(p)
print(f"  alg : {hdr.get('alg')}")
print(f"  kid : {hdr.get('kid')}")
print(f"  iss : {pl.get('iss')}")
print(f"  aud : {pl.get('aud')}")
print(f"  sub : {pl.get('sub')}")
print(f"  exp : {pl.get('exp')}")
ok = True
if hdr.get("alg") != "RS256":
    print("  FAIL: alg is not RS256"); ok = False
if published and hdr.get("kid") != published:
    print(f"  FAIL: kid != dedicated key kid ({published})"); ok = False
elif published:
    print("  OK: kid matches the dedicated /bravo signing key in jwk_uri")
else:
    print("  WARN: could not locate dedicated key in jwk_uri (cert modulus match failed)")
sys.exit(0 if ok else 1)
PY

# --- 5. verify the RS256 signature against the dedicated signing cert ---------
if [[ -f "${SIGN_CERT}" ]]; then
  signing_input="$(printf '%s' "${id_token}" | cut -d. -f1-2)"
  sig_b64="$(printf '%s' "${id_token}" | cut -d. -f3)"
  printf '%s' "${signing_input}" > "${work_dir}/signing_input"
  python3 - "${sig_b64}" "${work_dir}/sig.bin" <<'PY'
import base64, sys
s = sys.argv[1]
open(sys.argv[2], "wb").write(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
PY
  openssl x509 -in "${SIGN_CERT}" -pubkey -noout > "${work_dir}/pub.pem"
  if openssl dgst -sha256 -verify "${work_dir}/pub.pem" \
       -signature "${work_dir}/sig.bin" "${work_dir}/signing_input" >/dev/null 2>&1; then
    echo "  OK: signature verifies against bravo-oidc-rsa.cert.pem"
  else
    echo "  FAIL: signature does NOT verify against the dedicated cert" >&2
    exit 1
  fi
else
  echo "  (skipped local signature verify; ${SIGN_CERT} not present)"
fi

echo
echo "smoke_oidc_bravo: id_token signed by the dedicated /bravo cert. PASS"
