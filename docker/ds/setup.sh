#!/bin/sh
set -eux

rm -f template/config/tools.properties
cp -r samples/docker/Dockerfile samples/docker/README.md samples/docker/docker-entrypoint-help.md samples/docker/docker-entrypoint.sh samples/docker/logger.sh samples/docker/template .
rm -rf README* bat *.zip *.png *.bat

deployment_id="$(./bin/dskeymgr create-deployment-id --deploymentIdPassword password)"
root_password="${DS_ROOT_PASSWORD:-changeit}"
monitor_password="${DS_MONITOR_PASSWORD:-changeit}"
profile_password="${DS_AM_PROFILE_PASSWORD:-changeit}"

./setup --serverId                ds-jrsz \
        --hostname                ds.jrsz.org \
        --deploymentId            "${deployment_id}" \
        --deploymentIdPassword    password \
        --rootUserPassword        "${root_password}" \
        --adminConnectorPort      4444 \
        --ldapPort                1389 \
        --enableStartTls \
        --ldapsPort               1636 \
        --httpPort                8080 \
        --httpsPort               8443 \
        --replicationPort         8989 \
        --rootUserDn              uid=admin \
        --monitorUserDn           uid=monitor \
        --monitorUserPassword     "${monitor_password}" \
        --profile                 am-config \
        --set                     am-config/amConfigAdminPassword:"${profile_password}" \
        --profile                 am-cts \
        --set                     am-cts/amCtsAdminPassword:"${profile_password}" \
        --set                     am-cts/tokenExpirationPolicy:am \
        --profile                 am-identity-store \
        --set                     am-identity-store/amIdentityStoreAdminPassword:"${profile_password}" \
        --acceptLicense

./bin/dsconfig --offline --no-prompt --batch <<'END_OF_COMMAND_INPUT'
set-global-configuration-prop --set "server-id:&{ds.server.id|ds-jrsz}"
set-global-configuration-prop --set "group-id:&{ds.group.id|default}"
set-global-configuration-prop --set "advertised-listen-address:&{ds.advertised.listen.address|ds.jrsz.org}"
set-global-configuration-prop --advanced --set "trust-transaction-ids:&{platform.trust.transaction.header|false}"

delete-log-publisher --publisher-name "File-Based Error Logger"
delete-log-publisher --publisher-name "File-Based Access Logger"
delete-log-publisher --publisher-name "File-Based Audit Logger "
delete-log-publisher --publisher-name "File-Based HTTP Access Logger"
delete-log-publisher --publisher-name "Json File-Based Access Logger"
delete-log-publisher --publisher-name "Json File-Based HTTP Access Logger"

create-log-publisher --type console-error --publisher-name "Console Error Logger" --set enabled:true --set json-output:true --set default-severity:notice --set override-severity:SYNC=INFO
create-log-publisher --type external-access --publisher-name "Console LDAP Access Logger" --set enabled:true --set config-file:config/audit-handlers/ldap-access-stdout.json --set "filtering-policy:&{ds.log.filtering.policy|inclusive}"
create-log-publisher --type external-http-access --publisher-name "Console HTTP Access Logger" --set enabled:true --set config-file:config/audit-handlers/http-access-stdout.json

delete-sasl-mechanism-handler --handler-name "GSSAPI"
set-synchronization-provider-prop --provider-name "Multimaster synchronization" --set "bootstrap-replication-server:&{ds.bootstrap.replication.servers|ds.jrsz.org:8989}"
delete-replication-domain --provider-name "Multimaster synchronization" --domain-name "cn=schema"
END_OF_COMMAND_INPUT

./bin/ldifmodify config/config.ldif > config/config.ldif.tmp <<'EOF'
dn: cn=Filtering Criteria,cn=Filtered Json File-Based Access Logger,cn=Loggers,cn=config
changetype: moddn
newrdn: cn=Filtering Criteria
deleteoldrdn: 0
newsuperior: cn=Console LDAP Access Logger,cn=Loggers,cn=config

dn: cn=Filtered Json File-Based Access Logger,cn=Loggers,cn=config
changetype: delete
EOF
rm config/config.ldif
mv config/config.ldif.tmp config/config.ldif

mkdir -p data secrets
mv db/schema config

keytool -delete -keystore config/keystore -storepass:file config/keystore.pin -alias ca-cert || true
keytool -delete -keystore config/keystore -storepass:file config/keystore.pin -alias ssl-key-pair || true

remove_user_password() {
    file=$1
    dn=$2

    ./bin/ldifmodify "${file}" > "${file}.tmp" <<EOF
dn: ${dn}
changetype: modify
delete: userPassword
EOF
    rm "${file}"
    mv "${file}.tmp" "${file}"
}

remove_user_password db/rootUser/rootUser.ldif "uid=admin"
remove_user_password db/monitorUser/monitorUser.ldif "uid=monitor"
