/*
 * OAuth2 Evaluate Scope script (context: OAUTH2_EVALUATE_SCOPE) - "scriptlab"
 * proof point.
 *
 * Based on the PingAM 8.1 sample "OAuth2 Evaluate Scope Script". When the
 * legacy /oauth2/tokeninfo endpoint is called, this populates each granted
 * scope with the resource owner's matching profile attribute value, and adds a
 * star-tagged marker entry so the customization is obvious in the response.
 *
 * Bindings: accessToken, identity, scriptName, logger.
 * Returns: Map<String, Object> of token information.
 */
(function () {
    var map = new java.util.LinkedHashMap();

    // Star-tagged marker proving this script produced the tokeninfo payload.
    map.put("\u2B50evaluatedBy", scriptName);

    if (identity !== null) {
        var scopeArr = accessToken.getScope().toArray();
        for (var i = 0; i < scopeArr.length; i++) {
            var scope = scopeArr[i];
            try {
                var attrs = identity.getAttribute(scope).toArray();
                var vals = [];
                for (var j = 0; j < attrs.length; j++) {
                    vals.push(attrs[j]);
                }
                map.put(scope, vals.join(","));
            } catch (e) {
                logger.warning("EVALUATE_SCOPE: cannot read attribute for scope " + scope + ": " + e);
            }
        }
    } else {
        logger.error("EVALUATE_SCOPE: identity is null");
    }

    return map;
}());
