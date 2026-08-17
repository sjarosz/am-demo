FROM gcr.io/forgerock-io/java-21:latest

ENV CATALINA_HOME=/opt/tomcat
ENV FORGEROCK_HOME=/home/forgerock
ENV AM_HOME=/home/forgerock/openam
ENV AM_CONTAINER_JVM_ARGS=-XX:MaxRAMPercentage=75\ -XX:InitialRAMPercentage=75\ -XX:MaxTenuringThreshold=1\ -Djava.security.egd=file:/dev/urandom
ENV TOMCAT_VERSION=10.1.55

COPY docker/am/server.xml.template /tmp/server.xml.template
COPY docker/am/setenv.sh /tmp/setenv.sh
COPY docker/am/docker-entrypoint.sh /usr/local/bin/am-entrypoint.sh
COPY openam/AM-8.1.0.war /tmp/am.war
COPY openam/am-jul-8.1.0.jar /tmp/am-jul.jar
COPY openam/AM-shared-8.1.0.jar /tmp/openam-shared.jar
COPY openam/joda-time-2.14.0.jar /tmp/joda-time.jar
COPY openam/forgerock-util-27.1.0-20260306133242-984346d9c07513c0e2f566c5ee3af86a556d42ba.jar /tmp/forgerock-util.jar

RUN apt-get update && \
    apt-get install -y curl tar unzip && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /opt && \
    # downloads.apache.org removes superseded releases; archive.apache.org keeps all
    { curl -fsSL -o /tmp/tomcat.tar.gz "https://downloads.apache.org/tomcat/tomcat-10/v${TOMCAT_VERSION}/bin/apache-tomcat-${TOMCAT_VERSION}.tar.gz" \
      || curl -fsSL -o /tmp/tomcat.tar.gz "https://archive.apache.org/dist/tomcat/tomcat-10/v${TOMCAT_VERSION}/bin/apache-tomcat-${TOMCAT_VERSION}.tar.gz"; } && \
    tar -xzf /tmp/tomcat.tar.gz -C /opt && \
    rm -f /tmp/tomcat.tar.gz && \
    mv "/opt/apache-tomcat-${TOMCAT_VERSION}" "${CATALINA_HOME}" && \
    mv /tmp/server.xml.template ${CATALINA_HOME}/conf/server.xml.template && \
    mv /tmp/setenv.sh ${CATALINA_HOME}/bin/setenv.sh && \
    mv /tmp/am-jul.jar ${CATALINA_HOME}/lib/am-jul.jar && \
    mv /tmp/openam-shared.jar ${CATALINA_HOME}/lib/openam-shared.jar && \
    mv /tmp/joda-time.jar ${CATALINA_HOME}/lib/joda-time.jar && \
    mv /tmp/forgerock-util.jar ${CATALINA_HOME}/lib/forgerock-util.jar && \
    rm -rf ${CATALINA_HOME}/webapps/* && \
    unzip -q /tmp/am.war -d ${CATALINA_HOME}/webapps/am && \
    rm -f /tmp/am.war && \
    mkdir -p ${AM_HOME}/config ${AM_HOME}/security/secrets/default ${AM_HOME}/security/keystores && \
    chmod +x /usr/local/bin/am-entrypoint.sh ${CATALINA_HOME}/bin/setenv.sh

EXPOSE 8443

ENTRYPOINT ["/usr/local/bin/am-entrypoint.sh"]
