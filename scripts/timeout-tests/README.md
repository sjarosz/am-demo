# app6 session-timeout test harness

`run.py` exercises the automatable subset of the **app6** session-timeout /
logout / OIDC-SLO matrix (the `timeout-test` realm) end-to-end against the live
lab, scores each test **PASS / FAIL / SKIP / INFO** against the documented
expected result, and persists every run so several runs can be averaged.

It only **observes and scores** — it never changes AM/IG configuration to make a
test pass. See [`../../docs/session-timeout-testing.md`](../../docs/session-timeout-testing.md)
for the full matrix and pass/fail criteria.

## Quick start

```bash
# one run (instant tests only)
python3 scripts/timeout-tests/run.py

# run it 5 times, then print + write the average
python3 scripts/timeout-tests/run.py --runs 5

# also run the timed AM-idle test (only if the active idle window fits --max-wait)
python3 scripts/timeout-tests/run.py --include-timed --max-wait 180

# average everything already stored under results/
python3 scripts/timeout-tests/run.py --aggregate

# target the jrsz.com twin
python3 scripts/timeout-tests/run.py --side com
```

No dependencies beyond the Python 3 standard library. The lab must be up
(`docker compose up -d`), the `timeout-test` realm bootstrapped, and the app6 /
am hostnames present in `/etc/hosts` (they already are in this repo's setup).

## How it works

`tt-user` is authenticated against the `timeout-test` realm via REST to get the
SSO token under test. That token is injected as the `iPlanetDirectoryPro` cookie
into a per-test cookie jar, and the app6 RP endpoints are driven exactly like a
browser (following the redirect through AM `/authorize`, which auto-approves
because the RP clients use implied consent). app6 performs the confidential code
exchange, holds the RP tokens, and receives AM back-channel logout — so the
harness tests the **real** RP behavior, not a re-implementation.

AM session validation / logout / `logoutByUser` are called directly against the
AM REST `sessions` endpoint with an admin acting-token, so a verdict reflects the
specific session under test (independent of cookie state).

Each test re-authenticates a **fresh** SSO session for state isolation (the
"reset state" step from the manual procedure), so tests do not contaminate each
other and runs are repeatable.

## Test cases

| ID | Matrix | What it asserts (expected) |
|---|---|---|
| `AUTH` | A | tt-user authenticates; SSO token issued (records server-side vs client-side) |
| `AM-VALID-LIVE` | S1 | Live session validates (`refresh=false`) → valid |
| `SESSION-INFO` | S | `getSessionInfo` returns idle/max remaining > 0 |
| `RPC-LOGIN` | D | RP C (confidential) logs in; id/access/refresh tokens + sid/sub present |
| `RPD-LOGIN` | D | RP D (public PKCE) logs in |
| `PROMPT-NONE-LIVE` | O5 | `prompt=none` silently authenticates while AM is valid |
| `API-E-INTROSPECT-LIVE` | E | API E introspection accepts a live token |
| `API-E-JWT-LIVE` | E | *(INFO)* local-JWT validation of a live token |
| `REFRESH-LIVE` | T | Refresh-token grant succeeds while session valid |
| `O6-LOCAL-LOGOUT` | O6 | *Negative control:* RP-local logout clears RP C only; AM + RP D stay valid |
| `O1-RP-INITIATED-LOGOUT` | O1 | end-session logout → AM invalid + RP C cleared + RP D back-channel cleared |
| `O2-AM-REST-LOGOUT` | O2 | AM REST logout → AM invalid + RP C/D back-channel cleared |
| `O8-LOGOUT-BY-USER` | O8/T6 | `logoutByUser` → AM invalid + RP C cleared |
| `T3-LOGOUT-AT` | T3 | After logout: introspection rejects the captured AT (local-JWT residual recorded) |
| `T4-REFRESH-AFTER-LOGOUT` | T4 | Refresh after logout is rejected |
| `T5-REVOKE-RT` | T5 | Revoking the RT kills the grant (refresh fails, introspection rejects) |
| `G1-IG-NOCACHE` | G1 | IG App A (no cache) returns content pre-logout, redirects to login post-logout |
| `G3-IG-CACHE` | G3 | *(INFO)* IG App B (cached) stale window after logout |
| `S1-IDLE` | S1/O3 | *(timed, `--include-timed`)* AM idle expiry invalidates a server-side session |

`INFO` tests are recorded but excluded from the pass-rate. `SKIP` means the test
could not be exercised (e.g. timed test whose idle window exceeds `--max-wait`,
or a missing token).

## Session storage matters

The `timeout-test` realm's SSO storage is controlled by `TIMEOUT_REALM_STATELESS`
(default `true` = **client-side / stateless JWT** sessions). The harness records
the live session type in every result and run file.

With **client-side** sessions and no session denylisting, AM does **not**
invalidate the SSO JWT on logout, so the global-logout invariants
(`O1`/`O2`/`O8`/`T3`/`T4`/`G1`) are expected to **FAIL** — this is the lab's
documented residual risk, not a harness bug. Grant-level revocation (`T5`) still
passes because it acts on the OAuth2 tokens directly. Switch the realm to
**server-side** (`TIMEOUT_REALM_STATELESS=false`, re-bootstrap) or enable
denylisting to see those tests pass. The harness does not change this for you.

## Output

Written under `scripts/timeout-tests/results/`:

- `run-<timestamp>-<side>.json` — full per-run detail (config, session type,
  per-test verdict/observed/detail/duration, summary).
- `results.csv` — long format, one row per test per run
  (`run_ts,side,session_type,test_id,matrix,verdict,duration_ms`); ideal for
  spreadsheet/pandas averaging.
- `averages.csv` — written by `--aggregate` and by any `--runs N>1`: per-test
  runs / pass / fail / skip / info / pass-rate% / mean duration.

`--runs N` (N>1) automatically prints and writes the aggregate at the end.
Running the script several times separately and then `--aggregate` produces the
same averages.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--side {org,com}` | `org` | which stack to target (loads `.env` / `.env.com`) |
| `--runs N` | `1` | run the suite N times, then aggregate |
| `--sleep S` | `0` | seconds to sleep between runs |
| `--include-timed` | off | also run the timed `S1-IDLE` test |
| `--max-wait S` | `180` | max seconds to wait for a timed test (else SKIP) |
| `--grace S` | `8` | extra seconds added after a timeout window |
| `--only IDS` | all | comma-separated test ids to run |
| `--verify` | off | verify TLS against the lab CA (default: unverified, like the lab's curl scripts) |
| `--aggregate` | — | aggregate all stored runs and exit |
| `--list` | — | list test cases and exit |

Note: failing logout tests incur the back-channel poll timeout (~6s each), so a
run with the default config takes roughly 25–30s.
