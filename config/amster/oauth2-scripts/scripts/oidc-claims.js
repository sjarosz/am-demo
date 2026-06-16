/*
 * OIDC Claims script (context: OIDC_CLAIMS) - cross-AM "scriptlab" proof point.
 *
 * Trimmed from the PingAM 8.1 sample "OIDC Claims Script". It computes the
 * claims returned in the id_token and at the /userinfo endpoint:
 *   - copies the default server-provided claims,
 *   - maps the standard profile/email/phone scopes to identity attributes, and
 *   - injects clearly marked CUSTOM claims whose names are prefixed with the
 *     star emoji so the customization is obvious in the decoded token.
 *
 * Bindings: scopes, claims, identity, scriptName, logger (see the doc header).
 * Returns: org.forgerock.oauth2.core.UserInfoClaims(values, compositeScopes).
 */
(function () {
    var frJava = JavaImporter(
        org.forgerock.oauth2.core.UserInfoClaims,
        java.util.LinkedHashMap,
        java.util.HashMap,
        java.util.ArrayList
    );

    var values = new frJava.LinkedHashMap();
    var compositeScopes = new frJava.HashMap();

    // Preserve any default server-provided claims (e.g. sub).
    if (claims != null) {
        values.putAll(claims);
    }

    function firstAttr(name) {
        try {
            var set = identity.getAttribute(name);
            if (set != null) {
                var it = set.iterator();
                if (it.hasNext()) {
                    return it.next();
                }
            }
        } catch (e) {
            logger.warning("OIDC_CLAIMS: cannot read attribute " + name + ": " + e);
        }
        return null;
    }

    function hasScope(scope) {
        return scopes != null && scopes.contains(scope);
    }

    // Standard scope -> claim mapping (kept minimal but functional).
    if (hasScope("profile")) {
        var cn = firstAttr("cn");
        var given = firstAttr("givenname");
        var family = firstAttr("sn");
        if (cn != null) values.put("name", cn);
        if (given != null) values.put("given_name", given);
        if (family != null) values.put("family_name", family);
    }
    if (hasScope("email")) {
        var mail = firstAttr("mail");
        if (mail != null) values.put("email", mail);
    }
    if (hasScope("phone")) {
        var phone = firstAttr("telephonenumber");
        if (phone != null) values.put("phone_number", phone);
    }

    // ---- CUSTOM star-tagged claims (the proof point) ----------------------
    // These names carry a leading star emoji so any reviewer instantly spots
    // that the OIDC_CLAIMS script injected them.
    values.put("\u2B50script", scriptName);
    values.put("\u2B50source", "OIDC_CLAIMS sample script");
    var dept = firstAttr("ou");
    values.put("\u2B50dept", dept != null ? dept : "Platform Engineering");
    values.put("\u2B50realm", "scriptlab");

    return new frJava.UserInfoClaims(values, compositeScopes);
}());
