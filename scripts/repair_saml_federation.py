#!/usr/bin/env python3
"""Idempotently repair the jrsz.org <-> jrsz.com SAML federation config so the
four browser POST-SSO flows work end to end.

The hosted IDP/SP entities were originally provisioned with a minimal REST
template that left several SAML-critical settings wrong or empty. This script
re-applies the known-good values to BOTH hosted entities (org and com):

  * IdP authentication context  -> PasswordProtectedTransport at level 0
    (so the lab's level-0 demo-user session can be asserted at all).
  * IdP attribute mapper class  -> com.sun.identity.saml2.plugins.DefaultIDPAttributeMapper
    (the template stored a non-existent org.forgerock.* class -> ClassNotFound).
  * SP authentication context   -> PasswordProtectedTransport level 0 (default),
    comparison Exact (so the SP recognises the context the IdP asserts;
    otherwise SP-init fails with "AuthnContext doesn't match RequestedAuthnContext").
  * RelayState allow-lists (IdP + SP roles) -> the app7 consoles and AM hosts,
    with query-string wildcards (".../*?*") and correct ports, because AM's
    URLPatternMatcher does not match a query string with a plain ".../*".

It also re-syncs the demo-user password in /alpha on both stacks (the demo-user
bootstrap is create-only and never updates an existing user's password).

Safe to run repeatedly. Reads connection details from the environment (see the
scripts/repair_saml_federation.sh wrapper, which sources .env).
"""

import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

ssl._create_default_https_context = ssl._create_unverified_context

PPT = "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
IDP_ATTR_MAPPER = "com.sun.identity.saml2.plugins.DefaultIDPAttributeMapper"

ADMIN_USER = os.environ.get("AM_ADMIN_USER", "amadmin")
ADMIN_PW = os.environ.get("AM_ADMIN_PASSWORD", "")
DEMO_USER = os.environ.get("DEMO_USER_NAME", "demo-user")
DEMO_PW = os.environ.get("DEMO_USER_PASSWORD", "")
REALM_PATH = os.environ.get("AM_REALM_PATH", "realms/root/realms/alpha")

ORG_AM = os.environ.get("ORG_AM_BASE_URL", "https://am.jrsz.org:8443/am").rstrip("/")
COM_AM = os.environ.get("COM_AM_BASE_URL", "https://am.jrsz.com:9443/am").rstrip("/")
ORG_ENTITY = os.environ.get("ORG_ENTITY_ID", f"{ORG_AM}/jrsz-org")
COM_ENTITY = os.environ.get("COM_ENTITY_ID", f"{COM_AM}/jrsz-com")
ORG_APP7 = os.environ.get("APP7_BASE_URL", "https://app7.jrsz.org").rstrip("/")
COM_APP7 = os.environ.get("APP7_COM_BASE_URL", "https://app7.jrsz.com:8444").rstrip("/")

RELAY_PATTERNS = [
    f"{ORG_APP7}/*", f"{ORG_APP7}/*?*",
    f"{COM_APP7}/*", f"{COM_APP7}/*?*",
    f"{ORG_AM.split('/am')[0]}/*", f"{ORG_AM.split('/am')[0]}/*?*",
    f"{COM_AM.split('/am')[0]}/*", f"{COM_AM.split('/am')[0]}/*?*",
]

SIDES = {"org": (ORG_AM, ORG_ENTITY), "com": (COM_AM, COM_ENTITY)}


def admin_token(am):
    r = urllib.request.Request(
        f"{am}/json/realms/root/authenticate", data=b"", method="POST",
        headers={"X-OpenAM-Username": ADMIN_USER, "X-OpenAM-Password": ADMIN_PW,
                 "Accept-API-Version": "resource=2.1, protocol=1.0",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=30))["tokenId"]


def call(method, url, token, body=None, ver="protocol=2.1,resource=1.0"):
    h = {"iPlanetDirectoryPro": token, "Accept-API-Version": ver,
         "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        return 200, json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def repair_entity(side, am, entity, token):
    eid = base64.urlsafe_b64encode(entity.encode()).decode().rstrip("=")
    url = f"{am}/json/realms/root/{REALM_PATH.split('realms/root/')[-1]}/realm-config/saml2/hosted/{eid}"
    st, ent = call("GET", url, token)
    if st != 200:
        print(f"  [{side}] GET hosted entity failed: HTTP {st}: {str(ent)[:160]}")
        return False

    idp = ent["identityProvider"]
    sp = ent["serviceProvider"]

    idp["assertionContent"]["authenticationContext"]["authContextItems"] = \
        [{"contextReference": PPT, "level": 0}]
    idp["assertionContent"]["authenticationContext"].setdefault(
        "authenticationContextMapper",
        "com.sun.identity.saml2.plugins.DefaultIDPAuthnContextMapper")
    idp["assertionProcessing"]["attributeMapper"]["attributeMapper"] = IDP_ATTR_MAPPER

    sp["assertionContent"]["authenticationContext"]["authContextItems"] = \
        [{"contextReference": PPT, "level": 0, "defaultItem": True}]
    sp["assertionContent"]["authenticationContext"].setdefault(
        "authenticationComparisonType", "Exact")

    for role in (idp, sp):
        role.setdefault("advanced", {})["relayStateUrlList"] = \
            {"relayStateUrlList": list(RELAY_PATTERNS)}

    st, resp = call("PUT", url, token, ent)
    if st != 200:
        print(f"  [{side}] PUT hosted entity failed: HTTP {st}: {str(resp)[:200]}")
        return False
    print(f"  [{side}] hosted entity repaired (idp+sp authn context, attr mapper, relayState)")
    return True


def sync_demo_password(side, am, token):
    url = f"{am}/json/{REALM_PATH}/users/{DEMO_USER}"
    st, resp = call("PATCH", url, token,
                    [{"operation": "replace", "field": "/userPassword", "value": DEMO_PW}],
                    ver="resource=4.0, protocol=2.1")
    if st == 200:
        print(f"  [{side}] demo-user password synced")
        return True
    print(f"  [{side}] demo-user password sync failed: HTTP {st}: {str(resp)[:160]}")
    return False


def main():
    if not ADMIN_PW or not DEMO_PW:
        print("AM_ADMIN_PASSWORD and DEMO_USER_PASSWORD must be set (source .env).",
              file=sys.stderr)
        return 2
    ok = True
    for side, (am, entity) in SIDES.items():
        print(f"== {side} ({am}) ==")
        try:
            tok = admin_token(am)
        except Exception as e:
            print(f"  [{side}] admin auth failed: {e}")
            ok = False
            continue
        ok &= repair_entity(side, am, entity, tok)
        ok &= sync_demo_password(side, am, tok)
    print("SAML federation repair complete." if ok else "SAML federation repair had errors.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
