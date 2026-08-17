#!/usr/bin/env python3
"""Onboard ds.jrsz.net users into the bonaire05 AIC tenant through the lab RCS.

Tenant half of the rcs-com service (compose.com.yaml). Idempotent; re-run any time.
What it configures in bonaire05 (realm alpha, IDM):

  1. OAuth2 client   <RCS_CLIENT_ID>   client_credentials, scope fr:idm:*, client_secret_basic
                     (secret = secrets/rcs/client-secret; the same value the RCS container uses)
  2. authentication.rsFilter.staticUserMapping += {subject: <RCS_CLIENT_ID>, roles: [rcsclient-authorized]}
     (a custom RCS client id is otherwise refused by IDM's /openicf websocket endpoint)
     provisioner.openicf.connectorinfoprovider.remoteConnectorClients += {name: <RCS_NAME>}
  3. provisioner.openicf/<CONNECTOR_ID>  LDAP connector hosted on that RCS -> ds.jrsz.net:1636 (LDAPS)
                     based on the official "Directory Services (DS)" application template (ds.ldap 2.6)
  4. managed alpha_application "<BONAIRE_LDAP_APP_NAME>" (templateName ds.ldap, authoritative=true)
  5. config/mapping/system<Connectorid>User_managedAlpha_user  -- DS is AUTHORITATIVE:
                     ABSENT->CREATE, FOUND/CONFIRMED->UPDATE (correlate on userName == uid),
                     SOURCE_MISSING/UNQUALIFIED->DELETE, no password provisioned
  6. scheduler job recon-<CONNECTOR_ID>-alpha_user  cron every 15 min -> reconcile that mapping

Then (unless --no-recon) it tests the connector (needs the RCS container connected) and runs a
first reconciliation, printing the situation summary.

Usage:  scripts/provision_bonaire05_ldap_app.py [--dry-run] [--register-only] [--no-recon] [--recon-only] [--delete]
  --register-only  steps 1-3 only (safe before the RCS container exists; used by scripts/setup-ldap-onboarding.sh)
Env (from .env.com / .env, or the shell): RCS_NAME RCS_CLIENT_ID RCS_LDAP_HOST RCS_LDAP_PORT
  RCS_LDAP_BIND_DN RCS_LDAP_BIND_PASSWORD RCS_LDAP_BASE_DN RCS_LDAP_USERS_DN BONAIRE_LDAP_APP_NAME
  BONAIRE_AM_URL BONAIRE_REALM BONAIRE_FRODO_PROFILE RCS_CLIENT_SECRET (else secrets/rcs/client-secret)
Admin access uses the saved frodo connection profile (`frodo info <profile> --json`), like
scripts/provision_bonaire05_trust.py.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path):
    if not os.path.isfile(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)


load_env(os.path.join(ROOT, ".env.com"))
load_env(os.path.join(ROOT, ".env"))

E = os.environ.get
REMOTE_AM = (E("BONAIRE_AM_URL") or "https://openam-bonaire05.forgeblocks.com/am").rstrip("/")
IDM = REMOTE_AM[: -len("/am")] + "/openidm"
REALM = E("BONAIRE_REALM") or "alpha"
FRODO_PROFILE = E("BONAIRE_FRODO_PROFILE") or "openam-bonaire05"

RCS_NAME = E("RCS_NAME") or "jrsz-rcs"
CLIENT_ID = E("RCS_CLIENT_ID") or "RCSjrsz-rcs"
SECRET_FILE = os.path.join(ROOT, "secrets", "rcs", "client-secret")
LDAP_HOST = E("RCS_LDAP_HOST") or "ds.jrsz.net"
LDAP_PORT = int(E("RCS_LDAP_PORT") or "1636")
LDAP_BIND_DN = E("RCS_LDAP_BIND_DN") or "uid=am-identity-bind-account,ou=admins,ou=identities"
LDAP_BIND_PW = E("RCS_LDAP_BIND_PASSWORD") or E("DS_AM_PROFILE_PASSWORD") or "changeit"
LDAP_BASE_DN = E("RCS_LDAP_BASE_DN") or "ou=identities"
LDAP_USERS_DN = E("RCS_LDAP_USERS_DN") or "ou=people,ou=identities"
APP_NAME = E("BONAIRE_LDAP_APP_NAME") or "jrsz-ldap"
# IDM ids: connector id must be a plain identifier (it becomes system/<id>/User and part of the mapping name)
CONNECTOR_ID = re.sub(r"[^A-Za-z0-9]", "", APP_NAME).lower() or "jrszldap"
OBJECT_TYPE = "User"
MAPPING = f"system{CONNECTOR_ID.capitalize()}{OBJECT_TYPE}_managed{REALM.capitalize()}_user"
SCHEDULE_ID = f"recon-{CONNECTOR_ID}-{REALM}_user"
TEMPLATE_NAME, TEMPLATE_VERSION = "ds.ldap", "2.6"
ICON = "https://cdn.forgerock.com/platform/app-templates/images/fr-ds.svg"
RECON_CRON = E("BONAIRE_LDAP_RECON_CRON") or "0 0/15 * * * ?"

VER_AGENT = "protocol=2.0,resource=1.0"


def log(msg):
    print(f"  [bonaire05-ldap] {msg}", flush=True)


def client_secret():
    s = E("RCS_CLIENT_SECRET")
    if s:
        return s
    if os.path.isfile(SECRET_FILE):
        return open(SECRET_FILE, encoding="utf-8").read().strip()
    raise SystemExit(f"no RCS_CLIENT_SECRET and {SECRET_FILE} missing; create it (openssl rand -base64 24)")


def frodo_token():
    try:
        out = subprocess.check_output(["frodo", "info", FRODO_PROFILE, "--json"], text=True, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        raise SystemExit("frodo CLI not found (brew install frodo-cli); needed to mint a bonaire05 admin token")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"frodo info {FRODO_PROFILE} failed ({e.returncode}); check `frodo conn list`")
    tok = json.loads(out[out.find("{"):]).get("bearerToken")
    if not tok:
        raise SystemExit("frodo info returned no bearerToken")
    return tok


class Api:
    def __init__(self, tok):
        self.tok = tok

    def call(self, method, url, body=None, headers=None, timeout=120):
        h = {"Authorization": f"Bearer {self.tok}", "Content-Type": "application/json", "Accept": "application/json"}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read().decode() or "{}"
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw}

    # AM realm-config (agents) -----------------------------------------------------------
    def am(self, method, path, body=None, exists=None):
        h = {"Accept-API-Version": VER_AGENT}
        if method == "PUT":
            h["If-Match" if exists else "If-None-Match"] = "*"
        return self.call(method, f"{REMOTE_AM}/json/realms/root/realms/{REALM}/{path}", body, h)

    # IDM ----------------------------------------------------------------------------------
    def idm(self, method, path, body=None, params=None):
        url = f"{IDM}/{path}"
        if method == "PUT" and path.startswith("config/"):
            params = dict(params or {}, waitForCompletion="true")
        if params:
            url += "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        return self.call(method, url, body)


def must(st, resp, what, ok=(200, 201)):
    if st not in ok:
        raise SystemExit(f"{what} failed: HTTP {st}: {json.dumps(resp)[:600]}")
    return resp


# ---------------------------------------------------------------------------------------
# 1. OAuth2 client used by the RCS
# ---------------------------------------------------------------------------------------
def ensure_oauth2_client(api, secret, dry):
    path = f"realm-config/agents/OAuth2Client/{CLIENT_ID}"
    st, cur = api.am("GET", path)
    exists = st == 200
    body = {
        "coreOAuth2ClientConfig": {
            "userpassword": secret,
            "clientType": "Confidential",
            "scopes": ["fr:idm:*"],
            "defaultScopes": [],
            "status": "Active",
            "clientName": [f"Remote Connector Server {RCS_NAME} (am-demo rcs.jrsz.net)"],
        },
        "advancedOAuth2ClientConfig": {
            "grantTypes": ["client_credentials"],
            "tokenEndpointAuthMethod": "client_secret_basic",
            "isConsentImplied": True,
            "descriptions": ["OAuth2 client the am-demo RCS uses to open its websocket to IDM"],
        },
    }
    if dry:
        log(f"dry-run: would {'update' if exists else 'create'} OAuth2Client {CLIENT_ID}")
        return
    st, resp = api.am("PUT", path, body, exists=exists)
    must(st, resp, f"PUT OAuth2Client {CLIENT_ID}")
    log(f"OAuth2Client {CLIENT_ID} {'updated' if exists else 'created'} (client_credentials, fr:idm:*)")


# ---------------------------------------------------------------------------------------
# 2a. Let that client through IDM's rsFilter (the /openicf websocket needs role rcsclient-authorized)
# ---------------------------------------------------------------------------------------
STATIC_MAPPING = {"subject": CLIENT_ID, "localUser": "internal/user/connector-server-client",
                  "roles": ["rcsclient-authorized"]}


def ensure_auth_mapping(api, dry):
    st, auth = api.idm("GET", "config/authentication")
    must(st, auth, "GET config/authentication")
    sm = auth.setdefault("rsFilter", {}).setdefault("staticUserMapping", [])
    if any(m.get("subject") == CLIENT_ID and "rcsclient-authorized" in (m.get("roles") or []) for m in sm):
        log(f"authentication staticUserMapping for {CLIENT_ID} already present")
        return
    sm[:] = [m for m in sm if m.get("subject") != CLIENT_ID] + [STATIC_MAPPING]
    if dry:
        log(f"dry-run: would add rsFilter.staticUserMapping subject={CLIENT_ID} roles=[rcsclient-authorized]")
        return
    body = {k: v for k, v in auth.items() if k not in ("_id", "_rev")}
    st, resp = api.idm("PUT", "config/authentication", body)
    must(st, resp, "PUT config/authentication")
    log(f"authentication staticUserMapping added: {CLIENT_ID} -> internal/user/connector-server-client [rcsclient-authorized]")


# ---------------------------------------------------------------------------------------
# 2b. Register the connector server name
# ---------------------------------------------------------------------------------------
def ensure_connector_server(api, dry):
    st, cip = api.idm("GET", "config/provisioner.openicf.connectorinfoprovider")
    must(st, cip, "GET connectorinfoprovider")
    clients = cip.setdefault("remoteConnectorClients", [])
    for c in clients:
        if c.get("name") == RCS_NAME:
            if c.get("enabled") and c.get("useSSL"):
                log(f"connector server '{RCS_NAME}' already registered")
                return
            c["enabled"], c["useSSL"] = True, True
            break
    else:
        clients.append({"name": RCS_NAME, "clientId": CLIENT_ID, "enabled": True, "useSSL": True})
    if dry:
        log(f"dry-run: would register connector server '{RCS_NAME}'")
        return
    body = {k: v for k, v in cip.items() if k not in ("_id", "_rev")}
    st, resp = api.idm("PUT", "config/provisioner.openicf.connectorinfoprovider", body)
    must(st, resp, "PUT connectorinfoprovider")
    log(f"connector server '{RCS_NAME}' registered (remoteConnectorClients)")


# ---------------------------------------------------------------------------------------
# 3. LDAP connector (provisioner) hosted on the RCS
# ---------------------------------------------------------------------------------------
def prop(name, native=None, ptype="string", **kw):
    p = {"type": ptype, "nativeName": native or name, "nativeType": kw.pop("nativeType", "string")}
    p.update(kw)
    return p


def provisioner_body():
    user_props = {
        "dn": prop("dn", "__NAME__", required=True),
        "objectClass": {"type": "array", "items": {"type": "string", "nativeType": "string"},
                        "nativeName": "objectClass", "nativeType": "string",
                        "flags": ["NOT_CREATABLE", "NOT_UPDATEABLE"]},
        "cn": prop("cn", required=True),
        "sn": prop("sn", required=True),
        "uid": prop("uid"),
        "userPassword": {"type": "string", "nativeName": "__PASSWORD__", "nativeType": "JAVA_TYPE_GUARDEDSTRING",
                         "flags": ["NOT_READABLE", "NOT_RETURNED_BY_DEFAULT"], "autocomplete": "new-password"},
        "givenName": prop("givenName"),
        "displayName": prop("displayName"),
        "mail": prop("mail"),
        "description": prop("description"),
        "telephoneNumber": prop("telephoneNumber"),
        "title": prop("title"),
        "ou": {"type": "array", "items": {"type": "string", "nativeType": "string"}, "nativeName": "ou", "nativeType": "string"},
        "employeeNumber": prop("employeeNumber"),
        "employeeType": {"type": "array", "items": {"type": "string", "nativeType": "string"}, "nativeName": "employeeType", "nativeType": "string"},
        "inetUserStatus": prop("inetUserStatus"),
        "entryUUID": prop("entryUUID", flags=["NOT_CREATABLE", "NOT_UPDATEABLE"]),
        "createTimestamp": prop("createTimestamp", flags=["NOT_CREATABLE", "NOT_UPDATEABLE"]),
        "modifyTimestamp": prop("modifyTimestamp", flags=["NOT_CREATABLE", "NOT_UPDATEABLE"]),
    }
    cfg = {
        # connection
        "host": LDAP_HOST, "port": LDAP_PORT, "ssl": True, "startTLS": False,
        "principal": LDAP_BIND_DN, "credentials": LDAP_BIND_PW,
        "authType": "simple", "gssapiLoginContext": None,
        "hostNameVerification": False, "hostNameVerifierPattern": None,
        "alternateKeyStore": None, "alternateKeyStorePassword": None, "alternateKeyStoreType": None,
        "privateKeyAlias": None, "failover": [], "useDNSSRVRecord": False,
        "connectionTimeout": 30000, "checkAliveMinInterval": 60, "referralsHandling": "follow",
        "sendCAUDTxId": False,
        # accounts (from the ds.ldap 2.6 template defaults + this lab's layout)
        "baseContexts": [LDAP_USERS_DN],
        "baseContextsToSynchronize": [LDAP_USERS_DN],
        "accountObjectClasses": ["top", "person", "organizationalPerson", "inetOrgPerson"],
        "accountSearchFilter": "(objectClass=inetOrgPerson)",
        "accountSynchronizationFilter": None,
        "accountUserNameAttributes": ["uid"],
        "uidAttribute": "entryUUID",
        "passwordAttribute": "userPassword",
        "passwordHashAlgorithm": None,
        "respectResourcePasswordPolicyChangeAfterReset": False,
        # groups (unused, but keep the connector's DS defaults)
        "groupObjectClasses": ["top", "groupOfUniqueNames"],
        "groupSearchFilter": None, "groupSynchronizationFilter": None,
        "groupMemberAttribute": "uniqueMember", "getGroupMemberId": True,
        "ldapGroupsUseStaticGroups": False, "maintainLdapGroupMembership": False,
        "maintainPosixGroupMembership": False, "allowTreeDelete": False,
        # search / paging
        "readSchema": False, "usePagedResultControl": True, "useBlocks": False, "blockSize": 100,
        "vlvSortAttribute": "uid", "filterWithOrInsteadOfAnd": False, "excludeUnmodified": True,
        "attributesToSynchronize": [], "objectClassesToSynchronize": ["inetOrgPerson"],
        "modifiersNamesToFilterOut": [], "customOctetStringAttributes": [],
        # sync / changelog (not used: recon-driven)
        "changeLogBlockSize": 100, "changeNumberAttribute": "changeNumber",
        "removeLogEntryObjectClassFromFilter": True, "useTimestampsForSync": False,
        "timestampSyncOffset": 0, "resetSyncToken": "never",
        # AD-only knobs left at neutral values
        "convertADIntervalToISO8601": [], "convertGTToISO8601": [], "useOldADGUIDFormat": False,
    }
    return {
        "connectorRef": {
            "connectorHostRef": RCS_NAME,
            "bundleName": "org.forgerock.openicf.connectors.ldap-connector",
            "bundleVersion": "[1.5.20.11, 1.6.0.0)",
            "connectorName": "org.identityconnectors.ldap.LdapConnector",
            "displayName": "LDAP Connector",
            "systemType": "provisioner.openicf",
        },
        "poolConfigOption": {"maxObjects": 10, "maxIdle": 10, "maxWait": 150000,
                             "minEvictableIdleTimeMillis": 120000, "minIdle": 1},
        "resultsHandlerConfig": {"enableNormalizingResultsHandler": False, "enableFilteredResultsHandler": False,
                                 "enableCaseInsensitiveFilter": False, "enableAttributesToGetSearchResultsHandler": True},
        "operationTimeout": {op: -1 for op in ("CREATE", "UPDATE", "DELETE", "TEST", "SCRIPT_ON_CONNECTOR",
                                                "SCRIPT_ON_RESOURCE", "GET", "RESOLVEUSERNAME", "AUTHENTICATE",
                                                "SEARCH", "VALIDATE", "SYNC", "SCHEMA")},
        "configurationProperties": cfg,
        "enabled": True,
        "objectTypes": {
            OBJECT_TYPE: {
                "$schema": "http://json-schema.org/draft-03/schema",
                "id": "__ACCOUNT__", "nativeType": "__ACCOUNT__", "type": "object",
                "properties": user_props,
            }
        },
    }


def wait_for_rcs(api, wait_s=180):
    """Poll system?_action=testConnectorServers until our RCS reports ok (IDM needs the RCS connected
    before it can create/encrypt a provisioner that references it)."""
    deadline = time.time() + wait_s
    while True:
        st, resp = api.idm("POST", "system", params={"_action": "testConnectorServers"})
        entry = next((c for c in (resp.get("openicf") or []) if c.get("name") == RCS_NAME), None) if st == 200 else None
        if entry and entry.get("ok"):
            log(f"connector server '{RCS_NAME}' is connected")
            return True
        if time.time() > deadline:
            log(f"connector server '{RCS_NAME}' NOT connected after {wait_s}s (testConnectorServers: {json.dumps(entry)})")
            log("start it with: docker compose up -d --build rcs-com ; docker logs -f rcs.jrsz.net")
            return False
        time.sleep(10)


def ensure_provisioner(api, dry):
    path = f"config/provisioner.openicf/{CONNECTOR_ID}"
    st, cur = api.idm("GET", path)
    exists = st == 200
    body = provisioner_body()
    if dry:
        log(f"dry-run: would {'update' if exists else 'create'} {path} (RCS {RCS_NAME} -> ldaps://{LDAP_HOST}:{LDAP_PORT} {LDAP_USERS_DN})")
        return
    st, resp = api.idm("PUT", path, body)
    must(st, resp, f"PUT {path}")
    log(f"provisioner.openicf/{CONNECTOR_ID} {'updated' if exists else 'created'} "
        f"(connectorHostRef={RCS_NAME}, ldaps://{LDAP_HOST}:{LDAP_PORT}, base {LDAP_USERS_DN})")


# ---------------------------------------------------------------------------------------
# 4. Mapping (DS authoritative)
# ---------------------------------------------------------------------------------------
def mapping_body():
    src = f"system/{CONNECTOR_ID}/{OBJECT_TYPE}"
    tgt = f"managed/{REALM}_user"
    return {
        "name": MAPPING,
        "displayName": MAPPING,
        "source": src,
        "target": tgt,
        "consentRequired": False,
        "icon": None,
        "runTargetPhase": True,           # needed so SOURCE_MISSING (deleted in DS) is detected and acted on
        "reconSourceQueryPaging": True,
        "reconSourceQueryPageSize": 1000,
        "sourceQueryFullEntry": True,
        "allowEmptySourceSet": False,     # safety: an empty LDAP result set must never wipe the tenant
        "correlationQuery": [{
            "linkQualifier": "default",
            "type": "text/javascript",
            "source": "var qry = {'_queryFilter': 'userName eq \"' + source.uid + '\"'}; qry",
        }],
        "properties": [
            {"source": "uid", "target": "userName"},
            {"source": "givenName", "target": "givenName"},
            {"source": "sn", "target": "sn"},
            {"source": "mail", "target": "mail"},
            {"source": "", "target": "cn", "transform": {"type": "text/javascript",
                "source": "source.cn || (source.givenName + ' ' + source.sn)"}},
            {"source": "telephoneNumber", "target": "telephoneNumber"},
            {"source": "description", "target": "description"},
            {"source": "", "target": "accountStatus", "transform": {"type": "text/javascript",
                "source": "(source.inetUserStatus && source.inetUserStatus.toLowerCase() === 'inactive') ? 'inactive' : 'active'"}},
        ],
        # DS is the authoritative source: create/update/delete flow one way only.
        "policies": [
            {"situation": "ABSENT", "action": "CREATE"},
            {"situation": "MISSING", "action": "CREATE"},
            {"situation": "FOUND", "action": "UPDATE"},
            {"situation": "CONFIRMED", "action": "UPDATE"},
            {"situation": "FOUND_ALREADY_LINKED", "action": "EXCEPTION"},
            {"situation": "AMBIGUOUS", "action": "EXCEPTION"},
            {"situation": "SOURCE_MISSING", "action": "DELETE"},
            {"situation": "UNQUALIFIED", "action": "DELETE"},
            {"situation": "UNASSIGNED", "action": "IGNORE"},
            {"situation": "LINK_ONLY", "action": "EXCEPTION"},
            {"situation": "TARGET_IGNORED", "action": "IGNORE"},
            {"situation": "SOURCE_IGNORED", "action": "IGNORE"},
            {"situation": "ALL_GONE", "action": "IGNORE"},
        ],
    }


def ensure_mapping(api, dry):
    # Modern AIC keeps one config object per mapping (config/mapping/<name>); sync.json is legacy.
    path = f"config/mapping/{MAPPING}"
    st, cur = api.idm("GET", path)
    exists = st == 200
    new = mapping_body()
    if exists and all(cur.get(k) == v for k, v in new.items()):
        log(f"mapping {MAPPING} already up to date")
        return
    if dry:
        log(f"dry-run: would {'update' if exists else 'create'} {path}")
        return
    st, resp = api.idm("PUT", path, new)
    must(st, resp, f"PUT {path}")
    log(f"mapping {MAPPING} {'updated' if exists else 'created'} "
        f"(system/{CONNECTOR_ID}/{OBJECT_TYPE} -> managed/{REALM}_user, DS authoritative)")


# ---------------------------------------------------------------------------------------
# 5. Application object (shows under Applications in the admin UI)
# ---------------------------------------------------------------------------------------
def ensure_application(api, dry):
    st, q = api.idm("GET", f"managed/{REALM}_application",
                    params={"_queryFilter": f'name eq "{APP_NAME}"', "_fields": "_id,name,connectorId,mappingNames,authoritative,templateName,templateVersion"})
    must(st, q, "query applications")
    cur = (q.get("result") or [None])[0]
    wanted = {
        "name": APP_NAME,
        "description": f"PingDS ds.jrsz.net (am-demo lab) via RCS {RCS_NAME} - authoritative source of alpha users",
        "templateName": TEMPLATE_NAME,
        "templateVersion": TEMPLATE_VERSION,
        "authoritative": True,
        "icon": ICON,
        "connectorId": CONNECTOR_ID,
        "mappingNames": [MAPPING],
        "uiConfig": {"objectTypes": {OBJECT_TYPE: {"properties": {
            n: {"displayName": n, "order": i, "userSpecific": True, **({"isDisplay": True} if n == "uid" else {})}
            for i, n in enumerate(["uid", "cn", "givenName", "sn", "mail", "telephoneNumber", "inetUserStatus",
                                   "displayName", "employeeNumber", "title", "ou", "dn"])}}}},
    }
    if cur and all(cur.get(k) == v for k, v in wanted.items() if k in ("connectorId", "mappingNames", "authoritative", "templateName", "templateVersion")):
        log(f"application '{APP_NAME}' already present ({cur['_id']})")
        return cur["_id"]
    if dry:
        log(f"dry-run: would {'update' if cur else 'create'} application '{APP_NAME}'")
        return None
    if cur:
        patch = [{"operation": "replace", "field": "/" + k, "value": v} for k, v in wanted.items()]
        st, resp = api.idm("PATCH", f"managed/{REALM}_application/{cur['_id']}", patch)
        must(st, resp, "PATCH application")
        log(f"application '{APP_NAME}' updated ({cur['_id']})")
        return cur["_id"]
    st, resp = api.idm("POST", f"managed/{REALM}_application", wanted, params={"_action": "create"})
    must(st, resp, "create application")
    log(f"application '{APP_NAME}' created ({resp.get('_id')}, template {TEMPLATE_NAME} {TEMPLATE_VERSION}, authoritative)")
    return resp.get("_id")


# ---------------------------------------------------------------------------------------
# 6. Scheduled recon
# ---------------------------------------------------------------------------------------
def ensure_schedule(api, dry):
    body = {
        "enabled": True, "persisted": True, "recoverable": False, "misfirePolicy": "fireAndProceed",
        "type": "cron", "schedule": RECON_CRON, "concurrentExecution": False,
        "invokeService": "sync", "invokeLogLevel": "info",
        "invokeContext": {"action": "reconcile", "mapping": MAPPING},
    }
    st, cur = api.idm("GET", f"scheduler/job/{SCHEDULE_ID}")
    exists = st == 200
    if exists and cur.get("schedule") == RECON_CRON and cur.get("enabled") and (cur.get("invokeContext") or {}).get("mapping") == MAPPING:
        log(f"schedule {SCHEDULE_ID} already up to date ({RECON_CRON})")
        return
    if dry:
        log(f"dry-run: would {'update' if exists else 'create'} schedule {SCHEDULE_ID} ({RECON_CRON})")
        return
    st, resp = api.idm("PUT", f"scheduler/job/{SCHEDULE_ID}", body)
    must(st, resp, f"PUT scheduler/job/{SCHEDULE_ID}")
    log(f"schedule {SCHEDULE_ID} {'updated' if exists else 'created'}: cron '{RECON_CRON}' -> reconcile {MAPPING}")


# ---------------------------------------------------------------------------------------
# 7. Test + first recon
# ---------------------------------------------------------------------------------------
def test_connector(api, wait_s=180):
    deadline = time.time() + wait_s
    while True:
        st, resp = api.idm("POST", f"system/{CONNECTOR_ID}", params={"_action": "test"})
        if st == 200 and resp.get("ok"):
            log(f"connector test OK (system/{CONNECTOR_ID} via RCS {RCS_NAME})")
            return True
        if time.time() > deadline:
            log(f"connector test FAILED after {wait_s}s: HTTP {st} {json.dumps(resp)[:300]}")
            log("is rcs.jrsz.net up and connected?  docker compose up -d rcs-com && docker logs -f rcs.jrsz.net")
            return False
        time.sleep(10)


def run_recon(api, _rerun=False):
    st, resp = api.idm("POST", "recon", params={"_action": "recon", "mapping": MAPPING, "waitForCompletion": "true"})
    must(st, resp, "recon")
    rid = resp.get("_id")
    st, r = api.idm("GET", f"recon/{rid}")
    must(st, r, "recon status")
    summ = r.get("statusSummary") or {}
    log(f"recon {rid} {r.get('state')} {r.get('stage')}: {summ}")
    sit = (r.get("situationSummary") or {})
    log("situations: " + ", ".join(f"{k}={v}" for k, v in sorted(sit.items()) if v))
    if sit.get("FOUND_ALREADY_LINKED") and not _rerun:
        # DS was re-created (new entryUUIDs): pass 1 deletes the stale links/targets in the target
        # phase, pass 2 re-creates/links the users from the new source ids.
        log("FOUND_ALREADY_LINKED present (source ids changed, e.g. DS volume re-created) -> running recon again")
        return run_recon(api, _rerun=True)
    if summ.get("FAILURE"):
        st, aud = api.idm("GET", f"recon/assoc/{rid}/entry", params={"_queryFilter": 'status eq "FAILURE"', "_pageSize": "20"})
        for a in (aud.get("result") or []) if st == 200 else []:
            log(f"  FAILURE {a.get('sourceObjectId')} {a.get('situation')}: {str(a.get('message'))[:200]}")
        if st != 200:
            log(f"  (details: admin UI > Applications > {APP_NAME} > Reconciliation, recon id {rid})")
    return r


def delete_all(api):
    log("deleting the LDAP application, mapping, connector, schedule, RCS registration and OAuth2 client")
    st, q = api.idm("GET", f"managed/{REALM}_application", params={"_queryFilter": f'name eq "{APP_NAME}"', "_fields": "_id"})
    for a in q.get("result", []):
        api.idm("DELETE", f"managed/{REALM}_application/{a['_id']}")
    api.idm("DELETE", f"scheduler/job/{SCHEDULE_ID}")
    api.idm("DELETE", f"config/mapping/{MAPPING}")
    api.idm("DELETE", f"config/provisioner.openicf/{CONNECTOR_ID}")
    st, auth = api.idm("GET", "config/authentication")
    if st == 200:
        sm = auth.get("rsFilter", {}).get("staticUserMapping", [])
        auth["rsFilter"]["staticUserMapping"] = [m for m in sm if m.get("subject") != CLIENT_ID]
        api.idm("PUT", "config/authentication", {k: v for k, v in auth.items() if k not in ("_id", "_rev")})
    st, cip = api.idm("GET", "config/provisioner.openicf.connectorinfoprovider")
    if st == 200:
        cip["remoteConnectorClients"] = [c for c in cip.get("remoteConnectorClients", []) if c.get("name") != RCS_NAME]
        api.idm("PUT", "config/provisioner.openicf.connectorinfoprovider", {k: v for k, v in cip.items() if k not in ("_id", "_rev")})
    api.am("DELETE", f"realm-config/agents/OAuth2Client/{CLIENT_ID}")
    log("done (links and already-created alpha_users are left in place)")


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    no_recon = "--no-recon" in args
    recon_only = "--recon-only" in args
    register_only = "--register-only" in args
    log(f"tenant {REMOTE_AM} realm {REALM}; RCS '{RCS_NAME}' client {CLIENT_ID}; connector {CONNECTOR_ID}; "
        f"mapping {MAPPING}; app '{APP_NAME}'")
    tok = None if dry and recon_only else frodo_token()
    api = Api(tok) if tok else None
    if "--delete" in args:
        delete_all(api)
        return 0
    if not recon_only:
        secret = client_secret()
        if dry:
            api = Api(frodo_token())
        ensure_oauth2_client(api, secret, dry)
        ensure_auth_mapping(api, dry)
        ensure_connector_server(api, dry)
        if register_only:
            return 0
        if not dry and not wait_for_rcs(api):
            return 1
        ensure_provisioner(api, dry)
        ensure_mapping(api, dry)
        ensure_application(api, dry)
        ensure_schedule(api, dry)
    if dry or no_recon:
        return 0
    if not test_connector(api):
        return 1
    run_recon(api)
    return 0


if __name__ == "__main__":
    sys.exit(main())
