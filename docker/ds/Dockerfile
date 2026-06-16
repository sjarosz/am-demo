FROM gcr.io/forgerock-io/java-25:latest

ARG uid=11111
ARG gid=0
ARG DS_ROOT_PASSWORD=changeit
ARG DS_MONITOR_PASSWORD=changeit
ARG DS_AM_PROFILE_PASSWORD=changeit

COPY --chown=${uid}:${gid} opendj /opt/opendj
COPY --chown=${uid}:${gid} opendj/samples/docker/docker-entrypoint.sh /opt/opendj/docker-entrypoint.sh
COPY --chown=${uid}:${gid} opendj/samples/docker/logger.sh /opt/opendj/logger.sh
COPY --chown=${uid}:${gid} docker/ds/setup.sh /opt/opendj/setup.sh

RUN apt-get update && \
    apt-get install -y bash-completion unzip && \
    rm -rf /var/lib/apt/lists/* && \
    chmod +x /opt/opendj/docker-entrypoint.sh /opt/opendj/logger.sh /opt/opendj/setup.sh

USER ${uid}
WORKDIR /opt/opendj
ENV PATH=$PATH:/opt/jdk/bin:/opt/opendj/bin
ENV DS_ROOT_PASSWORD=${DS_ROOT_PASSWORD}
ENV DS_MONITOR_PASSWORD=${DS_MONITOR_PASSWORD}
ENV DS_AM_PROFILE_PASSWORD=${DS_AM_PROFILE_PASSWORD}

RUN ./setup.sh && rm ./setup.sh

EXPOSE 1389 1636 4444 8080 8443 8989

ENTRYPOINT ["./docker-entrypoint.sh"]
