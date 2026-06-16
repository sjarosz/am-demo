# Session timeout, logout & OIDC SLO test console (app6)

`app6.jrsz.org` is a self-contained lab for testing AM/IG session timeout,
inactivity, explicit logout, and OIDC single logout. It implements the practical
test plan: AM is the source of truth, IG must not be the weak link, and OIDC
token invalidation is treated separately from SSO session invalidation.

Open it at **https://app6.jrsz.org/** (or the twin **https://app6.jrsz.com:8444/**).

The console is a Node/Express app behind PingGateway. The dashboard, OIDC RPs,
API E and the probe endpoints are ungated; `/protected/a` and `/protected/b` are
IG-protected; `/logout` is a global IG logout.

## Components

| Component | What it is | Where |
|---|---|---|
| `timeout-test` realm | Isolated realm with short session settings; SSO storage type set by `TIMEOUT_REALM_STATELESS` (default `true` = client-side/stateless JWT) | created by `config/amster/oauth2-demo/run-bootstrap.sh` |
| `tt-user` | Realm-local test user (so logins create the SSO session **in** `timeout-test`, exercising its session type) | created by `run-bootstrap.sh`; creds via `TIMEOUT_TEST_USER` / `TIMEOUT_TEST_USER_PASSWORD` |
| IG App A | IG-protected path, **no** session cache (every request revalidates AM) | `GET /protected/a` via `AmServiceTimeout` |
| IG App B | IG-protected path, **cached** AmService (CACHE_TTL=1m; WebSocket notifications off by default) | `GET /protected/b` via `AmServiceCached` |
| OIDC RP C | Confidential OIDC client, back-channel logout, can introspect/revoke | `/rp/c/*`, client `rp-c-app` |
| OIDC RP D | Public PKCE OIDC client, back-channel logout (proves cross-RP logout) | `/rp/d/*`, client `rp-d-app` |
| API E | Resource server: introspection mode + local-JWT mode | `GET /api/e/resource?mode=introspect|jwt` |
| Back-channel collector | Logs received logout tokens (`sid`/`sub`/reason) and clears local RP sessions | `POST /rp/{c,d}/backchannel`, SSE feed at `/events` |
| Probes | Server-side helpers the dashboard calls | `/probe/*` |

Each OIDC client registers a back-channel logout URI and
`backchannel_logout_session_required=true`, so AM adds `sid` to the ID token and
delivers logout tokens when the authenticated session becomes invalid.

## Instrumentation strip (session type)

The sticky strip at the top shows live state polled from `/probe/state`:
`AM cookie`, `AM session` (valid/invalid), **`AM type`**, `idle left`, `max left`,
and the RP C/RP D local-session state.

AM has three kinds of session (see
[PingAM > Sessions](https://docs.pingidentity.com/pingam/8.1/am-sessions/preface.html)),
and the **`AM type`** chip indicates which one the current `iPlanetDirectoryPro`
cookie represents (hover the chip for details):

| Type | Indicator | How it is detected | Notes |
|---|---|---|---|
| **Server-side** | `Server-side (CTS)` (blue) | Short opaque reference token, **no embedded JWT** | State stored in the Core Token Service (DS). Centrally revocable; supports quotas; instant logout. |
| **Client-side** | `Client-side (JWT)` (amber) | Token **embeds a JWT** (`eyJ...` header; `enc` header ⇒ JWE, else JWS); cookie is large | Session state lives in the cookie; AM keeps only a denylist (when session blacklisting is enabled) for logout. |
| **In-memory** | (legend only) | n/a at SSO time | Transient journey/authentication session held in the AM instance's heap *during* login (an `authId`), not the SSO cookie. |

> **Detection note:** AM wraps **both** session types in the same "C66" SSO
> envelope, so the presence of `*` separators does **not** distinguish them
> (an earlier heuristic got this wrong). The reliable signal is whether the
> token **embeds a JWT**: a stateless/client-side session serializes the whole
> session as a JWT (`...*eyJhbGci...`, often ~2&nbsp;KB), whereas a server-side
> session is just a short opaque reference key (~100–200 chars).

The cookie is the authoritative runtime signal, independent of where the realm
stores its session config. The type is also returned by the
`POST /probe/am-session-info` probe (`sessionType`).

## Configuration knobs (timeout profiles)

Use `scripts/set-timeout-profile.sh <profile>` to set the realm session timeouts
(AM idle/max, minutes) and the RP token lifetimes (access/refresh/ID, seconds).
Re-login afterwards so a fresh session and fresh tokens pick up the new values.

| Profile | AM idle | AM max | AT | RT | ID | First expirer under test |
|---|---|---|---|---|---|---|
| `baseline` | 6m | 20m | 300s | 1800s | 300s | balanced default |
| `idle-first` | 2m | 20m | 3600s | 86400s | 3600s | AM idle |
| `max-first` | 10m | 4m | 3600s | 86400s | 3600s | AM max (keep active) |
| `app-first` | 10m | 30m | 3600s | 86400s | 3600s | app/RP local idle |
| `token-first` | 30m | 120m | 60s | 180s | 60s | access/refresh token |
| `race` | 2m | 20m | 120s | 120s | 120s | near-simultaneous |

For jrsz.com, source `.env.com` first:
`set -a; . ./.env.com; set +a; ./scripts/set-timeout-profile.sh baseline`.

PingGateway warns that an AM `sessionIdleRefresh.interval` below one minute can
adversely affect AM performance, so keep AM idle comfortably above that.

## Matrix-to-infrastructure mapping

The console renders the full matrix as cards with run buttons and an evidence
panel. The meaningful permutations are which clock expires first and which actor
initiates logout.

- **A. AM server-side timeout (S1-S5):** App A/B + RP C/D, then idle / stay
  active / refresh via IG. Use `AM validate (refresh=false)` and `AM session info`
  to watch idle/max remaining; `prompt=none` must fail with `login_required`
  once AM is invalid.
- **B. Client-side / stateless (C1-C5):** optional. Documented here; enable the
  client-side variant only if client-side AM sessions are in scope (see below).
- **C. IG cache & gateway-session (G1-G4):** App A (no cache, revalidates every
  request) vs App B (cached). WebSocket notifications are **off by default** so
  the gateway always boots (an enabled notification service is a fatal IG startup
  dependency, and would crash the gateway whenever the `timeout-test` realm/agent
  is absent - fresh clone or after `down -v`). With notifications off, App B can
  serve stale content for up to CACHE_TTL after AM logout (the G3 stale-cache
  behavior). To demo instant eviction (G2), set `notifications.enabled=true` on
  `AmServiceCached` in `config/gateway/config.json` **after** bootstrap, re-run
  `./scripts/render-com-config.sh`, and restart the gateway. `/logout` exercises
  the IG `logoutExpression` route.
- **D. OIDC session & logout (O1-O8):** RP-initiated logout (`/rp/c/rp-initiated-logout`),
  AM REST logout, idle/max, prompt=none session check, **local-only logout
  (O6 negative control)**, and `logoutByUser` for multi-device (O8).
- **E. Token behavior (T1-T6):** capture an access token, then introspect vs
  local-JWT validate it through API E; attempt refresh-token reuse; revoke RT;
  user-wide invalidation.

## Per-row procedure

For every matrix row:

1. **Reset state.** Click *Reset this browser* (AM logout + clears local RP C/D
   sessions and cookies). For multi-device tests use a second browser/profile.
2. **Set the profile.** `./scripts/set-timeout-profile.sh <profile>` so exactly
   one clock is the intended first expirer. Verify the effective timeouts with
   *AM session info* after login (tree/node settings can override realm values).
3. **Authenticate once.** Open IG App A, then App B, then RP C, then RP D.
4. **Capture artifacts.** *RP C status* shows the ID token (`sid`, `sub`),
   access and refresh tokens; *Capture RP C access token* stores one for replay.
5. **Trigger the condition.** Wait idle / stay active / call a logout / expire a
   token / revoke, depending on the row.
6. **Probe everywhere.** After the expected invalidation plus a small grace:
   - App A / App B must stop returning protected content (re-open the tab).
   - *RP C status* / *RP D status* must show signed out / logged out.
   - *AM validate (refresh=false)* must be invalid.
   - `prompt=none` must fail with `login_required`.
   - API E with the captured token must behave per your token design.
   - Refresh-token reuse must fail if logout revokes the grant.
7. **Verify cause.** Watch the back-channel logout SSE feed for `sid`/`sub`/reason
   (e.g. `SESSION_IDLE_TIMEOUT`, `SESSION_MAX_TIMEOUT`, `CLIENT_LOGOUT`) and
   confirm it matches the trigger. Cross-check AM audit and IG logs.

## Explicit logout entry points

| Path | Console action |
|---|---|
| AM browser/self-service logout | *Open AM login* tab, then log out in AM XUI |
| AM REST logout | *AM REST logout* probe (`sessions?_action=logout`) |
| IG logout route | *IG /logout route* (logoutExpression + landing page) |
| OIDC RP-initiated logout | *RP C end-session* (`id_token_hint` + `post_logout_redirect_uri`) |
| User-wide logout | *AM logoutByUser* (kills all sessions for the user) |
| Application-only logout | *RP C local logout* (negative control: AM session survives) |

## Pass/fail criteria

A row passes only if all hold:

- **AM source of truth:** once AM says invalid, no IG route returns protected content.
- **No stale IG authorization:** the IG cache does not allow access after AM logout/timeout.
- **OIDC RPs clear local state:** every RP that held an ID token for that AM
  session clears the matching local session by `sid`/`sub`.
- **No silent SSO after invalidation:** OIDC re-entry cannot silently mint tokens.
- **Token behavior is explicit:** old access/refresh tokens behave exactly per
  the documented design. A locally validated JWT accepted until `exp` must be
  documented as accepted residual risk or remediated (introspection / short TTL).
- **Multi-device scope is correct:** session-specific logout kills only that AM
  session; `logoutByUser` kills all of the user's sessions.
- **Negative controls behave:** app-only local logout, AT/ID expiry, and RP local
  idle do not falsely look like global logout.

## Key finding to validate

SSO session invalidation and OIDC token invalidation are **not** automatically the
same thing. The AM SSO cookie/session can be dead everywhere while an already
issued self-contained JWT access token still works until `exp`, unless API E
validates it against live server state (introspection / revocation). API E's two
modes make this explicit:

- `mode=introspect` reflects live AM state and rejects revoked/logged-out tokens.
- `mode=jwt` only checks signature + `exp` and will accept a captured token after
  logout until it expires (T3 residual risk).

## SSO session storage: client-side (default) vs server-side

The `timeout-test` realm's SSO session storage is controlled by
`TIMEOUT_REALM_STATELESS` (default `true`). The bootstrap applies it via
`general.statelessSessionsEnabled` on the realm authentication config, and the
dashboard's **AM type** chip reflects whichever mode is live.

- **Client-side (stateless, `true`, default):** session state is serialized into
  the cookie as a JWT (AM wraps it in the C66 SSO envelope). AM keeps only a
  denylist (when session blacklisting is enabled) to honour logout/revocation.
  AM does **not** auto-terminate client-side sessions on idle the way it does
  server-side ones, so deterministic idle enforcement through IG additionally
  needs `AmSessionIdleTimeoutFilter` (idle tracking) plus denylisting in front of
  `SingleSignOnFilter`.
- **Server-side (CTS, `false`):** an opaque reference token; state lives in the
  Core Token Service (DS). Centrally revocable with deterministic idle/max
  enforcement and session quotas. Set `TIMEOUT_REALM_STATELESS=false` (in `.env`
  / `.env.com`) and re-run the bootstrap to switch the realm to this mode.

## Apply / rebuild

```bash
# Build and start app6 + gateway (jrsz.org)
docker compose up -d --build app6 gateway

# (Re)create the timeout-test realm, RP clients and short session settings
docker compose --profile bootstrap up --abort-on-container-exit amster-bootstrap

# Tune the active timeout profile, then re-login
./scripts/set-timeout-profile.sh baseline
```

For the jrsz.com twin, regenerate the gateway config and use the `-com` services:

```bash
./scripts/render-com-config.sh
docker compose up -d --build app6-com gateway-com
docker compose --profile bootstrap up --abort-on-container-exit amster-bootstrap-com
```
