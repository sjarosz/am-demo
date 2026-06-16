#!/usr/bin/env python3
"""Browser-simulating QA smoketests for the cross-AM OIDC social login.

Each AM stack is the other's social (OpenID Connect) identity provider. This
drives the `SocialLogin` journey end to end in both directions, exactly as a
browser would, using AM's JSON authentication callback flow:

  1. Pre-authenticate demo-user at the partner OP (establishes the OP SSO cookie
     so the authorize step does not present a login page).
  2. Start the SocialLogin journey on the consumer AM. The Select Identity
     Provider node (single provider, local auth off) auto-advances, so the first
     response is a RedirectCallback to the partner OP's /authorize endpoint.
  3. Follow that redirect at the OP (with the SSO cookie + implied consent); the
     OP returns ?code=&state= back to the consumer redirect_uri.
  4. Resume the journey: POST authenticate again with the same authId plus the
     code/state query params. The Social Provider Handler exchanges the code
     back-channel, normalizes the profile, then finds or auto-provisions the
     user and issues a consumer session.
  5. Confirm the consumer issued a valid federated session.

Pure stdlib (urllib + http.cookiejar + html.parser); no external deps.
Exit code 0 only if every selected direction passes.
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

ORG_AM = os.environ.get("ORG_AM_BASE_URL", "https://am.jrsz.org:8443/am").rstrip("/")
COM_AM = os.environ.get("COM_AM_BASE_URL", "https://am.jrsz.com:9443/am").rstrip("/")
REALM_PATH = os.environ.get("AM_REALM_PATH", "realms/root/realms/alpha")
DEMO_USER = os.environ.get("DEMO_USER_NAME", "demo-user")
DEMO_PW = os.environ.get("DEMO_USER_PASSWORD", "")
ADMIN_USER = os.environ.get("AM_ADMIN_USER", "amadmin")
ADMIN_PW = os.environ.get("AM_ADMIN_PASSWORD", "")

JOURNEY = os.environ.get("SOCIAL_JOURNEY", "SocialLogin")
UA = "Mozilla/5.0 (social-smoketest; like-browser)"
MAX_STEPS = 12
API_VER = "resource=2.1, protocol=1.0"

SIDES = {
    "org": {"am": ORG_AM, "host": urllib.parse.urlparse(ORG_AM).hostname},
    "com": {"am": COM_AM, "host": urllib.parse.urlparse(COM_AM).hostname},
}

# name, consumer side, OP (partner) side
FLOWS = [
    ("org-consumer_com-op", "org", "com"),
    ("com-consumer_org-op", "com", "org"),
]


def make_ssl_context():
    ca = os.environ.get("SMOKE_CA_CERT")
    if os.environ.get("SMOKE_VERIFY") == "1" and ca and os.path.exists(ca):
        return ssl.create_default_context(cafile=ca)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode()
            h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(data, (bytes, str)):
            body = data.encode() if isinstance(data, str) else data
        req = urllib.request.Request(url, data=body, method=method, headers=h)
        try:
            resp = self.opener.open(req, timeout=30)
            return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode("utf-8", "replace")


def login(br, am, realm_path, user, pw):
    status, _, body = br.request(
        "POST", f"{am}/json/{realm_path}/authenticate", data=b"",
        headers={"X-OpenAM-Username": user, "X-OpenAM-Password": pw,
                 "Accept-API-Version": API_VER, "Content-Type": "application/json"})
    try:
        tok = json.loads(body).get("tokenId")
    except Exception:
        tok = None
    if status != 200 or not tok:
        raise RuntimeError(f"login failed for {user}@{am} (HTTP {status}): {body[:200]}")
    return tok


def admin_validate(ctx, am, realm_path, token):
    """Validate a session token on the consumer AM and return its username."""
    br = Browser(ctx, None)
    admin = login(br, am, "realms/root", ADMIN_USER, ADMIN_PW)
    status, _, body = br.request(
        "POST", f"{am}/json/{realm_path}/sessions?_action=getSessionInfo",
        data=json.dumps({"tokenId": token}).encode(),
        headers={"iPlanetDirectoryPro": admin,
                 "Accept-API-Version": "resource=5.1, protocol=1.0",
                 "Content-Type": "application/json"})
    if status != 200:
        return None, f"getSessionInfo HTTP {status}: {body[:200]}"
    try:
        d = json.loads(body)
    except Exception:
        return None, f"bad getSessionInfo body: {body[:200]}"
    return d.get("username"), d


def is_login_page(status, headers, body):
    loc = headers.get("Location", "") if headers else ""
    if "authIndexType" in loc or "/UI/Login" in loc:
        return True
    if status == 200 and ("XUI" in body and "code=" not in body):
        return True
    return False


def find_redirect_callback(payload):
    for cb in payload.get("callbacks", []):
        if cb.get("type") == "RedirectCallback":
            out = {o["name"]: o.get("value") for o in cb.get("output", [])}
            return cb, out.get("redirectUrl")
    return None, None


def follow_to_code(br, url, consumer_host, log):
    """Follow the OP authorize redirect chain and capture ?code=&state= sent
    back to the consumer redirect_uri."""
    method, u, data = "GET", url, None
    for step in range(MAX_STEPS):
        status, headers, body = br.request(method, u, data=data)
        loc = headers.get("Location", "") if headers else ""
        log(f"    [{step}] {method} {u.split('?')[0]} -> {status}"
            + (f" Location={loc.split('?')[0]}" if loc else ""))
        if status in (301, 302, 303, 307, 308) and loc:
            absloc = urllib.parse.urljoin(u, loc)
            p = urllib.parse.urlparse(absloc)
            q = urllib.parse.parse_qs(p.query)
            if p.hostname == consumer_host and "code" in q:
                return q["code"][0], (q.get("state", [None])[0]), None
            method, u, data = "GET", absloc, None
            continue
        if is_login_page(status, headers, body):
            return None, None, "OP returned a login page (SSO session not honored)"
        if status == 200:
            return None, None, f"unexpected 200 page at {u.split('?')[0]}: {' '.join(body.split())[:160]}"
        return None, None, f"HTTP {status} at {u.split('?')[0]}: {body[:160].strip()}"
    return None, None, "exceeded redirect budget before receiving an authorization code"


def run_flow(name, consumer_side, op_side, ctx, verbose):
    consumer = SIDES[consumer_side]
    op = SIDES[op_side]

    def log(msg):
        if verbose:
            print(msg)

    print(f"\n=== {name}  (consumer {consumer_side} <- OP {op_side}) ===")
    br = Browser(ctx, log)

    # 1. Pre-authenticate demo-user at the partner OP.
    login(br, op["am"], REALM_PATH, DEMO_USER, DEMO_PW)
    log(f"    logged in {DEMO_USER} at {op_side} OP")

    # 2. Start the SocialLogin journey on the consumer.
    auth_url = (f"{consumer['am']}/json/{REALM_PATH}/authenticate"
                f"?authIndexType=service&authIndexValue={JOURNEY}")
    status, _, body = br.request("POST", auth_url, data=b"{}",
                                 headers={"Accept-API-Version": API_VER,
                                          "Content-Type": "application/json"})
    if status != 200:
        return False, f"journey start failed (HTTP {status}): {body[:200]}"
    try:
        payload = json.loads(body)
    except Exception:
        return False, f"non-JSON journey start: {body[:200]}"
    auth_id = payload.get("authId")
    callback, redirect_url = find_redirect_callback(payload)
    if not redirect_url:
        return False, f"no RedirectCallback at journey start: {body[:200]}"
    log(f"    redirect to OP authorize: {redirect_url.split('?')[0]}")

    # 3. Follow the OP authorize redirect; capture the code/state returned to us.
    code, state, err = follow_to_code(br, redirect_url, consumer["host"], log)
    if err:
        return False, err
    log(f"    captured authorization code (state={state})")

    # 4. Resume the journey with the code/state and the original authId.
    q = urllib.parse.urlencode({"authIndexType": "service", "authIndexValue": JOURNEY,
                                "code": code, "state": state})
    resume_url = f"{consumer['am']}/json/{REALM_PATH}/authenticate?{q}"
    status, _, body = br.request(
        "POST", resume_url,
        data=json.dumps({"authId": auth_id, "callbacks": callback and [callback] or []}).encode(),
        headers={"Accept-API-Version": API_VER, "Content-Type": "application/json"})
    try:
        payload = json.loads(body)
    except Exception:
        return False, f"non-JSON resume response (HTTP {status}): {body[:200]}"
    tok = payload.get("tokenId")
    if not tok:
        return False, f"journey did not complete (HTTP {status}): {json.dumps(payload)[:240]}"

    # 5. Confirm the consumer issued a valid federated session.
    username, info = admin_validate(ctx, consumer["am"], REALM_PATH, tok)
    if username is None:
        return False, f"consumer session not valid: {info}"
    return True, f"federated session on {consumer_side} for '{username}'"


def main():
    ap = argparse.ArgumentParser(description="Cross-AM OIDC social login smoketests")
    ap.add_argument("flows", nargs="*", help="flow ids or all (default: all)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print each HTTP hop")
    ap.add_argument("-q", "--quiet", action="store_true", help="summary only")
    args = ap.parse_args()

    if not DEMO_PW or not ADMIN_PW:
        print("DEMO_USER_PASSWORD and AM_ADMIN_PASSWORD must be set (source .env).", file=sys.stderr)
        return 2

    selected = FLOWS
    if args.flows and "all" not in args.flows:
        sel = set(args.flows)
        selected = [f for f in FLOWS if f[0] in sel]
        if not selected:
            print(f"no flows match {args.flows}; valid: {[f[0] for f in FLOWS]}", file=sys.stderr)
            return 2

    ctx = make_ssl_context()
    results = []
    for name, consumer_side, op_side in selected:
        try:
            ok, detail = run_flow(name, consumer_side, op_side, ctx, verbose=not args.quiet)
        except Exception as e:
            ok, detail = False, f"exception: {e}"
        results.append((name, ok, detail))
        print(f"    {'PASS' if ok else 'FAIL'}: {detail}")

    print("\n================ cross-AM social login smoketest summary ================")
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:26s} {detail}")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"  {passed}/{len(results)} directions passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
