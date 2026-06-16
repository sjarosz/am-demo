# oidc-horizon: /bravo OIDC id_token signed by a horizon-trusted cert

This module makes a user logging into the local AM **`/bravo`** realm receive an
**id_token signed by a dedicated RSA certificate** whose public half is
registered in the frodo-accessible **horizon AIC** instance
(`https://openam-horizon.forgeblocks.com/am`). Horizon then accepts that id_token
as the assertion in an OAuth 2.0 **JWT bearer** grant
(`urn:ietf:params:oauth:grant-type:jwt-bearer`, RFC 7523).

## Why a dedicated realm + key

AM resolves the OIDC RS256 id_token signing key from the secret label
`am.services.oauth2.oidc.signing.RSA`, and **secret stores are realm-scoped**.
Putting the new key in a `/bravo` `KeyStoreSecretStore` confines it to `/bravo` —
`/alpha` (app4/app5) keeps signing with AM's global default key, untouched.

## What it provisions (idempotent REST, in `/bravo`)

1. The OAuth2/OIDC provider (cloned from `config/amster/oauth-oidc.service.json`).
2. A realm **`FileSystemSecretStore`** (`format: PLAIN`) at
   `security/secrets/bravo-oidc/` holding the keystore store/entry passwords.
   (The global default store is `ENCRYPTED_PLAIN`, so a dedicated PLAIN store is
   used rather than dropping plaintext into it.)
3. A realm **`KeyStoreSecretStore`** pointing at
   `security/keystores/bravo-oidc.jceks`.
4. A **mapping** `am.services.oauth2.oidc.signing.RSA` → alias `bravo-oidc-rsa`.
5. A dedicated public **PKCE OIDC client** (`horizon-oidc-app`).
6. The **demo user** in `/bravo`.

The signing keystore is generated on the host by
`scripts/generate-oidc-signing-key.sh` (run automatically by
`scripts/generate-tls.sh`) into `secrets/oidc-signing/bravo-oidc.jceks`, mounted
into the `amster-bootstrap` container at `/run/secrets/oidc`, and copied into the
shared `am-home` volume so the AM container can read it.

## Audience / subject (horizon side)

AM hard-codes a login id_token's `aud` to the client_id and `sub` from the
provider subject identifier; neither can be rewritten by an OIDC claims script in
AM 8.1.0. Per design, **horizon's Trusted JWT Issuer is configured to accept
AM's natural `aud`/`sub`** — see `docs/horizon-jwt-bearer.md`. We do not modify
horizon from this repo.

## Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `HORIZON_OIDC_REALM` | `bravo` | Target local realm |
| `HORIZON_OIDC_CLIENT_ID` | `horizon-oidc-app` | Dedicated client id |
| `HORIZON_OIDC_REDIRECT_URI` | `https://app6.jrsz.org/callback` | Client redirect (code parsed by the smoke script; no app required) |
| `OIDC_SIGNING_ALIAS` | `bravo-oidc-rsa` | Keystore alias / signing key |
| `DEMO_USER_NAME` / `DEMO_USER_PASSWORD` | from `.env` | `/bravo` login user |

Gated by `BOOTSTRAP_OIDC_HORIZON` (default `true`) in
`docker/amster/docker-entrypoint.sh`.

## Verify

```bash
scripts/smoke_oidc_bravo.sh     # login, capture id_token, assert alg=RS256 + new kid
scripts/export_horizon_jwk.sh   # write secrets/oidc-signing/bravo-oidc-rsa.jwks.json for horizon
```
