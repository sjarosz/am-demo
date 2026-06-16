# OAuth2 Demo Validation

## App4 PKCE

1. Regenerate TLS material so the gateway certificate includes `app4.jrsz.org`:
   `./scripts/generate-tls.sh`
2. Add `app4.jrsz.org` to `/etc/hosts`.
3. Rebuild and start the new service:
   `docker compose up -d --build app4 gateway`
4. Run the AM bootstrap profile so the configured `APP4_CLIENT_ID` is imported into `/`, cloned into `/alpha`, and the alpha PingGateway AM agent plus validation service are re-applied:
   `docker compose --profile bootstrap up --abort-on-container-exit amster-bootstrap`
5. Open `https://app4.jrsz.org`.
6. Confirm the landing page shows the configured AM OAuth issuer and `APP4_CLIENT_ID`.
7. Click `Start PKCE Login` and confirm the callback renders a token response and decoded claims.
8. Confirm the AM realms `/alpha` and `/bravo` exist, and that `/alpha` contains the cloned OAuth2 provider and `APP4_CLIENT_ID`.

## Pending

- `app5` validation will be added with token exchange support.
- `app6` validation will be added with RFC 7523 support.
