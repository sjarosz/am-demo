/*
 * OAuth2 Validate Scope script (context: OAUTH2_VALIDATE_SCOPE) - "scriptlab"
 * proof point.
 *
 * The PingAM 8.1 sample "OAuth2 Validate Scope Script", unchanged in behaviour:
 * the requested scopes are intersected with the allowed scopes, defaults are
 * applied when none are requested, and an InvalidScopeException is thrown for
 * any unknown scope. The proof point for this script is ENFORCEMENT - the app8
 * console deliberately requests a bogus scope and shows AM rejecting it here.
 *
 * Bindings: requestedScopes, defaultScopes, allowedScopes, scriptName, logger.
 * Returns: Set<String> of validated scopes (throws InvalidScopeException).
 */
function validateScopes() {
    var frJava = JavaImporter(
        org.forgerock.oauth2.core.exceptions.InvalidScopeException
    );

    var scopes;
    if (requestedScopes == null || requestedScopes.isEmpty()) {
        scopes = defaultScopes;
    } else {
        scopes = new java.util.HashSet(allowedScopes);
        scopes.retainAll(requestedScopes);
        if (requestedScopes.size() > scopes.size()) {
            logger.warning("VALIDATE_SCOPE: rejecting unknown/invalid scope(s) requested=" + requestedScopes);
            throw new frJava.InvalidScopeException("Unknown/invalid scope(s)");
        }
    }

    if (scopes == null || scopes.isEmpty()) {
        throw new frJava.InvalidScopeException("No scope requested and no default scope configured");
    }
    return scopes;
}

function validateAuthorizationScope() {
    return validateScopes();
}

function validateAccessTokenScope() {
    return validateScopes();
}

function validateRefreshTokenScope() {
    return validateScopes();
}

function validateBackChannelAuthorizationScope() {
    return validateScopes();
}
