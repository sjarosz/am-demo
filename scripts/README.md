# Local TLS Bootstrap

Run `./scripts/generate-tls.sh` to create the local certificate authority, service certificates, PKCS#12 keystores, the shared truststore, and the password file expected by the Compose stack.

Generated artifacts land under:

- `secrets/tls/ca/`
- `secrets/tls/am/`
- `secrets/tls/gateway/`
- `secrets/tls/ds/`
- `secrets/tls/app1/`
- `secrets/tls/app2/`
- `secrets/tls/app3/`
- `secrets/truststores/`
- `secrets/passwords/gateway/`

Defaults:

- all keystore and truststore passwords are `changeit`
- the gateway AM agent password file is generated from `IG_AGENT_PASSWORD`
- if `.env` does not exist, it is copied from `.env.example`
- `.env.example` defaults `app4` to the local AM issuer at `https://am.jrsz.org:8443/am/oauth2/realms/root/realms/alpha`

Override the default password for all generated stores with:

```bash
DEFAULT_PASSWORD='your-password' ./scripts/generate-tls.sh
```

The resulting CA certificate must be trusted by your host browser or OS if you want clean browser access to the `jrsz.org` HTTPS endpoints — or use a real certificate instead:

```bash
./scripts/le-cert.sh issue --domain jrsz.org --dry-run   # validate the zone's token (secrets/cloudflare/jrsz.org.ini)
./scripts/le-cert.sh issue --domain jrsz.org             # Let's Encrypt wildcard via Cloudflare DNS-01 (also --domain jrsz.net)
./scripts/le-cert.sh install --domain jrsz.org           # am.p12 + gateway PEM + ISRG root in the shared truststore
./scripts/le-cert.sh renew --domain jrsz.org             # manual renewal (no-op when not due)
```

`le-cert.sh` only affects `am`/`gateway` (jrsz.org, via `AM_TLS_DIR` / `GATEWAY_TLS_DIR` in `.env`) and `am-com`/`gateway-com` (jrsz.net, via `AM_COM_TLS_DIR` / `AM_COM_KEYSTORE_FILE` / `GATEWAY_COM_TLS_DIR`); see [docs/tls-letsencrypt.md](../docs/tls-letsencrypt.md).

Additional smoke tests:

- `./scripts/smoke_oidc_app4.sh`
  Drives the full `app4` browser-style Authorization Code + PKCE flow through Gateway and AM using cookies and redirects, then asserts token material is rendered on the final page.
