import org.forgerock.http.protocol.Request
import org.forgerock.http.protocol.Response
import org.forgerock.http.protocol.Status
import groovy.json.JsonOutput

// Global Single Logout: invoke AM's session logout service for the shared SSO
// token, then expire the iPlanetDirectoryPro cookie across the jrsz.net domain.
// Killing the shared AM session logs out app1-app3 everywhere at once; app4
// observes the dead AM session on its next status poll and honors the logout.

def CLEAR_COOKIE = 'iPlanetDirectoryPro=; Domain=jrsz.net; Path=/; Max-Age=0; HttpOnly'

def doneResponse = {
    Response r = new Response(Status.OK)
    r.headers.put('Content-Type', 'application/json')
    r.headers.put('Cache-Control', 'no-store')
    r.headers.add('Set-Cookie', CLEAR_COOKIE)
    r.entity.setString(JsonOutput.toJson([authenticated: false, loggedOut: true]))
    return r
}

def cookies = request.cookies['iPlanetDirectoryPro']
def token = (cookies != null && !cookies.isEmpty()) ? cookies[0].value : null
if (token == null || token.isEmpty()) {
    return doneResponse()
}

Request amReq = new Request()
amReq.setMethod('POST')
amReq.setUri('https://am.jrsz.net:9443/am/json/realms/root/realms/alpha/sessions?_action=logout')
amReq.headers.put('iPlanetDirectoryPro', token)
amReq.headers.put('Accept-API-Version', 'resource=5.1, protocol=1.0')
amReq.headers.put('Content-Type', 'application/json')
amReq.entity.setString('{}')

return http.send(amReq).then({ amResp ->
    return doneResponse()
})
