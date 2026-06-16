#!/bin/sh

CLASSPATH_BASE="${CATALINA_HOME}/lib/forgerock-util.jar:${CATALINA_HOME}/lib/am-jul.jar:${CATALINA_HOME}/lib/joda-time.jar:${CATALINA_HOME}/lib/openam-shared.jar"
export CLASSPATH="${CLASSPATH_BASE}${CLASSPATH:+:${CLASSPATH}}"

export CATALINA_OPTS="${CATALINA_OPTS} ${AM_CONTAINER_JVM_ARGS}"
export CATALINA_OPTS="${CATALINA_OPTS} -Dcom.sun.identity.configuration.directory=${AM_HOME}"
export CATALINA_OPTS="${CATALINA_OPTS} -Dorg.forgerock.donotupgrade=true"
export CATALINA_OPTS="${CATALINA_OPTS} -Dcom.sun.services.debug.mergeall=on"
export CATALINA_OPTS="${CATALINA_OPTS} -Dcom.iplanet.services.stats.state=off"
export CATALINA_OPTS="${CATALINA_OPTS} -DtomcatAccessLogDir=/proc/self/fd -DtomcatAccessLogFile=1"
export CATALINA_OPTS="${CATALINA_OPTS} -Djavax.net.ssl.trustStore=${TRUSTSTORE_FILE}"

export CATALINA_OPTS="${CATALINA_OPTS} -Djavax.net.ssl.trustStorePassword=${TRUSTSTORE_PASSWORD}"
export CATALINA_OPTS="${CATALINA_OPTS} -Djavax.net.ssl.trustStoreType=PKCS12"
export CATALINA_OPTS="${CATALINA_OPTS} -Dorg.apache.tomcat.util.buf.UDecoder.ALLOW_ENCODED_SLASH=true"
export CATALINA_OPTS="${CATALINA_OPTS} -Dcom.sun.identity.cookie.httponly=true"
