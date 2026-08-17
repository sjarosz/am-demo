# ds.jrsz.net → bonaire05: LDAP onboarding through a Remote Connector Server

The jrsz.net PingDS (`ds.jrsz.net`, AM identity store `ou=people,ou=identities`) is the
**authoritative source** of users for the bonaire05 PingOne Advanced Identity Cloud tenant
(`openam-bonaire05.forgeblocks.com`, realm `alpha`). A Remote Connector Server (RCS) running in
this compose stack hosts the LDAP connector; the tenant owns the application, mapping and
schedule.

```
 ds.jrsz.net:1636 (LDAPS) <-- LDAP connector -- rcs.jrsz.net (RCS 1.5.20.35, docker) --wss--> bonaire05 /openicf/0
                                                                                            IDM: application "jrsz-ldap"
                                                                                                 mapping systemJrszldapUser_managedAlpha_user
                                                                                                 recon every 15 min (+ on demand)
```

| Piece | Where |
|---|---|
| RCS image / entrypoint | `docker/rcs/Dockerfile`, `docker/rcs/docker-entrypoint.sh`, `docker/rcs/ConnectorServer.properties.template` |
| RCS distribution | `zips/openicf-zip-1.5.20.35.zip` unzipped to `openicf/` (both gitignored, copied from `~/projects/bedrock-agentcore-helloworld/rcs`) |
| Compose service | `rcs-com` in `compose.com.yaml` (container `rcs.jrsz.net`, network `jrsz_net`, no published ports) |
| RCS settings | `RCS_*` in `.env.com`; tenant OAuth2 client secret in `secrets/rcs/client-secret` (gitignored) |
| Tenant provisioning | `scripts/provision_bonaire05_ldap_app.py` |
| Demo users | `config/ds/seed-users.ldif` + `scripts/seed-ldap-users.sh` |
| Smoke test | `scripts/smoke_ldap_onboarding.sh` |

## What is configured in bonaire05

Created by `scripts/provision_bonaire05_ldap_app.py` (idempotent; admin token from the saved frodo
connection `openam-bonaire05`, exactly like `scripts/provision_bonaire05_trust.py`):

1. **OAuth2 client** `RCSjrsz-rcs` (realm alpha) — `client_credentials`, scope `fr:idm:*`,
   `client_secret_basic`; secret = `secrets/rcs/client-secret`.
2. **IDM `authentication.rsFilter.staticUserMapping`** entry
   `{subject: RCSjrsz-rcs, localUser: internal/user/connector-server-client, roles: [rcsclient-authorized]}`.
   Without it IDM's `/openicf` websocket endpoint rejects a non-default client id and the RCS logs
   `Timeout upgrading TCP connection to WebSocket` forever.
3. **Connector server** `jrsz-rcs` in `provisioner.openicf.connectorinfoprovider.remoteConnectorClients`
   (must equal `RCS_NAME` = `connectorserver.connectorServerName`).
4. **Connector** `provisioner.openicf/jrszldap` — LDAP connector on `connectorHostRef: jrsz-rcs`,
   `ldaps://ds.jrsz.net:1636`, bind `uid=am-identity-bind-account,ou=admins,ou=identities`,
   base `ou=people,ou=identities`, `uidAttribute entryUUID`, object type `User` (`__ACCOUNT__`),
   settings taken from the official *Directory Services (DS)* application template (`ds.ldap` 2.6).
5. **Application** `jrsz-ldap` (`managed/alpha_application`, template `ds.ldap` 2.6, **authoritative**),
   visible under *Applications* in the admin UI with the connector, mapping and data tabs.
6. **Mapping** `config/mapping/systemJrszldapUser_managedAlpha_user`
   (`system/jrszldap/User → managed/alpha_user`), one-way, DS authoritative:

   | situation | action | | situation | action |
   |---|---|---|---|---|
   | ABSENT / MISSING | CREATE | | SOURCE_MISSING / UNQUALIFIED | **DELETE** |
   | FOUND / CONFIRMED | UPDATE | | UNASSIGNED / TARGET_IGNORED / SOURCE_IGNORED / ALL_GONE | IGNORE |
   | AMBIGUOUS / FOUND_ALREADY_LINKED / LINK_ONLY | EXCEPTION | | | |

   Correlation: `userName eq <uid>` (so pre-existing tenant users such as `acarter` get linked and
   updated instead of duplicated). Properties: `uid→userName`, `givenName`, `sn`, `mail`, `cn`,
   `telephoneNumber`, `description`, `inetUserStatus→accountStatus`. **No password** is provisioned
   (DS stores hashes); onboarded users set one via a journey / forgot-password. `runTargetPhase: true`
   so deletions in DS are detected; `allowEmptySourceSet: false` so an empty LDAP result can never
   wipe the tenant.
7. **Schedule** `scheduler/job/recon-jrszldap-alpha_user` — cron `0 0/15 * * * ?` → reconcile that
   mapping.

## Runbook

**One command (idempotent, replayable):** `./scripts/setup-ldap-onboarding.sh [--smoke]` does every
step below in order (unzip RCS, client secret, seed users, tenant registration, start RCS, connector /
app / mapping / schedule, test, reconcile). `scripts/reset-stack.sh` calls it after the jrsz.net
bootstrap unless `BOOTSTRAP_LDAP_ONBOARDING=false` in `.env.com`, so a full wipe-and-rebuild of the
lab re-seeds DS and re-onboards the users. After a DS wipe the source ids (entryUUID) change; the first
recon reports `FOUND_ALREADY_LINKED` while it drops the stale links, and the script automatically runs a
second recon that re-links/creates everything.

Manual equivalent:

```bash
# 0. one-time: RCS distribution + client secret (both gitignored)
unzip -q zips/openicf-zip-1.5.20.35.zip -d .            # -> openicf/
openssl rand -base64 24 | tr -d '/+=\n' > secrets/rcs/client-secret && chmod 600 secrets/rcs/client-secret

# 1. demo users in ds.jrsz.net (idempotent)
./scripts/seed-ldap-users.sh com

# 2. tenant side, part 1 (client + auth mapping + connector-server registration), then start the RCS
./scripts/provision_bonaire05_ldap_app.py --no-recon    # waits up to 3 min for the RCS to connect
docker compose up -d --build rcs-com                    # in another shell if the script is waiting
docker logs -f rcs.jrsz.net                             # want: "... ConnectionGroup:jrsz-rcs:... - operational=true"

# 3. connector test + first reconciliation (also re-runnable any time)
./scripts/provision_bonaire05_ldap_app.py --recon-only

# 4. prove it end to end
./scripts/smoke_ldap_onboarding.sh
```

Order matters on a fresh tenant: IDM refuses to create the provisioner (`No meta-data provider
available yet`) until the RCS is connected, because the connector bundle metadata comes from the RCS.
The script therefore polls `system?_action=testConnectorServers` before the provisioner step.

Useful checks:

```bash
docker compose ps rcs-com                     # healthy == last ConnectionManager check says operational=true
docker exec rcs.jrsz.net tail -f logs/Connector.log       # LDAP connector log (only goes to this file)
frodo info openam-bonaire05 --json | jq -r .bearerToken   # admin token for ad-hoc calls
curl -s -X POST -H "Authorization: Bearer $TOK" "$IDM/system/jrszldap?_action=test"
curl -s -H "Authorization: Bearer $TOK" "$IDM/system/jrszldap/User?_queryFilter=true&_fields=uid,mail"
curl -s -X POST -H "Authorization: Bearer $TOK" "$IDM/recon?_action=recon&mapping=systemJrszldapUser_managedAlpha_user&waitForCompletion=true"
```
(`IDM=https://openam-bonaire05.forgeblocks.com/openidm`.) Admin UI: *Applications → jrsz-ldap*
(Provisioning / Data / Reconciliation tabs) and *Identities → Connect → Servers → jrsz-rcs*.

Remove everything from the tenant: `./scripts/provision_bonaire05_ldap_app.py --delete`
(alpha_users already onboarded and their links are left in place).

## RCS container details

- Built from `gcr.io/forgerock-io/java-21` + `openicf/` (RCS 1.5.20.35 needs Java 17+); runs as
  uid 11111; outbound only.
- `docker/rcs/docker-entrypoint.sh` renders `conf/ConnectorServer.properties` from `RCS_URL`,
  `RCS_NAME`, `RCS_TOKEN_ENDPOINT`, `RCS_CLIENT_ID`, `RCS_CLIENT_SECRET` (env or
  `/run/secrets/rcs/client-secret`), `RCS_SCOPE`, then builds `security/rcs-truststore.p12` =
  JDK cacerts (public CAs, needed for the tenant's Google Trust Services cert) **plus** the lab CA
  from `/run/secrets/ca/*.cert.pem` (needed for `ds.jrsz.net`'s LDAPS cert). Both
  `-Djavax.net.ssl.trustStore` **and** `connectorserver.trustStoreFile` point at it — the framework
  builds its own SSLContext from the latter, and without it the LDAP connector fails PKIX even
  though the JVM property is set.
- The file log is rotated on every start so the compose healthcheck (`grep operational= … | tail -1`)
  never trusts a stale line; first `operational=true` appears after `groupCheckInterval` (60 s), hence
  `start_period: 150s`.
- Rotating the client secret: write the new value to `secrets/rcs/client-secret`, re-run
  `provision_bonaire05_ldap_app.py --no-recon` (PUTs the OAuth2 client with the new secret), then
  `docker compose up -d --force-recreate rcs-com`.

## Gotchas / decisions

- `.env.com` is tracked in git, so the tenant client secret lives in `secrets/rcs/client-secret`, not
  in `.env.com`.
- Because DS is authoritative with `SOURCE_MISSING → DELETE`, **removing a user from ds.jrsz.net removes
  the linked alpha_user in bonaire05** on the next recon (≤ 15 min). This includes users that existed in
  the tenant before (they were linked on the first recon, e.g. `acarter`). Other tenant users that are
  not in DS are untouched (`UNASSIGNED → IGNORE`).
- `alpha_user` requires `userName, givenName, sn, mail`; a DS entry missing any of these fails to
  CREATE (shows as FAILURE in the recon summary; DS entries created through AM always have them).
- Modern AIC keeps mappings as `config/mapping/<name>` objects (what the console and this script use);
  `config/sync` still holds three legacy mappings on bonaire05 — leave it alone.
- Mapping name convention (`system` + Capitalized connector id + object type + `_managedAlpha_user`)
  and connector id (`jrszldap` = app name with non-alphanumerics stripped) mirror what the console
  generates, so the application's Data/Reconciliation tabs work.
- Only ds.jrsz.net is wired. To add ds.jrsz.org, run a second RCS (or point a second connector at
  `ds.jrsz.org` from the same RCS) with a different `BONAIRE_LDAP_APP_NAME`.
