# SAML2 custom-script tester (app9, `/samllab`)

The SAML analogue of the app8 OAuth2/OIDC script tester. It demonstrates three
of the PingAM 8.1 sample **SAML2 scripts** in a *genuine cross-AM federation*,
isolated in its own `/samllab` realm on **both** stacks so it has zero impact on
the app7 `/alpha` federation or the `saml-integrated` SPs.

Every custom element these scripts emit is named with the **star emoji (⭐)** so
it is instantly identifiable in the assertion and in the resulting SP session.

## Topology

```
        org AM (/samllab)                         com AM (/samllab)
   ┌───────────────────────────┐            ┌───────────────────────────┐
   │ hosted IdP  samllab-idp    │  assertion │ hosted SP   samllab-sp     │
   │   • IDP Attribute Mapper ──┼───(POST)──▶│   • SP Adapter             │
   │ remote SP   samllab-sp     │            │ remote IdP  samllab-idp    │
   │   • NameID Mapper          │            │                            │
   └───────────────────────────┘            └───────────────────────────┘
        circle of trust: samllab-cot (on both sides)
```

* The **org** AM hosts the IdP; the **com** AM hosts the SP.
* Each side imports the partner as a *remote* entity — which is precisely why
  the **NameID Mapper** wires cleanly: `nameIDMapperScript` only exists on a
  *remote SP* entity, so it lives on the org IdP's remote view of the com SP.
* Both `idpssoinit` (IdP-init) and `spssoinit` (SP-init) flows run org IdP → com
  SP and land the session on **com**, where the proof is shown.
* Auto-federation maps the assertion onto the local `demo-user` by the `uid`
  attribute, so the ⭐-tagged NameID is a visible marker only.

## The three scripts (`scripts/`)

| Script | Context | Wired on | ⭐ proof |
| --- | --- | --- | --- |
| `idp-attribute-mapper.js` | `SAML2_IDP_ATTRIBUTE_MAPPER` | org hosted IdP | Adds `⭐dept`, `⭐source`, `⭐hostedIdp`, `⭐mail` SAML attributes to the assertion (plus the real `uid`). |
| `nameid-mapper.js` | `SAML2_NAMEID_MAPPER` | org IdP's remote view of the com SP | Prefixes the assertion Subject NameID with `⭐`. |
| `sp-adapter.js` | `SAML2_SP_ADAPTER` | com hosted SP | On `postSingleSignOnSuccess`, reads the assertion and stashes every `⭐` attribute + the `⭐` NameID into the com session as the `samllabProof` property (JSON), which app9 reads back. |

## How app9 reads the proof

The SP Adapter sets one session property, `samllabProof`. So app9 can read it
over the AM sessions REST endpoint (`getSessionInfo`), `samllabProof` is added to
the **Session Property Allowlist** service (`amSessionPropertyWhitelist`) in the
com `/samllab` realm — both `sessionPropertyWhitelist` and
`whitelistedQueryProperties`. This is the supported AM mechanism for exposing a
session property over REST (not a workaround).

## Files

| File | Purpose |
| --- | --- |
| `scripts/*.js` | The three SAML2 scripts (legacy/`1.0` evaluator, JavaScript). |
| `samllab-idp.hosted.json` | org hosted IdP entity (IdP role only). |
| `samllab-sp.hosted.json` | com hosted SP entity (SP role only, auto-fed on `uid`). |
| `samllab-idp.metadata.xml` | org IdP standard metadata (imported as remote on com). |
| `samllab-sp.metadata.xml` | com SP standard metadata (imported as remote on org). |
| `provision.py` | Idempotent per-stack provisioner (side detected from `AM_COOKIE_DOMAIN`). |
| `run-bootstrap.sh` | Bootstrap wrapper, invoked by the amster entrypoint. |

`provision.py` per side: ensures the `/samllab` realm, upserts that side's
scripts, creates/updates the hosted entity with its script wired, imports the
partner as remote (org additionally wires the NameID Mapper onto it), ensures the
`samllab-cot` circle of trust, creates the realm-local `demo-user`, applies the
Validation Service (IG + app9 hosts), and — on com — allowlists `samllabProof`.

The metadata XML is exported from AM and embeds each AM's signing certificate, so
partner trust works without a live partner AM at bootstrap time.

## Out of scope

The two remaining sample SAML2 script types were intentionally left out to keep
one clear proof per script:

* **IDP Adapter** (`SAML2_IDP_ADAPTER`) — runs on the IdP but its visible effects
  (redirects/error handling) overlap less cleanly with a single ⭐ surface.
* **SP Account Mapper** (`SAML2_SP_ACCOUNT_MAPPER`) — would replace auto-federation
  account resolution; here auto-federation on `uid` is used so the NameID can be
  freely ⭐-tagged without breaking account linking.

Both *next-gen* evaluator variants are also out of scope; these use the legacy
(`1.0`) evaluator to match app8.

## Verify

```bash
# Provision (per stack; normally run by bootstrap)
AM_SERVER_URL=https://am.jrsz.org:8443/am AM_ADMIN_PASSWORD=changeit AM_COOKIE_DOMAIN=jrsz.org python3 provision.py
AM_SERVER_URL=https://am.jrsz.com:9443/am AM_ADMIN_PASSWORD=changeit AM_COOKIE_DOMAIN=jrsz.com python3 provision.py
```

Then open `https://app9.jrsz.org/`, launch a flow, log in as `demo-user`, and open
`https://app9.jrsz.com:8444/` — the **⭐ Script proof** panel lists the
`samllabProof` entries, all ⭐-tagged.
