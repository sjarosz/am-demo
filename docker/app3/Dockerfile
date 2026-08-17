FROM gcr.io/forgerock-io/java-21:latest

ENV CATALINA_HOME=/opt/tomcat
ENV TOMCAT_VERSION=10.1.55

ARG APP_DOMAIN=jrsz.org

COPY docker/app3/server.xml.template /tmp/server.xml.template
COPY docker/app3/docker-entrypoint.sh /usr/local/bin/app-entrypoint.sh
COPY apps/app3/ROOT /tmp/ROOT

RUN apt-get update && \
    apt-get install -y curl tar && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /opt && \
    # downloads.apache.org removes superseded releases; archive.apache.org keeps all
    { curl -fsSL -o /tmp/tomcat.tar.gz "https://downloads.apache.org/tomcat/tomcat-10/v${TOMCAT_VERSION}/bin/apache-tomcat-${TOMCAT_VERSION}.tar.gz" \
      || curl -fsSL -o /tmp/tomcat.tar.gz "https://archive.apache.org/dist/tomcat/tomcat-10/v${TOMCAT_VERSION}/bin/apache-tomcat-${TOMCAT_VERSION}.tar.gz"; } && \
    tar -xzf /tmp/tomcat.tar.gz -C /opt && \
    rm -f /tmp/tomcat.tar.gz && \
    mv "/opt/apache-tomcat-${TOMCAT_VERSION}" "${CATALINA_HOME}" && \
    rm -rf "${CATALINA_HOME}/webapps"/* && \
    mkdir -p "${CATALINA_HOME}/webapps/ROOT" && \
    mv /tmp/server.xml.template ${CATALINA_HOME}/conf/server.xml.template && \
    cp -R /tmp/ROOT/. ${CATALINA_HOME}/webapps/ROOT/ && \
    if [ "${APP_DOMAIN}" != "jrsz.org" ]; then \
      find ${CATALINA_HOME}/webapps/ROOT -type f -exec sed -i "s/jrsz\.org/${APP_DOMAIN}/g" {} +; \
    fi && \
    chmod +x /usr/local/bin/app-entrypoint.sh

EXPOSE 8443

ENTRYPOINT ["/usr/local/bin/app-entrypoint.sh"]
