#!/usr/bin/env python3
"""Provision cross-AM OIDC social login (jrsz.org <-> jrsz.com).

Each /alpha realm plays BOTH roles, symmetric to the SAML federation:
  * As an OpenID Provider (OP): hosts a confidential OIDC client that the PARTNER
    AM uses (``social-<partner>-rp``).
  * As a social consumer (RP): a Social Identity Provider Service entry
    (``<partner>Provider``) pointing at the PARTNER AM, plus the social-only
    ``SocialLogin`` journey.

The side (org/com) is detected from ``AM_COOKIE_DOMAIN`` so the same artifacts
drive both stacks. Idempotent; safe to re-run. Stdlib only (no pip).

Back-channel token/userinfo calls between the two AM containers work because both
share the ``jrsz_net`` Docker network and the same CA truststore; the OP URLs are
identical for the browser and for server-to-server calls.
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
REALM_PATH = os.environ.get("SOCIAL_REALM_PATH", "realms/root/realms/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))

SIDE = "com" if "com" in COOKIE_DOMAIN else "org"
PARTNER = "org" if SIDE == "com" else "com"

BASES = {
    "org": (os.environ.get("ORG_AM_BASE_URL") or "https://am.jrsz.org:8443/am").rstrip("/"),
    "com": (os.environ.get("COM_AM_BASE_URL") or "https://am.jrsz.com:9443/am").rstrip("/"),
}
SECRETS = {
    "org": os.environ.get("SOCIAL_ORG_RP_SECRET") or "org-social-secret-changeit",
    "com": os.environ.get("SOCIAL_COM_RP_SECRET") or "com-social-secret-changeit",
}

# Custom normalization script (committed body) provisioned identically on both
# stacks under a fixed id, referenced by the provider config's Transform Script.
NORM_SCRIPT_ID = "9e1f4c7a-2b3d-4e8f-a1c2-d3e4f5061728"
NORM_SCRIPT_NAME = "Cross-AM OIDC Profile Normalization"
# Built-in transformation script (kept for reference; the handler node uses the
# custom identity script below instead so dynamically provisioned users get email
# as their username rather than a generated UUID).
NORMALIZED_PROFILE_TO_IDENTITY = "ed685f9f-5909-4726-86e8-22bd38b47663"
# Custom "Normalized Profile to Identity" script used by the Social Provider
# Handler node. Same mapping as the built-in one plus `uid` (= email): the
# DefaultAccountProvider names dynamically provisioned accounts from `uid` and
# falls back to a random UUID when it is missing, so this makes the email the
# username for genuinely new users. Referenced by SocialLogin.json's handler node.
IDENTITY_SCRIPT_ID = "7c2e5a18-3f4b-4c9d-b0e1-a2b3c4d5e6f7"
IDENTITY_SCRIPT_NAME = "Cross-AM OIDC Normalized Profile to Identity"
# Scripted Decision Node (AUTHENTICATION_TREE_DECISION_NODE) wired on the handler
# node's NO_ACCOUNT branch. AM-standalone has no built-in node that matches an
# existing user by an arbitrary attribute (the handler matches only by social
# alias; IdentifyExistingUser is IDM-only; the realm id-store search attribute is
# single-valued uid). This script searches the realm id-store by `mail` and, on a
# hit, switches the journey to log in as that existing account instead of
# provisioning a duplicate. See SocialLogin.json + scripts/cross-am-oidc-email-match.groovy.
MATCH_SCRIPT_ID = "5d3a9b2c-6e7f-4a1b-9c8d-0e1f2a3b4c5d"
MATCH_SCRIPT_NAME = "Cross-AM OIDC Match Existing User by Email"
# The email-match script reaches the realm identity store (by mail) via AuthD +
# DNMapper, and the store object it returns is an IdentityStoreImpl. None of these
# are in the stock decision-node script whitelist. These three classes are the
# ENTIRE extra surface this feature opens in the scripting sandbox (no raw
# AMIdentityRepository / admin-token access; the lookup result is the already
# whitelisted ScriptedIdentity). Added idempotently to the global
# AUTHENTICATION_TREE_DECISION_NODE engine configuration.
DECISION_CONTEXT = "AUTHENTICATION_TREE_DECISION_NODE"
WHITELIST_ADDITIONS = [
    "com.sun.identity.authentication.service.AuthD",
    "com.sun.identity.sm.DNMapper",
    "com.sun.identity.idm.IdentityStoreImpl",
]

# The social journey lives in a SUB-realm reached via ?realm=<x> (not a DNS/realm
# alias and not the default realm). The OIDC redirect URL must therefore carry the
# realm so the End-User UI (XUI) resumes the in-progress authentication in the
# CORRECT realm on redirect-back. A bare AM base URL resolves to the root realm,
# and XUI then POSTs the resume to /json/realms/root/authenticate -> #failedLogin.
# (Trailing realm name of REALM_PATH, e.g. realms/root/realms/alpha -> /alpha.)
XUI_REALM = "/" + REALM_PATH.rsplit("/", 1)[-1]
REDIRECT_SUFFIX = f"/XUI/?realm={XUI_REALM}"

# On successful social login the SocialLogin journey's Set Success URL node sends
# the browser to THIS side's PingGateway (IG) launchpad. IG_BASE_URL is provided
# per stack via .env / .env.com; the oauth2-demo bootstrap already whitelists
# ``IG_BASE_URL/*`` in the realm Validation Service (validGotoDestinations), so AM
# honors the redirect. The trailing slash makes the bare host match the ``/*``
# goto pattern and lands on the launchpad route (path ``/``).
IG_DEFAULTS = {"org": "https://ig.jrsz.org", "com": "https://ig.jrsz.com:8444"}
IG_BASE = (os.environ.get("IG_BASE_URL") or IG_DEFAULTS[SIDE]).rstrip("/")
SUCCESS_URL = IG_BASE + "/"

# Derived identities (see module docstring for the symmetry proof).
SIDE_BASE = BASES[SIDE]
PARTNER_BASE = BASES[PARTNER]
OP_CLIENT_ID = f"social-{PARTNER}-rp"          # client this OP hosts for the partner consumer
OP_CLIENT_SECRET = SECRETS[PARTNER]
OP_REDIRECT = PARTNER_BASE + REDIRECT_SUFFIX    # OP returns the browser to the partner's realm-aware XUI
PROVIDER_ID = f"{PARTNER}Provider"              # this consumer's social provider entry
PROVIDER_CLIENT_ID = f"social-{SIDE}-rp"        # client registered for us on the partner OP
PROVIDER_SECRET = SECRETS[SIDE]
PARTNER_OAUTH = f"{PARTNER_BASE}/oauth2/{REALM_PATH}"

VER_AGENT = "protocol=2.0,resource=1.0"
VER_SERVICE = "protocol=2.1,resource=1.0"
VER_SCRIPT = "protocol=2.0,resource=1.0"
VER_TREE = "protocol=1.0,resource=1.0"


def log(msg):
    print(f"  [social/{SIDE}] {msg}")


def token():
    r = urllib.request.Request(
        f"{AM}/json/realms/root/authenticate", data=b"", method="POST",
        headers={"X-OpenAM-Username": "amadmin", "X-OpenAM-Password": ADMIN_PW,
                 "Accept-API-Version": "resource=2.1, protocol=1.0",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))["tokenId"]


def call(method, path, t, body=None, headers=None, ver=VER_SERVICE):
    url = path if path.startswith("http") else f"{AM}/json/{REALM_PATH}/{path}"
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
    """Create-or-update a config resource (GET to choose If-None-Match vs If-Match).

    ``strip_on_update`` lists top-level keys that are accepted on create but
    rejected on update (e.g. the OAuth2Client write-only ``userpassword``).
    """
    st, cur = call("GET", path, t, ver=ver)
    if st == 200:
        # If-Match:* handles concurrency; _rev in the body is rejected by some
        # endpoints (e.g. OAuth2Client agents), so never echo it back.
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


def provision_norm_script(t):
    with open(f"{HERE}/scripts/cross-am-oidc-normalization.groovy") as f:
        script_b64 = base64.b64encode(f.read().encode()).decode()
    body = {
        "_id": NORM_SCRIPT_ID,
        "name": NORM_SCRIPT_NAME,
        "description": "Maps partner AM OIDC claims to AM's normalized social profile (cross-AM social login).",
        "script": script_b64,
        "language": "GROOVY",
        "context": "SOCIAL_IDP_PROFILE_TRANSFORMATION",
        "evaluatorVersion": "1.0",
    }
    upsert(f"scripts/{NORM_SCRIPT_ID}", body, t, VER_SCRIPT, f"normalization script '{NORM_SCRIPT_NAME}'")


def provision_identity_script(t):
    with open(f"{HERE}/scripts/cross-am-oidc-identity.groovy") as f:
        script_b64 = base64.b64encode(f.read().encode()).decode()
    body = {
        "_id": IDENTITY_SCRIPT_ID,
        "name": IDENTITY_SCRIPT_NAME,
        "description": "Maps the normalized social profile to AM identity attributes, setting uid=email so new accounts use email (not a UUID) as the username.",
        "script": script_b64,
        "language": "GROOVY",
        "context": "SOCIAL_IDP_PROFILE_TRANSFORMATION",
        "evaluatorVersion": "1.0",
    }
    upsert(f"scripts/{IDENTITY_SCRIPT_ID}", body, t, VER_SCRIPT, f"identity script '{IDENTITY_SCRIPT_NAME}'")


def provision_match_script(t):
    with open(f"{HERE}/scripts/cross-am-oidc-email-match.groovy") as f:
        script_b64 = base64.b64encode(f.read().encode()).decode()
    body = {
        "_id": MATCH_SCRIPT_ID,
        "name": MATCH_SCRIPT_NAME,
        "description": "Scripted Decision Node: matches an existing local account by mail so social logins reuse it instead of provisioning a duplicate.",
        "script": script_b64,
        "language": "GROOVY",
        "context": DECISION_CONTEXT,
        "evaluatorVersion": "1.0",
    }
    upsert(f"scripts/{MATCH_SCRIPT_ID}", body, t, VER_SCRIPT, f"email-match script '{MATCH_SCRIPT_NAME}'")


def provision_decision_node_whitelist(t):
    """Idempotently add AuthD + DNMapper to the decision-node script sandbox so
    the email-match script can reach the realm identity store. Global config."""
    path = f"{AM}/json/global-config/services/scripting/contexts/{DECISION_CONTEXT}/engineConfiguration"
    st, cur = call("GET", path, t, ver=VER_SCRIPT)
    if st != 200 or not isinstance(cur, dict):
        raise RuntimeError(f"read decision-node engineConfiguration failed: HTTP {st}: {str(cur)[:200]}")
    whitelist = list(cur.get("whiteList", []))
    missing = [c for c in WHITELIST_ADDITIONS if c not in whitelist]
    if not missing:
        log("decision-node script whitelist already complete (AuthD + DNMapper + IdentityStoreImpl)")
        return
    body = dict(cur)
    body.pop("_rev", None)
    body["whiteList"] = whitelist + missing
    st2, resp = call("PUT", path, t, body, headers={"If-Match": "*"}, ver=VER_SCRIPT)
    if st2 not in (200, 201):
        raise RuntimeError(f"update decision-node whitelist failed: HTTP {st2}: {str(resp)[:300]}")
    log(f"decision-node script whitelist extended with {', '.join(missing)}")


def provision_op_client(t):
    body = {
        "_id": OP_CLIENT_ID,
        "userpassword": OP_CLIENT_SECRET,
        "advancedOAuth2ClientConfig": {
            "grantTypes": {"inherited": False, "value": ["authorization_code", "refresh_token"]},
            "isConsentImplied": {"inherited": False, "value": True},
            "responseTypes": {"inherited": False, "value": ["code"]},
            "subjectType": {"inherited": False, "value": "public"},
            "tokenEndpointAuthMethod": {"inherited": False, "value": "client_secret_basic"},
        },
        "coreOAuth2ClientConfig": {
            "clientName": {"inherited": False, "value": [OP_CLIENT_ID]},
            "clientType": {"inherited": False, "value": "Confidential"},
            "redirectionUris": {"inherited": False, "value": [OP_REDIRECT]},
            "scopes": {"inherited": False, "value": ["openid", "profile", "email"]},
            "status": {"inherited": False, "value": "Active"},
        },
        "coreOpenIDClientConfig": {},
        "signEncOAuth2ClientConfig": {
            "idTokenSignedResponseAlg": {"inherited": False, "value": "RS256"},
            "publicKeyLocation": {"inherited": False, "value": "jwks_uri"},
        },
    }
    upsert(f"realm-config/agents/OAuth2Client/{OP_CLIENT_ID}", body, t, VER_AGENT,
           f"OP client '{OP_CLIENT_ID}' (redirect {OP_REDIRECT})", strip_on_update=["userpassword"])


def enable_social_service(t):
    st, _ = call("GET", "realm-config/services/SocialIdentityProviders", t, ver=VER_SERVICE)
    if st == 200:
        log("Social Identity Provider Service already present")
        return
    st2, resp = call("PUT", "realm-config/services/SocialIdentityProviders", t,
                     {"enabled": True}, headers={"If-None-Match": "*"}, ver=VER_SERVICE)
    if st2 not in (200, 201):
        raise RuntimeError(f"enable SocialIdentityProviders failed: HTTP {st2}: {str(resp)[:300]}")
    log("Social Identity Provider Service enabled")


def provision_provider(t):
    body = {
        "_id": PROVIDER_ID,
        "clientId": PROVIDER_CLIENT_ID,
        "clientSecret": PROVIDER_SECRET,
        "authorizationEndpoint": f"{PARTNER_OAUTH}/authorize",
        "tokenEndpoint": f"{PARTNER_OAUTH}/access_token",
        "userInfoEndpoint": f"{PARTNER_OAUTH}/userinfo",
        "jwksUriEndpoint": f"{PARTNER_OAUTH}/connect/jwk_uri",
        "wellKnownEndpoint": f"{PARTNER_OAUTH}/.well-known/openid-configuration",
        "issuer": PARTNER_OAUTH,
        "issuerComparisonCheckType": "EXACT",
        "scopes": ["openid", "profile", "email"],
        "scopeDelimiter": " ",
        "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
        "redirectURI": SIDE_BASE + REDIRECT_SUFFIX,
        "pkceMethod": "S256",
        "userInfoResponseType": "JSON",
        "enabled": True,
        "transform": NORM_SCRIPT_ID,
        "authenticationIdKey": "id",
        "uiConfig": {
            "buttonImage": "",
            "buttonClass": "",
            "buttonCustomStyle": "",
            "buttonCustomStyleHover": "",
            "buttonDisplayName": f"Log in with jrsz.{PARTNER}",
            "iconBackground": "#0c819f",
            "iconClass": "fa-sign-in",
            "iconFontColor": "white",
            "providerButtonColor": "#0c819f",
        },
    }
    upsert(f"realm-config/services/SocialIdentityProviders/oidcConfig/{PROVIDER_ID}", body, t, VER_SERVICE,
           f"social provider '{PROVIDER_ID}' -> {PARTNER_OAUTH}")


def provision_journey(t):
    with open(f"{HERE}/trees/SocialLogin.json") as f:
        tree = json.load(f)
    base = "realm-config/authentication/authenticationtrees"
    for node in tree["nodes"]:
        body = node["body"]
        if node["type"] == "SetSuccessUrlNode":
            # The committed artifact carries a placeholder; the real success URL is
            # this side's IG launchpad, derived from IG_BASE_URL at provision time.
            body = dict(body, successUrl=SUCCESS_URL)
        upsert(f"{base}/nodes/{node['type']}/{node['id']}", body, t, VER_TREE,
               f"node {node['type']}/{node['id']}"
               + (f" (successUrl {SUCCESS_URL})" if node["type"] == "SetSuccessUrlNode" else ""))
    upsert(f"{base}/trees/{tree['name']}", tree["tree"], t, VER_TREE, f"journey '{tree['name']}'")


def main():
    if not AM or not ADMIN_PW:
        print("AM_SERVER_URL and AM_ADMIN_PASSWORD are required", file=sys.stderr)
        return 2
    log(f"provisioning against {AM} (partner={PARTNER}, OP for partner via {OP_CLIENT_ID})")
    t = token()
    provision_norm_script(t)
    provision_identity_script(t)
    provision_match_script(t)
    provision_decision_node_whitelist(t)
    provision_op_client(t)
    enable_social_service(t)
    provision_provider(t)
    provision_journey(t)
    log("cross-AM social login provisioning complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
