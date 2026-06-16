#!/usr/bin/env python3
"""Provision the OAuth2/OIDC custom-script lab (app8) on the jrsz.org stack.

This is an ORG-ONLY feature: it creates an isolated ``/scriptlab`` realm with its
own OAuth2 provider, wires six of the PingAM 8.1 sample OAuth2/OIDC scripts, and
registers the confidential OIDC client used by app8. Keeping it in a dedicated
realm means the provider-level script hooks do NOT affect the /alpha apps
(app4/app5) or the cross-AM social federation.

The side (org/com) is detected from ``AM_COOKIE_DOMAIN``; on the com stack this
script is a no-op so the same bootstrap wiring is safe on both stacks.

Each script tags its custom output with a leading star emoji so the
customization is instantly visible in the decoded tokens (the proof point).

Idempotent; safe to re-run. Stdlib only (no pip).
"""

import base64
import json
import os
import sys
import ssl
import urllib.error
import urllib.request

ssl._create_default_https_context = ssl._create_unverified_context

AM = (os.environ.get("AM_SERVER_URL") or os.environ.get("AM_URL") or "").rstrip("/")
ADMIN_PW = os.environ.get("AM_ADMIN_PWD") or os.environ.get("AM_ADMIN_PASSWORD") or ""
COOKIE_DOMAIN = os.environ.get("AM_COOKIE_DOMAIN", "jrsz.org")
HERE = os.path.dirname(os.path.abspath(__file__))

SIDE = "com" if "com" in COOKIE_DOMAIN else "org"

REALM_NAME = "scriptlab"
REALM_PATH = f"realms/root/realms/{REALM_NAME}"

APP8_BASE_URL = (os.environ.get("APP8_BASE_URL") or "https://app8.jrsz.org").rstrip("/")
IG_BASE_URL = (os.environ.get("IG_BASE_URL") or "https://ig.jrsz.org").rstrip("/")

CLIENT_ID = os.environ.get("SCRIPTLAB_CLIENT_ID") or "scriptlab-rp"
CLIENT_SECRET = os.environ.get("SCRIPTLAB_CLIENT_SECRET") or "scriptlab-secret-changeit"
REDIRECT_URI = f"{APP8_BASE_URL}/callback"

DEMO_USER = os.environ.get("DEMO_USER_NAME") or "demo-user"
DEMO_USER_PASSWORD = os.environ.get("DEMO_USER_PASSWORD") or "Jrsz$2025!"

PROVIDER_TEMPLATE = os.path.join(os.path.dirname(HERE), "oauth-oidc.service.json")

# Fixed script ids (so re-runs reconcile in place) + their AM script contexts.
# Each tuple: (uuid, name, filename, context).
SCRIPTS = [
    ("a8c10001-0001-4001-8001-0000000000c1", "Scriptlab OIDC Claims",
     "oidc-claims.js", "OIDC_CLAIMS"),
    ("a8c10002-0002-4002-8002-0000000000c2", "Scriptlab Access Token Modification",
     "access-token-modification.js", "OAUTH2_ACCESS_TOKEN_MODIFICATION"),
    ("a8c10003-0003-4003-8003-0000000000c3", "Scriptlab Evaluate Scope",
     "evaluate-scope.js", "OAUTH2_EVALUATE_SCOPE"),
    ("a8c10004-0004-4004-8004-0000000000c4", "Scriptlab Validate Scope",
     "validate-scope.js", "OAUTH2_VALIDATE_SCOPE"),
    ("a8c10005-0005-4005-8005-0000000000c5", "Scriptlab Authorize Endpoint Data Provider",
     "authorize-endpoint-data-provider.js", "OAUTH2_AUTHORIZE_ENDPOINT_DATA_PROVIDER"),
    ("a8c10006-0006-4006-8006-0000000000c6", "Scriptlab May Act",
     "may-act.js", "OAUTH2_MAY_ACT"),
]
SCRIPT_ID = {ctx: uuid for (uuid, _name, _f, ctx) in SCRIPTS}

VER_AGENT = "protocol=2.0,resource=1.0"
VER_SERVICE = "protocol=1.0,resource=1.0"
VER_SCRIPT = "protocol=2.0,resource=1.0"
VER_USER = "protocol=1.0,resource=2.0"
VER_REALM = "protocol=1.0,resource=1.0"


def log(msg):
    print(f"  [oauth2-scripts/{SIDE}] {msg}")


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
    """Create-or-update a config resource (GET to choose If-None-Match vs If-Match)."""
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


def provision_scripts(t):
    for (uuid, name, filename, context) in SCRIPTS:
        with open(f"{HERE}/scripts/{filename}", encoding="utf-8") as f:
            script_b64 = base64.b64encode(f.read().encode("utf-8")).decode()
        body = {
            "_id": uuid,
            "name": name,
            "description": f"PingAM 8.1 sample {context} script (star-tagged custom output) for the app8 scriptlab.",
            "script": script_b64,
            "language": "JAVASCRIPT",
            "context": context,
            "evaluatorVersion": "1.0",
        }
        upsert(f"{REALM_PATH}/scripts/{uuid}", body, t, VER_SCRIPT, f"script '{name}' ({context})")


def provision_provider(t):
    with open(PROVIDER_TEMPLATE, encoding="utf-8") as f:
        provider = json.load(f)["service"]["oauth-oidc"]

    plugins = provider["pluginsConfig"]
    plugins["oidcClaimsPluginType"] = "SCRIPTED"
    plugins["oidcClaimsScript"] = SCRIPT_ID["OIDC_CLAIMS"]
    plugins["accessTokenModificationPluginType"] = "SCRIPTED"
    plugins["accessTokenModificationScript"] = SCRIPT_ID["OAUTH2_ACCESS_TOKEN_MODIFICATION"]
    plugins["evaluateScopePluginType"] = "SCRIPTED"
    plugins["evaluateScopeScript"] = SCRIPT_ID["OAUTH2_EVALUATE_SCOPE"]
    plugins["validateScopePluginType"] = "SCRIPTED"
    plugins["validateScopeScript"] = SCRIPT_ID["OAUTH2_VALIDATE_SCOPE"]
    plugins["authorizeEndpointDataProviderPluginType"] = "SCRIPTED"
    plugins["authorizeEndpointDataProviderScript"] = SCRIPT_ID["OAUTH2_AUTHORIZE_ENDPOINT_DATA_PROVIDER"]

    core = provider["coreOAuth2Config"]
    core["accessTokenMayActScript"] = SCRIPT_ID["OAUTH2_MAY_ACT"]
    core["oidcMayActScript"] = SCRIPT_ID["OAUTH2_MAY_ACT"]
    # Client-based (JWT) access tokens so the star-tagged fields decode directly.
    core["statelessTokensEnabled"] = True

    upsert(f"{REALM_PATH}/realm-config/services/oauth-oidc", provider, t, VER_SERVICE,
           "OAuth2 provider (6 scripts wired)")


def provision_client(t):
    body = {
        "_id": CLIENT_ID,
        "userpassword": CLIENT_SECRET,
        "advancedOAuth2ClientConfig": {
            "grantTypes": {"inherited": False, "value": ["authorization_code", "refresh_token"]},
            "isConsentImplied": {"inherited": False, "value": True},
            "responseTypes": {"inherited": False, "value": ["code"]},
            "subjectType": {"inherited": False, "value": "public"},
            "tokenEndpointAuthMethod": {"inherited": False, "value": "client_secret_basic"},
        },
        "coreOAuth2ClientConfig": {
            "clientName": {"inherited": False, "value": [CLIENT_ID]},
            "clientType": {"inherited": False, "value": "Confidential"},
            "redirectionUris": {"inherited": False, "value": [REDIRECT_URI]},
            "scopes": {"inherited": False, "value": ["openid", "profile", "email", "phone"]},
            "status": {"inherited": False, "value": "Active"},
        },
        "coreOpenIDClientConfig": {},
        "signEncOAuth2ClientConfig": {
            "idTokenSignedResponseAlg": {"inherited": False, "value": "RS256"},
            "publicKeyLocation": {"inherited": False, "value": "jwks_uri"},
        },
    }
    upsert(f"{REALM_PATH}/realm-config/agents/OAuth2Client/{CLIENT_ID}", body, t, VER_AGENT,
           f"OIDC client '{CLIENT_ID}' (redirect {REDIRECT_URI})", strip_on_update=["userpassword"])


def provision_user(t):
    path = f"{REALM_PATH}/users/{DEMO_USER}"
    st, _ = call("GET", path, t, ver=VER_USER)
    body = {
        "userName": DEMO_USER,
        "givenName": "Demo",
        "sn": "User",
        "cn": "Demo User",
        "mail": f"{DEMO_USER}@jrsz.org",
        "telephoneNumber": "+1-555-0142",
        "ou": "Platform Engineering",
        "userPassword": DEMO_USER_PASSWORD,
        "inetUserStatus": "Active",
    }
    if st == 200:
        # Keep the password in sync on re-runs.
        st2, resp = call("PUT", path, t, body, headers={"If-Match": "*"}, ver=VER_USER)
        if st2 not in (200, 201):
            raise RuntimeError(f"user update failed: HTTP {st2}: {str(resp)[:300]}")
        log(f"realm user '{DEMO_USER}' updated")
        return
    st2, resp = call("PUT", path, t, body, headers={"If-None-Match": "*"}, ver=VER_USER)
    if st2 not in (200, 201):
        raise RuntimeError(f"user create failed: HTTP {st2}: {str(resp)[:300]}")
    log(f"realm user '{DEMO_USER}' created")


def provision_validation(t):
    valid_gotos = [f"{IG_BASE_URL}/*", f"{IG_BASE_URL}/*?*",
                   f"{APP8_BASE_URL}/*", f"{APP8_BASE_URL}/*?*"]
    path = f"{REALM_PATH}/realm-config/services/validation"
    body = {"validGotoDestinations": valid_gotos}
    st, _ = call("GET", path, t, ver="protocol=1.0,resource=0.0")
    if st == 200:
        st2, resp = call("PUT", path, t, body, headers={"If-Match": "*"}, ver="protocol=1.0,resource=0.0")
    else:
        st2, resp = call("POST", f"{path}?_action=create", t, body, ver="protocol=1.0,resource=0.0")
    if st2 not in (200, 201):
        raise RuntimeError(f"validation service failed: HTTP {st2}: {str(resp)[:300]}")
    log("Validation Service applied (IG + app8 hosts)")


def main():
    if SIDE == "com":
        log("org-only feature; skipping on com stack")
        return 0
    if not AM or not ADMIN_PW:
        print("AM_SERVER_URL and AM_ADMIN_PASSWORD are required", file=sys.stderr)
        return 2
    log(f"provisioning OAuth2/OIDC script lab against {AM} (realm /{REALM_NAME})")
    t = token()
    ensure_realm(t)
    provision_scripts(t)
    provision_provider(t)
    provision_client(t)
    provision_user(t)
    provision_validation(t)
    log("OAuth2/OIDC script lab provisioning complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
