# Rebuild Notes

This document captures the exact issues hit during bring-up and the fixes required to get back to a working state quickly.

It is for implementation efficiency, not for end users.

## Target outcome

A working lab means:

- `docker compose` starts `ds`, `am`, `app1`, `app2`, `app3`, and `gateway`
- `amster-bootstrap` completes `install-openam`
- unauthenticated access to `app1.jrsz.org`, `app2.jrsz.org`, `app3.jrsz.org` returns `302` to AM
- authenticated access proxies through Gateway to the backend app content

## Clean sequence that works

1. Generate TLS material:

```bash
./scripts/generate-tls.sh
```

2. Start AM/DS and bootstrap AM:

```bash
docker compose up -d --build ds am
docker compose up --abort-on-container-exit --build amster-bootstrap
```

3. Start apps and Gateway:

```bash
docker compose up -d --build app1 app2 app3 gateway
```

## Important working assumptions

- Use `docker compose`, not a hard-coded `docker-compose` path in repo docs or scripts.
- AM must be installed by Amster, not by the web wizard.
- AM must not bootstrap itself into file-based SMS mode before `install-openam`.
- Gateway routes should match on `request.uri.host`, not the `Host` header array.

## Failures we hit and the fixes

### 1. DS starts and only prints help

Symptom:

- the `ds` container starts but does not actually run the server

Fix:

- set the DS service command to:

```yaml
command: ["start-ds"]
```

File:

- [compose.yaml](/Users/jarosz/projects/forgerock/am-standalone/compose.yaml)

### 2. DS rejects `server-id`

Symptom:

- DS fails with a message saying `ds.jrsz.org` is not a valid `server-id`

Cause:

- the stock DS Docker entrypoint derives `DS_SERVER_ID` from `HOSTNAME`
- the hostname contains dots, which DS does not accept for `server-id`

Fix:

- set:

```yaml
DS_SERVER_ID: ds-jrsz
```

in the `ds` service environment

### 3. AM entrypoint cannot find `catalina.sh`

Symptom:

- AM exits with `exec: catalina.sh: not found`

Cause:

- we are not using a stock Tomcat image with `catalina.sh` on `PATH`

Fix:

- use the full path:

```sh
exec "${CATALINA_HOME}/bin/catalina.sh" run
```

File:

- [docker/am/docker-entrypoint.sh](/Users/jarosz/projects/forgerock/am-standalone/docker/am/docker-entrypoint.sh)

Apply the same fix to the app entrypoints.

### 4. Tomcat TLS connector fails on 10.1

Symptom:

- Tomcat rejects connector attributes such as `keystoreFile`, `keystorePass`, and `sslProtocol`
- startup error mentions missing `SSLHostConfig`

Cause:

- old connector syntax was used

Fix:

- use `SSLHostConfig` and nested `Certificate` elements in Tomcat `10.1`

Files:

- [docker/am/server.xml.template](/Users/jarosz/projects/forgerock/am-standalone/docker/am/server.xml.template)
- [docker/app1/server.xml.template](/Users/jarosz/projects/forgerock/am-standalone/docker/app1/server.xml.template)
- [docker/app2/server.xml.template](/Users/jarosz/projects/forgerock/am-standalone/docker/app2/server.xml.template)
- [docker/app3/server.xml.template](/Users/jarosz/projects/forgerock/am-standalone/docker/app3/server.xml.template)

### 5. AM skips the install page and self-configures with `localtest.me`

Symptoms:

- Amster says AM no longer presents the configuration page
- generated AM files reference `am.localtest.me` and `ds.localtest.me`

Cause:

- AM was started with:

```text
-Dcom.sun.identity.sm.sms_object_filebased_enabled=true
```

- that forces AM into a baseline/file-based bootstrap path
- that path is wrong for DS-backed `install-openam`

Fix:

- remove that JVM option from:
  - [docker/am/setenv.sh](/Users/jarosz/projects/forgerock/am-standalone/docker/am/setenv.sh)

### 6. AM keeps reusing stale install state

Symptom:

- AM behaves as already installed even after config changes

Cause:

- the named AM home volume persists previous install state

Fix:

- point AM at a fresh named volume when resetting bootstrap
- current working volume name is defined in:
  - [compose.yaml](/Users/jarosz/projects/forgerock/am-standalone/compose.yaml)

When redoing bootstrap, do not keep reusing an already-initialized AM home unless that is intentional.

### 7. Amster bootstrap script wrongly skips install

Symptom:

- `amster-bootstrap` reaches AM but skips `install-openam`

Cause:

- the bootstrap script used an HTML text check against the configuration page
- that check was brittle

Fix:

- once AM is reachable, run `install-openam` directly and let Amster decide whether the server is already configured

File:

- [docker/amster/docker-entrypoint.sh](/Users/jarosz/projects/forgerock/am-standalone/docker/amster/docker-entrypoint.sh)

### 8. Gateway fails because `AmService` is incomplete

Symptoms:

- `agent.username: Expecting a value`
- `secretsProvider: Expecting a value`

Cause:

- `AmService` requires:
  - `agent`
  - `secretsProvider`

Fix:

- configure both correctly in:
  - [config/gateway/config.json](/Users/jarosz/projects/forgerock/am-standalone/config/gateway/config.json)

Current lab config uses:

- `amadmin` as the AM credential
- a file-based secret store for the password

### 9. Gateway route files parse but do not match correctly

Symptoms:

- route parse errors on regex escaping
- HTTP/2 requests hit `404`

Fix:

- do not use the earlier regex form
- use:

```json
"condition": "${request.uri.host == 'app1.jrsz.org'}"
```

This works for browser-style HTTPS requests and for HTTP/2.

Files:

- [config/gateway/routes/app1.json](/Users/jarosz/projects/forgerock/am-standalone/config/gateway/routes/app1.json)
- [config/gateway/routes/app2.json](/Users/jarosz/projects/forgerock/am-standalone/config/gateway/routes/app2.json)
- [config/gateway/routes/app3.json](/Users/jarosz/projects/forgerock/am-standalone/config/gateway/routes/app3.json)

### 10. Gateway starts but backend proxying returns `502`

Symptoms:

- SSO redirect works
- authenticated request returns `502 Bad Gateway`

Causes hit during this build:

1. app containers were serving Tomcat’s default `ROOT`
2. backend TLS hostname verification still failed on the reverse-proxy hop

Fixes:

1. replace Tomcat’s default `webapps` content in each app image
2. for the current lab only, use a separate backend TLS config with:

```json
"hostnameVerifier": "ALLOW_ALL"
```

Files:

- [docker/app1/Dockerfile](/Users/jarosz/projects/forgerock/am-standalone/docker/app1/Dockerfile)
- [docker/app2/Dockerfile](/Users/jarosz/projects/forgerock/am-standalone/docker/app2/Dockerfile)
- [docker/app3/Dockerfile](/Users/jarosz/projects/forgerock/am-standalone/docker/app3/Dockerfile)
- [config/gateway/config.json](/Users/jarosz/projects/forgerock/am-standalone/config/gateway/config.json)

This is intentionally not hardened yet.

## Fast validation commands

Check stack:

```bash
docker compose ps
```

Check AM bootstrap:

```bash
docker compose logs --tail=200 amster-bootstrap
```

Check Gateway:

```bash
docker compose logs --tail=200 gateway
```

Check unauthenticated redirect:

```bash
curl --http1.1 -k -I https://app1.jrsz.org
```

Expected:

- `302` to `https://am.jrsz.org:8443/am/...`

## Current known shortcuts

These are deliberate and should not be mistaken for final design:

- Gateway uses `amadmin` instead of a dedicated AM agent
- Gateway backend proxy uses `ALLOW_ALL` hostname verification
- Gateway JWT session keys are temporary

## Files that matter most

- [compose.yaml](/Users/jarosz/projects/forgerock/am-standalone/compose.yaml)
- [docker/am/setenv.sh](/Users/jarosz/projects/forgerock/am-standalone/docker/am/setenv.sh)
- [docker/am/docker-entrypoint.sh](/Users/jarosz/projects/forgerock/am-standalone/docker/am/docker-entrypoint.sh)
- [docker/amster/docker-entrypoint.sh](/Users/jarosz/projects/forgerock/am-standalone/docker/amster/docker-entrypoint.sh)
- [docker/ds/setup.sh](/Users/jarosz/projects/forgerock/am-standalone/docker/ds/setup.sh)
- [config/gateway/config.json](/Users/jarosz/projects/forgerock/am-standalone/config/gateway/config.json)
- [config/gateway/routes/app1.json](/Users/jarosz/projects/forgerock/am-standalone/config/gateway/routes/app1.json)
- [config/gateway/routes/app2.json](/Users/jarosz/projects/forgerock/am-standalone/config/gateway/routes/app2.json)
- [config/gateway/routes/app3.json](/Users/jarosz/projects/forgerock/am-standalone/config/gateway/routes/app3.json)

