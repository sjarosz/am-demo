# Authentication journeys (alpha realm)

Imports four authentication journeys into the **`alpha`** realm of each stack
(`jrsz.org` and `jrsz.net`):

| Journey | Flow |
| --- | --- |
| `MFA` | Page (Username + Password) → Data Store Decision → OATH Token Verifier (registers via OATH Registration when no device) |
| `TOTP` | Username Collector → OATH Token Verifier |
| `Passkeys` | Page (Username + Password) → WebAuthn Authentication (falls back to WebAuthn Registration) |
| `Passwordless` | Username Collector → WebAuthn Authentication |

Reach them through the XUI, e.g.:

```
https://am.jrsz.org:8443/am/XUI/?realm=/alpha&authIndexType=service&authIndexValue=MFA#login/
https://am.jrsz.net:9443/am/XUI/?realm=/alpha&authIndexType=service&authIndexValue=Passkeys#login/
```

## Why these are authored, not exported

The same journeys exist in the **root** realm of the live `jrsz.org` instance,
but their page / collector / WebAuthn node configs are not retrievable through
the AM config REST API (they run on node defaults), so neither `frodo` nor a
plain REST export can clone them. Instead, each journey here is a complete,
self-contained artifact: the tree document plus every node body, with the real
OATH settings preserved and AM's default WebAuthn settings captured. This makes
the journeys fully readable, exportable, and reproducible on a fresh install.

Page nodes use AM's v1.0 page semantics (single `outcome`), and the trees route
to AM's built-in Success (`70e691a5-…`) and Failure (`e301438c-…`) nodes; any
otherwise-dangling node outcomes are wired to Failure so the journeys are
complete.

## Files

| Path | Purpose |
| --- | --- |
| `trees/*.json` | One artifact per journey: `{ name, nodes[], tree }` |
| `run-bootstrap.sh` | Upserts every node then the tree into the target realm |

## Running

Invoked automatically by the amster bootstrap when `BOOTSTRAP_JOURNEYS=true`
(the default). To run by hand against a stack:

```bash
# jrsz.org
AM_URL=https://am.jrsz.org:8443/am AM_ADMIN_PASSWORD=changeit \
  config/amster/journeys/run-bootstrap.sh

# jrsz.net
AM_URL=https://am.jrsz.net:9443/am AM_ADMIN_PASSWORD=changeit \
  config/amster/journeys/run-bootstrap.sh
```

The target realm defaults to `alpha`; override with `JOURNEYS_REALM`. The script
is idempotent — re-running upserts (`If-Match`) existing nodes and trees.
