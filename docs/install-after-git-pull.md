# Install After Git Pull

Use this guide when you clone or `git pull` the lab on a **new machine**. Everything needed to reproduce the stack is in git except vendor distributions, generated TLS material, and local `.env` secrets.

## What is in git (portable)

These artifacts are version-controlled and restored by `git pull`:

| Area | Contents |
|------|----------|
| `compose.yaml` | jrsz.org services (DS, AM, apps 1–5, Gateway, bootstrap profile) + `include: compose.com.yaml` |
| `compose.com.yaml` | Parallel jrsz.net twin of every service (see below) |
| `docker/` | Dockerfiles, entrypoints, nginx/Tomcat templates |
| `config/gateway/` | IG routes (`app1`–`app6`, launchpad), `config.json` (incl. `AmServiceTimeout`/`AmServiceCached`), `admin.json`, Groovy scripts |
| `config/gateway-com/` | Generated jrsz.net gateway config (`./scripts/render-com-config.sh`) |
| `config/amster/` | Bootstrap scripts: OAuth2 demo, Login Widget, demo user, auth journeys |
| `config/amster/oauth-oidc.service.json` | Canonical OAuth2/OIDC provider template |
| `apps/` | Static apps 1–3, PKCE app4, Login Widget app5, session-timeout test console app6 (source + `package-lock.json`) |
| `scripts/` | `generate-tls.sh`, `render-com-config.sh`, `set-timeout-profile.sh`, `reset-stack.sh`, smoke tests |
| `.env.example` | Documented defaults for the jrsz.org lab variables |
| `.env.com` | Runtime values for the jrsz.net stack |

**Not in git (by design):**

| Item | Why |
|------|-----|
| `.env` | Local secrets and overrides — copy from `.env.example` |
| `secrets/` | Generated PKI, truststores, gateway password files |
| `openam/`, `opendj/`, `amster/`, `openicf/`, `ping-gateway-2026.3.0/` | Ping vendor distributions (large, licensed) |
| Docker named volumes | Runtime AM + DS state (`am-home-bootstrap`, `ds-data`, `ds-secrets`) |
| `node_modules/`, `dist/` | Rebuilt during `docker compose build` |

AM configuration (realms, OAuth clients, journeys, IG agent) is **not** stored in git as AM export files. It is **re-applied on every bootstrap run** from the scripts under `config/amster/`.

---

## Prerequisites

### Software

- Docker Engine or Docker Desktop with `docker compose`
- `openssl` and `keytool` (for `./scripts/generate-tls.sh`)
- Shell access to edit `/etc/hosts` (or local DNS pointing at the Docker host)

### Vendor distributions

Restore these directories at the **repo root** (`am-standalone/`):

| Directory | Product | Minimum contents |
|-----------|---------|------------------|
| `openam/` | PingAM 8.1.0 | `AM-8.1.0.war` + support JARs used by `docker/am/Dockerfile` |
| `opendj/` | PingDS 8.1.0 | DS runtime used by `docker/ds/Dockerfile` |
| `amster/` | Amster 8.1.0 | `amster` launcher + `amster-8.1.0.jar` |
| `ping-gateway-2026.3.0/` | PingGateway 2026.3.0 | Gateway runtime used by `docker/gateway/Dockerfile` |
| `openicf/` | Remote Connector Server 1.5.20.35 | `unzip zips/openicf-zip-1.5.20.35.zip` at repo root; used by `docker/rcs/Dockerfile` (only needed for the bonaire05 LDAP onboarding, `docs/bonaire05-ldap-onboarding.md`) |

Optional: keep original archives in `zips/` (also gitignored).

---

## Step 1 — Hostname resolution

Add to `/etc/hosts` on the machine running Docker:

```text
127.0.0.1 am.jrsz.org ig.jrsz.org
127.0.0.1 app1.jrsz.org app2.jrsz.org app3.jrsz.org
127.0.0.1 app4.jrsz.org app5.jrsz.org app6.jrsz.org
127.0.0.1 app7.jrsz.org app8.jrsz.org app9.jrsz.org
```

Gateway terminates TLS on port **443** and maps hostnames internally. AM is also reachable directly on **8443**.

---

## Step 2 — Secrets and environment

### Generate TLS and `.env`

From the repo root:

```bash
./scripts/generate-tls.sh
```

This creates:

- Local CA: `secrets/tls/ca/jrsz-root-ca.cert.pem`
- Service keystores: `secrets/tls/{am,gateway,ds,app1,app2,app3}/` (+ `*-com` twins for the jrsz.net stack, same CA)
- Gateway PEM bundle: `secrets/tls/gateway/gateway.server.keypair.pem` (+ `secrets/tls/gateway-com/gateway.server.keypair.pem`)
- Shared truststore: `secrets/truststores/truststore.p12` (CA-only, used by both stacks)
- Gateway password files: `secrets/passwords/gateway/truststore.pass`, `ig.agent.alpha.pass`
- `.env` copied from `.env.example` if missing

Default passwords are **`changeit`** unless you set `DEFAULT_PASSWORD` when running the script.

### Review `.env`

Open `.env` and confirm at least these values for a standard lab:

| Variable | Purpose |
|----------|---------|
| `AM_ADMIN_PASSWORD` | AM `amadmin` password (bootstrap + admin UI) |
| `DS_*_PASSWORD` | PingDS root, monitor, AM profile passwords |
| `AM_KEYSTORE_PASSWORD`, `APP_KEYSTORE_PASSWORD`, `TRUSTSTORE_PASSWORD` | TLS keystore passwords (match `generate-tls.sh` default) |
| `IG_AGENT_PASSWORD` | PingGateway AM agent in `/alpha` |
| `DEMO_USER_NAME`, `DEMO_USER_PASSWORD` | End-user for app4 smoke test and app5 Login Widget |
| `LOGIN_WIDGET_*` | app5 OAuth client and journey names (defaults match bootstrap) |
| `BOOTSTRAP_*` | Toggle bootstrap steps (`true`/`false`) |

**Do not commit `.env` or `secrets/` to git.**

### Optional — publicly trusted certificate for jrsz.org

Instead of importing the lab CA into your browser, the org stack's browser-facing endpoints
(`am.jrsz.org:8443`, `ig.jrsz.org` / `app*.jrsz.org` on 443) can serve a Let's Encrypt wildcard
certificate obtained through Cloudflare DNS-01 — see [tls-letsencrypt.md](tls-letsencrypt.md)
(`./scripts/le-cert.sh issue && ./scripts/le-cert.sh install`, then set `AM_TLS_DIR` /
`GATEWAY_TLS_DIR` in `.env`). The `jrsz.net` twin, DS and the backends stay on the lab CA.

### Optional — trust the lab CA

Import `secrets/tls/ca/jrsz-root-ca.cert.pem` into your OS or browser trust store to avoid TLS warnings on `https://ig.jrsz.org` and the app hostnames.

---

## Step 3 — Build and start

First-time (or full rebuild):

```bash
docker compose up -d --build
docker compose --profile bootstrap up --abort-on-container-exit amster-bootstrap
```

The bootstrap container:

1. Runs `install-openam` (writes AM config to Docker volume `am-home-bootstrap`)
2. Applies OAuth2 demo config (`config/amster/oauth2-demo/`) — realms `/alpha` and `/bravo`, PKCE client, IG agent, ValidationService; **plus the `/timeout-test` realm** (app6) with the IG agent, OIDC clients `rp-c-app`/`rp-d-app` (back-channel logout), ValidationService, and short session settings (idle 6m / max 20m)
3. Creates demo end-user (`config/amster/demo-user/`) — `DEMO_USER_NAME` in `/alpha`
4. Applies Login Widget config (`config/amster/login-widget/`) — journey, `sdkPublicClient`, CORS
5. Imports auth journeys (`config/amster/journeys/`) — `MFA`, `TOTP`, `Passkeys`, `Passwordless` into `/alpha`

> The gateway is **boot-tolerant**: `AmServiceTimeout`/`AmServiceCached` (used by
> app6) have WebSocket notifications disabled, so the gateway starts even before
> the bootstrap creates `/timeout-test`. app6's IG-protected paths simply redirect
> to AM login until the realm/agent exist. Tune app6 timeouts any time with
> `./scripts/set-timeout-profile.sh <baseline|idle-first|max-first|app-first|token-first|race>`.

Normal restarts (skip bootstrap):

```bash
docker compose up -d
```

---

## Step 4 — Validate

| URL | Expected |
|-----|----------|
| `https://ig.jrsz.org/` | Launchpad with links to all demos |
| `https://app1.jrsz.org` – `app3.jrsz.org` | Gateway SSO → AM → backend |
| `https://app4.jrsz.org` | PKCE OIDC demo (`demo-pkce-app` in `/alpha`) |
| `https://app5.jrsz.org` | Login Widget demo — Sign in top-right, modal centered |
| `https://app6.jrsz.org` | Session timeout / logout / OIDC SLO test console (`/timeout-test` realm) |
| `https://am.jrsz.org:8443/am` | AM admin UI — `amadmin` / value of `AM_ADMIN_PASSWORD` |

**Login Widget test user** (from `.env`):

- Username: `demo-user` (or `DEMO_USER_NAME`)
- Password: value of `DEMO_USER_PASSWORD` in `.env.example`

Optional smoke test:

```bash
./scripts/smoke_oidc_app4.sh
```

---

## Parallel `jrsz.net` stack

A second, fully independent stack (`compose.com.yaml`, included automatically from `compose.yaml`) mirrors every `jrsz.org` service as a `jrsz.net` twin. Both run concurrently on the shared `jrsz_net` network with deconflicted host ports.

| Service | jrsz.org | jrsz.net |
|---------|----------|----------|
| AM | `https://am.jrsz.org:8443/am` | `https://am.jrsz.net:9443/am` |
| Gateway / apps | `https://*.jrsz.org` (443) | `https://*.jrsz.net:8444` |

> Naming: this stack was `jrsz.com` until 2026‑08‑16 and keeps the `-com` suffix in service names, files and
> env vars (`am-com`, `gateway-com`, `compose.com.yaml`, `.env.com`, `secrets/tls/*-com`, `AM_COM_*`). Only the
> domain changed; the provisioning scripts detect the side from `AM_COOKIE_DOMAIN` (`jrsz.org` = org, anything
> else = com; `AM_SIDE=org|com` overrides).

The `jrsz.net` AM listens on **9443 inside the container** (not 8443), so the single URL `https://am.jrsz.net:9443/am` is valid for both browsers and server-to-server callers (IG, app4). The gateway listens on 8443 internally and is published on 8444.

### Setup

1. Hostnames — add the `jrsz.net` names to `/etc/hosts`:

```text
127.0.0.1 am.jrsz.net ig.jrsz.net
127.0.0.1 app1.jrsz.net app2.jrsz.net app3.jrsz.net
127.0.0.1 app4.jrsz.net app5.jrsz.net app6.jrsz.net
127.0.0.1 app7.jrsz.net app9.jrsz.net
```

2. TLS + gateway config — `generate-tls.sh` already emits the `jrsz.net` leaves (`secrets/tls/*-com`) under the same CA. Render the `jrsz.net` gateway config (committed, but regenerate after editing `config/gateway/`):

```bash
./scripts/generate-tls.sh
./scripts/render-com-config.sh   # writes config/gateway-com/
```

3. Runtime values live in `.env.com` (committed with lab defaults). Shared passwords still come from the root `.env`.

4. Build, start, and bootstrap (both stacks come up together):

```bash
docker compose up -d --build
docker compose --profile bootstrap up --build --abort-on-container-exit amster-bootstrap       # jrsz.org
docker compose --profile bootstrap up --build --abort-on-container-exit amster-bootstrap-com   # jrsz.net
```

> Always pass `--build` to the bootstrap commands. The amster entrypoint (which
> decides the bootstrap steps, e.g. importing journeys) is baked into the image,
> so a stale image would silently skip newly added steps.

5. (Optional, needs `frodo` + the saved `openam-bonaire05` connection) ds.jrsz.net → bonaire05 user
   onboarding via the Remote Connector Server — idempotent, also replayed by `scripts/reset-stack.sh`:

```bash
./scripts/setup-ldap-onboarding.sh --smoke   # unzip zips/openicf-zip-*.zip, secret, seed users, rcs-com, tenant config, recon
```
See [bonaire05-ldap-onboarding.md](bonaire05-ldap-onboarding.md).

### Validate

| URL | Expected |
|-----|----------|
| `https://am.jrsz.net:9443/am` | AM admin UI for the com stack |
| `https://ig.jrsz.net:8444/` | jrsz.net launchpad |
| `https://app1.jrsz.net:8444` – `app6.jrsz.net:8444` | Same demos as jrsz.org, isolated identities |

The two stacks have **fully separate** AM/DS state, realms, and identities.

---

## Fresh install vs existing volumes

| Scenario | Command |
|----------|---------|
| New machine, first start | Steps above |
| Re-run bootstrap only (keep DS/AM data) | `docker compose --profile bootstrap up --build --abort-on-container-exit amster-bootstrap` |
| **Complete reset** (wipe AM + DS state) | `./scripts/reset-stack.sh` (recommended) |

After wiping you must run the bootstrap profile again.

> **Use `./scripts/reset-stack.sh` for a complete reset.** A plain
> `docker compose down -v` is **not** sufficient: the `amster-bootstrap` /
> `amster-bootstrap-com` services are one-shot containers gated behind the
> `bootstrap` profile, so `down -v` does not target them. They linger
> (`Exited`/`Created`) and keep the `am-home-bootstrap` volume mounted
> (`Resource is still in use`), so the volume survives the wipe. On the next
> `up`, AM's stale config points at a freshly-emptied DS config store and AM
> returns HTTP 500 (`Configuration store is not available`) — and because that
> 500 also fails AM's healthcheck, the bootstrap (which waits for AM to be
> healthy) deadlocks.
>
> If you prefer to do it by hand, include the profile so `down` removes the
> one-shot containers too:
>
> ```bash
> docker rm -f amster-bootstrap.jrsz.org amster-bootstrap.jrsz.net 2>/dev/null || true
> docker compose --profile bootstrap down -v --remove-orphans
> docker compose up -d --build
> docker compose --profile bootstrap up --build --abort-on-container-exit amster-bootstrap
> docker compose --profile bootstrap up --build --abort-on-container-exit amster-bootstrap-com
> docker rm -f amster-bootstrap.jrsz.org amster-bootstrap.jrsz.net 2>/dev/null || true
> ```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| AM logs `ConfigurationException: Configuration store is not available` (HTTP 500 on `/am/`) | AM home is out of sync with the DS config store (usually a `down -v` that left a pinned `am-home-bootstrap` volume). Run `./scripts/reset-stack.sh` |
| `down -v` reports `Volume ... Resource is still in use` | A lingering `amster-bootstrap*` container holds the volume. `docker rm -f amster-bootstrap.jrsz.org amster-bootstrap.jrsz.net`, then retry — or just use `./scripts/reset-stack.sh` |
| Bootstrap aborts with `container am.jrsz.org is unhealthy` | AM never reached the configurator (same stale-volume cause as above). Reset with `./scripts/reset-stack.sh` |
| Build fails: missing `openam/` etc. | Restore vendor distributions (Prerequisites) |
| TLS / connection errors | Re-run `./scripts/generate-tls.sh`; confirm `/etc/hosts` |
| Gateway 502 / AM agent errors | Re-run bootstrap; verify `secrets/passwords/gateway/ig.agent.alpha.pass` matches `IG_AGENT_PASSWORD` in `.env` |
| app5 Login Widget OAuth timeout | Re-run bootstrap; confirm `sdkPublicClient` has no OAuth `treeName` override |
| app5 / app4 login fails | Confirm demo user exists — re-run bootstrap with `BOOTSTRAP_DEMO_USER=true` |
| app5 shows old UI | Rebuild app5: `docker compose up -d --build app5` |

Logs:

```bash
docker compose logs -f am gateway app5
docker compose logs amster-bootstrap
```

---

## Related docs

- [README.md](../README.md) — quick overview
- [getting-started.md](getting-started.md) — shorter walkthrough
- [runbook.md](runbook.md) — operational commands
- [config/amster/login-widget/README.md](../config/amster/login-widget/README.md) — Login Widget AM artifacts
- [config/amster/journeys/README.md](../config/amster/journeys/README.md) — MFA / TOTP / Passkeys / Passwordless journeys
