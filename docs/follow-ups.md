# Follow-ups

Tech-debt and consolidation items surfaced while moving the demo from `/` to `/alpha`. None are blocking the current lab.

## ⚠️ Live-instance state note (2026-05-30)

All SAML configuration **and all non-root realms have been manually removed** from
the live local AM instance (`am.jrsz.org`). The instance now contains **only the
root realm `/`** — `/alpha` (and any `/bravo`) no longer exist, so everything that
was provisioned into `/alpha` is gone, including:

- All SAML federation (the iamshowcase IdP `mockidp`, the `IAMShowcase` remote SP,
  the `mock-cot-alpha` Circle of Trust — verified absent, realm 404). **No SAML
  config remains anywhere in this lab.**
- The `/alpha` OAuth2/OIDC clients used by app4 and app5 (login-widget).
- The `/alpha` `demo-user`, `IdentityGatewayAgent`, and `ValidationService`.

The repo has been cleaned to match: all SAML launchpad sections (MockSAML SP,
iamshowcase IdP, cross-AM federation) and the `config/amster/saml-cot/` bootstrap
have been removed. SAML was only ever configured manually and is not part of any
bootstrap script.

Implications:

- Repo bootstrap (`docker/amster/docker-entrypoint.sh` → `config/amster/**`) still
  targets `/alpha`. Re-running it on this instance would **recreate** `/alpha` and
  its OAuth/user/agent config from the repo. There is no SAML to recreate.
- Launchpad links and docs that reference `/alpha` (OIDC apps, auth trees) point at
  config that is currently absent until the realm is re-bootstrapped.

No repo files were changed for this note beyond recording the fact here.

## 1. ~~Dual `oauth-oidc.service.json`~~ (resolved)

Canonical provider config now lives at `config/amster/oauth-oidc.service.json`. The repo-root duplicate and compose bind-mount override were removed.

## 2. Two parallel bootstrap implementations

- `config/amster/oauth2-demo/run-bootstrap.sh` — REST + curl + python3. **This is what `docker/amster/docker-entrypoint.sh` runs.**
- `config/amster/oauth2-demo/scripts/*.amster` — full Amster Groovy doing the same realm + provider + client + IG agent + validation-service work. **Not invoked from anywhere.**

Either pick the Amster path (rip out `run-bootstrap.sh`) or treat the `.amster` files as a documented snapshot/reference and say so in `config/amster/oauth2-demo/README.md`.

## 3. Orphaned `import-app4-client.sh`

`config/amster/oauth2-demo/import-app4-client.sh` (frodo-based) is no longer called by `docker-entrypoint.sh`. Its default realm flipped to `/alpha` but nothing executes it. Either delete it or document it as a manual escape hatch in the oauth2-demo README.

## 4. `/bravo` realm has no purpose yet

`run-bootstrap.sh` creates `/bravo` but provisions nothing into it. If it is reserved for `app5`/`app6` (token-exchange / RFC 7523), say so in `docs/oauth2-demo-plan.md`. Otherwise drop it from the bootstrap to keep the realm list honest.

## 5. Smoke-test coverage shrank

`scripts/smoke_am_app4_client.sh` was deleted. Only `scripts/smoke_oidc_app4.sh` (end-to-end browser flow) remains. The new `/alpha` clone + `IdentityGatewayAgent` + `ValidationService` have no automated assertion beyond `run-bootstrap.sh`'s own verify pass. Add a smoke that hits AM REST and confirms:

- `/alpha` and `/bravo` exist
- `OAuth2Client/demo-pkce-app` exists in both `/` and `/alpha`
- `IdentityGatewayAgent/ig_agent_alpha` exists in `/alpha`
- `ValidationService` in `/alpha` lists the expected `validGotoDestinations`
