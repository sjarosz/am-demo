/*
 * SAML2 SP Adapter script (context: SAML2_SP_ADAPTER) - "samllab" proof point
 * (runs on the com hosted SP).
 *
 * Based on the PingAM 8.1 sample "SAML2 SP Adapter Script". On a successful SSO
 * (postSingleSignOnSuccess) it reads the assertion produced by the IdP, collects
 * the star-tagged CUSTOM attributes (from the IDP Attribute Mapper) and the
 * star-tagged Subject NameID (from the NameID Mapper), and stashes them - plus
 * its own star marker - into a single `samllabProof` session property as JSON.
 * app9 reads that property via the AM sessions REST endpoint and highlights
 * every star element, tying all three SAML scripts together in one view.
 *
 * Bindings (postSingleSignOnSuccess): hostedEntityId, realm, request, response,
 *   out, session (SSOToken), authnRequest, res (Response), profile,
 *   isFederation, spAdapterScriptHelper, logger.
 */
var STAR = "\u2B50";

function postSingleSignOnSuccess() {
    var proof = {};
    proof[STAR + "spAdapter"] = "postSingleSignOnSuccess on " + hostedEntityId;
    proof[STAR + "profile"] = String(profile);

    try {
        var assertions = res.getAssertion();
        if (assertions != null && assertions.size() > 0) {
            var assertion = assertions.get(0);

            // Star-tagged Subject NameID (from the NameID Mapper script).
            try {
                var nameId = assertion.getSubject().getNameID().getValue();
                if (nameId != null) {
                    proof[STAR + "nameId"] = String(nameId);
                }
            } catch (e) {
                logger.warning("SP_ADAPTER: could not read NameID: " + e);
            }

            // Star-tagged assertion attributes (from the IDP Attribute Mapper).
            var statements = assertion.getAttributeStatements();
            if (statements != null) {
                for (var i = 0; i < statements.size(); i++) {
                    var attrs = statements.get(i).getAttribute();
                    if (attrs == null) {
                        continue;
                    }
                    for (var j = 0; j < attrs.size(); j++) {
                        var attr = attrs.get(j);
                        var name = String(attr.getName());
                        if (name.indexOf(STAR) < 0) {
                            continue;
                        }
                        var value = "";
                        try {
                            var vals = attr.getAttributeValueString();
                            if (vals != null && vals.size() > 0) {
                                value = String(vals.get(0));
                            }
                        } catch (e) {
                            logger.warning("SP_ADAPTER: could not read attribute " + name + ": " + e);
                        }
                        proof[name] = value;
                    }
                }
            }
        }
    } catch (error) {
        logger.error("SP_ADAPTER: error building proof: " + error);
        proof[STAR + "error"] = String(error);
    }

    try {
        session.setProperty("samllabProof", JSON.stringify(proof));
    } catch (e) {
        logger.error("SP_ADAPTER: could not set samllabProof session property: " + e);
    }
    return false;
}

function preSingleSignOnRequest() {}
function preSingleSignOnProcess() {}
function postSingleSignOnFailure() { return false; }
function postNewNameIDSuccess() {}
function postTerminateNameIDSuccess() {}
function preSingleLogoutProcess() {}
function postSingleLogoutSuccess() {}
