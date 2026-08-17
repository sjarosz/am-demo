import org.forgerock.http.protocol.Form
import org.forgerock.http.protocol.Request
import org.forgerock.http.protocol.Response
import org.forgerock.http.protocol.Status
import groovy.json.JsonOutput
import static org.forgerock.util.promise.Promises.newResultPromise

/*
 * bonaire05-bridge: one-hop RFC 7523 bridge from the local IdP into the bonaire05 AIC tenant.
 *
 *   inbound  Authorization: Bearer <jrsz.net /bravo access token>   (JWT; aud = bonaire05 token
 *            endpoint, preferred_username = uid -- see config/amster/oidc-bonaire)
 *   action   POST <tokenEndpoint> grant_type=jwt-bearer assertion=<that JWT>
 *            client_id/client_secret (client_secret_post) scope=<scope>
 *   outbound mode "respond": the bonaire05 token response JSON is returned to the caller
 *            mode "forward": Authorization is replaced by the bonaire05 token and the request
 *                            continues down the chain (protect a bonaire05-facing backend)
 *
 * No local key material, no minting, no subject mapping: bonaire05 validates the assertion against
 * the Trusted JWT Issuer (jrsz-net-IDP) it holds for this IdP. Configuration comes from the
 * ScriptableFilter `args` (route JSON; each entry is bound as a script variable), which in turn
 * read the container environment (.env):
 *   tokenEndpoint  bonaire05 access_token URL
 *   clientId / clientSecret   bonaire05 client with the jwt-bearer grant
 *   defaultScope   scope requested when the caller passes none (?scope=... or form scope=)
 *   mode           "respond" | "forward"
 */

def jsonError = { Status status, String error, String description ->
    Response r = new Response(status)
    r.headers.put('Content-Type', 'application/json')
    r.headers.put('Cache-Control', 'no-store')
    if (status == Status.UNAUTHORIZED) {
        r.headers.put('WWW-Authenticate', 'Bearer realm="bonaire05-bridge", error="' + error + '"')
    }
    r.entity.setString(JsonOutput.toJson([error: error, error_description: description]))
    return r
}

// ScriptableFilter binds each `args` entry as a script variable of the same name; when an arg is
// absent/empty fall back to the container environment (BONAIRE_JWT_* / REMOTE_AS_* from .env).
def arg = { String name, String envName, String dflt ->
    def v = (binding.hasVariable(name) && binding.getVariable(name) != null) ? (binding.getVariable(name) as String) : ''
    if (!v && envName) { v = System.getenv(envName) ?: '' }
    return v ?: dflt
}
String tokenEndpoint = arg('tokenEndpoint', 'REMOTE_AS_TOKEN_ENDPOINT',
        'https://openam-bonaire05.forgeblocks.com:443/am/oauth2/realms/root/realms/alpha/access_token')
String clientId = arg('clientId', 'BONAIRE_JWT_CLIENT_ID', '')
String clientSecret = arg('clientSecret', 'BONAIRE_JWT_CLIENT_SECRET', '')
String defaultScope = arg('defaultScope', 'BONAIRE_JWT_SCOPE', 'a2a:invoke')
String mode = arg('mode', null, 'respond').toLowerCase()

if (!tokenEndpoint || !clientId || !clientSecret) {
    logger.error('bonaire05-bridge: missing tokenEndpoint/clientId/clientSecret args (check BONAIRE_JWT_* in .env)')
    return newResultPromise(jsonError(Status.INTERNAL_SERVER_ERROR, 'server_error', 'bridge is not configured'))
}

// --- 1. inbound assertion --------------------------------------------------------------------
String auth = request.headers.getFirst('Authorization')
if (auth == null || !auth.toLowerCase().startsWith('bearer ')) {
    return newResultPromise(jsonError(Status.UNAUTHORIZED, 'invalid_token',
            'send the local IdP (jrsz.net /bravo) access token as a Bearer token'))
}
String assertion = auth.substring(7).trim()
if (assertion.count('.') != 2) {
    return newResultPromise(jsonError(Status.UNAUTHORIZED, 'invalid_token', 'bearer token is not a JWT'))
}

// scope: form field or query parameter override, else the configured default
String scope = defaultScope
def formScope = null
try {
    if (request.method == 'POST' && (request.headers.getFirst('Content-Type') ?: '').startsWith('application/x-www-form-urlencoded')) {
        formScope = request.form.getFirst('scope')
    }
} catch (Exception ignored) { }
def queryScope = request.uri.query ? new Form().fromQueryString(request.uri.query).getFirst('scope') : null
if (formScope) { scope = formScope } else if (queryScope) { scope = queryScope }

// --- 2. RFC 7523 jwt-bearer at bonaire05 -----------------------------------------------------
Request tokenRequest = new Request()
tokenRequest.method = 'POST'
tokenRequest.uri = URI.create(tokenEndpoint)
Form form = new Form()
form.add('grant_type', 'urn:ietf:params:oauth:grant-type:jwt-bearer')
form.add('assertion', assertion)
form.add('client_id', clientId)
form.add('client_secret', clientSecret)
if (scope) { form.add('scope', scope) }
form.toRequestEntity(tokenRequest)

logger.info('bonaire05-bridge: jwt-bearer -> {} (client {}, scope "{}", mode {})', tokenEndpoint, clientId, scope, mode)

return http.send(context, tokenRequest).thenAsync { tokenResponse ->
    return tokenResponse.entity.jsonAsync
            .thenCatch { ioException -> [:] }
            .thenAsync { json ->
                json = json ?: [:]
                if (tokenResponse.status != Status.OK || !json.access_token) {
                    logger.warn('bonaire05-bridge: jwt-bearer rejected: {} {} {}',
                            tokenResponse.status, json.error ?: '', json.error_description ?: '')
                    // 401 for assertion problems, 400 for caller mistakes (scope/params), 502 otherwise
                    Status st = Status.BAD_GATEWAY
                    if (json.error == 'invalid_grant' || json.error == 'invalid_token') { st = Status.UNAUTHORIZED }
                    else if (json.error == 'invalid_scope' || json.error == 'invalid_request') { st = Status.BAD_REQUEST }
                    return newResultPromise(jsonError(st, (json.error ?: 'server_error') as String,
                            (json.error_description ?: "jwt-bearer failed at authorization server (${tokenResponse.status})") as String))
                }
                if (mode == 'forward') {
                    request.headers.put('Authorization', "Bearer ${json.access_token}" as String)
                    return next.handle(context, request)
                }
                Response out = new Response(Status.OK)
                out.headers.put('Content-Type', 'application/json')
                out.headers.put('Cache-Control', 'no-store')
                out.entity.setString(JsonOutput.toJson(json))
                return newResultPromise(out)
            }
}
