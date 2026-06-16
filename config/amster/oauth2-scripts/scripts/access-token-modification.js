/*
 * OAuth2 Access Token Modification script
 * (context: OAUTH2_ACCESS_TOKEN_MODIFICATION) - "scriptlab" proof point.
 *
 * Trimmed from the PingAM 8.1 sample "OAuth2 Access Token Modification Script".
 * It adds clearly marked CUSTOM fields (names prefixed with the star emoji) to
 * the access token so the customization is obvious when the token (a JWT in
 * this realm) or its introspection response is decoded.
 *
 * Bindings: accessToken, scopes, identity, session, scriptName, logger.
 * Returns: nothing - mutate accessToken directly.
 */
(function () {
    // Marker fields so reviewers instantly see the script ran.
    accessToken.setField("\u2B50script", scriptName);
    accessToken.setField("\u2B50source", "OAUTH2_ACCESS_TOKEN_MODIFICATION sample script");

    function firstAttr(name) {
        try {
            var set = identity.getAttribute(name);
            if (set != null) {
                var arr = set.toArray();
                if (arr.length > 0) {
                    return arr[0];
                }
            }
        } catch (e) {
            logger.warning("ACCESS_TOKEN_MOD: cannot read attribute " + name + ": " + e);
        }
        return null;
    }

    var mail = firstAttr("mail");
    if (mail != null) {
        accessToken.setField("\u2B50mail", mail);
    }
    var dept = firstAttr("ou");
    accessToken.setField("\u2B50dept", dept != null ? dept : "Platform Engineering");

    // Session is absent for non-interactive grants; guard it.
    if (session) {
        try {
            accessToken.setField("\u2B50loginHost", session.getProperty("Host"));
        } catch (e) {
            logger.warning("ACCESS_TOKEN_MOD: cannot read session Host: " + e);
        }
    }

    // No return value expected.
}());
