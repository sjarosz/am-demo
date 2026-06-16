/*
 * OAuth2 May Act script (context: OAUTH2_MAY_ACT) - "scriptlab" proof point.
 *
 * Based on the PingAM 8.1 sample "OAuth2 May Act Script". It adds a `may_act`
 * claim (RFC 8693 token exchange / delegation) to the access token and the
 * OIDC ID token. The delegation entries carry star-tagged keys so the
 * customization is obvious in the decoded token.
 *
 * Bindings: token (AccessToken or OpenIdConnectToken), scopes, identity,
 *           session, scriptName, logger.
 * Returns: nothing - mutate token directly via setMayAct().
 */
(function () {
    var frJava = JavaImporter(
        org.forgerock.json.JsonValue
    );

    var mayAct = frJava.JsonValue.json(frJava.JsonValue.object());
    mayAct.put("client_id", "scriptlab-rp");
    mayAct.put("\u2B50delegate", "demo-delegation");
    mayAct.put("\u2B50script", scriptName);

    token.setMayAct(mayAct);

    // No return value expected.
}());
