#!/usr/bin/env python3
"""Browser-simulating QA smoketests for the jrsz.org <-> jrsz.net SAML flows.

Drives all four IDP/SP-init permutations end to end using the SAML HTTP-POST
binding, exactly as a browser would:

  1. Authenticate demo-user at the IdP (establishes the IdP SSO cookie).
  2. Launch idpssoinit / spssoinit.
  3. Follow every 302 and auto-submit every self-posting SAML form
     (SAMLRequest -> IdP, SAMLResponse -> SP ACS) carrying cookies per domain.
  4. Confirm the SP created a federated session that resolves to demo-user.

Pure stdlib (urllib + http.cookiejar + html.parser); no external deps.

Exit code 0 only if every selected flow passes.
"""

import argparse
import http.cookiejar
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

# --------------------------------------------------------------------------
# Config (env overridable; defaults match the lab .env)
# --------------------------------------------------------------------------
ORG_AM = os.environ.get("ORG_AM_BASE_URL", "https://am.jrsz.org:8443/am").rstrip("/")
COM_AM = os.environ.get("COM_AM_BASE_URL", "https://am.jrsz.net:9443/am").rstrip("/")
ORG_ENTITY = os.environ.get("ORG_ENTITY_ID", f"{ORG_AM}/jrsz-org")
COM_ENTITY = os.environ.get("COM_ENTITY_ID", f"{COM_AM}/jrsz-com")
ORG_IDP_MA = os.environ.get("ORG_IDP_METAALIAS", "/alpha/idp-org")
ORG_SP_MA = os.environ.get("ORG_SP_METAALIAS", "/alpha/sp-org")
COM_IDP_MA = os.environ.get("COM_IDP_METAALIAS", "/alpha/idp-com")
COM_SP_MA = os.environ.get("COM_SP_METAALIAS", "/alpha/sp-com")

REALM_PATH = os.environ.get("AM_REALM_PATH", "realms/root/realms/alpha")
DEMO_USER = os.environ.get("DEMO_USER_NAME", "demo-user")
DEMO_PW = os.environ.get("DEMO_USER_PASSWORD", "")
ADMIN_USER = os.environ.get("AM_ADMIN_USER", "amadmin")
ADMIN_PW = os.environ.get("AM_ADMIN_PASSWORD", "")

ORG_APP7 = os.environ.get("APP7_BASE_URL", "https://app7.jrsz.org").rstrip("/")
COM_APP7 = "https://app7.jrsz.net:8444"

POST_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
UA = "Mozilla/5.0 (SAML-smoketest; like-browser)"
MAX_STEPS = 12

SIDES = {
    "org": {"am": ORG_AM, "entity": ORG_ENTITY, "idp_ma": ORG_IDP_MA, "sp_ma": ORG_SP_MA,
            "host": urllib.parse.urlparse(ORG_AM).hostname, "app7": ORG_APP7},
    "com": {"am": COM_AM, "entity": COM_ENTITY, "idp_ma": COM_IDP_MA, "sp_ma": COM_SP_MA,
            "host": urllib.parse.urlparse(COM_AM).hostname, "app7": COM_APP7},
}

# name, init type, IdP side, SP side
FLOWS = [
    ("org-idp_com-sp_idp-init", "idp", "org", "com"),
    ("org-idp_com-sp_sp-init", "sp", "org", "com"),
    ("com-idp_org-sp_idp-init", "idp", "com", "org"),
    ("com-idp_org-sp_sp-init", "sp", "com", "org"),
]


def make_ssl_context():
    # Lab default: do not verify TLS. These tests hit local self-signed AM
    # instances whose root CA omits the keyUsage extension that OpenSSL 3
    # enforces, so verification is opt-in via SMOKE_VERIFY=1.
    ca = os.environ.get("SMOKE_CA_CERT")
    if os.environ.get("SMOKE_VERIFY") == "1" and ca and os.path.exists(ca):
        return ssl.create_default_context(cafile=ca)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Disable automatic redirects so we can capture cookies and forms per hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SamlForm(HTMLParser):
    """Extract the first <form> plus its hidden inputs (the SAML auto-post form)."""

    def __init__(self):
        super().__init__()
        self.action = None
        self.method = "POST"
        self.fields = {}
        self._in_form = False
        self._done = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and not self._done:
            self._in_form = True
            self.action = a.get("action")
            self.method = (a.get("method") or "POST").upper()
        elif tag == "input" and self._in_form:
            name = a.get("name")
            if name:
                self.fields[name] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self._in_form:
            self._in_form = False
            self._done = True


class Browser:
    def __init__(self, ctx, log):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=ctx),
        )
        self.log = log

    def request(self, method, url, data=None, headers=None):
        body = None
        h = {"User-Agent": UA, "Accept": "text/html,application/json"}
        if headers:
            h.update(headers)
        if data is not None and isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode()
            h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(data, (bytes, str)):
            body = data.encode() if isinstance(data, str) else data
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            resp = self.opener.open(req, timeout=30)
            status = resp.status
            rheaders = resp.headers
            content = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status = e.code
            rheaders = e.headers
            content = e.read().decode("utf-8", "replace")
        return status, rheaders, content

    def cookie(self, host, name="iPlanetDirectoryPro"):
        found = None
        for c in self.jar:
            if c.name == name and (host == c.domain or host.endswith(c.domain.lstrip("."))):
                found = c.value
        return found


def login(br, am, realm_path, user, pw):
    status, _, body = br.request(
        "POST", f"{am}/json/{realm_path}/authenticate",
        data=b"",
        headers={
            "X-OpenAM-Username": user,
            "X-OpenAM-Password": pw,
            "Accept-API-Version": "resource=2.1, protocol=1.0",
            "Content-Type": "application/json",
        },
    )
    try:
        tok = json.loads(body).get("tokenId")
    except Exception:
        tok = None
    if status != 200 or not tok:
        raise RuntimeError(f"login failed for {user}@{am} (HTTP {status}): {body[:200]}")
    return tok


def admin_validate(ctx, am, realm_path, token):
    """Validate a session token on the SP AM and return its username (or None)."""
    br = Browser(ctx, None)
    admin = login(br, am, "realms/root", ADMIN_USER, ADMIN_PW)
    status, _, body = br.request(
        "POST", f"{am}/json/{realm_path}/sessions?_action=getSessionInfo",
        data=json.dumps({"tokenId": token}).encode(),
        headers={
            "iPlanetDirectoryPro": admin,
            "Accept-API-Version": "resource=5.1, protocol=1.0",
            "Content-Type": "application/json",
        },
    )
    if status != 200:
        return None, f"getSessionInfo HTTP {status}: {body[:200]}"
    try:
        d = json.loads(body)
    except Exception:
        return None, f"bad getSessionInfo body: {body[:200]}"
    return d.get("username"), d


def is_login_page(status, headers, body):
    loc = headers.get("Location", "") if headers else ""
    if "/XUI" in loc or "authIndexType" in loc or "/UI/Login" in loc:
        return True
    if status == 200 and ("XUI" in body and "SAMLResponse" not in body and "SAMLRequest" not in body):
        return True
    return False


def chase(br, start_url, stop_hosts, log):
    """Follow 302s and auto-submit SAML forms until we hand off to a stop_host
    (the RelayState landing) or hit a terminal page/error."""
    method, url, data = "GET", start_url, None
    for step in range(MAX_STEPS):
        status, headers, body = br.request(method, url, data=data)
        loc = headers.get("Location", "") if headers else ""
        log(f"    [{step}] {method} {url.split('?')[0]} -> {status}"
            + (f" Location={loc.split('?')[0]}" if loc else ""))
        if is_login_page(status, headers, body):
            return "login-required", url, status, body
        if status in (301, 302, 303, 307, 308) and loc:
            host = urllib.parse.urlparse(loc).hostname or ""
            if host in stop_hosts:
                return "handoff", loc, status, body
            method, url, data = "GET", loc, None
            continue
        if status == 200:
            form = SamlForm()
            form.feed(body)
            if form.action and ("SAMLResponse" in form.fields or "SAMLRequest" in form.fields):
                log(f"        auto-submit SAML form -> {form.action.split('?')[0]} "
                    f"({'SAMLResponse' if 'SAMLResponse' in form.fields else 'SAMLRequest'})")
                method, url, data = form.method, form.action, form.fields
                continue
            return "page", url, status, body
        return "error", url, status, body
    return "max-steps", url, 0, ""


def run_flow(name, init, idp_side, sp_side, ctx, verbose):
    idp = SIDES[idp_side]
    sp = SIDES[sp_side]
    relay = f"{sp['app7']}/?flow={name}"

    def log(msg):
        if verbose:
            print(msg)

    print(f"\n=== {name}  ({init}-init: {idp_side} IdP -> {sp_side} SP) ===")
    br = Browser(ctx, log)

    # 1. Authenticate demo-user at the IdP.
    login(br, idp["am"], REALM_PATH, DEMO_USER, DEMO_PW)
    log(f"    logged in {DEMO_USER} at {idp_side} IdP")

    # 2. Build the launch URL.
    if init == "idp":
        q = urllib.parse.urlencode({
            "metaAlias": idp["idp_ma"], "spEntityID": sp["entity"],
            "binding": POST_BINDING, "RelayState": relay})
        start = f"{idp['am']}/idpssoinit?{q}"
    else:
        q = urllib.parse.urlencode({
            "metaAlias": sp["sp_ma"], "idpEntityID": idp["entity"],
            "binding": POST_BINDING, "RelayState": relay})
        start = f"{sp['am']}/spssoinit?{q}"

    # 3. Drive the browser POST SSO chain. Success = the SP redirects the
    # browser to the RelayState landing (app7), having set its session cookie.
    relay_host = urllib.parse.urlparse(relay).hostname
    outcome, where, status, body = chase(br, start, {relay_host}, log)

    if outcome == "login-required":
        return False, "IdP did not honor the SSO session (login page returned)"
    if outcome == "error":
        return False, f"HTTP {status} during flow at {where.split('?')[0]}: {body[:160].strip()}"
    if outcome == "max-steps":
        return False, "exceeded redirect/form budget without completing"
    if outcome == "page":
        snippet = " ".join(body.split())[:200]
        return False, f"landed on a non-handoff page (HTTP {status}): {snippet}"
    # outcome == "handoff": SP should have created a session cookie.

    sp_token = br.cookie(sp["host"])
    if not sp_token:
        return False, f"no SP session cookie set on {sp['host']} after assertion POST"

    username, info = admin_validate(ctx, sp["am"], REALM_PATH, sp_token)
    if username is None:
        return False, f"SP session not valid: {info}"
    if username != DEMO_USER:
        return False, f"SP session resolved to '{username}', expected '{DEMO_USER}'"
    return True, f"federated session on {sp_side} SP for '{username}'"


def main():
    ap = argparse.ArgumentParser(description="SAML POST-SSO smoketests")
    ap.add_argument("flows", nargs="*", help="flow ids or idp/sp/all (default: all)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print each HTTP hop")
    ap.add_argument("-q", "--quiet", action="store_true", help="summary only")
    args = ap.parse_args()

    if not DEMO_PW or not ADMIN_PW:
        print("DEMO_USER_PASSWORD and AM_ADMIN_PASSWORD must be set (source .env).", file=sys.stderr)
        return 2

    selected = FLOWS
    if args.flows and "all" not in args.flows:
        sel = set(args.flows)
        selected = [f for f in FLOWS if f[0] in sel or f[1] in sel]
        if not selected:
            print(f"no flows match {args.flows}; valid: {[f[0] for f in FLOWS]}", file=sys.stderr)
            return 2

    ctx = make_ssl_context()
    results = []
    for name, init, idp_side, sp_side in selected:
        try:
            ok, detail = run_flow(name, init, idp_side, sp_side, ctx, verbose=not args.quiet)
        except Exception as e:
            ok, detail = False, f"exception: {e}"
        results.append((name, ok, detail))
        print(f"    {'PASS' if ok else 'FAIL'}: {detail}")

    print("\n==================== SAML smoketest summary ====================")
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:32s} {detail}")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"  {passed}/{len(results)} flows passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
