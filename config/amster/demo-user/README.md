# Demo end-user bootstrap

Creates the lab end-user account used by:

- Login Widget sign-in on `app5.jrsz.org`
- `./scripts/smoke_oidc_app4.sh`
- SAML federation smoke tests (both stacks)
- Social login and OAuth2/OIDC script-lab tests

## Credentials

| Field    | Value         |
|----------|---------------|
| Username | `demo-user`   |
| Password | `Jrsz$2025!`  |

The password is set via `DEMO_USER_PASSWORD` in `.env` (and `.env.com` for the
jrsz.com stack). The default in `.env.example` is `Jrsz$2025!`.

**Why this password?** The DS identities backend runs dictionary and
common-password validators. "Jrsz" is a project-specific token that does not
appear in either word list, so the password clears all validators while
remaining easy to remember. Do not substitute words like `Demo`, `Lab`, `Code`,
`Ping`, or any common English word — they are substring-matched
case-insensitively and will be rejected.

## Run manually

```bash
AM_URL=https://am.jrsz.org:8443/am \
AM_ADMIN_PASSWORD=changeit \
DEMO_USER_NAME=demo-user \
DEMO_USER_PASSWORD='Jrsz$2025!' \
./config/amster/demo-user/run-bootstrap.sh
```

Invoked automatically from the Amster bootstrap container when
`BOOTSTRAP_DEMO_USER=true`.
