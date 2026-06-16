# OAuth2 Demo Amster Package

This directory is the repeatable AM artifact package for the additive OAuth/OIDC demo apps:

- `app4.jrsz.org`: Authorization Code + PKCE
- `app5.jrsz.org`: Token Exchange
- `app6.jrsz.org`: RFC 7523

The current implementation phase keeps the demo bootstrap rooted in the local AM install, provisions the `app4` client in `/`, creates the `/alpha` and `/bravo` realms, clones the root OAuth2/OIDC demo config into `/alpha`, and creates the dedicated PingGateway AM agent and validation service in `/alpha`.

## Layout

- `vars/demo.properties`: environment-neutral defaults
- `vars/demo.local.properties`: example local overrides
- `entities/`: JSON templates consumed by the bootstrap helper
- `import-app4-client.sh`: idempotent client bootstrap for the configured `APP4_CLIENT_ID`
- `run-bootstrap.sh`: idempotent post-install realm and OAuth2 demo bootstrap
- `scripts/`: Amster script snapshots of the intended realm/provider/client state

## Variable Model

Required values:

- `AM_URL`
- `AM_ADMIN_PWD`
- `DEMO_REALM_PATH`
- `APP4_BASE_URL`
- `APP5_BASE_URL`
- `APP6_BASE_URL`
- `APP4_REDIRECT_URI`
- `APP5_REDIRECT_URI`
- `APP6_REDIRECT_URI`

## Current State

Implemented in this phase:

- root-realm client bootstrap for the configured local `app4` PKCE client
- creation of `/alpha` and `/bravo`
- cloning of the root OAuth2 provider and `app4` client config into `/alpha`
- creation of the `alpha` `IdentityGatewayAgent` used by PingGateway
- creation of the `alpha` `ValidationService` valid goto URL allowlist for `ig.jrsz.org` and the app hostnames
- parameter files for repeatable environment mapping
- JSON client template for the PKCE app

Still to be filled in during `app5` and `app6` work:

- finalized AM service JSON for token exchange
- finalized client JSON for RFC 7523 and JWKS material
- verification and extension steps for the later demo clients
