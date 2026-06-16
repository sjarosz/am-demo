# Cross-AM OIDC social login (jrsz.org &harr; jrsz.com)

Each `/alpha` realm is the **other** stack's social (OpenID Connect) identity
provider, symmetric to the SAML federation in `../saml/`. This folder holds the
canonical artifacts and an idempotent REST provisioner so a clean `git pull` +
bootstrap recreates the working state on both stacks.

## Roles (per side)

When provisioning runs on a side it configures BOTH roles:

- **OpenID Provider (OP)** — hosts a confidential OIDC client the PARTNER uses:
  `social-<partner>-rp`, redirect = partner AM base, `client_secret_basic`,
  implied consent, scopes `openid profile email`.
- **Social consumer (RP)** — a Social Identity Provider Service entry
  `<partner>Provider` (subtype `oidcConfig`) pointing at the partner AM, plus the
  social-only `SocialLogin` journey.

| Provision side | OP client it hosts | Consumer provider | Consumer client id | Well-known |
|---|---|---|---|---|
| org | `social-com-rp` (redirect com `/alpha` XUI) | `comProvider` | `social-org-rp` | com `/alpha` |
| com | `social-org-rp` (redirect org `/alpha` XUI) | `orgProvider` | `social-com-rp` | org `/alpha` |

Both stacks share `SOCIAL_ORG_RP_SECRET` and `SOCIAL_COM_RP_SECRET` (in `.env` /
`.env.com`) so the OP registration and the consumer provider config agree.

## Redirect URL must carry the realm (sub-realm gotcha)

The OIDC redirect URL is **not** the bare AM base. It is the realm-aware XUI URL:

```
https://am.jrsz.<side>:<port>/am/XUI/?realm=/alpha
```

registered identically on the OP client (`redirectionUris`) and the consumer
provider (`redirectURI`) — `provision.py` derives it as
`<AM base>/XUI/?realm=<XUI_REALM>` from `SOCIAL_REALM_PATH`.

Why: the `SocialLogin` journey lives in the **`/alpha` sub-realm**, reached via
`?realm=/alpha` (no DNS/realm alias, not the default realm). On redirect-back the
End-User UI (XUI) resumes the in-progress authentication using the realm from the
landing URL. ForgeRock's docs say to use the bare AM base because XUI restores the
realm from browser storage — but that only holds for the **default** realm or a
**DNS-aliased** realm. With a bare base URL here, XUI resolves to the **root**
realm and POSTs the resume to `/json/realms/root/authenticate`, where there is no
social auth in progress, so the browser lands on `…#failedLogin` ("Unable to
login"). Putting `realm=/alpha` in the redirect URL makes XUI resume in `/alpha`.
(REST/JSON callers that target `realms/root/realms/alpha` directly are unaffected,
which is why the headless smoketest passed while the browser failed.)

A bare-base alternative would be to give `/alpha` a DNS/realm alias so the realm
resolves from the hostname; that needs an extra hostname, cert SAN, and IG route
per side, so the realm-in-redirect form is used instead.

## Artifacts

- `provision.py` — admin auth, side detection from `AM_COOKIE_DOMAIN`, GET-then
  -PUT idempotent upserts (If-None-Match on create, If-Match on update; `_rev`
  is never echoed back because the OAuth2Client endpoint rejects it, and
  `userpassword` is stripped on update). Creates: the normalization script, the
  OP client, enables the Social Identity Provider Service, the consumer provider
  config, and the `SocialLogin` journey.
- `run-bootstrap.sh` — thin wrapper invoked by `docker/amster/docker-entrypoint.sh`
  behind `BOOTSTRAP_SOCIAL` (default true).
- `scripts/cross-am-oidc-normalization.groovy` — `SOCIAL_IDP_PROFILE_TRANSFORMATION`
  script (fixed id `9e1f4c7a-…`) mapping the partner's standard OIDC claims to
  AM's normalized profile, with `displayName`/`familyName`/`username` fallbacks so
  DS account creation (mandatory `cn`/`sn`) cannot fail.
- `scripts/cross-am-oidc-identity.groovy` — `SOCIAL_IDP_PROFILE_TRANSFORMATION`
  script (fixed id `7c2e5a18-…`) used by the handler node as its *Normalized
  Profile to Identity* mapping. Same as the built-in one **plus `uid` (= email)**;
  see "User matching & provisioning" below.
- `scripts/cross-am-oidc-email-match.groovy` — `AUTHENTICATION_TREE_DECISION_NODE`
  script (fixed id `5d3a9b2c-…`) that matches an EXISTING local account by `mail`;
  see "User matching & provisioning" below.
- `trees/SocialLogin.json` — the journey: `SelectIdPNode` (local auth off) &rarr;
  `SocialProviderHandlerNodeV2` (transform = the custom *Normalized Profile to
  Identity* script above). `ACCOUNT_EXISTS` &rarr; `SetSuccessUrlNode`;
  `NO_ACCOUNT` &rarr; **`ScriptedDecisionNode` (Match Existing User by Email)** with
  `found` &rarr; `SetSuccessUrlNode` and `notFound` &rarr;
  `ProvisionDynamicAccountNode` &rarr; `SetSuccessUrlNode` &rarr; Success. The
  `SocialProviderHandlerNodeV2` `SOCIAL_AUTH_INTERRUPTED` outcome = Failure.

## User matching & provisioning (email, not GUID)

A social login resolves to a local account in this order:

1. **By social alias** — the handler node matches an account already carrying the
   `<partner>Provider-<sub>` alias (`ACCOUNT_EXISTS`). This is how *repeat* logins
   of a federated user reuse their account.
2. **By email** — on `NO_ACCOUNT`, the *Match Existing User by Email* scripted
   decision node searches the realm identity store for an account whose `mail`
   equals the social profile's email. On a hit it switches the journey principal
   to that account (`found`), so e.g. `acarter` logging in cross-AM lands on the
   pre-existing local `acarter` instead of a brand-new account.
3. **Provision a new account** — only when neither matches (`notFound`). The
   account's **username is the email**, not a random UUID. The Provision Dynamic
   Account node's `DefaultAccountProvider` names the account from the `uid`
   attribute and falls back to a UUID when `uid` is absent; the custom identity
   script sets `uid = email`, so genuinely new social users get an email username.

Why a script for step 2: AM-standalone has **no built-in, non-IDM node** that
matches a user by an arbitrary attribute. The handler only searches by the social
alias; `IdentifyExistingUserNode` is IDM-only (NPEs without the IDM Integration
Service); and the realm identity store's `users-search-attribute` is
**single-valued** (`uid`), so `mail` cannot be added there. The scripting engine's
`idRepository` binding is built with an empty search-attribute set, so its
`getIdentity()` only resolves by `uid`. The script therefore builds its own
`ScriptIdentityRepository(store, {"mail"})` and reuses `getIdentity()` to resolve
by email, then sets the shared-state `username` to the matched account.

### Scripting sandbox whitelist (the one non-default bit)

To reach the realm identity store the decision-node script needs three classes
that are **not** in the stock `AUTHENTICATION_TREE_DECISION_NODE` whitelist;
`provision.py` adds them idempotently to the global scripting engine config:

| Class | Why |
|---|---|
| `com.sun.identity.authentication.service.AuthD` | get the realm's identity store (admin context) |
| `com.sun.identity.sm.DNMapper` | realm path (`/alpha`) &rarr; store DN |
| `com.sun.identity.idm.IdentityStoreImpl` | concrete type returned by the store lookup |

That is the **entire** extra surface this feature opens — no raw
`AMIdentityRepository` or admin-token access; the lookup result is the already
whitelisted `ScriptedIdentity`. (This is the documented exception to the
"no sandbox-broadening without sign-off" rule in `.cursor/rules/`.)

## Success URL = this side's PingGateway (IG) launchpad

Both success paths (`ACCOUNT_EXISTS` and a freshly provisioned account) pass
through a **`SetSuccessUrlNode`** that sets the post-login redirect to **this
side's IG launchpad**:

| Side | Success URL |
|---|---|
| org | `https://ig.jrsz.org/` |
| com | `https://ig.jrsz.com:8444/` |

The committed `trees/SocialLogin.json` carries a `@@SUCCESS_URL@@` placeholder;
`provision.py` substitutes the real value from `IG_BASE_URL` (per stack, in
`.env` / `.env.com`; falls back to the side default) at provision time, with a
trailing `/` so the bare host matches the `/*` goto pattern and lands on the
launchpad route (path `/`). AM only honors the redirect if the target is a
**valid goto destination**; the `oauth2-demo` bootstrap (which runs before this
one) already whitelists `${IG_BASE_URL}/*` in the realm Validation Service, so no
extra config is needed here.

## Re-provision / verify

```bash
# Provisioned automatically by the bootstrap profile; to run manually:
AM_SERVER_URL=https://am.jrsz.org:8443/am AM_ADMIN_PASSWORD=changeit \
  AM_COOKIE_DOMAIN=jrsz.org ./run-bootstrap.sh
AM_SERVER_URL=https://am.jrsz.com:9443/am AM_ADMIN_PASSWORD=changeit \
  AM_COOKIE_DOMAIN=jrsz.com ./run-bootstrap.sh

# End-to-end browser-style smoketest (both directions):
../../../scripts/smoke_social.sh
```

Browser check: launchpad &rarr; "OIDC external IDP login" &rarr; the partner login
page &rarr; lands authenticated on the consumer. A login resolves to a local
account by social alias, then by email, else provisions a new email-named account
(see "User matching & provisioning" above).

## Why the back-channel works in this lab

The consumer AM calls the partner AM's `access_token` / `userinfo` endpoints
server-to-server. Both AM containers share the `jrsz_net` Docker network and the
same CA truststore, and the published ports equal the internal ports, so one URL
serves both the browser redirect and the back-channel call.
