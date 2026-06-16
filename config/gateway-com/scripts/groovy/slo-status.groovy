import org.forgerock.http.protocol.Request
import org.forgerock.http.protocol.Response
import org.forgerock.http.protocol.Status
import groovy.json.JsonOutput
import groovy.json.JsonSlurper

// Reports whether the shared AM SSO session is live, using the iPlanetDirectoryPro
// cookie that the browser sends to this app host (cookie domain is jrsz.com).
// Server-side check via AM REST getSessionInfo, so no AM CORS config is needed.

def jsonResponse = { boolean authed, String username ->
    Response r = new Response(Status.OK)
    r.headers.put('Content-Type', 'application/json')
    r.headers.put('Cache-Control', 'no-store')
    r.entity.setString(JsonOutput.toJson([authenticated: authed, username: username]))
    return r
}

def cookies = request.cookies['iPlanetDirectoryPro']
def token = (cookies != null && !cookies.isEmpty()) ? cookies[0].value : null
if (token == null || token.isEmpty()) {
    return jsonResponse(false, null)
}

Request amReq = new Request()
amReq.setMethod('POST')
amReq.setUri('https://am.jrsz.com:9443/am/json/realms/root/realms/alpha/sessions?_action=getSessionInfo')
amReq.headers.put('iPlanetDirectoryPro', token)
amReq.headers.put('Accept-API-Version', 'resource=5.1, protocol=1.0')
amReq.headers.put('Content-Type', 'application/json')
amReq.entity.setString('{}')

return http.send(amReq).then({ amResp ->
    if (amResp.status.isSuccessful()) {
        def info = new JsonSlurper().parseText(amResp.entity.getString())
        def user = info?.username ?: null
        return jsonResponse(true, user)
    }
    return jsonResponse(false, null)
})
