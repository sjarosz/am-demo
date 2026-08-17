#!/bin/bash
# RCS container entrypoint: render conf/ConnectorServer.properties from env,
# add the lab CA (PingDS LDAPS cert issuer) to a private copy of the JDK
# truststore, then hand off to the vendor bin/docker-entrypoint.sh.
set -euo pipefail

HOME_DIR="${CONNECTOR_SERVER_HOME:-/opt/openicf}"
PROPS="${HOME_DIR}/conf/ConnectorServer.properties"
TEMPLATE="${HOME_DIR}/conf/ConnectorServer.properties.template"

# The tenant OAuth2 client secret may come from env or (preferred, since .env.com is
# tracked in git) from a mounted file: secrets/rcs/client-secret.
SECRET_FILE="${RCS_CLIENT_SECRET_FILE:-/run/secrets/rcs/client-secret}"
if [ -z "${RCS_CLIENT_SECRET:-}" ] && [ -r "$SECRET_FILE" ]; then
  RCS_CLIENT_SECRET="$(tr -d '\r\n' < "$SECRET_FILE")"
  export RCS_CLIENT_SECRET
fi

: "${RCS_URL:?RCS_URL is required (wss://openam-<tenant>.forgeblocks.com/openicf/0)}"
: "${RCS_NAME:?RCS_NAME is required (must match remoteConnectorClients[].name in the tenant)}"
: "${RCS_TOKEN_ENDPOINT:?RCS_TOKEN_ENDPOINT is required}"
: "${RCS_CLIENT_ID:?RCS_CLIENT_ID is required}"
: "${RCS_CLIENT_SECRET:?RCS_CLIENT_SECRET is required (env, or file $SECRET_FILE)}"
export RCS_SCOPE="${RCS_SCOPE:-fr:idm:*}"
export RCS_WS_CONNECTIONS="${RCS_WS_CONNECTIONS:-2}"
export RCS_WS_MAX_CONNECTIONS="${RCS_WS_MAX_CONNECTIONS:-3}"
export RCS_TRUSTSTORE_PASSWORD="${RCS_TRUSTSTORE_PASSWORD:-changeit}"

# Render properties (envsubst is not in the base image; use bash parameter expansion).
render() {
  local line out=""
  while IFS= read -r line || [ -n "$line" ]; do
    while [[ "$line" =~ \$\{([A-Z_][A-Z0-9_]*)\} ]]; do
      local var="${BASH_REMATCH[1]}"
      line="${line//\$\{${var}\}/${!var-}}"
    done
    out+="$line"$'\n'
  done < "$TEMPLATE"
  printf '%s' "$out"
}
render > "$PROPS"
echo "[rcs-entrypoint] rendered $PROPS:"
sed -E 's/(clientSecret=|trustStorePass=).*/\1<redacted>/' "$PROPS" | sed 's/^/    /'

# Truststore: start from the JDK cacerts (public CAs for the AIC websocket) and
# add every PEM found in /run/secrets/ca (lab CA that signed ds.jrsz.net's cert).
CACERTS="${JAVA_HOME:-/opt/jdk}/lib/security/cacerts"
TRUST="${HOME_DIR}/security/rcs-truststore.p12"
TRUST_PASS="${RCS_TRUSTSTORE_PASSWORD}"
rm -f "$TRUST"
keytool -importkeystore -srckeystore "$CACERTS" -srcstorepass changeit \
        -destkeystore "$TRUST" -deststorepass "$TRUST_PASS" -deststoretype PKCS12 -noprompt >/dev/null 2>&1
shopt -s nullglob
for pem in /run/secrets/ca/*.cert.pem /run/secrets/ca/*.crt /run/secrets/ca/*.pem; do
  case "$pem" in *bundle*) continue;; esac
  alias="lab-$(basename "$pem" | sed -E 's/\.(cert\.pem|crt|pem)$//')"
  keytool -importcert -keystore "$TRUST" -storepass "$TRUST_PASS" -file "$pem" -alias "$alias" -noprompt >/dev/null 2>&1 \
    && echo "[rcs-entrypoint] trusted CA $alias ($pem)"
done
shopt -u nullglob

# Rotate the file log so the compose healthcheck never sees a stale operational=true.
mkdir -p "${HOME_DIR}/logs"
[ -f "${HOME_DIR}/logs/ConnectorServer.log" ] && mv -f "${HOME_DIR}/logs/ConnectorServer.log" "${HOME_DIR}/logs/ConnectorServer.log.prev"

export OPENICF_OPTS="${OPENICF_OPTS:-} -Djavax.net.ssl.trustStore=${TRUST} -Djavax.net.ssl.trustStorePassword=${TRUST_PASS} -Djavax.net.ssl.trustStoreType=PKCS12"

exec "${HOME_DIR}/bin/docker-entrypoint.sh" "$@"
