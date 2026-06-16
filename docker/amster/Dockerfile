FROM gcr.io/forgerock-io/java-25:latest

ENV JAVA_HOME=/opt/jdk

COPY amster /opt/amster
COPY docker/amster/docker-entrypoint.sh /opt/amster/docker-entrypoint.sh

RUN apt-get update && \
    apt-get install -y curl python3 && \
    rm -rf /var/lib/apt/lists/* && \
    chmod +x /opt/amster/amster /opt/amster/docker-entrypoint.sh

WORKDIR /opt/amster

ENTRYPOINT ["/opt/amster/docker-entrypoint.sh"]
