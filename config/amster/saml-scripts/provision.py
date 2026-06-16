#!/usr/bin/env python3
"""Provision the SAML2 custom-script lab (app9) across the jrsz.org <-> jrsz.com
federation, from the checked-in canon artifacts in this directory. Idempotent.

This exercises three of the PingAM 8.1 sample SAML2 scripts in a genuine
cross-AM federation, isolated in its own ``/samllab`` realm on BOTH stacks (zero
impact on the app7 ``/alpha`` federation or the saml-integrated SPs):

  * org hosts the IdP  ``samllab-idp``  -> IDP Attribute Mapper script
      (injects star-tagged SAML attributes into the assertion)
  * the IdP's REMOTE view of the com SP carries the NameID Mapper script
      (star-tags the assertion Subject NameID)
  * com hosts the SP   ``samllab-sp``   -> SP Adapter script
      (stashes the star-tagged attributes + NameID into the SP session as the
       ``samllabProof`` property, which app9 reads back)

The side (org/com) is detected from ``AM_COOKIE_DOMAIN`` so the same artifacts
drive both stacks. Per side it: ensures the realm, upserts that side's scripts,
creates/updates that side's hosted entity (with its script wired), imports the
partner entity as remote (org additionally wires the NameID Mapper onto it),
ensures the ``samllab-cot`` circle of trust, creates the realm-local demo-user,
applies the Validation Service, and (com) allowlists the ``samllabProof`` session
property so app9 can read it over REST.

Stdlib only (no pip). Partner trust works because the exported metadata embeds
each AM's signing certificate.
"""

import base64
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

ssl._create_default_https_context = ssl._create_unverified_context

AM = (os.environ.get("AM_SERVER_URL") or os.environ.get("AM_URL") or "").rstrip("/")
ADMIN_PW = os.environ.get("AM_ADMIN_PWD") or os.environ.get("AM_ADMIN_PASSWORD") or ""
COOKIE_DOMAIN = os.environ.get("AM_COOKIE_DOMAIN", "jrsz.org")
HERE = os.path.dirname(os.path.abspath(__file__))

SIDE = "com" if "com" in COOKIE_DOMAIN else "org"
PARTNER = "org" if SIDE == "com" else "com"

REALM_NAME = "samllab"
REALM_PATH = f"realms/root/realms/{REALM_NAME}"
COT_NAME = "samllab-cot"

DEMO_USER = os.environ.get("DEMO_USER_NAME") or "demo-user"
DEMO_USER_PASSWORD = os.environ.get("DEMO_USER_PASSWORD") or "Jrsz$2025!"

IG_DEFAULTS = {"org": "https://ig.jrsz.org", "com": "https://ig.jrsz.com:8444"}
IG_BASE = (os.environ.get("IG_BASE_URL") or IG_DEFAULTS[SIDE]).rstrip("/")
APP9_DEFAULTS = {"org": "https://app9.jrsz.org", "com": "https://app9.jrsz.com:8444"}
APP9_BASE = (os.environ.get("APP9_BASE_URL") or APP9_DEFAULTS[SIDE]).rstrip("/")

# Fixed script ids (so re-runs reconcile in place).
IDP_ATTR_SCRIPT = "5a313001-1001-4001-8001-00000000a101"
NAMEID_SCRIPT = "5a313002-1002-4002-8002-00000000a102"
SP_ADAPTER_SCRIPT = "5a313003-1003-4003-8003-00000000a103"

# Scripts to create on each side: (uuid, name, filename, context).
SCRIPTS_BY_SIDE = {
    "org": [
        (IDP_ATTR_SCRIPT, "Samllab IDP Attribute Mapper", "idp-attribute-mapper.js", "SAML2_IDP_ATTRIBUTE_MAPPER"),
        (NAMEID_SCRIPT, "Samllab NameID Mapper", "nameid-mapper.js", "SAML2_NAMEID_MAPPER"),
    ],
    "com": [
        (SP_ADAPTER_SCRIPT, "Samllab SP Adapter", "sp-adapter.js", "SAML2_SP_ADAPTER"),
    ],
}

VER_SAML = "protocol=2.1,resource=1.0"
VER_SCRIPT = "protocol=2.0,resource=1.0"
VER_REALM = "protocol=1.0,resource=1.0"
VER_USER = "protocol=1.0,resource=2.0"
VER_SERVICE = "protocol=1.0,resource=1.0"
VER_VALIDATION = "protocol=1.0,resource=0.0"


def log(msg):
    print(f"  [saml-scripts/{SIDE}] {msg}")


def b64url(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def token():
    r = urllib.request.Request(
        f"{AM}/json/realms/root/authenticate", data=b"", method="POST",
        headers={"X-OpenAM-Username": "amadmin", "X-OpenAM-Password": ADMIN_PW,
                 "Accept-API-Version": "resource=2.1, protocol=1.0",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))["tokenId"]


def call(method, path, t, body=None, headers=None, ver=VER_SAML):
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
    # global-config/realms is a GLOBAL endpoint, not realm-scoped: pass an
    # absolute URL so call() does NOT prepend REALM_PATH (the realm we are about
    # to create does not exist yet, which otherwise yields HTTP 404 Realm not found).
    realms_url = f"{AM}/json/global-config/realms"
    st, cur = call("GET", f"{realms_url}?_queryFilter=true", t, ver=VER_REALM)
    if st == 200 and isinstance(cur, dict):
        for r in cur.get("result", []):
            if r.get("name") == REALM_NAME:
                log(f"realm '{REALM_NAME}' already exists")
                return
    st2, resp = call("POST", f"{realms_url}?_action=create", t,
                     {"parentPath": "/", "name": REALM_NAME, "active": True, "aliases": []},
                     ver=VER_REALM)
    if st2 not in (200, 201):
        raise RuntimeError(f"realm create failed: HTTP {st2}: {str(resp)[:300]}")
    log(f"realm '{REALM_NAME}' created")


def provision_scripts(t):
    for (uuid, name, filename, context) in SCRIPTS_BY_SIDE[SIDE]:
        with open(f"{HERE}/scripts/{filename}", encoding="utf-8") as f:
            script_b64 = base64.b64encode(f.read().encode("utf-8")).decode()
        body = {
            "_id": uuid,
            "name": name,
            "description": f"PingAM 8.1 sample {context} script (star-tagged custom output) for the app9 samllab.",
            "script": script_b64,
            "language": "JAVASCRIPT",
            "context": context,
            "evaluatorVersion": "1.0",
        }
        upsert(f"scripts/{uuid}", body, t, VER_SCRIPT, f"script '{name}' ({context})")


def provision_hosted(t):
    fname = "samllab-idp.hosted.json" if SIDE == "org" else "samllab-sp.hosted.json"
    with open(f"{HERE}/{fname}", encoding="utf-8") as f:
        hosted = json.load(f)
    # Wire this side's script onto the hosted entity.
    if SIDE == "org":
        hosted["identityProvider"]["assertionProcessing"]["attributeMapper"]["attributeMapperScript"] = IDP_ATTR_SCRIPT
    else:
        hosted["serviceProvider"]["assertionProcessing"]["adapter"]["spAdapterScript"] = SP_ADAPTER_SCRIPT

    entity = hosted["entityId"]
    eid = b64url(entity)
    st, cur = call("GET", f"realm-config/saml2/hosted/{eid}", t)
    if st == 200:
        body = dict(hosted)
        body["_id"] = cur.get("_id")
        body["_rev"] = cur.get("_rev")
        st, resp = call("PUT", f"realm-config/saml2/hosted/{eid}", t, body)
        if st != 200:
            raise RuntimeError(f"hosted PUT failed: HTTP {st}: {str(resp)[:200]}")
        log(f"hosted {SIDE} entity updated ({entity})")
    else:
        st, resp = call("POST", "realm-config/saml2/hosted?_action=create", t, hosted)
        if st not in (200, 201):
            raise RuntimeError(f"hosted create failed: HTTP {st}: {str(resp)[:200]}")
        log(f"hosted {SIDE} entity created ({entity})")
    return entity


def provision_remote(t):
    partner_file = "samllab-sp.metadata.xml" if SIDE == "org" else "samllab-idp.metadata.xml"
    with open(f"{HERE}/{partner_file}", encoding="utf-8") as f:
        xml = f.read()
    m = re.search(r'entityID="([^"]+)"', xml)
    if not m:
        raise RuntimeError("could not read partner entityID from metadata")
    partner_entity = m.group(1)
    peid = b64url(partner_entity)

    st, _ = call("GET", f"realm-config/saml2/remote/{peid}", t)
    if st != 200:
        st, resp = call("POST", "realm-config/saml2/remote?_action=importEntity", t,
                        {"standardMetadata": base64.urlsafe_b64encode(xml.encode()).decode().rstrip("="),
                         "updateType": "CREATE"})
        if st not in (200, 201):
            raise RuntimeError(f"remote import failed: HTTP {st}: {str(resp)[:200]}")
        log(f"remote partner imported ({partner_entity})")
    else:
        log(f"remote partner already imported ({partner_entity})")

    # On the org IdP side, wire the NameID Mapper script onto the IdP's REMOTE
    # view of the com SP (the only place nameIDMapperScript exists).
    if SIDE == "org":
        st, cur = call("GET", f"realm-config/saml2/remote/{peid}", t)
        if st != 200:
            raise RuntimeError(f"remote SP GET failed: HTTP {st}")
        sp = cur.get("serviceProvider") or {}
        ap = sp.setdefault("assertionProcessing", {})
        am = ap.setdefault("accountMapper", {})
        if am.get("nameIDMapperScript") != NAMEID_SCRIPT:
            am["nameIDMapperScript"] = NAMEID_SCRIPT
            body = dict(cur)
            st, resp = call("PUT", f"realm-config/saml2/remote/{peid}", t, body, headers={"If-Match": "*"})
            if st != 200:
                raise RuntimeError(f"remote SP nameID-mapper wiring failed: HTTP {st}: {str(resp)[:200]}")
            log("NameID Mapper script wired onto remote SP (IdP side)")
        else:
            log("NameID Mapper script already wired onto remote SP")
    return partner_entity


def ensure_cot(t, own_entity, partner_entity):
    members = sorted({f"{own_entity}|saml2", f"{partner_entity}|saml2"})
    st, cur = call("GET", f"realm-config/federation/circlesoftrust/{COT_NAME}", t)
    if st == 200:
        existing = set(cur.get("trustedProviders", []))
        if set(members).issubset(existing):
            log(f"circle of trust '{COT_NAME}' already complete")
            return
        body = dict(cur)
        body.pop("_rev", None)
        body.pop("_type", None)
        body["trustedProviders"] = sorted(existing.union(members))
        st, resp = call("PUT", f"realm-config/federation/circlesoftrust/{COT_NAME}", t, body)
        if st != 200:
            raise RuntimeError(f"COT update failed: HTTP {st}: {str(resp)[:200]}")
        log(f"circle of trust '{COT_NAME}' members updated")
        return
    body = {"_id": COT_NAME, "status": "active", "trustedProviders": members}
    st, resp = call("PUT", f"realm-config/federation/circlesoftrust/{COT_NAME}", t, body,
                    headers={"If-None-Match": "*"})
    if st in (200, 201):
        log(f"circle of trust '{COT_NAME}' created")
        return
    st, resp = call("POST", "realm-config/federation/circlesoftrust?_action=create", t, body)
    if st not in (200, 201):
        raise RuntimeError(f"COT create failed: HTTP {st}: {str(resp)[:200]}")
    log(f"circle of trust '{COT_NAME}' created")


def provision_user(t):
    path = f"users/{DEMO_USER}"
    body = {
        "userName": DEMO_USER,
        "givenName": "Demo",
        "sn": "User",
        "cn": "Demo User",
        "mail": f"{DEMO_USER}@jrsz.{SIDE}",
        "telephoneNumber": "+1-555-0142",
        "userPassword": DEMO_USER_PASSWORD,
        "inetUserStatus": "Active",
    }
    st, _ = call("GET", path, t, ver=VER_USER)
    if st == 200:
        st2, resp = call("PUT", path, t, body, headers={"If-Match": "*"}, ver=VER_USER)
        verb = "updated"
    else:
        st2, resp = call("PUT", path, t, body, headers={"If-None-Match": "*"}, ver=VER_USER)
        verb = "created"
    if st2 not in (200, 201):
        raise RuntimeError(f"user {verb} failed: HTTP {st2}: {str(resp)[:200]}")
    log(f"realm user '{DEMO_USER}' {verb}")


def provision_validation(t):
    gotos = [f"{IG_BASE}/*", f"{IG_BASE}/*?*", f"{APP9_BASE}/*", f"{APP9_BASE}/*?*"]
    path = "realm-config/services/validation"
    body = {"validGotoDestinations": gotos}
    st, _ = call("GET", path, t, ver=VER_VALIDATION)
    if st == 200:
        st2, resp = call("PUT", path, t, body, headers={"If-Match": "*"}, ver=VER_VALIDATION)
    else:
        st2, resp = call("POST", f"{path}?_action=create", t, body, ver=VER_VALIDATION)
    if st2 not in (200, 201):
        raise RuntimeError(f"validation service failed: HTTP {st2}: {str(resp)[:200]}")
    log("Validation Service applied (IG + app9 hosts)")


def provision_whitelist(t):
    # Allowlist the samllabProof session property so app9 can read it back over
    # REST (getSessionInfo). Only needed on the SP side (com), where the session
    # is created and the SP Adapter sets the property.
    path = "realm-config/services/amSessionPropertyWhitelist"
    body = {
        "sessionPropertyWhitelist": ["AMCtxId", "samllabProof"],
        "whitelistedQueryProperties": ["samllabProof"],
    }
    st, _ = call("GET", path, t, ver=VER_SERVICE)
    if st == 200:
        st2, resp = call("PUT", path, t, body, headers={"If-Match": "*"}, ver=VER_SERVICE)
    else:
        st2, resp = call("POST", f"{path}?_action=create", t, body, ver=VER_SERVICE)
    if st2 not in (200, 201):
        raise RuntimeError(f"session-property allowlist failed: HTTP {st2}: {str(resp)[:200]}")
    log("Session Property Allowlist applied (samllabProof)")


def main():
    if not AM or not ADMIN_PW:
        print("AM_SERVER_URL and AM_ADMIN_PASSWORD are required", file=sys.stderr)
        return 2
    log(f"provisioning SAML script lab against {AM} (this side hosts the {'IdP' if SIDE == 'org' else 'SP'})")
    t = token()
    ensure_realm(t)
    provision_scripts(t)
    own = provision_hosted(t)
    partner = provision_remote(t)
    ensure_cot(t, own, partner)
    provision_user(t)
    provision_validation(t)
    if SIDE == "com":
        provision_whitelist(t)
    log("SAML custom-script lab provisioning complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
