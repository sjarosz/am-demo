# PingAM Standalone Lab

This repo contains a local Docker Compose lab for:

- PingDS
- PingAM
- PingGateway
- three protected HTTPS web apps

Published hostnames:

- `am.jrsz.org`
- `ig.jrsz.org`
- `app1.jrsz.org`
- `app2.jrsz.org`
- `app3.jrsz.org`
- `app4.jrsz.org`
- `app5.jrsz.org`
- `app6.jrsz.org`

Current implementation state:

- `app1.jrsz.org` to `app3.jrsz.org`: active session-based gateway demos
- `app4.jrsz.org`: active PKCE demo, with `/alpha` as the default local realm
- `app5.jrsz.org`: active Login Widget demo against `/alpha`
- `app6.jrsz.org`: reserved for the next OAuth demo phase
- local AM realms `/alpha` and `/bravo`: created during bootstrap for additive realm work

**New machine?** See [docs/install-after-git-pull.md](docs/install-after-git-pull.md) for the full post-clone checklist (vendor bits, secrets, bootstrap).

## Fresh clone prerequisites

This repo does not include vendor distributions, generated TLS material, or local secret files. A fresh checkout is not runnable until those inputs are restored locally.

Required local software:

- Docker Engine or Docker Desktop with `docker compose`
- shell access with permission to edit `/etc/hosts`
- `openssl`
- `keytool`

Required vendor directories at repo root:

- `openam/` from the PingAM `8.1.0` distribution
- `opendj/` from the PingDS `8.1.0` distribution
- `amster/` from the Amster `8.1.0` distribution
- `ping-gateway-2026.3.0/` from the PingGateway `2026.3.0` distribution

Expected contents:

- `openam/` must contain at least `AM-8.1.0.war` and the AM support jars used by the AM image build
- `amster/` must contain the `amster` launcher and `amster-8.1.0.jar`
- `opendj/` must contain the DS runtime used by `docker/ds/Dockerfile`
- `ping-gateway-2026.3.0/` must contain the Gateway runtime used by `docker/gateway/Dockerfile`

Optional local-only directories:

- `zips/` if you want to keep the original vendor archives in the workspace
- `AM-monitoring-dashboard-samples/` if you want the sample monitoring assets locally

Generated files that are intentionally not in git:

- `.env`
- `secrets/`

Create those generated files with:

```bash
./scripts/generate-tls.sh
```

## Quick start

1. Restore the vendor distributions listed above into the expected repo-root directories.

2. Add these to `/etc/hosts`:

```text
127.0.0.1 am.jrsz.org
127.0.0.1 ig.jrsz.org
127.0.0.1 app1.jrsz.org
127.0.0.1 app2.jrsz.org
127.0.0.1 app3.jrsz.org
127.0.0.1 app4.jrsz.org
127.0.0.1 app5.jrsz.org
127.0.0.1 app6.jrsz.org
```

3. Generate TLS material:

```bash
./scripts/generate-tls.sh
```

4. Start the lab:

```bash
docker compose up -d --build
docker compose --profile bootstrap up --abort-on-container-exit amster-bootstrap
```

On later restarts, use only:

```bash
docker compose up -d
```

The `amster-bootstrap` service is profile-gated and should only be run when you need the one-time AM install flow.
It is safe to rerun when you need to re-apply the checked-in local AM bootstrap, including the `app4` OAuth client import.
On each run it also ensures the local AM realms `/alpha` and `/bravo` exist, clones the checked-in root OAuth2/OIDC demo config into `/alpha` only, and re-applies the dedicated PingGateway AM agent plus `ValidationService` in `/alpha`.

`app4` defaults to the local AM authorization server in `.env.example`, with `/alpha` as the assumed realm:

- `APP4_BASE_URL=https://app4.jrsz.org`
- `AM_REALM=/alpha`
- `AM_REALM_PATH=realms/root/realms/alpha`
- `APP4_OIDC_ISSUER_URL=https://am.jrsz.org:8443/am/oauth2/realms/root/realms/alpha`
- `APP4_CLIENT_ID=demo-pkce-app`
- `APP4_CLIENT_SECRET=`

If you point `app4` at a different AS later, update those `.env` values and rerun the `amster-bootstrap` profile if the local AM client definition also needs to change.

5. Open:

- `https://ig.jrsz.org/` — launchpad
- `https://app1.jrsz.org`
- `https://app2.jrsz.org`
- `https://app3.jrsz.org`
- `https://app4.jrsz.org`
- `https://app5.jrsz.org` — Login Widget demo

`app6.jrsz.org` is reserved for a future phase and has no backend route yet.

Expected behavior:

- Gateway redirects unauthenticated users to AM
- after login, Gateway proxies the request to the target app

## Default lab credentials

- username: `amadmin`
- password: `changeit`

## Useful commands

Start everything:

```bash
docker compose up -d
```

Rebuild and restart everything:

```bash
docker compose up -d --build
```

See status:

```bash
docker compose ps
```

See logs:

```bash
docker compose logs -f
```

Run the one-shot AM bootstrap:

```bash
docker compose --profile bootstrap up --abort-on-container-exit amster-bootstrap
```

Stop everything:

```bash
docker compose down
```

## Documentation

- **Install on a new machine:** [docs/install-after-git-pull.md](docs/install-after-git-pull.md)
- User guide: [docs/getting-started.md](docs/getting-started.md)
- Rebuild/debug notes: [docs/rebuild-notes.md](docs/rebuild-notes.md)
- Operational index: [docs/runbook.md](docs/runbook.md)
- Single Logout + smart buttons: [docs/single-logout.md](docs/single-logout.md)
- OAuth demo expansion: [docs/oauth2-demo-plan.md](docs/oauth2-demo-plan.md)
- OAuth demo validation: [docs/oauth2-demo-validation.md](docs/oauth2-demo-validation.md)

## Current lab status

Working:

- non-interactive AM install through Amster
- TLS-enabled DS, AM, Gateway, and backend apps
- Gateway host-based routing
- redirect to AM for unauthenticated requests
- successful proxying after a valid AM session is presented

Not hardened yet:

- backend proxy TLS uses a development-mode hostname-verifier override
- Gateway JWT session encryption keys are temporary
