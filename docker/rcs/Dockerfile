# PingIDM Remote Connector Server (RCS) 1.5.20.x, built from the vendor
# openicf-zip exploded at repo root (openicf/, gitignored; zip in zips/).
#
# The RCS runs in "cloud client" mode: it opens an outbound websocket to the
# PingOne AIC tenant (connectorserver.url) authenticated with OAuth2 client
# credentials, and hosts connectors (here: the LDAP connector talking to the
# lab's PingDS). No inbound port is required.
FROM gcr.io/forgerock-io/java-21:latest

ARG uid=11111
ARG gid=0

COPY --chown=${uid}:${gid} openicf /opt/openicf
COPY --chown=${uid}:${gid} docker/rcs/docker-entrypoint.sh /opt/openicf/bin/rcs-entrypoint.sh
COPY --chown=${uid}:${gid} docker/rcs/ConnectorServer.properties.template /opt/openicf/conf/ConnectorServer.properties.template

RUN mkdir -p /opt/openicf/logs /opt/openicf/tmp /opt/openicf/scripts && \
    chown -R ${uid}:${gid} /opt/openicf && \
    chmod +x /opt/openicf/bin/*.sh

USER ${uid}
WORKDIR /opt/openicf
ENV PATH=$PATH:/opt/jdk/bin
ENV CONNECTOR_SERVER_HOME=/opt/openicf

ENTRYPOINT ["/opt/openicf/bin/rcs-entrypoint.sh"]
