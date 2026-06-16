#!/usr/bin/env bash
set -euo pipefail

: "${AM_SERVER_URL:?AM_SERVER_URL is required}"
: "${AM_CFG_DIR:?AM_CFG_DIR is required}"
: "${AM_ADMIN_PASSWORD:?AM_ADMIN_PASSWORD is required}"
: "${AM_CFG_STORE_HOST:?AM_CFG_STORE_HOST is required}"
: "${AM_CFG_STORE_PORT:?AM_CFG_STORE_PORT is required}"
: "${AM_CFG_STORE_ADMIN_PORT:?AM_CFG_STORE_ADMIN_PORT is required}"
: "${AM_CFG_STORE_ROOT_SUFFIX:?AM_CFG_STORE_ROOT_SUFFIX is required}"
: "${AM_CFG_STORE_SSL:?AM_CFG_STORE_SSL is required}"
: "${AM_CFG_STORE_DIR_MGR:?AM_CFG_STORE_DIR_MGR is required}"
: "${AM_CFG_STORE_DIR_MGR_PWD:?AM_CFG_STORE_DIR_MGR_PWD is required}"
: "${AM_USER_STORE_HOST:?AM_USER_STORE_HOST is required}"
: "${AM_USER_STORE_PORT:?AM_USER_STORE_PORT is required}"
: "${AM_USER_STORE_ROOT_SUFFIX:?AM_USER_STORE_ROOT_SUFFIX is required}"
: "${AM_USER_STORE_SSL:?AM_USER_STORE_SSL is required}"
: "${AM_USER_STORE_DIR_MGR:?AM_USER_STORE_DIR_MGR is required}"
: "${AM_USER_STORE_DIR_MGR_PWD:?AM_USER_STORE_DIR_MGR_PWD is required}"
: "${AM_USER_STORE_TYPE:?AM_USER_STORE_TYPE is required}"

export JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Djavax.net.ssl.trustStore=/run/secrets/trust/truststore.p12 -Djavax.net.ssl.trustStorePassword=${TRUSTSTORE_PASSWORD:-changeit} -Djavax.net.ssl.trustStoreType=PKCS12"

wait_for_am() {
  local config_url="${AM_SERVER_URL}/config/options.htm"
  local response="000"

  echo "Waiting for AM at ${config_url}"
  while true; do
    response="$(curl -k -s -o /dev/null -w '%{http_code}' "${config_url}" || true)"
    if [[ "${response}" == "200" || "${response}" == "302" ]]; then
      return 0
    fi
    sleep 5
  done
}

wait_for_am

am_configured() {
  local resp
  resp="$(curl -k -s -X POST \
    -H "X-OpenAM-Username: amadmin" \
    -H "X-OpenAM-Password: ${AM_ADMIN_PASSWORD}" \
    -H "Accept-API-Version: resource=2.1, protocol=1.0" \
    "${AM_SERVER_URL}/json/realms/root/authenticate" || true)"
  [[ "${resp}" == *'"tokenId"'* ]]
}

if am_configured; then
  echo "AM already configured at ${AM_SERVER_URL}; skipping install-openam"
else

cat > /tmp/install-openam.amster <<EOF
install-openam \
 --serverUrl ${AM_SERVER_URL} \
 --adminPwd ${AM_ADMIN_PASSWORD} \
 --acceptLicense \
 --cookieDomain ${AM_COOKIE_DOMAIN:-jrsz.org} \
 --cfgDir ${AM_CFG_DIR} \
 --cfgStoreDirMgr ${AM_CFG_STORE_DIR_MGR} \
 --cfgStoreDirMgrPwd ${AM_CFG_STORE_DIR_MGR_PWD} \
 --cfgStore dirServer \
 --cfgStoreHost ${AM_CFG_STORE_HOST} \
 --cfgStoreAdminPort ${AM_CFG_STORE_ADMIN_PORT} \
 --cfgStorePort ${AM_CFG_STORE_PORT} \
 --cfgStoreSsl ${AM_CFG_STORE_SSL} \
 --cfgStoreRootSuffix ${AM_CFG_STORE_ROOT_SUFFIX} \
 --userStoreDirMgr ${AM_USER_STORE_DIR_MGR} \
 --userStoreDirMgrPwd ${AM_USER_STORE_DIR_MGR_PWD} \
 --userStoreHost ${AM_USER_STORE_HOST} \
 --userStoreType ${AM_USER_STORE_TYPE} \
 --userStorePort ${AM_USER_STORE_PORT} \
 --userStoreSsl ${AM_USER_STORE_SSL} \
 --userStoreRootSuffix ${AM_USER_STORE_ROOT_SUFFIX}
:exit
EOF

/opt/amster/amster /tmp/install-openam.amster

fi

if [[ "${BOOTSTRAP_OAUTH2_DEMO_CONFIG:-${BOOTSTRAP_APP4_DEMO_CLIENT:-true}}" == "true" ]]; then
  /opt/amster-config/oauth2-demo/run-bootstrap.sh
fi

if [[ "${BOOTSTRAP_OAUTH2_SCRIPTS:-true}" == "true" ]]; then
  /opt/amster-config/oauth2-scripts/run-bootstrap.sh
fi

if [[ "${BOOTSTRAP_OIDC_HORIZON:-true}" == "true" ]]; then
  /opt/amster-config/oidc-horizon/run-bootstrap.sh
fi

if [[ "${BOOTSTRAP_DEMO_USER:-true}" == "true" ]]; then
  /opt/amster-config/demo-user/run-bootstrap.sh
fi

if [[ "${BOOTSTRAP_LOGIN_WIDGET_CONFIG:-true}" == "true" ]]; then
  /opt/amster-config/login-widget/run-bootstrap.sh
fi

if [[ "${BOOTSTRAP_JOURNEYS:-true}" == "true" ]]; then
  /opt/amster-config/journeys/run-bootstrap.sh
fi

if [[ "${BOOTSTRAP_SAML:-true}" == "true" ]]; then
  /opt/amster-config/saml/run-bootstrap.sh
fi

if [[ "${BOOTSTRAP_SOCIAL:-true}" == "true" ]]; then
  /opt/amster-config/social/run-bootstrap.sh
fi

if [[ "${BOOTSTRAP_SAML_INTEGRATED:-true}" == "true" ]]; then
  /opt/amster-config/saml-integrated/run-bootstrap.sh
fi

if [[ "${BOOTSTRAP_SAML_SCRIPTS:-true}" == "true" ]]; then
  /opt/amster-config/saml-scripts/run-bootstrap.sh
fi
