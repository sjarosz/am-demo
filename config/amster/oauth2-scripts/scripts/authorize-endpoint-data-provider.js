/*
 * OAuth2 Authorize Endpoint Data Provider script
 * (context: OAUTH2_AUTHORIZE_ENDPOINT_DATA_PROVIDER) - "scriptlab" proof point.
 *
 * Based on the PingAM 8.1 sample "OAuth2 Authorize Endpoint Data Provider
 * Script". It returns additional data when the /authorize endpoint is called.
 * Every key is prefixed with the star emoji so the contributed data is obvious
 * wherever AM surfaces it (and in the OAUTH2_AUTHORIZE_ENDPOINT_DATA_PROVIDER
 * debug log).
 *
 * Bindings: session, httpClient, scriptName, logger.
 * Returns: Map<String, String> of additional data.
 */
(function () {
    var map = new java.util.HashMap();

    map.put("\u2B50authData", "hello-from-authorize-endpoint-script");
    map.put("\u2B50script", scriptName);
    map.put("\u2B50authTime", "" + (new Date().getTime()));

    if (session != null) {
        try {
            map.put("\u2B50loginHost", session.getProperty("Host"));
        } catch (e) {
            logger.warning("AUTHORIZE_DATA_PROVIDER: cannot read session Host: " + e);
        }
    }

    return map;
}());
