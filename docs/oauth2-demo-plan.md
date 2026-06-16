# OAuth2 Demo Expansion

This repo keeps the existing `app1` to `app3` PingGateway session demo intact and adds protocol-driven demo apps alongside it.

## Public hostnames

- `app4.jrsz.org`: OIDC Authorization Code + PKCE
- `app5.jrsz.org`: OAuth 2.0 Token Exchange
- `app6.jrsz.org`: RFC 7523

## Current implementation phase

Implemented:

- `app4` Node/Express demo app and Docker image
- `app4` IG reverse proxy route
- gateway certificate SAN coverage for `app4` to `app6`
- additive hostnames in compose and docs
- initial `config/amster/oauth2-demo` package plus root-realm `demo-pkce-app` bootstrap

Not implemented yet:

- `app5` and `app6` containers
- final AM client and service import artifacts for `app5` and `app6`
- validation that the imported client payload matches standalone AM exactly
