# AM Auto-Config

This stack uses Amster's `install-openam` command for the first AM bootstrap when AM is backed by external PingDS stores.

The `amster-bootstrap` service mounts the same `am-home` volume as the `am` container and runs:

- `install-openam --cfgDir /home/forgerock/openam ...`

That shared directory is the AM configuration directory consumed by the AM container through:

- `-Dcom.sun.identity.configuration.directory=/home/forgerock/openam`

This is the supported non-interactive path for DS-backed AM configuration. `boot.json` remains part of AM startup state, but it is not a replacement for the initial install step.

After `install-openam`, the bootstrap container applies the repo's repeatable demo state:

- ensure `/alpha` and `/bravo` exist
- ensure the checked-in root OAuth2/OIDC demo config exists in `/`
- clone that root OAuth2/OIDC demo config into `/alpha` only
- create the demo end-user in `/alpha` (`config/amster/demo-user/`)
- apply Login Widget AM config (`config/amster/login-widget/`)
- provision the cross-domain SAML federation (`config/amster/saml/`)
- provision cross-AM OIDC social login (`config/amster/social/`)
- provision the `/bravo` OIDC realm whose id_tokens are signed by a dedicated
  cert trusted by the horizon AIC instance (`config/amster/oidc-horizon/`, org stack)
- provision `/bravo` on the jrsz.net stack as an RFC 7523 JWT-bearer IdP for the
  bonaire05 AIC tenant (`config/amster/oidc-bonaire/`, `BOOTSTRAP_OIDC_BONAIRE`), whose
  remote half is `scripts/provision_bonaire05_trust.py`

OAuth provider template: `config/amster/oauth-oidc.service.json`
