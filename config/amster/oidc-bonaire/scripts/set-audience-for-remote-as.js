/*
 * OAUTH2_ACCESS_TOKEN_MODIFICATION script for the jrsz.net /bravo realm.
 *
 * Mirrors the "set-audience-for-remote-bonaire05" script that horizon pins on its
 * Portal client: access tokens issued to the portal client are stamped so that a
 * remote PingOne AIC tenant (bonaire05) accepts them as RFC 7523 jwt-bearer
 * assertions through a Trusted JWT Issuer keyed on `preferred_username`:
 *
 *   aud                 = the remote tenant's access_token endpoint (AM requires
 *                         the assertion audience to be its own token endpoint)
 *   preferred_username  = the resource owner's uid (AIC identities are named by
 *                         UUID, so a foreign `sub` never resolves; the trusted
 *                         issuer maps this claim to the local userName instead)
 *
 * Only tokens for the portal client are touched; other /bravo clients keep the
 * default token shape. Placeholders are filled by provision.py.
 */
(function () {
    var PORTAL_CLIENT_ID = "@@PORTAL_CLIENT_ID@@";
    var REMOTE_AS_TOKEN_ENDPOINT = "@@REMOTE_AS_TOKEN_ENDPOINT@@";

    var clientId = null;
    try {
        if (clientProperties && clientProperties.get("clientId") != null) {
            clientId = String(clientProperties.get("clientId"));
        }
    } catch (e) {
        logger.warning("set-audience-for-remote-as: cannot read clientId: " + e);
    }
    if (clientId !== PORTAL_CLIENT_ID) {
        return;
    }

    accessToken.setField("aud", REMOTE_AS_TOKEN_ENDPOINT);

    var uid = null;
    try {
        var set = identity.getAttribute("uid");
        if (set != null && !set.isEmpty()) {
            uid = String(set.iterator().next());
        }
    } catch (e) {
        logger.warning("set-audience-for-remote-as: cannot read uid: " + e);
    }
    if (uid != null) {
        accessToken.setField("preferred_username", uid);
    } else {
        logger.warning("set-audience-for-remote-as: no uid attribute for resource owner; preferred_username not set");
    }
}());
