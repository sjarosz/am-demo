# Login Widget AM Bootstrap

This package provisions PingAM artifacts required by the
[Login Widget tutorial](https://developer.pingidentity.com/login-widget/login-widget/tutorial/01-install.html)
for the lab instance at `https://am.jrsz.org:8443`.

## Applied configuration

- Authentication journey `sdkUsernamePasswordJourney` in `/alpha`
  - Page node with username + password collectors
  - Stage `UsernamePassword`
  - Data store decision node
- Public OAuth2/OIDC client `sdkPublicClient` in `/alpha`
  - Redirect URIs: `https://app5.jrsz.org/callback.html`, `https://app5.jrsz.org/`
  - Scopes: `openid profile email address`
  - Implied consent enabled; no OAuth client tree override (journey runs in the widget only)
- Global CORS secondary configuration `LoginWidget`
  - Accepted origin: `https://app5.jrsz.org`

## Run manually

```bash
AM_URL=https://am.jrsz.org:8443/am \
AM_ADMIN_PASSWORD=changeit \
LOGIN_WIDGET_BASE_URL=https://app5.jrsz.org \
./config/amster/login-widget/run-bootstrap.sh
```

The script is also invoked automatically from the Amster bootstrap container when
`BOOTSTRAP_LOGIN_WIDGET_CONFIG=true`.
