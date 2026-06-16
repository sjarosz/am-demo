#!/bin/sh
set -eu

: "${KEYSTORE_FILE:?KEYSTORE_FILE is required}"
: "${KEYSTORE_PASSWORD:?KEYSTORE_PASSWORD is required}"

sed \
  -e "s|@@KEYSTORE_FILE@@|${KEYSTORE_FILE}|g" \
  -e "s|@@KEYSTORE_PASSWORD@@|${KEYSTORE_PASSWORD}|g" \
  -e "s|@@HTTPS_PORT@@|${AM_HTTPS_PORT:-8443}|g" \
  "${CATALINA_HOME}/conf/server.xml.template" > "${CATALINA_HOME}/conf/server.xml"

exec "${CATALINA_HOME}/bin/catalina.sh" run
