# OAuth2/OIDC custom-script tester (`/scriptlab` + app8)

An **org-only** lab that exercises six of the PingAM 8.1
[sample OAuth2/OIDC scripts](https://docs.pingidentity.com/pingam/8.1/am-scripting/sample-scripts.html)
and surfaces their customizations in decoded tokens. Every custom token element
these scripts add is named with a leading **star emoji (`⭐`)** so the
customization is instantly visible in the proof point.

Everything lives in a dedicated **`/scriptlab`** realm with its own OAuth2
provider and confidential client, so the provider-level script hooks have **zero
impact** on the `/alpha` apps (app4/app5), app6's `/timeout-test` realm, or the
cross-AM social federation.

## Topology

```
Browser ──launchpad card──▶ PingGateway (ungated route app8.json)
                                  │
                                  ▼
                       app8  (confidential OIDC RP, Node/Express)
                                  │  auth code + PKCE (client_secret_basic)
                                  ▼
                    AM  /scriptlab realm
                       • OAuth2 provider (client-based / JWT access tokens)
                       • 6 wired sample scripts (JavaScript, evaluator 1.0)
                       • client  scriptlab-rp   (redirect https://app8.jrsz.org/callback)
                       • user    demo-user      (realm-local, has mail/phone/ou)
                                  │
                                  ▼
   app8 decodes id_token + access token, then calls userinfo, tokeninfo and
   introspect, and HIGHLIGHTS every ⭐-tagged element.
```

## The six wired scripts

All scripts are in `scripts/*.js`, provisioned by `provision.py` into the
`/scriptlab` realm with the legacy (`1.0`) JavaScript evaluator, and referenced
from the OAuth2 provider's `pluginsConfig` (type `SCRIPTED`) / `coreOAuth2Config`.

| Script | AM context | Wired via | Proof surface |
| --- | --- | --- | --- |
| `oidc-claims.js` | `OIDC_CLAIMS` | `pluginsConfig.oidcClaimsScript` | `⭐dept`, `⭐script`, `⭐source`, `⭐realm` in the **id_token** and **userinfo** |
| `access-token-modification.js` | `OAUTH2_ACCESS_TOKEN_MODIFICATION` | `pluginsConfig.accessTokenModificationScript` | `⭐mail`, `⭐dept`, `⭐script`, `⭐loginHost` in the **access token** (JWT) and **introspect** |
| `evaluate-scope.js` | `OAUTH2_EVALUATE_SCOPE` | `pluginsConfig.evaluateScopeScript` | scope→attribute values + `⭐evaluatedBy` from the legacy **/tokeninfo** endpoint |
| `validate-scope.js` | `OAUTH2_VALIDATE_SCOPE` | `pluginsConfig.validateScopeScript` | **rejection** of a deliberately bogus scope (`invalid_scope`) |
| `authorize-endpoint-data-provider.js` | `OAUTH2_AUTHORIZE_ENDPOINT_DATA_PROVIDER` | `pluginsConfig.authorizeEndpointDataProviderScript` | `⭐authData`, `⭐script`, `⭐authTime` returned at **/authorize** (shown on the callback when AM propagates it; always visible in AM debug) |
| `may-act.js` | `OAUTH2_MAY_ACT` | `coreOAuth2Config.accessTokenMayActScript` **and** `oidcMayActScript` | a **`may_act`** claim carrying `⭐delegate` / `⭐script` in the access and id tokens |

Client-based (stateless / JWT) access tokens are enabled on the provider so the
`⭐` access-token fields and the `may_act` claim decode directly in app8 without
a server round-trip.

## Out of scope (documented, not wired)

The sample-scripts doc covers three more OAuth2/OIDC scripts that this lab
intentionally does **not** wire, to keep the proof point focused:

- **OAuth2 Access Token / Scripted JWT Issuer** (`OAUTH2_SCRIPTED_JWT_ISSUER`) — custom JWT issuance.
- **OAuth2 Dynamic Client Registration** (`OAUTH2_DYNAMIC_CLIENT_REGISTRATION`) — mutate dynamically registered clients.
- **Cache Loader** (`CACHE_LOADER`) — not an OAuth2 token-shaping hook.

They can be added later as additional `scripts/*.js` + provider references.

## Files

- `scripts/*.js` — the six sample scripts (trimmed; each emits `⭐`-prefixed names; no external HTTP so the default scripting whitelist suffices).
- `provision.py` — org-only, idempotent. Creates the realm, upserts the six scripts, builds the provider from [`../oauth-oidc.service.json`](../oauth-oidc.service.json) with the script hooks + JWT access tokens, creates `scriptlab-rp`, the realm-local `demo-user`, and the realm Validation Service.
- `run-bootstrap.sh` — thin wrapper invoked by `docker/amster/docker-entrypoint.sh` behind the `BOOTSTRAP_OAUTH2_SCRIPTS` gate.

## Run / verify

```bash
# 1. (re)generate TLS so the gateway cert has app8.jrsz.org in its SANs
./scripts/generate-tls.sh

# 2. build + start (app8 + gateway), then run the org amster bootstrap
docker compose up -d --build app8 gateway
docker compose --profile bootstrap up amster   # provisions /scriptlab

# 3. open the launchpad card, or app8 directly
open https://app8.jrsz.org/
```

In app8:

1. **Standard login** as `demo-user` — confirm the id_token + userinfo carry the
   `⭐` claims, the access token (and introspect) carry the `⭐` fields and the
   `may_act` claim, and tokeninfo reflects evaluate-scope.
2. **Request bad scope** — confirm AM rejects it with `invalid_scope`
   (validate-scope proof).

All `⭐`-tagged elements are highlighted in app8's rendered JSON.
