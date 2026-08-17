# jrsz.net /bravo → bonaire05 (PingOne AIC): RFC 7523 JWT bearer

`am.jrsz.net` acts as an identity provider for the **bonaire05** AIC tenant
(`https://openam-bonaire05.forgeblocks.com/am`, realm `alpha`) in exactly the way the
**horizon** tenant does today: a user logs in at the local AM `/bravo` realm and receives an
RS256 **JWT access token** that bonaire05 accepts as an RFC 7523 assertion
(`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`) through a **Trusted JWT Issuer**,
returning a bonaire05 access token for the same user. From there the token can be exchanged
onward (RFC 8693) exactly like the horizon-originated ones in the mcp-demo.

Reference: <https://docs.pingidentity.com/pingoneaic/am-oauth2/oauth2-jwt-bearer-grant.html>

## How bonaire05 trusts horizon (the model)

| Piece | horizon → bonaire05 | jrsz.net → bonaire05 (this repo) |
|---|---|---|
| Issuing client | horizon `Portal` (password grant, `openid profile email`) | `bonaire-portal` in `/bravo` on `am.jrsz.net` (password + code grants) |
| Access-token modification script | `set-audience-for-remote-bonaire05`: `aud` = bonaire05 token endpoint, `preferred_username` = uid | `set-audience-for-remote-bonaire05` (`config/amster/oidc-bonaire/scripts/set-audience-for-remote-as.js`), same two fields, portal client only |
| Token signing key | horizon tenant key, published on its jwk_uri | dedicated RSA key `secrets/oidc-signing-net/` mapped for both `oidc.signing.RSA` and `stateless.signing.RSA` in `/bravo` |
| bonaire05 Trusted JWT Issuer | `horizon-IDP`: issuer = horizon alpha issuer, **jwksUri**, `resourceOwnerIdentityClaim=preferred_username`, `consentedScopesClaim=scope` | `jrsz-net-IDP`: issuer `https://am.jrsz.net:9443/am/oauth2/realms/root/realms/bravo`, **embedded `jwkSet`** (bonaire05 cannot reach localhost), same claims |
| jwt-bearer client at bonaire05 | `jrsz-concierge` etc. (`client_secret_post`, scope `a2a:invoke`) | same clients — trusted issuers are realm-wide |
| Identity | `acarter` exists in both tenants by userName | `acarter` created in `/bravo` (`BONAIRE_DEMO_USER*` in `.env`) |

Two details matter and are why the local recipe is what it is:

- AM requires the assertion's `aud` to be **its own access_token endpoint**
  (`https://openam-bonaire05.forgeblocks.com:443/am/oauth2/realms/root/realms/alpha/access_token`);
  an id_token's `aud` is the client_id and AM 8.1 cannot rewrite it, hence access tokens + script.
- AIC names identities by UUID, so a foreign `sub` never resolves; the trusted issuer is keyed on
  `preferred_username` (the mcp-demo "hard-won lesson"), and its `consentedScopesClaim=scope` means
  the assertion's `scope` claim must contain the scopes requested at bonaire05 — so the portal
  client's scopes include `a2a:invoke`.

## Local side (automated, jrsz.net stack)

`config/amster/oidc-bonaire/` runs from `amster-bootstrap-com` (`BOOTSTRAP_OIDC_BONAIRE`, default
`true` on that service; the org stack has `BOOTSTRAP_OIDC_HORIZON` instead):

1. `scripts/generate-tls.sh` (or `OIDC_DIR=secrets/oidc-signing-net scripts/generate-oidc-signing-key.sh`)
   creates the dedicated key `secrets/oidc-signing-net/bravo-oidc.jceks` (mounted at
   `/run/secrets/oidc` in `amster-bootstrap-com`).
2. `run-bootstrap.sh` copies keystore + PLAIN password secrets into the `am-home-bootstrap-com`
   volume, then `provision.py` creates, idempotently:
   - `/bravo` OAuth2 provider (cloned template; client-based JWT access tokens, RS256,
     `accessTokenModificationScript` wired)
   - the script `set-audience-for-remote-bonaire05` (fixed id `b0a1e001-…-00000000b0a1`)
   - realm secret stores `bravo-oidc-passwords` (PLAIN) + `bravo-oidc` (JCEKS) with mappings
     `am.services.oauth2.oidc.signing.RSA` and `am.services.oauth2.stateless.signing.RSA` → `bravo-oidc-rsa`
   - client `bonaire-portal` (confidential, `client_secret_basic`, grants `password authorization_code refresh_token`,
     scopes `openid profile email a2a:invoke`, redirect `https://app6.jrsz.net:8444/callback`)
   - users `acarter` (password = `BONAIRE_DEMO_USER_PASSWORD`, must equal bonaire05's) and `demo-user`

Knobs (all in `.env` / `.env.example`, consumed via `compose.com.yaml`): `BONAIRE_DEMO_USER`,
`BONAIRE_DEMO_USER_PASSWORD`, `BONAIRE_PORTAL_CLIENT_ID`, `BONAIRE_PORTAL_CLIENT_SECRET`,
`BONAIRE_PORTAL_SCOPES`, `REMOTE_AS_TOKEN_ENDPOINT`, `REMOTE_AS_NAME`, `BOOTSTRAP_OIDC_BONAIRE`.

Issuer (`iss`) of `/bravo` tokens: `https://am.jrsz.net:9443/am/oauth2/realms/root/realms/bravo`.

## Remote side (automated, bonaire05)

```bash
./scripts/provision_bonaire05_trust.py --dry-run   # shows the TrustedJwtIssuer body
./scripts/provision_bonaire05_trust.py             # creates/updates jrsz-net-IDP in bonaire05/alpha
./scripts/provision_bonaire05_trust.py --delete    # removes it
```

The script fetches `am.jrsz.net`'s `/bravo` jwk_uri, picks the RSA `sig` key whose modulus matches
`secrets/oidc-signing-net/bravo-oidc-rsa.cert.pem` (AM's `kid` is a hash of the key, so the JWK
must be copied from what AM publishes), writes it to
`secrets/oidc-signing-net/bravo-oidc-rsa.jwks.json`, mints an admin token with
`frodo info openam-bonaire05 --json` (saved frodo connection profile with a service account —
same mechanism the mcp-demo provisioner uses) and PUTs
`realm-config/agents/TrustedJwtIssuer/jrsz-net-IDP` with the embedded JWK Set. It never writes the
admin token to disk. Nothing else in bonaire05 is changed: the jwt-bearer-capable clients
(`jrsz-concierge`, `Weather-auth`, `Records-auth`, …) and the realm-default access-token script
that normalises `sub` for jwt-bearer grants already exist from the mcp-demo.

## Exchange / verify

```bash
./scripts/smoke_jwt_bearer_bonaire05.sh
```

Does the calls below (plus the IG bridge, next section) and prints the decoded tokens:

```
1) password grant at am.jrsz.net /bravo (bonaire-portal, acarter)   -> RS256 AT: aud=<bonaire05 token endpoint>, preferred_username=acarter, scope [openid profile email a2a:invoke]
2) POST bonaire05 .../alpha/access_token grant_type=jwt-bearer assertion=<AT> client_id=jrsz-concierge client_secret=… scope=a2a:invoke
   -> bonaire05 AT: sub=<acarter uuid>, subname=acarter, aud=jrsz-concierge, scope [a2a:invoke]
```

Raw form:

```bash
curl -X POST https://openam-bonaire05.forgeblocks.com/am/oauth2/realms/root/realms/alpha/access_token \
  --data-urlencode 'grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer' \
  --data-urlencode "assertion=${JRSZ_NET_ACCESS_TOKEN}" \
  --data-urlencode 'client_id=jrsz-concierge' --data-urlencode "client_secret=${SECRET}" \
  --data-urlencode 'scope=a2a:invoke'
```

## One-hop bridge through PingGateway (the simplest client experience)

Because AM's RFC 8693 token exchange only accepts tokens *it* issued, a foreign JWT cannot be
put straight into `token-exchange` at bonaire05 (`invalid_request / Invalid token exchange` for
`access_token`, `jwt` and `id_token` subject types — verified). The jwt-bearer call is the
minimal entry into bonaire05's trust domain, so it is packaged as **one IG hop** on the jrsz.net
gateway — no local key material, no minting, no subject mapping (compare the mcp-demo broker
ingress: JwtValidationFilter + AssignmentFilter + GrantSwapJwtAssertionOAuth2ClientFilter +
broker key + `AddIssuedTokenType` shim):

```
POST https://ig.jrsz.net:8444/bridge/bonaire05/token          (optional ?scope=... / form scope=)
Authorization: Bearer <jrsz.net /bravo access token>
  -> 200 {"access_token": <bonaire05 AT>, "scope": "a2a:invoke", "expires_in": 3599, ...}
  -> 401 invalid_token / invalid_grant (missing, malformed or rejected assertion)
  -> 400 invalid_scope (scope not in the assertion's `scope` claim), 502 anything else
```

Files: route `config/gateway/routes.com-only/bridge-bonaire05.json` (jrsz.net-only; the render
script copies `routes.com-only/*` into `config/gateway-com/routes/` after its rewrites) and the
filter `config/gateway/scripts/groovy/bonaire05-bridge.groovy` (`ScriptableFilter`, ~100 lines:
read Bearer → jwt-bearer POST with `client_secret_post` → return JSON, or in `mode: forward` swap
the `Authorization` header and continue to a backend). The bonaire05 client id/secret/scope and
token endpoint reach the gateway container as `BONAIRE_JWT_CLIENT_ID`, `BONAIRE_JWT_CLIENT_SECRET`,
`BONAIRE_JWT_SCOPE`, `REMOTE_AS_TOKEN_ENDPOINT` (`compose.com.yaml` `gateway-com.environment`,
values from `.env`).

```bash
# 1) IdP login at jrsz.net /bravo -> assertion (RS256 JWT, aud = bonaire05, preferred_username)
A=$(curl -s -u bonaire-portal:bonaire-portal-secret-changeit \
  -X POST https://am.jrsz.net:9443/am/oauth2/realms/root/realms/bravo/access_token \
  --data-urlencode grant_type=password --data-urlencode username=acarter --data-urlencode 'password=…' \
  --data-urlencode 'scope=openid profile email a2a:invoke' | jq -r .access_token)

# 2) one hop: bonaire05 token out
curl -s -X POST -H "Authorization: Bearer $A" https://ig.jrsz.net:8444/bridge/bonaire05/token | jq

# 3) onward RFC 8693 hops use the bonaire05 token exactly like the mcp-demo egress gateways
```

After editing the route/filter: `./scripts/render-com-config.sh && docker compose restart gateway-com`
(IG's route watcher unloads a route whose file is replaced by the render's `rm -rf` + copy).
To protect a bonaire05-facing backend instead of returning the token, reuse the same filter with
`"args": {"mode": "forward"}` in front of a `ReverseProxyHandler`.

## Rotation / re-runs

- New signing key: delete `secrets/oidc-signing-net/bravo-oidc.jceks`, re-run
  `scripts/generate-tls.sh`, re-run the com bootstrap, then `scripts/provision_bonaire05_trust.py`
  (it replaces the embedded JWK Set; bonaire05 caches JWKs for an hour — `jwksCacheTimeout`).
- Domain / port changes: the issuer string is derived from `COM_AM_BASE_URL`; re-run both halves.
- The bootstrap and the trust provisioner are idempotent; re-running reports "already up to date".

## Related

- [horizon-jwt-bearer.md](horizon-jwt-bearer.md) — the org-stack `/bravo` → horizon variant (id_token based, manual horizon side)
- mcp-demo `scripts/provision/provision_tenants.py` (`ensure_trusted_jwt_issuer`) — where `horizon-IDP` comes from
