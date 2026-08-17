# Publicly-trusted TLS for jrsz.org and jrsz.net (Let's Encrypt via Cloudflare DNS-01)

By default the lab uses a private CA (`scripts/generate-tls.sh`), so browsers warn on every
`https://*.jrsz.org` / `https://*.jrsz.net` URL unless the JRSZ root is imported. This guide swaps
the **browser-facing endpoints of both stacks** — PingGateway (`ig.jrsz.org` / `app1..9.jrsz.org`
on 443, `ig.jrsz.net` / `app*.jrsz.net` on 8444) and PingAM (`am.jrsz.org:8443`, `am.jrsz.net:9443`)
— to real Let's Encrypt wildcard certificates obtained through Cloudflare's DNS-01 challenge, one
lineage per zone. Everything else stays on the private CA: DS, the app1–3 Tomcat backends, and all
container-internal hops.

> Naming: the `jrsz.net` stack keeps its historical `-com` suffix in Compose service names, files
> and env vars (`am-com`, `gateway-com`, `compose.com.yaml`, `.env.com`, `AM_COM_TLS_DIR`, …). It was
> `jrsz.com` until 2026‑08‑16; only the domain changed.

Why DNS-01 and not the certificates on Cloudflare's *Edge Certificates* page: edge certificates
live at Cloudflare's edge and their private keys cannot be exported, so they can only front
traffic that goes *through* Cloudflare. The lab is reached locally (`/etc/hosts → 127.0.0.1`), so it
needs a certificate whose key it holds. Let's Encrypt via DNS-01 gives a publicly trusted cert
without exposing anything to the internet — the only external interaction is a temporary
`_acme-challenge.jrsz.org` TXT record.

## What you get

| Item | Value |
|------|-------|
| Certificates | one lineage per zone: `jrsz.org` (SANs `jrsz.org`, `*.jrsz.org`) and `jrsz.net` (SANs `jrsz.net`, `*.jrsz.net`), RSA 2048, 90-day validity |
| Presented by | `am.jrsz.org:8443` / `am.jrsz.net:9443` (Tomcat, PKCS12) and `ig.jrsz.org:443` / `ig.jrsz.net:8444` (PingGateway, PEM bundle) |
| Trust for internal callers | ISRG Root X1 added to the shared `secrets/truststores/truststore.p12` (used by AM, AM-com, IG, IG-com, amster) — so IG→AM, `am.jrsz.net`→`am.jrsz.org` (SAML/social) and amster keep working |
| Host tooling | `secrets/tls/ca/ca-bundle.pem` (JRSZ root + ISRG Root X1); the smoke scripts pick it up automatically |
| State | `secrets/letsencrypt/` (certbot account + certs), `secrets/tls/le/<domain>/` (converted material), `secrets/cloudflare/<domain>.ini` (tokens), all gitignored |

## Prerequisites

1. `jrsz.org` and `jrsz.net` are zones in your Cloudflare account, with no CAA record that
   excludes `letsencrypt.org` (`dig CAA jrsz.org` — empty is fine).
2. One Cloudflare **API token per zone** (My Profile → API Tokens → Create Token) with permissions
   `Zone → DNS → Edit` and `Zone → Zone → Read`, scoped to that zone. Verify:
   ```bash
   curl -s https://api.cloudflare.com/client/v4/user/tokens/verify -H "Authorization: Bearer <token>"
   ```
3. Store them as `secrets/cloudflare/<domain>.ini` (gitignored, `secrets/.gitignore` covers
   everything under `secrets/`):
   ```bash
   mkdir -p secrets/cloudflare
   printf 'dns_cloudflare_api_token = %s\n' '<org-token>' > secrets/cloudflare/jrsz.org.ini
   printf 'dns_cloudflare_api_token = %s\n' '<net-token>' > secrets/cloudflare/jrsz.net.ini
   chmod 600 secrets/cloudflare/*.ini
   ```
4. `LE_EMAIL=you@example.com` in `.env` (ACME account contact; see `.env.example`).
5. Docker (certbot runs in the `certbot/dns-cloudflare` image — nothing is installed on the host),
   plus `openssl` and `keytool` (already required by `generate-tls.sh`).

## Issue and install

Every command takes `--domain <zone>` (default `jrsz.org`):

```bash
for d in jrsz.org jrsz.net; do
  ./scripts/le-cert.sh issue   --domain $d --dry-run   # staging: proves the token can edit DNS, spends no quota
  ./scripts/le-cert.sh issue   --domain $d             # production certificate
  ./scripts/le-cert.sh install --domain $d             # am.p12 + gateway PEM + truststore + ca-bundle
done
```

`install --domain <d>` produces:

- `secrets/tls/le/<d>/am/am.p12` — PKCS12, alias `am`, password `AM_KEYSTORE_PASSWORD`, leaf + intermediates
- `secrets/tls/le/<d>/gateway/gateway.server.keypair.pem` — key + fullchain, the exact filename
  `config/gateway/admin.json` maps to secret id `gateway.server.keypair`
- alias `isrg-root-x1` in `secrets/truststores/truststore.p12` (idempotent, shared by both stacks)
- `secrets/tls/ca/ca-bundle.pem`

Then point the stacks at the new material. In `.env` (see `.env.example`):

```dotenv
# org stack (compose.yaml)
AM_TLS_DIR=./secrets/tls/le/jrsz.org/am
GATEWAY_TLS_DIR=./secrets/tls/le/jrsz.org/gateway
# jrsz.net stack (compose.com.yaml) — its AM expects am-com.p12 by default, hence the extra var
AM_COM_TLS_DIR=./secrets/tls/le/jrsz.net/am
AM_COM_KEYSTORE_FILE=/run/secrets/tls/am.p12
GATEWAY_COM_TLS_DIR=./secrets/tls/le/jrsz.net/gateway
```

The compose files mount `${AM_TLS_DIR:-./secrets/tls/am}` / `${AM_COM_TLS_DIR:-./secrets/tls/am-com}`
into the AMs and `${GATEWAY_TLS_DIR:-./secrets/tls/gateway}` / `${GATEWAY_COM_TLS_DIR:-./secrets/tls/gateway-com}`
into the gateways; the values **must** start with `./` (Compose treats a bare path as a named
volume). Because the mount *source* changes, the containers must be recreated:

```bash
docker compose up -d am gateway am-com gateway-com
```

## Verify

```bash
./scripts/le-cert.sh status
for u in https://am.jrsz.org:8443/am/ https://ig.jrsz.org/ https://am.jrsz.net:9443/am/ https://ig.jrsz.net:8444/; do
  curl -sv "$u" 2>&1 | grep -E 'issuer|subject'      # O=Let's Encrypt, no --cacert needed
done
./scripts/smoke_oidc_app4.sh && ./scripts/smoke_saml.sh && ./scripts/smoke_social.sh
docker logs ig.jrsz.org 2>&1 | grep -iE 'PKIX|trustAnchors'                # expect nothing (same for ig.jrsz.net)
```

The browser should show no warning on `am.jrsz.org`, `ig.jrsz.org`, `app1..9.jrsz.org`,
`am.jrsz.net:9443`, `ig.jrsz.net:8444`, `app*.jrsz.net:8444`.

## Renewal

See [tls-maintenance.md](tls-maintenance.md) for the full maintenance runbook (all certificates,
expiry dates, failure handling, backups). Short version:

Let's Encrypt certificates last 90 days and Let's Encrypt no longer sends expiry e-mails.
Renewal is a manual command in this lab:

```bash
./scripts/le-cert.sh renew --domain jrsz.org   # certbot renew (only when <30 days left); if the cert changed:
./scripts/le-cert.sh renew --domain jrsz.net   # re-run install and restart that stack's am + gateway
./scripts/le-cert.sh renew --domain <d> --force  # force a new certificate (rate limit: 5 duplicates/week)
```

`renew` is safe to run any time (no-op when nothing is due), so it can be scheduled — Docker
Desktop must be running at that moment. Example weekly cron entry:

```cron
0 4 * * 1 cd /Users/jarosz/projects/am-demo && for d in jrsz.org jrsz.net; do ./scripts/le-cert.sh renew --domain $d; done >> secrets/letsencrypt/renew.log 2>&1
```

## Revert to the private CA

Comment out `AM_TLS_DIR`/`GATEWAY_TLS_DIR` (org) and/or `AM_COM_TLS_DIR`/`AM_COM_KEYSTORE_FILE`/
`GATEWAY_COM_TLS_DIR` (jrsz.net) in `.env`, then `docker compose up -d am gateway am-com gateway-com`.
The `isrg-root-x1` truststore entry is harmless and can stay.

## Notes / gotchas

- **PingGateway trust manager.** `config/gateway/config.json` (and the rendered `gateway-com` copy)
  now use `SecretsTrustManager.certificateVerificationSecretId` instead of `verificationSecretId`.
  The latter resolves the truststore entries as *signature-verification* keys, and IG rejects a
  CA certificate whose `KeyUsage` is only `keyCertSign, cRLSign` (as ISRG Root X1's is) —
  the symptom is `Key 'null' is configured as valid but not available` followed by
  `PKIX path building failed` on every AM call. The private JRSZ root only worked before because it
  carries no `KeyUsage` extension at all. `certificateVerificationSecretId` is the documented option
  for trusting CAs and works with both roots.
- **Trust scope.** Adding ISRG Root X1 to the lab truststore means IG/AM would also trust any
  publicly-issued Let's Encrypt certificate presented by a backend — fine for a lab.
- **Docker Desktop credential helper.** On macOS `docker pull` can hang forever when
  `docker-credential-desktop` blocks on keychain access. `le-cert.sh` pre-pulls the public certbot
  image with a throw-away `DOCKER_CONFIG` (no credential store) to sidestep that. If a plain
  `docker pull` hangs for you, that is the cause (`ps aux | grep docker-credential`).
- **Regenerating secrets.** `scripts/generate-tls.sh` reuses an existing truststore and never touches
  `secrets/tls/le`, `secrets/letsencrypt` or `secrets/cloudflare`. If you delete `secrets/` entirely,
  re-run `./scripts/le-cert.sh install` after `generate-tls.sh` to re-import the root and rebuild the
  bundle (the certbot state is gone too in that case → `issue` again).
- **Chain.** As of 2026 the certificate chains leaf → `YR1` → `Root YR` (cross-signed by ISRG Root X1),
  which is what `--preferred-chain "ISRG Root X1"` selects; Java validates it against the ISRG Root X1
  anchor in the truststore.
