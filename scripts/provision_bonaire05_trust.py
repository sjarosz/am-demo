#!/usr/bin/env python3
"""Register the jrsz.net /bravo realm as a Trusted JWT Issuer in the bonaire05 AIC tenant.

Remote half of config/amster/oidc-bonaire (the local half provisions /bravo on am.jrsz.net).
Mirrors the existing `horizon-IDP` trusted issuer in bonaire05, except that the JWK Set is
EMBEDDED (bonaire05 cannot reach the lab's localhost jwk_uri):

  TrustedJwtIssuer  <ISSUER_ID>            (default jrsz-net-IDP, realm alpha)
    issuer                     = https://am.jrsz.net:9443/am/oauth2/realms/root/realms/bravo
    jwkSet                     = {"keys":[<the RSA sig JWK that signs /bravo tokens>]}
    resourceOwnerIdentityClaim = preferred_username
    consentedScopesClaim       = scope
    allowedSubjects            = []   (any subject; the userName must exist in bonaire05)

Admin access to bonaire05 comes from the saved frodo connection profile
(`frodo info <profile> --json`), like scripts/provision in the mcp-demo repo. Nothing is
written to disk except secrets/oidc-signing-net/bravo-oidc-rsa.jwks.json (the public key).

Usage:  scripts/provision_bonaire05_trust.py [--dry-run] [--delete]
Env (from .env / .env.com, or the shell):
  COM_AM_BASE_URL          local AM (default https://am.jrsz.net:9443/am)
  BONAIRE_OIDC_REALM       local realm (default bravo)
  BONAIRE_AM_URL           remote AM (default https://openam-bonaire05.forgeblocks.com/am)
  BONAIRE_REALM            remote realm (default alpha)
  BONAIRE_FRODO_PROFILE    frodo connection substring (default openam-bonaire05)
  BONAIRE_TRUSTED_ISSUER_ID  agent id (default jrsz-net-IDP)
"""
import base64
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path):
    if not os.path.isfile(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)


load_env(os.path.join(ROOT, ".env"))
load_env(os.path.join(ROOT, ".env.com"))

LOCAL_AM = (os.environ.get("COM_AM_BASE_URL") or "https://am.jrsz.net:9443/am").rstrip("/")
LOCAL_REALM = os.environ.get("BONAIRE_OIDC_REALM") or "bravo"
LOCAL_ISSUER = f"{LOCAL_AM}/oauth2/realms/root/realms/{LOCAL_REALM}"
LOCAL_JWK_URI = f"{LOCAL_ISSUER}/connect/jwk_uri"
SIGN_CERT = os.path.join(ROOT, "secrets", "oidc-signing-net", "bravo-oidc-rsa.cert.pem")
JWKS_OUT = os.path.join(ROOT, "secrets", "oidc-signing-net", "bravo-oidc-rsa.jwks.json")
CA_BUNDLE = os.path.join(ROOT, "secrets", "tls", "ca", "ca-bundle.pem")

REMOTE_AM = (os.environ.get("BONAIRE_AM_URL") or "https://openam-bonaire05.forgeblocks.com/am").rstrip("/")
REMOTE_REALM = os.environ.get("BONAIRE_REALM") or "alpha"
FRODO_PROFILE = os.environ.get("BONAIRE_FRODO_PROFILE") or "openam-bonaire05"
ISSUER_ID = os.environ.get("BONAIRE_TRUSTED_ISSUER_ID") or "jrsz-net-IDP"
AGENT_URL = f"{REMOTE_AM}/json/realms/root/realms/{REMOTE_REALM}/realm-config/agents/TrustedJwtIssuer/{ISSUER_ID}"
VER_AGENT = "protocol=2.0,resource=1.0"


def log(msg):
    print(f"  [bonaire05-trust] {msg}")


def local_ssl_context():
    ctx = ssl.create_default_context()
    if os.path.isfile(CA_BUNDLE):
        ctx.load_verify_locations(CA_BUNDLE)
    return ctx


def fetch_signing_jwk():
    """Pick, from the live jwk_uri, the RSA 'sig' key whose modulus matches our cert."""
    if not os.path.isfile(SIGN_CERT):
        raise SystemExit(f"missing {SIGN_CERT}; run scripts/generate-tls.sh (creates secrets/oidc-signing-net)")
    mod_hex = subprocess.check_output(
        ["openssl", "x509", "-in", SIGN_CERT, "-noout", "-modulus"], text=True).strip().split("=", 1)[1]
    want = int(mod_hex, 16)
    try:
        raw = json.load(urllib.request.urlopen(LOCAL_JWK_URI, timeout=30, context=local_ssl_context()))
    except urllib.error.URLError as e:
        raise SystemExit(f"cannot fetch {LOCAL_JWK_URI}: {e} (is the jrsz.net stack up?)")
    for k in raw.get("keys", []):
        if k.get("kty") != "RSA" or k.get("use") not in (None, "sig"):
            continue
        n = k["n"]
        if int.from_bytes(base64.urlsafe_b64decode(n + "=" * (-len(n) % 4)), "big") == want:
            return k
    raise SystemExit("the dedicated signing key is not published on jwk_uri -- re-run the com bootstrap "
                     "(config/amster/oidc-bonaire) and make sure the stateless.signing.RSA mapping exists")


def frodo_token():
    try:
        out = subprocess.check_output(["frodo", "info", FRODO_PROFILE, "--json"], text=True, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        raise SystemExit("frodo CLI not found (brew install frodo-cli); needed to mint a bonaire05 admin token")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"frodo info {FRODO_PROFILE} failed ({e.returncode}); check `frodo conn list`")
    start = out.find("{")
    tok = json.loads(out[start:]).get("bearerToken")
    if not tok:
        raise SystemExit("frodo info returned no bearerToken")
    return tok


def remote(method, url, tok, body=None):
    h = {"Authorization": f"Bearer {tok}", "Accept-API-Version": VER_AGENT, "Content-Type": "application/json"}
    if method == "PUT":
        h["If-Match" if body.get("_exists") else "If-None-Match"] = "*"
        body = {k: v for k, v in body.items() if k != "_exists"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def main():
    dry = "--dry-run" in sys.argv
    delete = "--delete" in sys.argv

    jwk = fetch_signing_jwk()
    jwks = {"keys": [jwk]}
    os.makedirs(os.path.dirname(JWKS_OUT), exist_ok=True)
    with open(JWKS_OUT, "w", encoding="utf-8") as f:
        json.dump(jwks, f, indent=2)
    log(f"signing JWK kid={jwk.get('kid')} exported -> {os.path.relpath(JWKS_OUT, ROOT)}")

    wanted = {
        "issuer": LOCAL_ISSUER,
        "jwkSet": json.dumps(jwks),
        "jwksUri": "",
        "resourceOwnerIdentityClaim": "preferred_username",
        "consentedScopesClaim": "scope",
        "allowedSubjects": [],
        "jwksCacheTimeout": 3600000,
        "jwkStoreCacheMissCacheTime": 60000,
    }
    log(f"target: {AGENT_URL}")
    if dry:
        log("dry run; would PUT:\n" + json.dumps({**wanted, "jwkSet": "<jwks>"}, indent=2))
        return 0

    tok = frodo_token()
    st, cur = remote("GET", AGENT_URL, tok)
    exists = st == 200
    if delete:
        if not exists:
            log(f"{ISSUER_ID} not present; nothing to delete")
            return 0
        st, resp = remote("DELETE", AGENT_URL, tok)
        log(f"deleted {ISSUER_ID}: HTTP {st}")
        return 0 if st == 200 else 1

    if exists:
        # AIC may return attributes wrapped as {"inherited": bool, "value": ...}; unwrap for compare.
        def val(x):
            if isinstance(x, dict) and "inherited" in x:      # wrapped attribute; absent value = empty
                return x.get("value", "")
            return x
        current = {k: val(cur.get(k)) for k in wanted}
        unchanged = all((current.get(k) or "") == (v or "") for k, v in wanted.items() if k != "jwkSet") \
            and json.loads(current.get("jwkSet") or "{}") == jwks
        if unchanged:
            log(f"{ISSUER_ID} already up to date (issuer {LOCAL_ISSUER})")
            return 0
        body = dict(wanted)          # only the valid attributes; no _id/_type/_rev
        body["_exists"] = True
    else:
        body = dict(wanted)
    st, resp = remote("PUT", AGENT_URL, tok, body)
    if st not in (200, 201):
        raise SystemExit(f"PUT failed: HTTP {st}: {json.dumps(resp)[:400]}")
    def rv(x):
        return x.get("value") if isinstance(x, dict) and "value" in x else x
    log(f"{ISSUER_ID} {'updated' if exists else 'created'}: issuer={rv(resp.get('issuer'))} "
        f"resourceOwnerIdentityClaim={rv(resp.get('resourceOwnerIdentityClaim'))} embedded jwkSet={bool(rv(resp.get('jwkSet')))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
