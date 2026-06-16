# SAML 2.0 external IdP — integrated mode (cross-AM)

This directory is the **source of truth** for the cross-AM SAML 2.0 **integrated
mode** feature between the `jrsz.org` and `jrsz.com` AM stacks. A clean
`git clone` + `scripts/reset-stack.sh` recreates it from these artifacts — no
manual steps.

It is **separate from, and leaves untouched**, the standalone `app7` SAML
federation in [`../saml`](../saml). That one uses the classic dual-role
`jrsz-<side>` entities with `Consumer` endpoints; this one adds a new SP-only
entity per side that runs inside an authentication tree.

## Integrated mode vs. standalone

In *integrated mode* the SAML SP flow runs **inside an authentication tree** via
a **SAML2 Authentication node**, instead of standalone SP servlets. Per the
[PingAM docs](https://docs.pingidentity.com/pingam/8.1/am-saml2/saml2-integrated-mode.html),
the hosted SP must expose its assertion-consumer endpoints under **`AuthConsumer`**
(not `Consumer`) so the assertion is fed back into the running tree.

```
SamlLogin journey (SP-init)
  SAML2 Authentication node           SP = <side>-integrated-sp, IdP = partner jrsz-<partner>
    ├─ ACCOUNT_EXISTS ─► Set Success URL ─► Success      (auto-fed uid -> demo-user)
    ├─ NO_ACCOUNT     ─► Provision Dynamic Account ─► Set Success URL ─► Success
    └─ ERROR          ─► Failure
```

## What gets provisioned

Each `/alpha` realm gets a new **SP-only** hosted entity, imports the partner's
integrated SP as a remote entity (so this side's IdP trusts it for the reverse
direction), joins the existing `jrsz-federation` circle of trust, and gets the
`SamlLogin` journey.

| | org | com |
|--|-----|-----|
| SP entity ID | `org-integrated-sp` | `com-integrated-sp` |
| SP metaAlias | `/alpha/integrated-sp-org` | `/alpha/integrated-sp-com` |
| ACS endpoints | `…/am/AuthConsumer/metaAlias/alpha/integrated-sp-org` | `…/am/AuthConsumer/metaAlias/alpha/integrated-sp-com` |
| Partner IdP (SP-init target) | `https://am.jrsz.com:9443/am/jrsz-com` | `https://am.jrsz.org:8443/am/jrsz-org` |
| Hosted artifact | `org-integrated-sp.hosted.json` | `com-integrated-sp.hosted.json` |
| Remote metadata (for partner) | `org-integrated-sp.metadata.xml` | `com-integrated-sp.metadata.xml` |
| Journey | `SamlLogin` | `SamlLogin` |

The partner IdP is the existing `jrsz-<partner>` entity already imported as a
remote IdP by the standalone [`../saml`](../saml) provisioner — this feature
reuses it rather than defining a new IdP.

## Artifacts

- **`<side>-integrated-sp.hosted.json`** — SP-only hosted entity. Key points:
  - `assertionConsumerService` locations use **`AuthConsumer`** (integrated mode),
    HTTP-Artifact (index 0) + HTTP-POST (index 1, default).
  - `autoFederation` on **`uid`** so a successful assertion resolves the existing
    `demo-user` to the `ACCOUNT_EXISTS` outcome.
  - authentication context `PasswordProtectedTransport` at **level 0** (the lab's
    username/password login yields `authLevel=0`; matching the standalone canon).
- **`<side>-integrated-sp.metadata.xml`** — standard SP metadata, imported by the
  *partner* as a remote SP. Reuses the default AM signing/encryption certificate
  blocks (identical to [`../saml`](../saml)), so partner trust works without key
  management and without the partner AM being up at bootstrap.
- **`trees/SamlLogin.json`** — the journey above, with placeholders
  `@@IDP_ENTITY@@`, `@@SP_METAALIAS@@`, `@@SUCCESS_URL@@` substituted at provision
  time. The SAML2 node is type `product-Saml2Node`; outcomes are `ACCOUNT_EXISTS`,
  `NO_ACCOUNT`, `ERROR`.

## How it runs

`docker/amster/docker-entrypoint.sh` calls `run-bootstrap.sh` once per stack
(gated by `BOOTSTRAP_SAML_INTEGRATED`, default on), **after** the standalone
`saml` block so the partner IdP is already present. It detects the side from
`AM_COOKIE_DOMAIN`, then `provision.py`:

1. creates/updates this stack's hosted SP from `<side>-integrated-sp.hosted.json`,
2. imports the partner's `<partner>-integrated-sp.metadata.xml` as a remote SP,
3. ensures the `jrsz-federation` COT contains both integrated SPs and the partner IdP,
4. provisions the `SamlLogin` journey, substituting:
   - SAML2 node `idpEntityId` = partner `jrsz-<partner>` entity,
   - SAML2 node `metaAlias` = local SP metaAlias,
   - Set Success URL = this side's IG launchpad (`IG_BASE_URL/`, already whitelisted
     in `validGotoDestinations` by the oauth2-demo bootstrap).

It is **idempotent** — re-running reconciles to canon.

## Verify

Drive SP-init from each launchpad's **"External SAML IDP login"** card, or open
directly:

```
https://am.jrsz.org:8443/am/XUI/?realm=/alpha&authIndexType=service&authIndexValue=SamlLogin#login/
https://am.jrsz.com:9443/am/XUI/?realm=/alpha&authIndexType=service&authIndexValue=SamlLogin#login/
```

Expect a federated session for `demo-user` (Account Exists via auto-fed `uid`).

## Manual restore / repair (running stack)

```bash
# Re-apply canon to both already-provisioned stacks:
AM_SERVER_URL=https://am.jrsz.org:8443/am AM_COOKIE_DOMAIN=jrsz.org \
  AM_ADMIN_PASSWORD=changeit python3 config/amster/saml-integrated/provision.py
AM_SERVER_URL=https://am.jrsz.com:9443/am AM_COOKIE_DOMAIN=jrsz.com \
  AM_ADMIN_PASSWORD=changeit python3 config/amster/saml-integrated/provision.py
```
