#!/usr/bin/env python3
"""Make the local AM /bravo realm an identity provider for a remote PingOne AIC
tenant (bonaire05) via RFC 7523 JWT bearer -- the same shape as horizon -> bonaire05.

Provisions, idempotently and over REST, in the local AM (AM_SERVER_URL):

  1. /bravo realm + a cloned OAuth2/OIDC provider (client-based JWT access tokens,
     RS256), with an OAUTH2_ACCESS_TOKEN_MODIFICATION script wired in
  2. that script (scripts/set-audience-for-remote-as.js): for the portal client only,
     aud = <remote AS token endpoint> and preferred_username = uid
  3. realm secret stores (PLAIN passwords + JCEKS keystore) and mappings so BOTH
     am.services.oauth2.oidc.signing.RSA and am.services.oauth2.stateless.signing.RSA
     resolve to the dedicated key -> id_tokens AND access tokens are signed by the key
     whose public JWK gets registered in the remote tenant
  4. a confidential "portal" client (password + authorization_code + refresh_token)
     whose scopes include the scopes to be requested from the remote tenant, so the
     access token's `scope` claim satisfies the trusted issuer's consentedScopesClaim
  5. the demo identities that must exist by the same userName in the remote tenant

The keystore itself is copied into the shared am-home volume by run-bootstrap.sh.
The remote (bonaire05) half is scripts/provision_bonaire05_trust.py.
"""
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

ssl._create_default_https_context = ssl._create_unverified_context

AM = (os.environ.get("AM_SERVER_URL") or os.environ.get("AM_URL") or "").rstrip("/")
ADMIN_PW = os.environ.get("AM_ADMIN_PWD") or os.environ.get("AM_ADMIN_PASSWORD") or ""
COOKIE_DOMAIN = (os.environ.get("AM_COOKIE_DOMAIN") or "jrsz.net").lstrip(".")
HERE = os.path.dirname(os.path.abspath(__file__))
PROVIDER_TEMPLATE = os.path.join(os.path.dirname(HERE), "oauth-oidc.service.json")

REALM_NAME = os.environ.get("BONAIRE_OIDC_REALM") or "bravo"
REALM_PATH = f"realms/root/realms/{REALM_NAME}"

REMOTE_AS_NAME = os.environ.get("REMOTE_AS_NAME") or "bonaire05"
REMOTE_AS_TOKEN_ENDPOINT = (os.environ.get("REMOTE_AS_TOKEN_ENDPOINT")
                            or "https://openam-bonaire05.forgeblocks.com:443/am/oauth2/realms/root/realms/alpha/access_token")

PORTAL_CLIENT_ID = os.environ.get("BONAIRE_PORTAL_CLIENT_ID") or "bonaire-portal"
PORTAL_CLIENT_SECRET = os.environ.get("BONAIRE_PORTAL_CLIENT_SECRET") or "bonaire-portal-secret-changeit"
PORTAL_REDIRECT_URI = os.environ.get("BONAIRE_PORTAL_REDIRECT_URI") or f"https://app6.{COOKIE_DOMAIN}:8444/callback"
# openid/profile/email like horizon's Portal, plus the scopes we want bonaire05 to grant on
# jwt-bearer (they must appear in the assertion's `scope` claim -> consentedScopesClaim).
PORTAL_SCOPES = (os.environ.get("BONAIRE_PORTAL_SCOPES") or "openid profile email a2a:invoke").split()

# Identities that exist by the same userName in the remote tenant.
BONAIRE_DEMO_USER = os.environ.get("BONAIRE_DEMO_USER") or "acarter"
BONAIRE_DEMO_USER_PASSWORD = os.environ.get("BONAIRE_DEMO_USER_PASSWORD") or ""
DEMO_USER = os.environ.get("DEMO_USER_NAME") or "demo-user"
DEMO_USER_PASSWORD = os.environ.get("DEMO_USER_PASSWORD") or ""

# Dedicated signing material (installed into am-home by run-bootstrap.sh).
AM_CFG_DIR = os.environ.get("AM_CFG_DIR") or "/home/forgerock/openam"
KEYSTORE_DEST = f"{AM_CFG_DIR}/security/keystores/bravo-oidc.jceks"
SECRET_DIR = f"{AM_CFG_DIR}/security/secrets/bravo-oidc"
SIGNING_ALIAS = os.environ.get("OIDC_SIGNING_ALIAS") or "bravo-oidc-rsa"
KEYSTORE_STORE_ID = os.environ.get("KEYSTORE_STORE_ID") or "bravo-oidc"
PASSWORD_STORE_ID = os.environ.get("PASSWORD_STORE_ID") or "bravo-oidc-passwords"
STORE_PASS_LABEL = "bravo.oidc.keystore.storepass"
ENTRY_PASS_LABEL = "bravo.oidc.keystore.entrypass"

SCRIPT_UUID = "b0a1e001-0001-4001-8001-00000000b0a1"
SCRIPT_NAME = f"set-audience-for-remote-{REMOTE_AS_NAME}"

VER_AGENT = "protocol=2.0,resource=1.0"
VER_SERVICE = "protocol=1.0,resource=1.0"
VER_SCRIPT = "protocol=2.0,resource=1.0"
VER_USER = "protocol=1.0,resource=2.0"
VER_REALM = "protocol=1.0,resource=1.0"


def log(msg):
    print(f"  [oidc-bonaire] {msg}")


def token():
    r = urllib.request.Request(
        f"{AM}/json/realms/root/authenticate", data=b"", method="POST",
        headers={"X-OpenAM-Username": "amadmin", "X-OpenAM-Password": ADMIN_PW,
                 "Accept-API-Version": "resource=2.1, protocol=1.0",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))["tokenId"]


def call(method, path, t, body=None, headers=None, ver=VER_SERVICE):
    url = path if path.startswith("http") else f"{AM}/json/{path}"
    h = {"iPlanetDirectoryPro": t, "Accept-API-Version": ver, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def upsert(path, body, t, ver, label, strip_on_update=None):
    st, cur = call("GET", path, t, ver=ver)
    if st == 200:
        b = dict(body)
        for k in (strip_on_update or []):
            b.pop(k, None)
        if isinstance(cur, dict) and cur.get("_id") is not None:
            b["_id"] = cur["_id"]
        st2, resp = call("PUT", path, t, b, headers={"If-Match": "*"}, ver=ver)
        verb = "updated"
    else:
        st2, resp = call("PUT", path, t, body, headers={"If-None-Match": "*"}, ver=ver)
        verb = "created"
    if st2 not in (200, 201):
        raise RuntimeError(f"{label} {verb} failed: HTTP {st2}: {str(resp)[:300]}")
    log(f"{label} {verb}")
    return resp


def ensure_realm(t):
    st, cur = call("GET", "global-config/realms?_queryFilter=true", t, ver=VER_REALM)
    if st == 200 and isinstance(cur, dict):
        for r in cur.get("result", []):
            if r.get("name") == REALM_NAME:
                log(f"realm '{REALM_NAME}' already exists")
                return
    st2, resp = call("POST", "global-config/realms?_action=create", t,
                     {"parentPath": "/", "name": REALM_NAME, "active": True, "aliases": []},
                     ver=VER_REALM)
    if st2 not in (200, 201):
        raise RuntimeError(f"realm create failed: HTTP {st2}: {str(resp)[:300]}")
    log(f"realm '{REALM_NAME}' created")


def provision_script(t):
    with open(os.path.join(HERE, "scripts", "set-audience-for-remote-as.js"), encoding="utf-8") as f:
        src = f.read()
    src = (src.replace("@@PORTAL_CLIENT_ID@@", PORTAL_CLIENT_ID)
              .replace("@@REMOTE_AS_TOKEN_ENDPOINT@@", REMOTE_AS_TOKEN_ENDPOINT))
    body = {
        "_id": SCRIPT_UUID,
        "name": SCRIPT_NAME,
        "description": (f"Stamp aud={REMOTE_AS_TOKEN_ENDPOINT} and preferred_username=uid on access "
                        f"tokens of client {PORTAL_CLIENT_ID} so {REMOTE_AS_NAME} accepts them as "
                        "RFC 7523 assertions (Trusted JWT Issuer)."),
        "script": base64.b64encode(src.encode("utf-8")).decode("ascii"),
        "language": "JAVASCRIPT",
        "context": "OAUTH2_ACCESS_TOKEN_MODIFICATION",
        "evaluatorVersion": "1.0",
    }
    upsert(f"{REALM_PATH}/scripts/{SCRIPT_UUID}", body, t, VER_SCRIPT,
           f"script '{SCRIPT_NAME}' (OAUTH2_ACCESS_TOKEN_MODIFICATION)")


def provision_provider(t):
    with open(PROVIDER_TEMPLATE, encoding="utf-8") as f:
        provider = json.load(f)["service"]["oauth-oidc"]
    plugins = provider["pluginsConfig"]
    plugins["accessTokenModificationPluginType"] = "SCRIPTED"
    plugins["accessTokenModificationScript"] = SCRIPT_UUID
    core = provider["coreOAuth2Config"]
    core["statelessTokensEnabled"] = True           # JWT access tokens (needed as bearer assertions)
    adv = provider["advancedOAuth2Config"]
    adv["tokenSigningAlgorithm"] = "RS256"           # signed with the mapped stateless.signing.RSA key
    upsert(f"{REALM_PATH}/realm-config/services/oauth-oidc", provider, t, VER_SERVICE,
           "OAuth2 provider (JWT access tokens, RS256, access-token script wired)")


def provision_secret_stores(t):
    base = f"{REALM_PATH}/realm-config/secrets/stores"
    upsert(f"{base}/FileSystemSecretStore/{PASSWORD_STORE_ID}",
           {"format": "PLAIN", "directory": SECRET_DIR}, t, VER_SERVICE,
           f"FileSystemSecretStore '{PASSWORD_STORE_ID}' (PLAIN)")
    upsert(f"{base}/KeyStoreSecretStore/{KEYSTORE_STORE_ID}",
           {"file": KEYSTORE_DEST, "storetype": "JCEKS", "providerName": "SunJCE",
            "storePassword": STORE_PASS_LABEL, "keyEntryPassword": ENTRY_PASS_LABEL,
            "leaseExpiryDuration": 5}, t, VER_SERVICE,
           f"KeyStoreSecretStore '{KEYSTORE_STORE_ID}'")
    for secret_id in ("am.services.oauth2.oidc.signing.RSA", "am.services.oauth2.stateless.signing.RSA"):
        upsert(f"{base}/KeyStoreSecretStore/{KEYSTORE_STORE_ID}/mappings/{secret_id}",
               {"secretId": secret_id, "aliases": [SIGNING_ALIAS]}, t, VER_SERVICE,
               f"mapping {secret_id} -> {SIGNING_ALIAS}")


def provision_client(t):
    body = {
        "_id": PORTAL_CLIENT_ID,
        "userpassword": PORTAL_CLIENT_SECRET,
        "advancedOAuth2ClientConfig": {
            "grantTypes": {"inherited": False, "value": ["password", "authorization_code", "refresh_token"]},
            "isConsentImplied": {"inherited": False, "value": True},
            "responseTypes": {"inherited": False, "value": ["code"]},
            "subjectType": {"inherited": False, "value": "public"},
            "tokenEndpointAuthMethod": {"inherited": False, "value": "client_secret_basic"},
        },
        "coreOAuth2ClientConfig": {
            "clientName": {"inherited": False, "value": [PORTAL_CLIENT_ID]},
            "clientType": {"inherited": False, "value": "Confidential"},
            "redirectionUris": {"inherited": False, "value": [PORTAL_REDIRECT_URI]},
            "scopes": {"inherited": False, "value": PORTAL_SCOPES},
            "defaultScopes": {"inherited": False, "value": []},
            "status": {"inherited": False, "value": "Active"},
        },
        "coreOpenIDClientConfig": {},
        "signEncOAuth2ClientConfig": {
            "idTokenSignedResponseAlg": {"inherited": False, "value": "RS256"},
        },
    }
    upsert(f"{REALM_PATH}/realm-config/agents/OAuth2Client/{PORTAL_CLIENT_ID}", body, t, VER_AGENT,
           f"portal client '{PORTAL_CLIENT_ID}' (password/code grants, scopes {' '.join(PORTAL_SCOPES)})",
           strip_on_update=["userpassword"])


def provision_user(t, username, password, given, sn):
    if not password:
        log(f"no password configured for '{username}'; skipping")
        return
    path = f"{REALM_PATH}/users/{username}"
    st, _ = call("GET", path, t, ver=VER_USER)
    if st == 200:
        st2, resp = call("PATCH", path, t,
                         [{"operation": "replace", "field": "/userPassword", "value": password}],
                         ver="protocol=2.1,resource=4.0")
        if st2 not in (200, 201):
            raise RuntimeError(f"user '{username}' password sync failed: HTTP {st2}: {str(resp)[:300]}")
        log(f"realm user '{username}' present; password synced")
        return
    body = {"userName": username, "givenName": given, "sn": sn, "cn": f"{given} {sn}",
            "mail": f"{username}@{COOKIE_DOMAIN}", "userPassword": password, "inetUserStatus": "Active"}
    st2, resp = call("PUT", path, t, body, headers={"If-None-Match": "*"}, ver=VER_USER)
    if st2 not in (200, 201):
        raise RuntimeError(f"user '{username}' create failed: HTTP {st2}: {str(resp)[:300]}")
    log(f"realm user '{username}' created")


def verify(t):
    st, jwks = call("GET", f"{AM}/oauth2/realms/root/realms/{REALM_NAME}/connect/jwk_uri", t)
    keys = jwks.get("keys", []) if isinstance(jwks, dict) else []
    log(f"jwk_uri publishes {len(keys)} key(s): {[k.get('kid') for k in keys]}")
    log(f"issuer: {AM}/oauth2/realms/root/realms/{REALM_NAME}")


def main():
    if not AM or not ADMIN_PW:
        print("AM_SERVER_URL and AM_ADMIN_PASSWORD are required", file=sys.stderr)
        return 2
    log(f"provisioning /{REALM_NAME} on {AM} as IdP for {REMOTE_AS_NAME} ({REMOTE_AS_TOKEN_ENDPOINT})")
    t = token()
    ensure_realm(t)
    provision_script(t)
    provision_provider(t)
    provision_secret_stores(t)
    provision_client(t)
    provision_user(t, BONAIRE_DEMO_USER, BONAIRE_DEMO_USER_PASSWORD, "Amy", "Carter")
    provision_user(t, DEMO_USER, DEMO_USER_PASSWORD, "Demo", "User")
    verify(t)
    log("provisioning complete")
    log(f"Next: scripts/provision_bonaire05_trust.sh  (register the public JWK in {REMOTE_AS_NAME})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
