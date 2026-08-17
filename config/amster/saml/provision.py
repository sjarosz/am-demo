#!/usr/bin/env python3
"""Provision the cross-domain SAML federation (jrsz.org <-> jrsz.net) from the
checked-in canon artifacts in this directory. Idempotent; safe to re-run.

Runs once per stack from the amster bootstrap container, targeting the local AM
(``AM_SERVER_URL``). The side (org/com) is detected from ``AM_COOKIE_DOMAIN`` so
the same artifacts drive both stacks.

On each side it:
  1. Creates/updates THIS stack's hosted IDP+SP entity from ``jrsz-<side>.hosted.json``
     (the canon JSON already contains every fix: IdP/SP authn context at level 0,
     the correct IdP attribute-mapper class, and the query-aware RelayState lists).
  2. Imports the PARTNER's standard metadata (``jrsz-<partner>.metadata.xml``) as a
     remote entity -- no live partner AM required, so bootstrap ordering is safe.
  3. Ensures the ``jrsz-federation`` circle of trust contains both entities.

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


def log(msg):
    print(f"  [saml/{SIDE}] {msg}")


def b64url(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def token():
    r = urllib.request.Request(
        f"{AM}/json/realms/root/authenticate", data=b"", method="POST",
        headers={"X-OpenAM-Username": "amadmin", "X-OpenAM-Password": ADMIN_PW,
                 "Accept-API-Version": "resource=2.1, protocol=1.0",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))["tokenId"]


def call(method, path, t, body=None, headers=None, ver="protocol=2.1,resource=1.0"):
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


def provision_hosted(t):
    with open(f"{HERE}/jrsz-{SIDE}.hosted.json") as f:
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
            raise RuntimeError(f"hosted PUT failed: HTTP {st}: {str(resp)[:200]}")
        log(f"hosted entity updated to canon ({entity})")
    else:
        st, resp = call("POST", "realm-config/saml2/hosted?_action=create", t, hosted)
        if st not in (200, 201):
            raise RuntimeError(f"hosted create failed: HTTP {st}: {str(resp)[:200]}")
        log(f"hosted entity created ({entity})")
    return entity


def provision_remote(t):
    with open(f"{HERE}/jrsz-{PARTNER}.metadata.xml") as f:
        xml = f.read()
    m = re.search(r'entityID="([^"]+)"', xml)
    if not m:
        raise RuntimeError("could not read partner entityID from metadata")
    partner_entity = m.group(1)
    peid = b64url(partner_entity)
    st, _ = call("GET", f"realm-config/saml2/remote/{peid}", t)
    if st == 200:
        log(f"remote partner already imported ({partner_entity})")
        return partner_entity
    st, resp = call("POST", "realm-config/saml2/remote?_action=importEntity", t,
                    {"standardMetadata": base64.urlsafe_b64encode(xml.encode()).decode().rstrip("="),
                     "updateType": "CREATE"})
    if st not in (200, 201):
        raise RuntimeError(f"remote import failed: HTTP {st}: {str(resp)[:200]}")
    log(f"remote partner imported ({partner_entity})")
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


def main():
    if not AM or not ADMIN_PW:
        print("AM_SERVER_URL and AM_ADMIN_PASSWORD are required", file=sys.stderr)
        return 2
    log(f"provisioning against {AM} (partner={PARTNER})")
    t = token()
    own = provision_hosted(t)
    partner = provision_remote(t)
    ensure_cot(t, own, partner)
    log("SAML federation provisioning complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
