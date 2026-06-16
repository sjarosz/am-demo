# Single Logout (SLO) + smart login/logout buttons

This lab adds a "smart" Login/Logout button to the protected app pages (app1–app4).
The button reflects each app's live session state and drives a **global Single
Logout**: ending the shared AM SSO session logs the user out of every app at once.

## Behavior

- Each app page (`app1`–`app4`) shows a fixed pill in the top-right corner.
- **Signed in** → button reads **Log out**. Clicking it performs global SLO.
- **Signed out** → button reads **Log in**. Clicking it drives the SSO process.
- The widget polls its own host every 5 seconds (and on window focus), so when the
  AM session is ended from any app (or directly in AM), every open app page flips
  its button to **Log in** within a few seconds — true single logout.

## Why the behavior differs per app

| App | Session model | What "logged in" means |
|-----|---------------|------------------------|
| app1–app3 | IG `SingleSignOnFilter` gate; **no independent app session** | The shared AM SSO session (`iPlanetDirectoryPro` cookie on `jrsz.org`) is valid |
| app4 | Own Express session **plus** OIDC PKCE login | Local OIDC tokens exist **and** the shared AM session is still valid |
| app5 | Login Widget (OAuth public client), static nginx | The shared AM SSO session is valid (reported by IG `/__slo/status`); logout revokes the widget's OAuth tokens via the SDK |

app1–app3 share one AM SSO session, so their state is always identical. app4 is the
only app with a genuinely separate local session; it drops its local tokens when it
detects the AM session has been killed elsewhere (honors SLO).

> app1–app3 stay hard-gated. You only reach their pages with a live session, so the
> button starts as **Log out**. It flips to **Log in** in place when the AM session
> dies out-of-band (e.g. logout from app4 in another tab). A page reload while
> signed out goes through the IG gate to the AM login page as before.

## Same-origin control surface

Every app host exposes the same three endpoints (no AM CORS config needed):

| Endpoint | Method | app1–app3 (served by IG) | app4 (served by Express) | app5 (Login Widget) |
|----------|--------|--------------------------|--------------------------|---------------------|
| `/__slo/status` | GET | IG checks the AM session server-side via REST `getSessionInfo` using the forwarded cookie | app4 checks local tokens + AM `getSessionInfo` | IG checks the AM session (same handler as app1–3) |
| `/__slo/logout` | POST | IG calls AM REST `?_action=logout`, expires the shared cookie | app4 calls AM REST `?_action=logout`, destroys local session, expires the shared cookie | `user.logout()` (SDK token revoke + end session) then IG `?_action=logout` to clear the cookie |
| `/__slo/login` | GET | 302 to the app root → triggers the IG SSO gate | 302 to `/login` → starts OIDC PKCE | n/a — login opens the Login Widget modal in-page |

app5 reuses the shared `SloStatusHandler` / `SloLogoutHandler` from the IG heap via a
`DispatchHandler` in `config/gateway/routes/app5.json`; its page logic lives in
`apps/login-widget/src/main.js`.

All three responses are same-origin, so the browser sends credentials automatically.

## IG wiring

- `config/gateway/config.json` heap adds:
  - `AmClientHandler` — `ClientHandler` using `ClientTlsOptions` (trusts the lab CA).
  - `SloStatusHandler` / `SloLogoutHandler` — `ScriptableHandler`s running the Groovy
    scripts below, with `AmClientHandler` as their HTTP client.
  - `SloLoginHandler` — `StaticResponseHandler` that 302-redirects to the app root.
- `config/gateway/scripts/groovy/slo-status.groovy` — server-side AM session check.
- `config/gateway/scripts/groovy/slo-logout.groovy` — AM session logout + cookie clear.
- `config/gateway/routes/app1.json`–`app3.json` wrap the existing SSO `Chain` in a
  `DispatchHandler`. The `/__slo/*` paths are matched **before** the SSO gate so the
  control endpoints work even when the session is gone.

## AM logout service used

Global logout invokes AM's session logout REST service:

```
POST {AM}/json/realms/root/realms/alpha/sessions?_action=logout
Header: iPlanetDirectoryPro: <SSO token>
Header: Accept-API-Version: resource=5.1, protocol=1.0
```

Session status uses the matching `?_action=getSessionInfo` action.

## Deploy

The IG config is bind-mounted, so the gateway only needs a restart. app1–app5 bake
their content into images, so they must be rebuilt:

```bash
docker compose up -d --build app1 app2 app3 app4 app5
docker compose restart gateway
```

## Try it

1. Open `https://app4.jrsz.org/` (ungated) — button shows **Log in**.
2. Click **Log in** → complete AM login → button shows **Log out** with your username.
3. Open `https://app1.jrsz.org/` in another tab — you're already signed in (shared
   AM session), button shows **Log out**.
4. Click **Log out** on either page → within ~5s both tabs flip to **Log in**.
5. Click **Log in** on app1 → IG redirects through AM SSO and back.
