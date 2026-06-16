#!/usr/bin/env python3
"""Session-timeout / logout / OIDC-SLO test harness for the app6 lab.

This exercises the automatable subset of the app6 (`timeout-test` realm) matrix
end-to-end against the live lab, records a PASS / FAIL / SKIP / INFO verdict per
test against the documented expected result, and persists each run so that
several runs can be averaged.

It does NOT try to fix anything. It only observes and scores what the lab
actually does. Tests that genuinely cannot be exercised without a long timed
wait (idle/max expiry) are SKIPPED unless `--include-timed` is given, and even
then only when the active profile's idle window fits inside `--max-wait`.

How the OIDC flows work headlessly
----------------------------------
`tt-user` is authenticated against the `timeout-test` realm via REST to obtain
the SSO token under test. That token is injected as the `iPlanetDirectoryPro`
cookie into a per-test cookie jar, and the app6 RP login endpoints are driven
exactly like a browser would (following the redirect through AM `/authorize`,
which auto-approves because the RP clients have implied consent). app6 itself
performs the confidential code exchange, holds the RP tokens, and receives
AM back-channel logout - so this harness tests the real RP behavior.

Usage
-----
  python3 scripts/timeout-tests/run.py                 # one run, instant tests
  python3 scripts/timeout-tests/run.py --runs 5        # 5 runs, then average
  python3 scripts/timeout-tests/run.py --include-timed # also run the idle test
  python3 scripts/timeout-tests/run.py --aggregate     # average all stored runs
  python3 scripts/timeout-tests/run.py --side com      # target the jrsz.com twin
  python3 scripts/timeout-tests/run.py --list          # list test cases

Results are written under scripts/timeout-tests/results/.
"""

import argparse
import datetime as _dt
import glob
import http.cookiejar
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def load_env_file(path):
    """Parse a KEY=VALUE .env file into a dict (no os.environ mutation)."""
    out = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


class Config:
    def __init__(self, side, args):
        env_file = ROOT_DIR / (".env.com" if side == "com" else ".env")
        env = load_env_file(env_file)

        def pick(*names, default=None):
            for n in names:
                if n in os.environ and os.environ[n] != "":
                    return os.environ[n]
            for n in names:
                if n in env and env[n] != "":
                    return env[n]
            return default

        self.side = side
        if side == "com":
            self.am_url = (pick("AM_URL", default="https://am.jrsz.com:9443/am")).rstrip("/")
            self.app6_base = (pick("APP6_BASE_URL", default="https://app6.jrsz.com:8444")).rstrip("/")
            self.cookie_domain = pick("AM_COOKIE_DOMAIN", default="jrsz.com")
        else:
            self.am_url = (pick("AM_URL", default="https://am.jrsz.org:8443/am")).rstrip("/")
            self.app6_base = (pick("APP6_BASE_URL", default="https://app6.jrsz.org")).rstrip("/")
            self.cookie_domain = pick("AM_COOKIE_DOMAIN", default="jrsz.org")

        self.admin_user = pick("AM_ADMIN_USER", default="amadmin")
        self.admin_password = pick("AM_ADMIN_PASSWORD", "AM_ADMIN_PWD", default="changeit")
        self.realm_path = pick("TIMEOUT_REALM_PATH", default="realms/root/realms/timeout-test")
        self.realm_name = pick("TIMEOUT_REALM", default="/timeout-test")
        self.tt_user = pick("TIMEOUT_TEST_USER", default="tt-user")
        self.tt_password = pick("TIMEOUT_TEST_USER_PASSWORD", default="St@telessTest-2026-xyz")

        # TLS: the lab uses a self-signed CA (whose cert omits the key-usage
        # extension that Python's strict verifier requires), so like the repo's
        # own bootstrap/curl scripts we default to unverified TLS. Opt in to CA
        # verification with --verify.
        self.ca_cert = ROOT_DIR / "secrets/tls/ca/jrsz-root-ca.cert.pem"
        self.verify = bool(getattr(args, "verify", False))

        self.sessions_url = f"{self.am_url}/json/{self.realm_path}/sessions"
        self.user_auth_url = f"{self.am_url}/json/{self.realm_path}/authenticate"
        self.admin_auth_url = f"{self.am_url}/json/realms/root/authenticate"

    def ssl_context(self):
        if self.verify and self.ca_cert.is_file():
            return ssl.create_default_context(cafile=str(self.ca_cert))
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


# ---------------------------------------------------------------------------
# HTTP layer (urllib + cookiejar, optional redirect following)
# ---------------------------------------------------------------------------
class Resp:
    def __init__(self, status, headers, body, final_url):
        self.status = status
        self.headers = headers
        self.body = body
        self.final_url = final_url

    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # do not follow; surfaces as HTTPError to the caller


class Http:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ctx = cfg.ssl_context()

    def _opener(self, jar, follow):
        handlers = [urllib.request.HTTPSHandler(context=self.ctx)]
        if jar is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        if not follow:
            handlers.append(_NoRedirect())
        return urllib.request.build_opener(*handlers)

    def request(self, method, url, jar=None, follow=True, headers=None, data=None,
                timeout=30):
        hdrs = dict(headers or {})
        body = None
        if data is not None:
            if isinstance(data, (dict, list)):
                body = json.dumps(data).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            elif isinstance(data, bytes):
                body = data
            else:
                body = str(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        opener = self._opener(jar, follow)
        try:
            with opener.open(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                return Resp(r.status, dict(r.headers), raw, r.geturl())
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            return Resp(e.code, dict(e.headers or {}), raw, url)


# ---------------------------------------------------------------------------
# AM REST helpers (admin-acting, independent of any cookie jar)
# ---------------------------------------------------------------------------
class Am:
    def __init__(self, cfg, http):
        self.cfg = cfg
        self.http = http
        self._admin = None

    def admin_token(self, force=False):
        if self._admin and not force:
            return self._admin
        r = self.http.request(
            "POST", self.cfg.admin_auth_url,
            headers={
                "X-OpenAM-Username": self.cfg.admin_user,
                "X-OpenAM-Password": self.cfg.admin_password,
                "Accept-API-Version": "resource=2.1, protocol=1.0",
                "Content-Type": "application/json",
            },
            data="{}",
        )
        j = r.json() or {}
        if not j.get("tokenId"):
            raise RuntimeError(f"AM admin auth failed (status {r.status})")
        self._admin = j["tokenId"]
        return self._admin

    def authenticate_user(self):
        r = self.http.request(
            "POST", self.cfg.user_auth_url,
            headers={
                "X-OpenAM-Username": self.cfg.tt_user,
                "X-OpenAM-Password": self.cfg.tt_password,
                "Accept-API-Version": "resource=2.1, protocol=1.0",
                "Content-Type": "application/json",
            },
            data="{}",
        )
        j = r.json() or {}
        return j.get("tokenId")

    def _session_action(self, action, token_id, extra_query="", body=None,
                        use_admin=True):
        admin = self.admin_token() if use_admin else token_id
        url = f"{self.cfg.sessions_url}?_action={action}{extra_query}"
        hdr = token_id if (action == "logout" and not use_admin) else admin
        r = self.http.request(
            "POST", url,
            headers={
                "iPlanetDirectoryPro": hdr,
                "Accept-API-Version": "resource=5.1, protocol=1.0",
                "Content-Type": "application/json",
            },
            data=body if body is not None else (json.dumps({"tokenId": token_id})),
        )
        if r.status == 401 and use_admin:
            self.admin_token(force=True)
            return self._session_action(action, token_id, extra_query, body, use_admin)
        return r

    def validate(self, token_id):
        r = self._session_action("validate", token_id, "&refresh=false")
        j = r.json() or {}
        return bool(j.get("valid")), j

    def session_info(self, token_id):
        r = self._session_action("getSessionInfo", token_id)
        return r.json() or {}

    def logout_token(self, token_id):
        # Kill the specific session: token to kill goes in the header.
        r = self.http.request(
            "POST", f"{self.cfg.sessions_url}?_action=logout",
            headers={
                "iPlanetDirectoryPro": token_id,
                "Accept-API-Version": "resource=5.1, protocol=1.0",
                "Content-Type": "application/json",
            },
            data="{}",
        )
        return r.status, (r.json() or {})

    def logout_by_user(self, username):
        r = self._session_action("logoutByUser", "", body=json.dumps({"username": username}))
        return r.status, (r.json() or {})


def session_type(token):
    """Classify the SSO cookie value: client-side JWT vs server-side opaque."""
    if not token:
        return "none"
    import re
    if re.search(r"eyJ[A-Za-z0-9_-]{16,}", token):
        return "client-side"
    return "server-side"


# ---------------------------------------------------------------------------
# app6 driving (cookie-jar based; mimics a browser)
# ---------------------------------------------------------------------------
class App6:
    RP_COOKIE = {"c": "rpc_sid", "d": "rpd_sid"}

    def __init__(self, cfg, http):
        self.cfg = cfg
        self.http = http

    def new_jar_with_sso(self, token):
        jar = http.cookiejar.CookieJar()
        domain = "." + self.cfg.cookie_domain.lstrip(".")
        c = http.cookiejar.Cookie(
            version=0, name="iPlanetDirectoryPro", value=token,
            port=None, port_specified=False,
            domain=domain, domain_specified=True, domain_initial_dot=True,
            path="/", path_specified=True,
            secure=True, expires=None, discard=True,
            comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False,
        )
        jar.set_cookie(c)
        return jar

    def rp_login(self, jar, key, prompt=None):
        url = f"{self.cfg.app6_base}/rp/{key}/login"
        if prompt:
            url += "?prompt=" + urllib.parse.quote(prompt)
        return self.http.request("GET", url, jar=jar, follow=True)

    def rp_status(self, jar, key):
        r = self.http.request(
            "GET", f"{self.cfg.app6_base}/rp/{key}/status",
            jar=jar, follow=True, headers={"Accept": "application/json"},
        )
        return r.json() or {}

    def rp_initiated_logout(self, jar, key):
        return self.http.request(
            "GET", f"{self.cfg.app6_base}/rp/{key}/rp-initiated-logout",
            jar=jar, follow=True,
        )

    def rp_local_logout(self, jar, key):
        return self.http.request(
            "POST", f"{self.cfg.app6_base}/rp/{key}/logout",
            jar=jar, follow=True, headers={"Accept": "application/json"}, data={},
        )

    def probe(self, jar, path, body=None):
        r = self.http.request(
            "POST", f"{self.cfg.app6_base}{path}",
            jar=jar, follow=True,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            data=body if body is not None else {},
        )
        return r.json() or {"_status": r.status}

    def api_e(self, jar, mode, rp=None, token=None):
        body = {"mode": mode}
        if token:
            body["token"] = token
        if rp:
            body["rp"] = rp
        return self.probe(jar, "/probe/api-e", body)

    def protected_get(self, token, path):
        """Direct IG-protected GET with a raw SSO cookie, NOT following redirects."""
        r = self.http.request(
            "GET", f"{self.cfg.app6_base}{path}",
            jar=None, follow=False,
            headers={"Cookie": f"iPlanetDirectoryPro={token}"},
        )
        return r


def poll_logged_out(app6, jar, key, timeout_s=6.0, interval=0.4):
    """Poll an RP status until it reports loggedOut (back-channel may be async)."""
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        last = app6.rp_status(jar, key)
        if last.get("loggedOut") or not last.get("authenticated"):
            return True, last
        time.sleep(interval)
    return False, last


# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------
PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"


class Result:
    def __init__(self, tid, matrix, title, expected):
        self.id = tid
        self.matrix = matrix
        self.title = title
        self.expected = expected
        self.verdict = SKIP
        self.observed = ""
        self.detail = {}
        self.duration_ms = 0

    def to_dict(self):
        return {
            "id": self.id, "matrix": self.matrix, "title": self.title,
            "expected": self.expected, "verdict": self.verdict,
            "observed": self.observed, "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


class Suite:
    def __init__(self, cfg, args):
        self.cfg = cfg
        self.args = args
        self.http = Http(cfg)
        self.am = Am(cfg, self.http)
        self.app6 = App6(cfg, self.http)
        self.session_type = "unknown"

    # -- per-test helpers ----------------------------------------------------
    def fresh_sso(self):
        """A brand-new authenticated SSO session for state isolation per test."""
        token = self.am.authenticate_user()
        if not self.session_type or self.session_type == "unknown":
            self.session_type = session_type(token)
        return token

    def login_rp(self, jar, key, prompt=None):
        self.app6.rp_login(jar, key, prompt=prompt)
        return self.app6.rp_status(jar, key)

    # -- test cases (each returns a Result) ----------------------------------
    def t_auth(self):
        r = Result("AUTH", "A", "tt-user authenticates to the timeout-test realm",
                   "Authentication succeeds; an SSO token is issued")
        token = self.fresh_sso()
        st = session_type(token)
        self.session_type = st
        if token:
            r.verdict = PASS
            r.observed = f"SSO token issued ({st}, len={len(token)})"
            r.detail = {"sessionType": st, "tokenLen": len(token)}
        else:
            r.verdict = FAIL
            r.observed = "no tokenId returned"
        return r

    def t_validate_live(self):
        r = Result("AM-VALID-LIVE", "S1", "Live AM session validates (refresh=false)",
                   "valid=true")
        token = self.fresh_sso()
        valid, _ = self.am.validate(token)
        r.verdict = PASS if valid else FAIL
        r.observed = f"valid={valid}"
        return r

    def t_session_info(self):
        r = Result("SESSION-INFO", "S", "getSessionInfo reports idle/max remaining",
                   "idle and max remaining are present and > 0")
        token = self.fresh_sso()
        info = self.am.session_info(token)
        now = time.time() * 1000
        idle_exp = info.get("maxIdleExpirationTime")
        max_exp = info.get("maxSessionExpirationTime")

        def left(ts):
            if not ts:
                return None
            try:
                dt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return max(0, round((dt.timestamp() * 1000 - now) / 1000))
            except Exception:
                return None
        idle_left, max_left = left(idle_exp), left(max_exp)
        r.detail = {"idleLeftSec": idle_left, "maxLeftSec": max_left}
        if (idle_left or 0) > 0 and (max_left or 0) > 0:
            r.verdict = PASS
        else:
            r.verdict = FAIL
        r.observed = f"idleLeft={idle_left}s maxLeft={max_left}s"
        return r

    def t_rpc_login(self):
        r = Result("RPC-LOGIN", "D", "RP C (confidential) OIDC login via app6",
                   "RP C authenticated; id/access/refresh tokens + sid/sub present")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        s = self.login_rp(jar, "c")
        toks = s.get("tokens") or {}
        ok = bool(s.get("authenticated") and toks.get("access_token")
                  and toks.get("id_token") and s.get("sub"))
        r.verdict = PASS if ok else FAIL
        r.detail = {"authenticated": s.get("authenticated"), "sub": s.get("sub"),
                    "hasRefresh": bool(toks.get("refresh_token"))}
        r.observed = f"authenticated={s.get('authenticated')} sub={s.get('sub')}"
        return r

    def t_rpd_login(self):
        r = Result("RPD-LOGIN", "D", "RP D (public PKCE) OIDC login via app6",
                   "RP D authenticated; tokens present")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        s = self.login_rp(jar, "d")
        ok = bool(s.get("authenticated") and (s.get("tokens") or {}).get("access_token"))
        r.verdict = PASS if ok else FAIL
        r.observed = f"authenticated={s.get('authenticated')} sub={s.get('sub')}"
        return r

    def t_prompt_none_live(self):
        r = Result("PROMPT-NONE-LIVE", "O5", "prompt=none with a live AM session",
                   "Silent authentication succeeds (no login_required)")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        resp = self.app6.rp_login(jar, "c", prompt="none")
        body = resp.body or ""
        s = self.app6.rp_status(jar, "c")
        if "login_required" in body:
            r.verdict = FAIL
            r.observed = "login_required returned despite live session"
        elif s.get("authenticated"):
            r.verdict = PASS
            r.observed = "silent auth succeeded"
        else:
            r.verdict = FAIL
            r.observed = "prompt=none did not authenticate"
        return r

    def t_api_e_introspect_live(self):
        r = Result("API-E-INTROSPECT-LIVE", "E", "API E introspection accepts a live token",
                   "accepted=true (token active)")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        self.login_rp(jar, "c")
        res = self.app6.api_e(jar, "introspect", rp="c")
        accepted = bool(res.get("accepted"))
        r.verdict = PASS if accepted else FAIL
        r.observed = f"accepted={accepted}"
        return r

    def t_api_e_jwt_live(self):
        r = Result("API-E-JWT-LIVE", "E", "API E local-JWT validation of a live token (informational)",
                   "Informational: JWT accepted if client-based JWT tokens are configured, else 'not a JWT'")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        self.login_rp(jar, "c")
        res = self.app6.api_e(jar, "jwt", rp="c")
        r.verdict = INFO
        r.detail = {"accepted": res.get("accepted"), "reason": res.get("reason")}
        r.observed = f"accepted={res.get('accepted')} reason={res.get('reason')}"
        return r

    def t_refresh_live(self):
        r = Result("REFRESH-LIVE", "T", "Refresh-token grant with a valid session",
                   "Refresh succeeds; a new access token is issued")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        s = self.login_rp(jar, "c")
        if not (s.get("tokens") or {}).get("refresh_token"):
            r.verdict = SKIP
            r.observed = "no refresh token issued to RP C"
            return r
        res = self.app6.probe(jar, "/probe/refresh", {"rp": "c"})
        ok = bool(res.get("ok"))
        r.verdict = PASS if ok else FAIL
        r.observed = f"refresh ok={ok} status={res.get('status')}"
        return r

    def t_o6_local_logout(self):
        r = Result("O6-LOCAL-LOGOUT", "O6", "RP-local logout is NOT global (negative control)",
                   "RP C local session cleared, but AM session stays valid and RP D stays signed in")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        self.login_rp(jar, "c")
        self.login_rp(jar, "d")
        self.app6.rp_local_logout(jar, "c")
        valid, _ = self.am.validate(token)
        sc = self.app6.rp_status(jar, "c")
        sd = self.app6.rp_status(jar, "d")
        ok = valid and sc.get("loggedOut") and sd.get("authenticated")
        r.verdict = PASS if ok else FAIL
        r.detail = {"amValid": valid, "rpC_loggedOut": sc.get("loggedOut"),
                    "rpD_authenticated": sd.get("authenticated")}
        r.observed = (f"amValid={valid} rpC_loggedOut={sc.get('loggedOut')} "
                      f"rpD_auth={sd.get('authenticated')}")
        return r

    def t_o1_rp_initiated_logout(self):
        r = Result("O1-RP-INITIATED-LOGOUT", "O1", "RP-initiated logout (end-session) is global",
                   "AM session invalid; RP C cleared; RP D cleared via back-channel")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        self.login_rp(jar, "c")
        self.login_rp(jar, "d")
        self.app6.rp_initiated_logout(jar, "c")
        valid, _ = self.am.validate(token)
        c_out, sc = poll_logged_out(self.app6, jar, "c")
        d_out, sd = poll_logged_out(self.app6, jar, "d")
        ok = (not valid) and c_out and d_out
        r.verdict = PASS if ok else FAIL
        r.detail = {"amValid": valid, "rpC_loggedOut": sc.get("loggedOut"),
                    "rpD_loggedOut": sd.get("loggedOut")}
        r.observed = (f"amValid={valid} rpC_out={c_out} rpD_out(back-channel)={d_out}")
        return r

    def t_o2_am_rest_logout(self):
        r = Result("O2-AM-REST-LOGOUT", "O2", "AM REST logout triggers back-channel logout to RPs",
                   "AM session invalid; RP C and RP D cleared via back-channel")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        self.login_rp(jar, "c")
        self.login_rp(jar, "d")
        self.am.logout_token(token)
        valid, _ = self.am.validate(token)
        c_out, sc = poll_logged_out(self.app6, jar, "c")
        d_out, sd = poll_logged_out(self.app6, jar, "d")
        ok = (not valid) and c_out and d_out
        r.verdict = PASS if ok else FAIL
        r.detail = {"amValid": valid, "rpC_loggedOut": sc.get("loggedOut"),
                    "rpD_loggedOut": sd.get("loggedOut")}
        r.observed = f"amValid={valid} rpC_out(back-channel)={c_out} rpD_out(back-channel)={d_out}"
        return r

    def t_o8_logout_by_user(self):
        r = Result("O8-LOGOUT-BY-USER", "O8/T6", "logoutByUser invalidates the user's session",
                   "AM session invalid; RP C cleared")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        self.login_rp(jar, "c")
        self.am.logout_by_user(self.cfg.tt_user)
        valid, _ = self.am.validate(token)
        c_out, sc = poll_logged_out(self.app6, jar, "c")
        ok = (not valid) and c_out
        r.verdict = PASS if ok else FAIL
        r.detail = {"amValid": valid, "rpC_loggedOut": sc.get("loggedOut")}
        r.observed = f"amValid={valid} rpC_out={c_out}"
        return r

    def t_t3_logout_at_residual(self):
        r = Result("T3-LOGOUT-AT", "T3", "Captured access token after AM logout: introspect vs local-JWT",
                   "Introspection rejects (active=false); local-JWT may accept until exp (documented residual)")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        s = self.login_rp(jar, "c")
        at = (s.get("tokens") or {}).get("access_token")
        if not at:
            r.verdict = SKIP
            r.observed = "no access token captured"
            return r
        self.am.logout_token(token)
        intro = self.app6.api_e(jar, "introspect", token=at)
        jwt = self.app6.api_e(jar, "jwt", token=at)
        introspect_rejected = not bool(intro.get("accepted"))
        r.verdict = PASS if introspect_rejected else FAIL
        r.detail = {"introspect_accepted": intro.get("accepted"),
                    "jwt_accepted": jwt.get("accepted"), "jwt_reason": jwt.get("reason")}
        r.observed = (f"introspect_rejected={introspect_rejected} "
                      f"localJwt_accepted={jwt.get('accepted')} (residual)")
        return r

    def t_t4_refresh_after_logout(self):
        r = Result("T4-REFRESH-AFTER-LOGOUT", "T4", "Refresh after AM logout is rejected",
                   "Refresh fails (grant revoked by logout)")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        s = self.login_rp(jar, "c")
        if not (s.get("tokens") or {}).get("refresh_token"):
            r.verdict = SKIP
            r.observed = "no refresh token issued"
            return r
        self.am.logout_token(token)
        res = self.app6.probe(jar, "/probe/refresh", {"rp": "c"})
        failed = not bool(res.get("ok"))
        r.verdict = PASS if failed else FAIL
        r.observed = f"refresh ok={res.get('ok')} status={res.get('status')}"
        return r

    def t_t5_revoke_rt(self):
        r = Result("T5-REVOKE-RT", "T5", "Revoking the refresh token revokes the grant",
                   "Post-revoke refresh fails; introspection of captured access token rejects")
        token = self.fresh_sso()
        jar = self.app6.new_jar_with_sso(token)
        s = self.login_rp(jar, "c")
        toks = s.get("tokens") or {}
        at = toks.get("access_token")
        if not toks.get("refresh_token"):
            r.verdict = SKIP
            r.observed = "no refresh token issued"
            return r
        self.app6.probe(jar, "/probe/revoke", {"rp": "c", "type": "refresh"})
        res = self.app6.probe(jar, "/probe/refresh", {"rp": "c"})
        refresh_failed = not bool(res.get("ok"))
        intro = self.app6.api_e(jar, "introspect", token=at) if at else {"accepted": None}
        intro_rejected = not bool(intro.get("accepted"))
        ok = refresh_failed and intro_rejected
        r.verdict = PASS if ok else FAIL
        r.detail = {"refresh_failed": refresh_failed, "introspect_accepted": intro.get("accepted")}
        r.observed = f"refresh_failed={refresh_failed} introspect_rejected={intro_rejected}"
        return r

    def t_g1_ig_nocache(self):
        r = Result("G1-IG-NOCACHE", "G1", "IG App A (no cache) rejects immediately after AM logout",
                   "Pre-logout returns protected content; post-logout redirects to AM login")
        token = self.fresh_sso()
        pre = self.app6.protected_get(token, "/protected/a")
        pre_ok = pre.status == 200 and "Protected content delivered" in (pre.body or "")
        self.am.logout_token(token)
        post = self.app6.protected_get(token, "/protected/a")
        post_rejected = (post.status in (301, 302, 303, 307, 401, 403)) or \
                        ("Protected content delivered" not in (post.body or ""))
        ok = pre_ok and post_rejected
        r.verdict = PASS if ok else FAIL
        r.detail = {"preStatus": pre.status, "postStatus": post.status}
        r.observed = f"pre={pre.status}(content={pre_ok}) post={post.status}(rejected={post_rejected})"
        return r

    def t_g3_ig_cache(self):
        r = Result("G3-IG-CACHE", "G3", "IG App B (cached) after AM logout within CACHE_TTL (informational)",
                   "Documented stale window: App B may still return content until the cache TTL expires")
        token = self.fresh_sso()
        pre = self.app6.protected_get(token, "/protected/b")
        pre_ok = pre.status == 200 and "Protected content delivered" in (pre.body or "")
        self.am.logout_token(token)
        post = self.app6.protected_get(token, "/protected/b")
        stale = post.status == 200 and "Protected content delivered" in (post.body or "")
        r.verdict = INFO
        r.detail = {"preStatus": pre.status, "postStatus": post.status, "stale": stale}
        r.observed = f"pre={pre.status}(content={pre_ok}) post={post.status} stale={stale}"
        return r

    def t_s1_idle(self):
        r = Result("S1-IDLE", "S1/O3", "AM idle timeout invalidates the session (timed)",
                   "After the idle window: server-side session becomes invalid; client-side is informational")
        token = self.fresh_sso()
        st = session_type(token)
        jar = self.app6.new_jar_with_sso(token)
        self.login_rp(jar, "c")
        info = self.am.session_info(token)
        now = time.time() * 1000
        idle_exp = info.get("maxIdleExpirationTime")
        try:
            dt = _dt.datetime.fromisoformat(idle_exp.replace("Z", "+00:00"))
            idle_left = (dt.timestamp() * 1000 - now) / 1000.0
        except Exception:
            idle_left = None
        if idle_left is None:
            r.verdict = SKIP
            r.observed = "could not read idle expiry"
            return r
        wait = idle_left + self.args.grace
        if wait > self.args.max_wait:
            r.verdict = SKIP
            r.observed = (f"idle window {idle_left:.0f}s + grace exceeds --max-wait "
                          f"{self.args.max_wait}s (set a shorter profile, e.g. idle-first/race)")
            return r
        time.sleep(max(0, wait))
        valid, _ = self.am.validate(token)
        c_out, sc = poll_logged_out(self.app6, jar, "c", timeout_s=8)
        r.detail = {"sessionType": st, "idleWaitSec": round(wait, 1),
                    "amValid": valid, "rpC_loggedOut": sc.get("loggedOut")}
        if st == "server-side":
            ok = (not valid)
            r.verdict = PASS if ok else FAIL
            r.observed = f"server-side: amValid={valid} rpC_out={c_out} (waited {wait:.0f}s)"
        else:
            r.verdict = INFO
            r.observed = (f"client-side: amValid={valid} rpC_out={c_out} (waited {wait:.0f}s) "
                          f"- AM has no server-side idle reaper for stateless sessions")
        return r

    # -- registry ------------------------------------------------------------
    def all_tests(self):
        instant = [
            self.t_auth, self.t_validate_live, self.t_session_info,
            self.t_rpc_login, self.t_rpd_login, self.t_prompt_none_live,
            self.t_api_e_introspect_live, self.t_api_e_jwt_live, self.t_refresh_live,
            self.t_o6_local_logout, self.t_o1_rp_initiated_logout,
            self.t_o2_am_rest_logout, self.t_o8_logout_by_user,
            self.t_t3_logout_at_residual, self.t_t4_refresh_after_logout,
            self.t_t5_revoke_rt, self.t_g1_ig_nocache, self.t_g3_ig_cache,
        ]
        timed = [self.t_s1_idle]
        return instant, timed

    def run(self):
        instant, timed = self.all_tests()
        tests = list(instant)
        if self.args.include_timed:
            tests += timed
        wanted = None
        if self.args.only:
            wanted = {x.strip().upper() for x in self.args.only.split(",") if x.strip()}

        results = []
        for fn in tests:
            start = time.time()
            try:
                res = fn()
            except Exception as e:  # never let one test abort the suite
                res = Result(getattr(fn, "__name__", "?"), "?", fn.__name__,
                             "test executed without raising")
                res.verdict = FAIL
                res.observed = f"exception: {e}"
            res.duration_ms = int((time.time() - start) * 1000)
            if wanted is not None and res.id.upper() not in wanted:
                continue
            results.append(res)
            line = f"  [{res.verdict:<4}] {res.id:<24} {res.matrix:<7} {res.observed}"
            print(line)
        return results


# ---------------------------------------------------------------------------
# Persistence + reporting
# ---------------------------------------------------------------------------
def summarize(results):
    counts = {PASS: 0, FAIL: 0, SKIP: 0, INFO: 0}
    for r in results:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    scored = counts[PASS] + counts[FAIL]
    pass_rate = (counts[PASS] / scored) if scored else None
    return {"counts": counts, "scored": scored, "passRate": pass_rate}


def persist(cfg, results, summ, session_type_str):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run = {
        "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        "side": cfg.side, "amUrl": cfg.am_url, "app6": cfg.app6_base,
        "realm": cfg.realm_name, "sessionType": session_type_str,
        "summary": summ, "results": [r.to_dict() for r in results],
    }
    run_path = RESULTS_DIR / f"run-{ts}-{cfg.side}.json"
    run_path.write_text(json.dumps(run, indent=2))

    # Long-format CSV (one row per test per run) for easy spreadsheet averaging.
    csv_path = RESULTS_DIR / "results.csv"
    new = not csv_path.exists()
    with csv_path.open("a") as f:
        if new:
            f.write("run_ts,side,session_type,test_id,matrix,verdict,duration_ms\n")
        for r in results:
            f.write(f"{run['timestamp']},{cfg.side},{session_type_str},"
                    f"{r.id},{r.matrix},{r.verdict},{r.duration_ms}\n")
    return run_path


def aggregate(side_filter=None):
    files = sorted(glob.glob(str(RESULTS_DIR / "run-*.json")))
    runs = []
    for fp in files:
        try:
            data = json.loads(Path(fp).read_text())
        except Exception:
            continue
        if side_filter and data.get("side") != side_filter:
            continue
        runs.append(data)
    if not runs:
        print("No stored runs to aggregate (run the suite first).")
        return

    # Per-test aggregation.
    per = {}  # id -> dict
    order = []
    for run in runs:
        for r in run["results"]:
            tid = r["id"]
            if tid not in per:
                per[tid] = {"matrix": r["matrix"], "title": r["title"],
                            "PASS": 0, "FAIL": 0, "SKIP": 0, "INFO": 0,
                            "durations": []}
                order.append(tid)
            per[tid][r["verdict"]] = per[tid].get(r["verdict"], 0) + 1
            per[tid]["durations"].append(r.get("duration_ms", 0))

    print()
    print(f"Aggregate over {len(runs)} run(s)"
          + (f" [side={side_filter}]" if side_filter else ""))
    print("=" * 96)
    print(f"{'TEST':<24}{'MATRIX':<8}{'PASS':>5}{'FAIL':>5}{'SKIP':>5}{'INFO':>5}"
          f"{'PASS%':>8}{'AVG ms':>9}")
    print("-" * 96)
    overall_rates = []
    for tid in order:
        p = per[tid]
        scored = p["PASS"] + p["FAIL"]
        rate = (p["PASS"] / scored * 100) if scored else None
        if rate is not None:
            overall_rates.append(rate)
        avg_ms = round(sum(p["durations"]) / len(p["durations"])) if p["durations"] else 0
        rate_s = f"{rate:6.1f}%" if rate is not None else "    n/a"
        print(f"{tid:<24}{p['matrix']:<8}{p['PASS']:>5}{p['FAIL']:>5}{p['SKIP']:>5}"
              f"{p['INFO']:>5}{rate_s:>8}{avg_ms:>9}")
    print("-" * 96)

    # Per-run pass rate, then the average of those.
    run_rates = []
    for run in runs:
        s = run.get("summary", {})
        pr = s.get("passRate")
        if pr is not None:
            run_rates.append(pr * 100)
    if run_rates:
        avg = sum(run_rates) / len(run_rates)
        mn, mx = min(run_rates), max(run_rates)
        print(f"Per-run scored pass rate: avg {avg:.1f}%  (min {mn:.1f}%, max {mx:.1f}%, "
              f"n={len(run_rates)})")
    print("=" * 96)

    # averages.csv
    out = RESULTS_DIR / "averages.csv"
    with out.open("w") as f:
        f.write("test_id,matrix,runs,pass,fail,skip,info,pass_rate_pct,avg_duration_ms\n")
        for tid in order:
            p = per[tid]
            scored = p["PASS"] + p["FAIL"]
            rate = (p["PASS"] / scored * 100) if scored else ""
            avg_ms = round(sum(p["durations"]) / len(p["durations"])) if p["durations"] else 0
            total = p["PASS"] + p["FAIL"] + p["SKIP"] + p["INFO"]
            rate_s = f"{rate:.1f}" if rate != "" else ""
            f.write(f"{tid},{p['matrix']},{total},{p['PASS']},{p['FAIL']},"
                    f"{p['SKIP']},{p['INFO']},{rate_s},{avg_ms}\n")
    print(f"Wrote {out}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="app6 session-timeout test harness")
    ap.add_argument("--side", choices=["org", "com"], default="org",
                    help="target stack (default org)")
    ap.add_argument("--runs", type=int, default=1, help="number of runs to execute")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="seconds to sleep between runs")
    ap.add_argument("--include-timed", action="store_true",
                    help="also run the timed idle test (S1) within --max-wait")
    ap.add_argument("--max-wait", type=float, default=180.0,
                    help="max seconds to wait for a timed test (default 180)")
    ap.add_argument("--grace", type=float, default=8.0,
                    help="extra seconds added after a timeout window (default 8)")
    ap.add_argument("--only", default="",
                    help="comma-separated test ids to run (e.g. O1-RP-INITIATED-LOGOUT,G1-IG-NOCACHE)")
    ap.add_argument("--verify", action="store_true",
                    help="verify TLS against the lab CA (default: unverified, matching the lab's curl/bootstrap scripts)")
    ap.add_argument("--aggregate", action="store_true",
                    help="aggregate all stored runs and exit")
    ap.add_argument("--list", action="store_true", help="list test cases and exit")
    args = ap.parse_args()

    if args.aggregate:
        aggregate(side_filter=None)
        return 0

    cfg = Config(args.side, args)

    if args.list:
        suite = Suite(cfg, args)
        instant, timed = suite.all_tests()
        print("Instant tests:")
        for fn in instant:
            print(f"  - {fn.__name__}")
        print("Timed tests (need --include-timed):")
        for fn in timed:
            print(f"  - {fn.__name__}")
        return 0

    print(f"Session-timeout test harness  side={args.side}  AM={cfg.am_url}")
    print(f"  app6={cfg.app6_base}  realm={cfg.realm_name}  user={cfg.tt_user}")
    if args.include_timed:
        print(f"  timed idle test enabled (max-wait={args.max_wait}s, grace={args.grace}s)")
    print()

    last_summ = None
    for i in range(args.runs):
        print(f"--- run {i + 1}/{args.runs} ---")
        suite = Suite(cfg, args)
        results = suite.run()
        summ = summarize(results)
        last_summ = summ
        path = persist(cfg, results, summ, suite.session_type)
        c = summ["counts"]
        pr = summ["passRate"]
        pr_s = f"{pr * 100:.1f}%" if pr is not None else "n/a"
        print(f"  => PASS {c[PASS]}  FAIL {c[FAIL]}  SKIP {c[SKIP]}  INFO {c[INFO]}  "
              f"| scored pass rate {pr_s}  | session={suite.session_type}")
        print(f"  saved {path.name}")
        if i < args.runs - 1 and args.sleep > 0:
            time.sleep(args.sleep)
        print()

    if args.runs > 1:
        aggregate(side_filter=cfg.side)
    return 0


if __name__ == "__main__":
    sys.exit(main())
