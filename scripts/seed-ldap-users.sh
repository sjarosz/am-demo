#!/usr/bin/env bash
# Seed demo users into the AM identity store (ou=people,ou=identities) of the
# jrsz.net DS (default) or the jrsz.org DS, then list what is there.
#
# Usage: scripts/seed-ldap-users.sh [com|org] [ldif]
#   com -> ds.jrsz.net (.env.com)   org -> ds.jrsz.org (.env)
# Idempotent: existing entries are reported ("already exists") and skipped.
# These users are what the bonaire05 LDAP application (docs/bonaire05-ldap-onboarding.md)
# onboards as alpha_users; DS is authoritative, so deleting one here removes it there on the next recon.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SIDE="${1:-com}"
case "$SIDE" in
  com) CONTAINER=ds.jrsz.net; ENV_FILE=.env.com ;;
  org) CONTAINER=ds.jrsz.org; ENV_FILE=.env ;;
  *) echo "usage: $0 [com|org] [ldif]" >&2; exit 2 ;;
esac
LDIF="${2:-config/ds/seed-users.ldif}"
PW="$(grep -E '^DS_ROOT_PASSWORD=' "$ENV_FILE" | cut -d= -f2- | tr -d "\"'" || true)"
PW="${PW:-changeit}"

LDAP=(--noPropertiesFile --no-prompt --hostname localhost --port 1636 --useSsl --trustAll --bindDn uid=admin --bindPassword "$PW")

echo "[seed-ldap-users] adding $(grep -c '^dn:' "$LDIF") entries from $LDIF into $CONTAINER"
docker exec -i "$CONTAINER" /opt/opendj/bin/ldapmodify "${LDAP[@]}" --continueOnError < "$LDIF" \
  | grep -E 'ADD operation|already exists|error|ERROR|Result Code' | sed 's/^/    /' || true

echo "[seed-ldap-users] users now in ou=people,ou=identities on $CONTAINER:"
docker exec "$CONTAINER" /opt/opendj/bin/ldapsearch "${LDAP[@]}" \
  --baseDn ou=people,ou=identities '(objectClass=inetOrgPerson)' uid mail inetUserStatus \
  | awk '/^uid:/{u=$2} /^mail:/{m=$2} /^inetUserStatus:/{s=$2} /^$/{if(u!="")printf "    %-20s %-30s %s\n",u,m,s; u="";m="";s=""}'
