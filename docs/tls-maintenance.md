# TLS certificate maintenance

Everything you need to keep the lab's certificates valid. Companion to
[tls-letsencrypt.md](tls-letsencrypt.md) (initial setup) and `scripts/generate-tls.sh`
(private CA material). All secrets live under `secrets/` — **not in git** — so back that
directory up (see [Backups](#7-backups)).

Two public zones are in play: `jrsz.org` (org stack: `am`, `gateway`) and `jrsz.net`
(the stack that keeps its historical `-com` suffix: `am-com`, `gateway-com`, `compose.com.yaml`,
`.env.com`; it was `jrsz.com` until 2026‑08‑16).

## 1. Inventory

| Material | Where | Used by | Lifetime | Expires (current) | Renew with |
|---|---|---|---|---|---|
| **Let's Encrypt** `jrsz.org` + `*.jrsz.org` | `secrets/letsencrypt/live/jrsz.org/`, converted into `secrets/tls/le/jrsz.org/{am,gateway}/` | `am.jrsz.org:8443`, `ig.jrsz.org:443` | **90 days** | **2026‑11‑14** | `./scripts/le-cert.sh renew --domain jrsz.org` |
| **Let's Encrypt** `jrsz.net` + `*.jrsz.net` | `secrets/letsencrypt/live/jrsz.net/`, converted into `secrets/tls/le/jrsz.net/{am,gateway}/` | `am.jrsz.net:9443`, `ig.jrsz.net:8444` | **90 days** | **2026‑11‑14** | `./scripts/le-cert.sh renew --domain jrsz.net` |
| Private leaf certs (`am`, `gateway`, `ds`, `app1‑3` and the `-com` twins for jrsz.net) | `secrets/tls/<svc>/` | DS, app1‑3 backends; AM/IG fallback when LE is switched off | 825 days | org 2028‑09‑18, `-com` 2028‑11‑19 (regenerated 2026‑08‑16 for jrsz.net) | `generate-tls.sh` after removing the leaf (§5) |
| DS keystores (`ssl-key-pair` = TLS leaf, `master-key` = **data encryption**, `ca-cert`) | `secrets/tls/ds/keystore`, `secrets/tls/ds-com/keystore` | PingDS | leaf 825 d / master-key 10 y | org 2028‑09‑18, com 2028‑11‑19 / 2036 | §5 — never regenerate `master-key` |
| JRSZ Local Root CA | `secrets/tls/ca/jrsz-root-ca.{key,cert}.pem` | signs all private leaves | 10 years | 2036‑06‑13 | new lab; out of scope |
| Shared truststore (`jrsz-root-ca`, `isrg-root-x1`) | `secrets/truststores/truststore.p12` | AM, AM‑com, IG, IG‑com, amster JVMs | roots above | — | `le-cert.sh install` re-adds ISRG if missing |
| CA bundle for host tools | `secrets/tls/ca/ca-bundle.pem` | smoke scripts, curl | — | — | `le-cert.sh install` |
| Bravo OIDC signing key | `secrets/oidc-signing/` | AM `/bravo` id_token signing | 10 years | 2036‑06‑13 | `scripts/generate-oidc-signing-key.sh` |
| Cloudflare API tokens (one per zone) | `secrets/cloudflare/jrsz.org.ini`, `secrets/cloudflare/jrsz.net.ini` | `le-cert.sh` (DNS‑01) | until revoked/expired in Cloudflare | — | §3 |

Check expiries any time:

```bash
./scripts/le-cert.sh status                       # both LE certs, mount sources, truststore aliases
for f in secrets/tls/*/*.cert.pem secrets/tls/le/*/certbot/cert.pem; do
  printf '%-52s %s\n' "$f" "$(openssl x509 -in "$f" -noout -enddate)"; done
```

## 2. Routine: renew the Let's Encrypt certificates (every ≤ 60 days, both zones)

Let's Encrypt no longer sends expiry e-mails. Put a reminder in your calendar for
**~30 days before expiry** (first one: mid‑October 2026), or schedule the command (below).

```bash
cd /Users/jarosz/projects/am-demo
./scripts/le-cert.sh renew --domain jrsz.org
./scripts/le-cert.sh renew --domain jrsz.net
```

What it does: runs `certbot renew` for that lineage (only actually renews when < 30 days remain),
and **only if the certificate changed** rebuilds `am.p12` + the gateway PEM, re-checks the
truststore, and restarts that stack's services (`am gateway` for jrsz.org, `am-com gateway-com` for
jrsz.net). When nothing is due it prints `Certificate unchanged … nothing to install` and exits —
safe to run as often as you like.

Then confirm:

```bash
./scripts/le-cert.sh status                                   # new notAfter for both zones
curl -sv https://am.jrsz.org:8443/am/ 2>&1 | grep -E 'expire|issuer'
curl -sv https://am.jrsz.net:9443/am/ 2>&1 | grep -E 'expire|issuer'
./scripts/smoke_oidc_app4.sh && ./scripts/smoke_saml.sh       # IG ↔ AM and cross-AM trust intact
```

Requirements at renewal time: Docker Desktop running, the zone's `secrets/cloudflare/<domain>.ini`
still valid, `.env` still has `LE_EMAIL`. Nothing else in the stack has to be up — `renew` restarts
the stack's `am`/`gateway` services itself if they are running.

Optional automation (weekly; harmless when nothing is due):

```cron
0 4 * * 1 cd /Users/jarosz/projects/am-demo && for d in jrsz.org jrsz.net; do ./scripts/le-cert.sh renew --domain $d; done >> secrets/letsencrypt/renew.log 2>&1
```

Force a fresh certificate outside the window (e.g. key compromise):
`./scripts/le-cert.sh renew --domain <d> --force` (Let's Encrypt limit: 5 duplicate certificates per week).

## 3. Cloudflare API tokens

- One token per zone, needed only while running `issue`/`renew`. Scope: `Zone → DNS → Edit` +
  `Zone → Zone → Read` on that zone.
- Rotate or replace: put the new value in `secrets/cloudflare/<domain>.ini`
  (`dns_cloudflare_api_token = …`, `chmod 600`) and validate with
  `./scripts/le-cert.sh issue --domain <domain> --dry-run` (staging, spends no quota).
- If Cloudflare reports the token invalid (`renew` fails with `Error determining zone_id` /
  authentication errors), the current certificate keeps working until its expiry — fix the token
  and re-run `renew`.

## 4. If a renewal fails

The old certificate keeps working until `notAfter`; you have until then to fix it.

| Symptom | Fix |
|---|---|
| certbot: token / zone errors | §3 |
| certbot: `_acme-challenge` not found / propagation | `LE_DNS_PROPAGATION_SECONDS=60 ./scripts/le-cert.sh renew --domain <d>` |
| Rate limited (`too many certificates`, `too many failed authorizations`) | wait (limits are per week / per hour); use `issue --dry-run` to test without spending quota |
| `docker pull`/`docker run` hangs before certbot prints anything | stuck `docker-credential-desktop` (macOS keychain). `pkill -f docker-credential-desktop`; the script pre-pulls the image with a credential-free `DOCKER_CONFIG`, so simply re-run |
| Renewed but AM/IG still present the old cert | mounts point at `secrets/tls/le/<domain>/*` which `install` overwrote — `docker compose restart am gateway` (org) / `am-com gateway-com` (jrsz.net); if `.env` mount vars changed, `docker compose up -d …` (recreate) |
| IG logs `PKIX path building failed` / `Key 'null' is configured as valid but not available` | truststore lost `isrg-root-x1` (`keytool -list -keystore secrets/truststores/truststore.p12 -storepass changeit`) → `./scripts/le-cert.sh install --domain jrsz.org`, then `docker compose restart am gateway am-com gateway-com`; also make sure `config/gateway*/config.json` still uses `certificateVerificationSecretId` |
| Certificate expired before you noticed | `./scripts/le-cert.sh renew --domain <d>` (or `issue --domain <d> --force`); it restarts the stack itself; browsers/IG recover immediately |
| Want to fall back to the private CA temporarily | comment `AM_TLS_DIR`/`GATEWAY_TLS_DIR` (org) or `AM_COM_TLS_DIR`/`AM_COM_KEYSTORE_FILE`/`GATEWAY_COM_TLS_DIR` (jrsz.net) in `.env`, `docker compose up -d am gateway am-com gateway-com`; reverse to go back |

## 5. Long-term: private-CA material (due 2028‑09‑18 org / 2028‑11‑19 com)

The generated leaves for DS and the app1‑3 backends (and the AM/IG fallbacks) expire on 2028‑09‑18
(org, 825 days from 2026‑06‑16) and 2028‑11‑19 (`-com` twins, regenerated for jrsz.net on
2026‑08‑16). `generate-tls.sh` is *reuse-if-exists*, so to renew a leaf you delete its artefacts and
re-run the script — the CA, truststore, DS master key and everything you did not delete are kept.

```bash
# stop what you are about to change (example: the whole lab)
docker compose down

# 1) leaves consumed as PKCS12 / PEM (any subset)
for svc in am gateway app1 app2 app3 am-com gateway-com app1-com app2-com app3-com; do
  rm -f secrets/tls/$svc/$svc.{key,cert,chain}.pem secrets/tls/$svc/$svc.p12 secrets/tls/$svc/$svc.fullchain.p12 \
        secrets/tls/$svc/gateway.server.keypair.pem
done
./scripts/generate-tls.sh          # regenerates only the removed leaves + gateway PEM bundles

# 2) DS — replace ONLY the TLS key pair, keep master-key (it encrypts the DS data; losing it = data loss)
for d in ds ds-com; do
  rm -f secrets/tls/$d/$d.{key,cert,chain}.pem secrets/tls/$d/$d.p12 secrets/tls/$d/$d.fullchain.p12
  ./scripts/generate-tls.sh        # recreates $d.fullchain.p12 (keystore itself is reused, unchanged)
  keytool -delete -alias ssl-key-pair -keystore secrets/tls/$d/keystore -storepass changeit -storetype PKCS12
  keytool -importkeystore -noprompt \
    -srckeystore secrets/tls/$d/$d.fullchain.p12 -srcstoretype PKCS12 -srcstorepass changeit -srcalias ssl-key-pair \
    -destkeystore secrets/tls/$d/keystore -deststoretype PKCS12 -deststorepass changeit -destalias ssl-key-pair
done
keytool -list -keystore secrets/tls/ds/keystore -storepass changeit -storetype PKCS12   # ca-cert, master-key, ssl-key-pair

docker compose up -d
```

Notes:
- `am`/`gateway` (both stacks) only need their private leaves if you ever revert from Let's Encrypt.
- Nothing needs to change in AM/DS/IG configuration — same filenames, same CA.
- The root CA (2036) and the shared truststore need no action until then; if the CA is ever
  regenerated, every leaf, the DS `ca-cert` alias, the truststore, `ca-bundle.pem` and the OS/browser
  trust import must be redone, and `am-home-bootstrap*` volumes should be recreated — plan that as a
  rebuild, not maintenance.

## 6. After changing any certificate — what to restart

| Changed | Recreate/restart |
|---|---|
| LE cert (`le-cert.sh install --domain <d>`) | `docker compose restart am gateway` (jrsz.org) / `am-com gateway-com` (jrsz.net); first activation: `docker compose up -d …` |
| truststore (`isrg-root-x1` added/removed) | `docker compose restart am am-com gateway gateway-com` (JVMs read it at start); amster reads it per run |
| private leaf for a service | `docker compose up -d <service>` (bind mounts are directories, restart is enough) |
| DS keystore | `docker compose restart ds` (or `ds-com`) |
| `.env` `AM_TLS_DIR` / `GATEWAY_TLS_DIR` / `AM_COM_*` / `GATEWAY_COM_TLS_DIR` | `docker compose up -d am gateway am-com gateway-com` (recreate — the mount *source* changed) |

## 7. Backups

`secrets/` is gitignored and irreplaceable in parts. Back up (encrypted) at least:

- `secrets/tls/ds/keystore`, `secrets/tls/ds-com/keystore` + `keystore.pin` — contain the DS
  **master-key**; without it the `ds-data*` volumes are unreadable.
- `secrets/tls/ca/` — the private root CA (needed to issue new leaves without redoing trust).
- `secrets/letsencrypt/` — ACME account + current certs (recreatable with `issue`, costs quota only).
- `secrets/cloudflare/*.ini` — or just create new tokens.
- `.env` — passwords used by AM/DS/IG.

Everything else under `secrets/` can be regenerated (`generate-tls.sh`, `le-cert.sh install`).
