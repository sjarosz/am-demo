/*
 * SAML2 IDP Attribute Mapper script (context: SAML2_IDP_ATTRIBUTE_MAPPER) -
 * "samllab" proof point (runs on the org hosted IdP).
 *
 * Trimmed from the PingAM 8.1 sample "SAML2 IDP Attribute Mapper Script". It
 * returns the list of SAML Attribute objects the IdP inserts into the generated
 * assertion. Besides the real `uid` (needed for the SP's auto-federation), it
 * injects clearly marked CUSTOM attributes whose names are prefixed with the
 * star emoji, so the customization is obvious in the assertion received by the
 * SP (surfaced into the SP session by the SP Adapter script).
 *
 * Bindings: session (SSOToken), hostedEntityId, remoteEntityId, realm,
 *           idpAttributeMapperScriptHelper, logger.
 * Returns: java.util.List<com.sun.identity.saml2.assertion.Attribute>.
 */
(function () {
    var frJava = JavaImporter(
        java.util.ArrayList,
        java.util.HashSet,
        com.sun.identity.saml2.common.SAML2Exception
    );

    var attributes = new frJava.ArrayList();

    function addSamlAttribute(samlName, value) {
        var values = new frJava.HashSet();
        values.add(value);
        attributes.add(idpAttributeMapperScriptHelper.createSAMLAttribute(samlName, null, values));
    }

    try {
        // Real uid so the SP can auto-federate the assertion onto the local user.
        var uid = null;
        try {
            var resolved = idpAttributeMapperScriptHelper.getAttributes(session, new frJava.HashSet(["uid"]));
            if (resolved != null && resolved.get("uid") != null) {
                var it = resolved.get("uid").iterator();
                if (it.hasNext()) {
                    uid = it.next();
                }
            }
        } catch (e) {
            logger.warning("IDP_ATTR_MAPPER: could not resolve uid from datastore: " + e);
        }
        if (uid == null) {
            var props = idpAttributeMapperScriptHelper.getPropertySet(session, "UserId");
            if (props != null && !props.isEmpty()) {
                uid = props.iterator().next();
            }
        }
        if (uid != null) {
            addSamlAttribute("uid", uid);
        }

        // ---- CUSTOM star-tagged SAML attributes (the proof point) ----------
        addSamlAttribute("\u2B50dept", "Platform Engineering");
        addSamlAttribute("\u2B50source", "SAML2_IDP_ATTRIBUTE_MAPPER sample script");
        addSamlAttribute("\u2B50hostedIdp", hostedEntityId);
        if (uid != null) {
            addSamlAttribute("\u2B50mail", uid + "@jrsz.org");
        }

        return attributes;
    } catch (error) {
        logger.error("IDP_ATTR_MAPPER: error mapping attributes: " + error);
        throw new frJava.SAML2Exception(error);
    }
}());
