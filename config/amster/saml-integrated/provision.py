#!/usr/bin/env python3
"""Provision the cross-AM SAML 2.0 external IdP in INTEGRATED MODE
(jrsz.org <-> jrsz.net), from the checked-in canon artifacts in this directory.
Idempotent; safe to re-run.

This sits alongside (and leaves untouched) the standalone ``app7`` SAML
federation in ``config/amster/saml/``. Integrated mode runs the SAML SP flow
inside an authentication tree via a SAML2 Authentication node, so the hosted SP
uses ``AuthConsumer`` (not ``Consumer``) assertion-consumer endpoints.

The side (org/com) is detected from ``AM_COOKIE_DOMAIN`` so the same artifacts
drive both stacks. On each side it:
  1. Creates/updates THIS stack's hosted SP-only entity ``<side>-integrated-sp``
     from ``<side>-integrated-sp.hosted.json`` (AuthConsumer ACS, auto-fed uid).
  2. Imports the PARTNER's integrated SP metadata (``<partner>-integrated-sp.metadata.xml``)
     as a remote entity so this side's IdP trusts the partner's integrated SP for
     the reverse SP-init direction.
  3. Ensures the ``jrsz-federation`` circle of trust contains both integrated SPs
     and the partner IdP (the base jrsz-<side>/<partner> entities are added by the
     standalone ``config/amster/saml`` provisioner, which runs first).
  4. Provisions the ``SamlLogin`` journey, substituting placeholders: the SAML2
     node's ``idpEntityId`` = partner IdP entity, ``metaAlias`` = local SP
     metaAlias, and the Set Success URL = this side's IG launchpad.

Stdlib only (no pip). Partner trust works because the exported metadata embeds
the (default AM) signing certificate.
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
REALM_PATH = os.environ.get("SAML_REALM_PATH", "realms/root/realms/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))
COT_NAME = "jrsz-federation"

# Side detection: the org stack owns jrsz.org; any other cookie domain (jrsz.net, formerly
# jrsz.com) is the "com" side. AM_SIDE=org|com overrides.
SIDE = os.environ.get("AM_SIDE") or ("org" if COOKIE_DOMAIN.lstrip(".") == "jrsz.org" else "com")
PARTNER = "org" if SIDE == "com" else "com"

# Partner IdP entity ID = the partner's standalone hosted SAML entity (jrsz-<partner>),
# which the standalone saml/ provisioner imports as a remote IdP on this stack.
BASES = {
    "org": (os.environ.get("ORG_AM_BASE_URL") or "https://am.jrsz.org:8443/am").rstrip("/"),
    "com": (os.environ.get("COM_AM_BASE_URL") or "https://am.jrsz.net:9443/am").rstrip("/"),
}
PARTNER_IDP_ENTITY = f"{BASES[PARTNER]}/jrsz-{PARTNER}"

SP_ENTITY = f"{SIDE}-integrated-sp"
SP_METAALIAS = f"/alpha/integrated-sp-{SIDE}"

# On success the SamlLogin journey's Set Success URL node sends the browser to
# THIS side's PingGateway (IG) launchpad (same pattern as the OIDC social
# feature; IG_BASE_URL/* is already whitelisted in validGotoDestinations).
IG_DEFAULTS = {"org": "https://ig.jrsz.org", "com": "https://ig.jrsz.net:8444"}
IG_BASE = (os.environ.get("IG_BASE_URL") or IG_DEFAULTS[SIDE]).rstrip("/")
SUCCESS_URL = IG_BASE + "/"

VER_SERVICE = "protocol=2.1,resource=1.0"
VER_TREE = "protocol=1.0,resource=1.0"


def log(msg):
    print(f"  [saml-integrated/{SIDE}] {msg}")


def b64url(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def token():
    r = urllib.request.Request(
        f"{AM}/json/realms/root/authenticate", data=b"", method="POST",
        headers={"X-OpenAM-Username": "amadmin", "X-OpenAM-Password": ADMIN_PW,
                 "Accept-API-Version": "resource=2.1, protocol=1.0",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))["tokenId"]


def call(method, path, t, body=None, headers=None, ver=VER_SERVICE):
    url = path if path.startswith("http") else f"{AM}/json/{REALM_PATH}/{path}"
    h = {"iPlanetDirectoryPro": t, "Accept-API-Version": ver,
         "Content-Type": "application/json"}
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


def upsert_tree(path, body, t, label):
    """Create-or-update an auth-tree resource (node or tree)."""
    st, cur = call("GET", path, t, ver=VER_TREE)
    if st == 200:
        b = dict(body)
        if isinstance(cur, dict) and cur.get("_id") is not None:
            b["_id"] = cur["_id"]
        st2, resp = call("PUT", path, t, b, headers={"If-Match": "*"}, ver=VER_TREE)
        verb = "updated"
    else:
        st2, resp = call("PUT", path, t, body, headers={"If-None-Match": "*"}, ver=VER_TREE)
        verb = "created"
    if st2 not in (200, 201):
        raise RuntimeError(f"{label} {verb} failed: HTTP {st2}: {str(resp)[:300]}")
    log(f"{label} {verb}")
    return resp


def provision_hosted(t):
    with open(f"{HERE}/{SIDE}-integrated-sp.hosted.json") as f:
        hosted = json.load(f)
    entity = hosted["entityId"]
    eid = b64url(entity)
    st, cur = call("GET", f"realm-config/saml2/hosted/{eid}", t)
    if st == 200:
        body = dict(hosted)
        body["_id"] = cur.get("_id")
        body["_rev"] = cur.get("_rev")
        st, resp = call("PUT", f"realm-config/saml2/hosted/{eid}", t, body)
        if st != 200:
            raise RuntimeError(f"hosted SP PUT failed: HTTP {st}: {str(resp)[:200]}")
        log(f"hosted integrated SP updated to canon ({entity})")
    else:
        st, resp = call("POST", "realm-config/saml2/hosted?_action=create", t, hosted)
        if st not in (200, 201):
            raise RuntimeError(f"hosted SP create failed: HTTP {st}: {str(resp)[:200]}")
        log(f"hosted integrated SP created ({entity})")
    return entity


def provision_remote(t):
    with open(f"{HERE}/{PARTNER}-integrated-sp.metadata.xml") as f:
        xml = f.read()
    m = re.search(r'entityID="([^"]+)"', xml)
    if not m:
        raise RuntimeError("could not read partner integrated-SP entityID from metadata")
    partner_entity = m.group(1)
    peid = b64url(partner_entity)
    st, _ = call("GET", f"realm-config/saml2/remote/{peid}", t)
    if st == 200:
        log(f"remote partner integrated SP already imported ({partner_entity})")
        return partner_entity
    st, resp = call("POST", "realm-config/saml2/remote?_action=importEntity", t,
                    {"standardMetadata": base64.urlsafe_b64encode(xml.encode()).decode().rstrip("="),
                     "updateType": "CREATE"})
    if st not in (200, 201):
        raise RuntimeError(f"remote integrated SP import failed: HTTP {st}: {str(resp)[:200]}")
    log(f"remote partner integrated SP imported ({partner_entity})")
    return partner_entity


def ensure_cot(t, own_sp, partner_sp):
    # Add both integrated SPs and the partner IdP. The base jrsz-<side>/<partner>
    # entities are added by the standalone saml/ provisioner (runs first).
    members = sorted({f"{own_sp}|saml2", f"{partner_sp}|saml2", f"{PARTNER_IDP_ENTITY}|saml2"})
    st, cur = call("GET", f"realm-config/federation/circlesoftrust/{COT_NAME}", t)
    if st == 200:
        existing = set(cur.get("trustedProviders", []))
        if set(members).issubset(existing):
            log(f"circle of trust '{COT_NAME}' already contains integrated entities")
            return
        body = dict(cur)
        # GET echoes read-only fields (_rev, _type) the PUT endpoint rejects with
        # "Invalid attribute specified"; keep only the writable COT attributes.
        body.pop("_rev", None)
        body.pop("_type", None)
        body["trustedProviders"] = sorted(existing.union(members))
        st, resp = call("PUT", f"realm-config/federation/circlesoftrust/{COT_NAME}", t, body)
        if st != 200:
            raise RuntimeError(f"COT update failed: HTTP {st}: {str(resp)[:200]}")
        log(f"circle of trust '{COT_NAME}' members updated (integrated SPs added)")
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


def provision_journey(t):
    with open(f"{HERE}/trees/SamlLogin.json") as f:
        tree = json.load(f)
    base = "realm-config/authentication/authenticationtrees"
    for node in tree["nodes"]:
        body = dict(node["body"])
        if node["type"] == "product-Saml2Node":
            body["idpEntityId"] = PARTNER_IDP_ENTITY
            body["metaAlias"] = SP_METAALIAS
        elif node["type"] == "SetSuccessUrlNode":
            body["successUrl"] = SUCCESS_URL
        upsert_tree(f"{base}/nodes/{node['type']}/{node['id']}", body, t,
                    f"node {node['type']}/{node['id']}")
    upsert_tree(f"{base}/trees/{tree['name']}", tree["tree"], t, f"journey '{tree['name']}'")


def main():
    if not AM or not ADMIN_PW:
        print("AM_SERVER_URL and AM_ADMIN_PASSWORD are required", file=sys.stderr)
        return 2
    log(f"provisioning against {AM} (partner={PARTNER}, SP={SP_ENTITY}, IdP={PARTNER_IDP_ENTITY})")
    t = token()
    own = provision_hosted(t)
    partner = provision_remote(t)
    ensure_cot(t, own, partner)
    provision_journey(t)
    log("cross-AM SAML integrated-mode provisioning complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
