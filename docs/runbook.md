# PingAM Lab Runbook

This file is now the short operational index.

For normal use, start with:

- [install-after-git-pull.md](install-after-git-pull.md) — new machine / post-clone setup
- [getting-started.md](getting-started.md) — shorter walkthrough

For rebuild/debug history and exact failure fixes, use:

- [rebuild-notes.md](/Users/jarosz/projects/forgerock/am-standalone/docs/rebuild-notes.md)

This workspace now contains a Docker Compose starter stack for:

- `ds.jrsz.org`
- `am.jrsz.org`
- `ig.jrsz.org`
- `app1.jrsz.org`
- `app2.jrsz.org`
- `app3.jrsz.org`
- `app4.jrsz.org`
- `app5.jrsz.org`
- `app6.jrsz.org`

A parallel **jrsz.com** stack (`compose.com.yaml`, included from `compose.yaml`) runs
the same services as independent twins on deconflicted host ports — AM on `9443`,
gateway/apps on `8444`. See the "Parallel jrsz.com stack" section in
[install-after-git-pull.md](install-after-git-pull.md). Render its gateway config with
`./scripts/render-com-config.sh` and bootstrap with the `amster-bootstrap-com` service.

Current state:

- `app1.jrsz.org`–`app3.jrsz.org`: Gateway SSO demos
- `app4.jrsz.org`: PKCE OIDC demo in `/alpha`
- `app5.jrsz.org`: Login Widget demo in `/alpha`
- `app6.jrsz.org`: reserved, no route yet

## 1. Local host resolution

Add these entries to `/etc/hosts` on the Docker host:

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

## 2. Generate local TLS material

Run:

```bash
./scripts/generate-tls.sh
```

This creates:

- local CA cert: `secrets/tls/ca/jrsz-root-ca.cert.pem`
- Gateway TLS bundle: `secrets/tls/gateway/gateway.server.keypair.pem`
- DS keystore: `secrets/tls/ds/keystore`
- shared truststore: `secrets/truststores/truststore.p12`

## 3. Start Docker Desktop or the local Docker daemon

The current stack cannot build or run until the daemon is available at the local Docker socket.

## 4. Build and start the DS service

```bash
docker compose build ds
docker compose up -d ds
```

DS startup assumptions:

- root bind DN: `uid=admin`
- monitor bind DN: `uid=monitor`
- AM config bind DN: `uid=am-config,ou=admins,ou=am-config`
- AM identity bind DN: `uid=am-identity-bind-account,ou=admins,ou=identities`
- AM CTS bind DN: `uid=openam_cts,ou=admins,ou=famrecords,ou=openam-session,ou=tokens`

All passwords default to `changeit` in the generated `.env` unless you override them.

## 5. Build and bootstrap AM

```bash
docker compose build am
docker compose up -d am
docker compose build amster-bootstrap
docker compose --profile bootstrap up --abort-on-container-exit amster-bootstrap
```

The intended path is non-interactive AM bootstrap through Amster, not the web wizard.

For normal restarts after bootstrap, do not start `amster-bootstrap`. Use only:

```bash
docker compose up -d
```

Bootstrap values used by the `amster-bootstrap` container:

- deployment URI: `https://am.jrsz.org:8443/am`
- cookie domain: `jrsz.org`
- config directory: `/home/forgerock/openam`
- configuration store host: `ds.jrsz.org`
- configuration store port: `1636`
- configuration store bind DN: `uid=am-config,ou=admins,ou=am-config`
- configuration store password: value of `DS_AM_PROFILE_PASSWORD`
- identity store host: `ds.jrsz.org`
- identity store port: `1636`
- identity store bind DN: `uid=am-identity-bind-account,ou=admins,ou=identities`
- identity store password: value of `DS_AM_PROFILE_PASSWORD`

The `am` and `amster-bootstrap` containers share the same `am-home` volume, which holds the generated config and security material.

AM still needs to trust the CA from:

- `secrets/tls/ca/jrsz-root-ca.cert.pem`

## 6. Build and start the backend apps

```bash
docker compose build app1 app2 app3
docker compose up -d app1 app2 app3
```

## 7. Build and start Gateway

```bash
docker compose build gateway
docker compose up -d gateway
```

Gateway should then front:

- `https://app1.jrsz.org`
- `https://app2.jrsz.org`
- `https://app3.jrsz.org`

## 8. Validation sequence

Run these in order:

1. Confirm DS is reachable on `ldaps://ds.jrsz.org:1636` from the AM container.
2. Confirm `amster-bootstrap` completes without install errors.
3. Confirm AM admin login works at `https://am.jrsz.org:8443/am`.
4. Confirm each backend app serves its landing page over HTTPS internally.
5. Confirm Gateway starts on `https://app1.jrsz.org`, `https://app2.jrsz.org`, and `https://app3.jrsz.org`.
6. Confirm unauthenticated app access redirects to AM.
7. Confirm post-login return lands on the original app hostname.

## 9. Known gaps

- AM bootstrap now targets Amster-based automation, but it has not yet been runtime-verified on this machine.
- Gateway uses starter config and may need object-level fixes once first runtime logs are available.
- DS has a first-pass AM profile bootstrap, but it has not yet been verified with a live `docker compose build ds` on this machine.
