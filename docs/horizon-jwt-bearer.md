# Local /bravo id_token → horizon AIC (OAuth 2.0 JWT bearer)

This connects the local lab to the frodo-accessible **horizon** AIC instance
(`https://openam-horizon.forgeblocks.com/am`). A user logs into the local AM
`/bravo` realm, gets an **id_token signed by a dedicated cert**, and replays that
id_token to horizon as an RFC 7523 assertion
(`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`) to obtain a horizon
access token.

Reference: <https://docs.pingidentity.com/pingoneaic/am-oauth2/oauth2-jwt-bearer-grant.html>

The local side is fully automated (`config/amster/oidc-horizon/`). The horizon
side is **yours to configure** — this repo does not modify horizon.

## 1. Local side (already automated)

```bash
scripts/generate-oidc-signing-key.sh      # run by generate-tls.sh on a fresh clone
# ... bootstrap (BOOTSTRAP_OIDC_HORIZON=true) ...
scripts/smoke_oidc_bravo.sh               # confirms id_token signed by the new cert
scripts/export_horizon_jwk.sh             # writes the public JWK Set for horizon
```

`scripts/export_horizon_jwk.sh` produces
`secrets/oidc-signing/bravo-oidc-rsa.jwks.json` — the public key with the exact
`kid` AM stamps on `/bravo` id_tokens.

Issuer (`iss`) of `/bravo` id_tokens:
```
https://am.jrsz.org:8443/am/oauth2/realms/root/realms/bravo
```

## 2. Horizon side (you configure, e.g. via the AIC console or frodo)

### a. Register a Trusted JWT Issuer
In the target horizon realm (e.g. `alpha`), create a **Trusted JWT Issuer**:
- **JWT Issuer**: the `iss` above.
- **JWK Set**: paste the contents of `bravo-oidc-rsa.jwks.json`.
  (Embedded key, because horizon cannot reach the lab's localhost jwk_uri.)
- **Allowed subjects**: the `sub` from a `/bravo` id_token (printed by
  `smoke_oidc_bravo.sh`) mapped to a horizon identity, or allow-list as needed.
  Note: AM 8.1.0 emits `sub` in its universal-id form, e.g. `(usr!demo-user)`,
  not the bare username — account for that in horizon's subject mapping.

### b. Audience (`aud`) — important
AIC expects the assertion `aud` to be **its own access_token endpoint**. A normal
AM login id_token has `aud = client_id` (`horizon-oidc-app`), and AM 8.1.0 cannot
rewrite a login id_token's `aud` (the OIDC claims script can't set `aud`).

So choose one:
- **Configure horizon to accept the id_token's `aud`** (the decision for this lab):
  set the Trusted JWT Issuer / provider to treat `horizon-oidc-app` as an
  acceptable audience. Simplest if your horizon config allows it.
- **Or** mint an `aud`-correct token locally via AM token-exchange
  (`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`,
  `requested_token_type=...id_token`, `audience=<horizon token endpoint>`; requires
  the `/bravo` provider `acceptAudienceParametersInTokenExchangeRequests=true` and
  the client `allowedAudienceValues` to include horizon's endpoint). Use this if
  horizon strictly requires `aud = <horizon token endpoint>`.

### c. Create the OAuth2 client for the grant
Create a horizon OAuth2 client with grant type **JWT Bearer**, a client_id/secret
for HTTP Basic auth, and the scopes you want to issue.

## 3. Exchange

```bash
ID_TOKEN="<id_token from smoke_oidc_bravo.sh>"
curl --request POST \
  --user 'CLIENT_ID:CLIENT_SECRET' \
  --data 'grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer' \
  --data "assertion=${ID_TOKEN}" \
  --data 'scope=openid' \
  'https://openam-horizon.forgeblocks.com/am/oauth2/realms/root/realms/alpha/access_token'
```

A successful response returns a horizon `access_token`.

`scripts/smoke_jwt_bearer_horizon.sh` wraps this (set `HORIZON_*` env vars first);
it only works once the Trusted JWT Issuer + client above exist in horizon.

## Rotation

Re-running `scripts/generate-oidc-signing-key.sh` after deleting
`secrets/oidc-signing/bravo-oidc.jceks` mints a new key (new `kid`); re-bootstrap,
re-run `export_horizon_jwk.sh`, and update the JWK Set in horizon.
