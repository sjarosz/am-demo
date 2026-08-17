#!/usr/bin/env bash
#
# End-to-end check of ds.jrsz.net -> RCS -> bonaire05 user onboarding (docs/bonaire05-ldap-onboarding.md):
#   1. add a throw-away user to ds.jrsz.net (ou=people,ou=identities)
#   2. reconcile the LDAP application's mapping in bonaire05 -> the alpha_user must appear
#   3. delete the user from DS, reconcile again -> the alpha_user must be gone (DS is authoritative)
# Skip step 3 with --keep. Reads .env.com/.env for RCS_LDAP_*/BONAIRE_* defaults.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# .env.com has unquoted multi-word values; import only KEY=VALUE lines we care about.
while IFS='=' read -r k v; do
  case "$k" in RCS_*|BONAIRE_*|DS_ROOT_PASSWORD) [[ -z "${!k:-}" ]] && export "$k=${v%\"}" ;; esac
done < <(grep -h -E '^(RCS_|BONAIRE_|DS_ROOT_PASSWORD)[A-Z_]*=' .env.com .env 2>/dev/null | sed -E "s/^([A-Z_]+)=['\"]?([^'\"]*)['\"]?$/\1=\2/")

KEEP=false; [[ "${1:-}" == "--keep" ]] && KEEP=true
BONAIRE_AM_URL="${BONAIRE_AM_URL:-https://openam-bonaire05.forgeblocks.com/am}"
IDM="${BONAIRE_AM_URL%/am}/openidm"
PROFILE="${BONAIRE_FRODO_PROFILE:-openam-bonaire05}"
APP="${BONAIRE_LDAP_APP_NAME:-jrsz-ldap}"
CONNECTOR_ID="$(echo "$APP" | tr -cd 'A-Za-z0-9' | tr 'A-Z' 'a-z')"
REALM="${BONAIRE_REALM:-alpha}"
MAPPING="system$(echo "${CONNECTOR_ID:0:1}" | tr 'a-z' 'A-Z')${CONNECTOR_ID:1}User_managed$(echo "${REALM:0:1}" | tr 'a-z' 'A-Z')${REALM:1}_user"
DS_CONTAINER="${RCS_LDAP_HOST:-ds.jrsz.net}"
DS_PW="${DS_ROOT_PASSWORD:-changeit}"
USERS_DN="${RCS_LDAP_USERS_DN:-ou=people,ou=identities}"
U="smoke$(date +%s)"

TOK="$(frodo info "$PROFILE" --json 2>/dev/null | python3 -c 'import sys,json;s=sys.stdin.read();print(json.loads(s[s.find("{"):])["bearerToken"])')"
LDAP=(--noPropertiesFile --no-prompt --hostname localhost --port 1636 --useSsl --trustAll --bindDn uid=admin --bindPassword "$DS_PW")

recon() {
  local id
  id="$(curl -sS -X POST -H "Authorization: Bearer $TOK" \
    "$IDM/recon?_action=recon&mapping=$MAPPING&waitForCompletion=true" | python3 -c 'import sys,json;print(json.load(sys.stdin)["_id"])')"
  curl -sS -H "Authorization: Bearer $TOK" "$IDM/recon/$id" \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print("   recon",d.get("_id","?")[-8:],d.get("state"),d.get("statusSummary"),{k:v for k,v in (d.get("situationSummary") or {}).items() if v})'
}
count() {
  curl -sS -G -H "Authorization: Bearer $TOK" "$IDM/managed/${REALM}_user" \
    --data-urlencode "_queryFilter=userName eq \"$U\"" --data-urlencode "_fields=userName,givenName,sn,mail,accountStatus" \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["resultCount"]);[print("   ",json.dumps(r)) for r in d["result"]]'
}

echo "1. adding uid=$U to $DS_CONTAINER ($USERS_DN)"
docker exec -i "$DS_CONTAINER" /opt/opendj/bin/ldapmodify "${LDAP[@]}" <<LDIF | sed 's/^/   /'
dn: uid=$U,$USERS_DN
changetype: add
objectClass: top
objectClass: person
objectClass: organizationalperson
objectClass: inetorgperson
objectClass: inetuser
uid: $U
cn: Smoke Test
sn: Test
givenName: Smoke
mail: $U@jrsz.net
telephoneNumber: +1 555 0199
inetUserStatus: Active
userPassword: Jrsz\$2025!
LDIF

echo "2. reconciling $MAPPING in bonaire05"
recon
N="$(count | head -1)"; count | tail -n +2
[[ "$N" == "1" ]] && echo "   PASS: $U onboarded as ${REALM}_user" || { echo "   FAIL: expected 1 ${REALM}_user named $U, got $N"; exit 1; }

$KEEP && { echo "   (--keep: leaving $U in place)"; exit 0; }

echo "3. deleting uid=$U from DS and reconciling again"
docker exec "$DS_CONTAINER" /opt/opendj/bin/ldapdelete "${LDAP[@]}" "uid=$U,$USERS_DN" | sed 's/^/   /'
recon
N="$(count | head -1)"
[[ "$N" == "0" ]] && echo "   PASS: $U removed from bonaire05 (SOURCE_MISSING -> DELETE)" || { echo "   FAIL: $U still present ($N)"; exit 1; }
echo "OK"
