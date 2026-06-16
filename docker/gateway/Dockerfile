FROM gcr.io/forgerock-io/java-21:latest

ENV INSTALL_DIR=/opt/gateway
ENV IG_INSTANCE_DIR=/var/gateway

COPY --chown=forgerock:root ping-gateway-2026.3.0 ${INSTALL_DIR}

RUN mkdir -p "${IG_INSTANCE_DIR}" \
    && chown -R forgerock:root "${IG_INSTANCE_DIR}" "${INSTALL_DIR}" \
    && chmod -R g+rwx "${IG_INSTANCE_DIR}" "${INSTALL_DIR}"

USER 11111

ENTRYPOINT ["/opt/gateway/bin/start.sh", "/var/gateway"]
