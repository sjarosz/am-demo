#!/usr/bin/env python3
"""Re-export the live SAML federation config into the canon artifacts under
config/amster/saml/. See scripts/export_saml_canon.sh (the wrapper) and
config/amster/saml/README.md.

Writes, for each side (org/com):
  * jrsz-<side>.hosted.json   - hosted entity structured config (server-managed
                                _id/_rev stripped so it is create-ready)
  * jrsz-<side>.metadata.xml  - standard SAML metadata (partner imports this)
and a single circle-of-trust.json with both members.

Stdlib only. TLS verification is skipped (local self-signed lab AMs).
"""

import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

ssl._create_default_https_context = ssl._create_unverified_context

OUT = os.environ.get("SAML_CANON_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "amster", "saml")
ADMIN_USER = os.environ.get("AM_ADMIN_USER", "amadmin")
ADMIN_PW = os.environ.get("AM_ADMIN_PASSWORD", "")

ORG_AM = os.environ.get("ORG_AM_BASE_URL", "https://am.jrsz.org:8443/am").rstrip("/")
COM_AM = os.environ.get("COM_AM_BASE_URL", "https://am.jrsz.com:9443/am").rstrip("/")
ORG_ENTITY = os.environ.get("ORG_ENTITY_ID", f"{ORG_AM}/jrsz-org")
COM_ENTITY = os.environ.get("COM_ENTITY_ID", f"{COM_AM}/jrsz-com")

SIDES = {"org": (ORG_AM, ORG_ENTITY), "com": (COM_AM, COM_ENTITY)}


def token(am):
    r = urllib.request.Request(
        f"{am}/json/realms/root/authenticate", data=b"", method="POST",
        headers={"X-OpenAM-Username": ADMIN_USER, "X-OpenAM-Password": ADMIN_PW,
                 "Accept-API-Version": "resource=2.1, protocol=1.0",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=30))["tokenId"]


def get_json(url, t):
    r = urllib.request.Request(url, headers={"iPlanetDirectoryPro": t,
        "Accept-API-Version": "protocol=2.1,resource=1.0"})
    return json.load(urllib.request.urlopen(r, timeout=30))


def get_text(url, t):
    r = urllib.request.Request(url, headers={"iPlanetDirectoryPro": t})
    return urllib.request.urlopen(r, timeout=30).read().decode()


def main():
    if not ADMIN_PW:
        print("AM_ADMIN_PASSWORD is required (source .env).", file=sys.stderr)
        return 2
    os.makedirs(OUT, exist_ok=True)
    for side, (am, entity) in SIDES.items():
        try:
            t = token(am)
            eid = base64.urlsafe_b64encode(entity.encode()).decode().rstrip("=")
            hosted = get_json(f"{am}/json/realms/root/realms/alpha/realm-config/saml2/hosted/{eid}", t)
            for k in ("_id", "_rev"):
                hosted.pop(k, None)
            with open(f"{OUT}/jrsz-{side}.hosted.json", "w") as f:
                json.dump(hosted, f, indent=2)
                f.write("\n")
            md = get_text(f"{am}/saml2/jsp/exportmetadata.jsp?entityid={entity}&realm=/alpha", t)
            with open(f"{OUT}/jrsz-{side}.metadata.xml", "w") as f:
                f.write(md)
            print(f"exported {side}: hosted.json + metadata.xml")
        except urllib.error.URLError as e:
            print(f"  {side}: export failed ({e}); is the {side} stack up?", file=sys.stderr)
            return 1

    cot = {"_id": "jrsz-federation", "status": "active",
           "trustedProviders": sorted([f"{ORG_ENTITY}|saml2", f"{COM_ENTITY}|saml2"])}
    with open(f"{OUT}/circle-of-trust.json", "w") as f:
        json.dump(cot, f, indent=2)
        f.write("\n")
    print("exported circle-of-trust.json")
    print(f"canon written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
