# SAML 2.0 cross-domain federation (canon)

This directory is the **source of truth** for the SAML federation between the
`jrsz.org` and `jrsz.com` AM stacks. A clean `git clone` + `scripts/reset-stack.sh`
recreates a fully working federation from these artifacts — no manual steps.

## What gets provisioned

Each `/alpha` realm hosts one dual-role (IDP + SP) entity and imports the other
AM as a remote provider, joined by the `jrsz-federation` circle of trust.

| | org | com |
|--|-----|-----|
| Entity ID | `https://am.jrsz.org:8443/am/jrsz-org` | `https://am.jrsz.com:9443/am/jrsz-com` |
| IDP / SP metaAlias | `/alpha/idp-org`, `/alpha/sp-org` | `/alpha/idp-com`, `/alpha/sp-com` |
| Hosted artifact | `jrsz-org.hosted.json` | `jrsz-com.hosted.json` |
| Standard metadata | `jrsz-org.metadata.xml` | `jrsz-com.metadata.xml` |

All four flows work: `{org,com}`-IDP -> `{com,org}`-SP, each IDP-init and SP-init,
with auto-federation on `uid` -> `demo-user`.

## Artifacts

- **`jrsz-<side>.hosted.json`** — the hosted entity's full structured config
  (IDP + SP roles). These already contain every correctness fix:
  - IDP/SP authentication context = `PasswordProtectedTransport` at **level 0**
    (the lab's username/password login yields an `authLevel=0` session; without
    this the IDP throws *"No IDP Authentication Context matches the current Auth
    Level"* and SP-init throws *"AuthnContext doesn't match RequestedAuthnContext"*).
  - IDP attribute mapper = `com.sun.identity.saml2.plugins.DefaultIDPAttributeMapper`
    (a wrong `org.forgerock.*` class caused `ClassNotFoundException`).
  - RelayState allow-lists with **query-string** patterns (`.../*?*`) and correct
    ports — AM's `URLPatternMatcher` will not match `...?flow=...` with a plain `.../*`.
- **`jrsz-<side>.metadata.xml`** — standard SAML metadata, imported by the *partner*
  as a remote entity. It embeds endpoints + the (default AM) signing certificate,
  so partner trust works without any key management and without the partner AM
  being up at bootstrap time.
- **`circle-of-trust.json`** — the `jrsz-federation` COT and its two members.

## How it runs

`docker/amster/docker-entrypoint.sh` calls `run-bootstrap.sh` once per stack
(gated by `BOOTSTRAP_SAML`, default on). It detects the side from
`AM_COOKIE_DOMAIN`, then `provision.py`:

1. creates/updates THIS stack's hosted entity from `jrsz-<side>.hosted.json`,
2. imports the partner's `jrsz-<partner>.metadata.xml` as a remote entity,
3. ensures the `jrsz-federation` COT contains both entities.

It is **idempotent** — re-running reconciles to canon.

## Verify

```bash
scripts/smoke_saml.sh            # browser POST-SSO smoketest, all four flows
```

## Manual restore / repair (running stack)

```bash
# Re-apply canon to both already-provisioned stacks:
AM_SERVER_URL=https://am.jrsz.org:8443/am AM_COOKIE_DOMAIN=jrsz.org \
  AM_ADMIN_PASSWORD=changeit python3 config/amster/saml/provision.py
AM_SERVER_URL=https://am.jrsz.com:9443/am AM_COOKIE_DOMAIN=jrsz.com \
  AM_ADMIN_PASSWORD=changeit python3 config/amster/saml/provision.py
```

`scripts/repair_saml_federation.sh` is a related tool that patches only the
known-fragile settings on existing entities; full provisioning above is preferred.

## Re-exporting canon (after intentional console changes)

If you change the federation config in the AM console and want it to become the
new canon, re-export the artifacts from the live stacks:

```bash
scripts/export_saml_canon.sh
```

Then re-run the smoketest and commit the updated artifacts.
