const crypto = require("crypto");
const express = require("express");

const app = express();
app.use(express.urlencoded({ extended: false }));
app.use(express.json());

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const port = Number.parseInt(process.env.PORT || "3000", 10);
const appBaseUrl = (process.env.APP6_BASE_URL || "https://app6.jrsz.org").replace(/\/+$/, "");
const amBaseUrl = (process.env.AM_BASE_URL || "https://am.jrsz.org:8443/am").replace(/\/+$/, "");
const amCookieDomain = process.env.AM_COOKIE_DOMAIN || "jrsz.org";
const amAdminUser = process.env.AM_ADMIN_USER || "amadmin";
const amAdminPassword = process.env.AM_ADMIN_PASSWORD || "changeit";
const demoUser = process.env.DEMO_USER_NAME || "demo-user";
// Dedicated short-timeout realm for the test lab.
const realmPath = process.env.TIMEOUT_REALM_PATH || "realms/root/realms/timeout-test";
const realmName = process.env.TIMEOUT_REALM || "/timeout-test";
const issuerUrl = (
  process.env.OIDC_ISSUER_URL || `${amBaseUrl}/oauth2/${realmPath}`
).replace(/\/+$/, "");
const sessionsUrl = `${amBaseUrl}/json/${realmPath}/sessions`;
const authenticateUrl = `${amBaseUrl}/json/realms/root/authenticate`;
const oidcMetadataUrl = `${issuerUrl}/.well-known/openid-configuration`;
const scope = process.env.SCOPE || "openid profile email";

// AM XUI login URL for the timeout-test realm (used by the dashboard hints).
const amXuiLogin = `${amBaseUrl}/XUI/?realm=${realmName}#login/`;

// OIDC RP definitions. RP C is confidential (so API E can introspect/revoke),
// RP D is a public PKCE client (proves cross-RP back-channel logout).
const RPS = {
  c: {
    key: "c",
    label: "RP C",
    clientId: process.env.RP_C_CLIENT_ID || "rp-c-app",
    clientSecret: process.env.RP_C_CLIENT_SECRET || "",
    cookie: "rpc_sid",
  },
  d: {
    key: "d",
    label: "RP D",
    clientId: process.env.RP_D_CLIENT_ID || "rp-d-app",
    clientSecret: process.env.RP_D_CLIENT_SECRET || "",
    cookie: "rpd_sid",
  },
};

function redirectUri(key) {
  return `${appBaseUrl}/rp/${key}/callback`;
}
function postLogoutUri(key) {
  return `${appBaseUrl}/rp/${key}/post-logout`;
}
function backchannelUri(key) {
  return `${appBaseUrl}/rp/${key}/backchannel`;
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
function base64Url(input) {
  return input.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}
function randomString(byteLength = 32) {
  return base64Url(crypto.randomBytes(byteLength));
}
function sha256(input) {
  return crypto.createHash("sha256").update(input).digest();
}
function decodeJwtClaims(token) {
  if (!token) return null;
  const parts = String(token).split(".");
  if (parts.length < 2) return null;
  const padded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const remainder = padded.length % 4;
  const normalized = remainder === 0 ? padded : padded + "=".repeat(4 - remainder);
  try {
    return JSON.parse(Buffer.from(normalized, "base64").toString("utf8"));
  } catch {
    return null;
  }
}
function decodeJwtHeader(token) {
  if (!token) return null;
  const parts = String(token).split(".");
  if (parts.length < 2) return null;
  const padded = parts[0].replace(/-/g, "+").replace(/_/g, "/");
  const remainder = padded.length % 4;
  const normalized = remainder === 0 ? padded : padded + "=".repeat(4 - remainder);
  try {
    return JSON.parse(Buffer.from(normalized, "base64").toString("utf8"));
  } catch {
    return null;
  }
}
function htmlEscape(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
function parseCookies(req) {
  const header = req.headers.cookie || "";
  const out = {};
  header.split(";").forEach((part) => {
    const idx = part.indexOf("=");
    if (idx > -1) {
      out[part.slice(0, idx).trim()] = decodeURIComponent(part.slice(idx + 1).trim());
    }
  });
  return out;
}
function getAmToken(req) {
  return parseCookies(req)["iPlanetDirectoryPro"] || null;
}
function clearAmCookieHeader() {
  return `iPlanetDirectoryPro=; Domain=${amCookieDomain}; Path=/; Max-Age=0; HttpOnly`;
}

// ---------------------------------------------------------------------------
// AM REST helpers
// ---------------------------------------------------------------------------
let adminTokenCache = { value: null, expiresAt: 0 };

async function getAdminToken(force = false) {
  const now = Date.now();
  if (!force && adminTokenCache.value && adminTokenCache.expiresAt > now) {
    return adminTokenCache.value;
  }
  const response = await fetch(authenticateUrl, {
    method: "POST",
    headers: {
      "X-OpenAM-Username": amAdminUser,
      "X-OpenAM-Password": amAdminPassword,
      "Accept-API-Version": "resource=2.1, protocol=1.0",
      "Content-Type": "application/json",
    },
    body: "{}",
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload || !payload.tokenId) {
    throw new Error("AM admin authentication failed");
  }
  adminTokenCache = { value: payload.tokenId, expiresAt: now + 60_000 };
  return payload.tokenId;
}

// Validate a session token using an admin acting token so we still get a clean
// {valid:false} once the session under test is dead. refresh=false is REQUIRED:
// AM resets idle time on validate by default, which would corrupt idle tests.
async function amValidate(token) {
  if (!token) return { valid: false, error: "no token" };
  try {
    const admin = await getAdminToken();
    const response = await fetch(`${sessionsUrl}?_action=validate&refresh=false`, {
      method: "POST",
      headers: {
        iPlanetDirectoryPro: admin,
        "Accept-API-Version": "resource=5.1, protocol=1.0",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ tokenId: token }),
    });
    const payload = await response.json().catch(() => null);
    if (response.status === 401) {
      // Admin token may have expired; retry once with a fresh one.
      const admin2 = await getAdminToken(true);
      const retry = await fetch(`${sessionsUrl}?_action=validate&refresh=false`, {
        method: "POST",
        headers: {
          iPlanetDirectoryPro: admin2,
          "Accept-API-Version": "resource=5.1, protocol=1.0",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ tokenId: token }),
      });
      const retryPayload = await retry.json().catch(() => null);
      return { valid: !!(retryPayload && retryPayload.valid), raw: retryPayload, status: retry.status };
    }
    return { valid: !!(payload && payload.valid), raw: payload, status: response.status };
  } catch (error) {
    return { valid: false, error: String(error) };
  }
}

async function amSessionInfo(token) {
  if (!token) return { ok: false, error: "no token" };
  try {
    const admin = await getAdminToken();
    const response = await fetch(`${sessionsUrl}?_action=getSessionInfo`, {
      method: "POST",
      headers: {
        iPlanetDirectoryPro: admin,
        "Accept-API-Version": "resource=5.1, protocol=1.0",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ tokenId: token }),
    });
    const payload = await response.json().catch(() => null);
    return { ok: response.ok, status: response.status, raw: payload };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

// Classify the AM session type from the iPlanetDirectoryPro cookie value.
//
// AM has three kinds of session (see PingAM > Sessions):
//   - Server-side : state lives in the Core Token Service (CTS/DS). The cookie
//                   is a short, opaque reference key.
//   - Client-side : the whole session is serialized into the cookie as a JWT
//                   (JWS) or encrypted JWT (JWE). The cookie is large.
//   - In-memory   : transient authentication/journey session held in the AM
//                   instance's heap *during* a login flow (an authId JWT, not
//                   the SSO cookie). It is not observable once authenticated.
//
// IMPORTANT: AM wraps BOTH server-side and client-side SSO tokens in the same
// "C66" envelope, so the presence of '*' separators does NOT distinguish them.
// The reliable signal is whether the token embeds a JWT: a client-side
// (stateless) session contains a base64url JWT header segment ('eyJ...'),
// e.g. "*<base64 metadata>..*eyJhbGci...". A server-side session is just a
// short opaque reference key with no embedded JWT.
function detectAmSessionType(token) {
  if (!token) {
    return { type: "none", label: "no AM session", detail: "No iPlanetDirectoryPro cookie present." };
  }
  // A base64url-encoded JWT header ('{"alg"...' / '{"typ"...') always starts
  // with 'eyJ' followed by a long base64url run. Server-side reference keys
  // never contain such a segment.
  const embeddedJwt = token.match(/eyJ[A-Za-z0-9_-]{16,}/);
  if (embeddedJwt) {
    // Inspect the embedded JWT header to tell JWS (signed) from JWE (encrypted).
    let header = {};
    try { header = JSON.parse(Buffer.from(embeddedJwt[0].slice(3), "base64").toString("utf8")) || {}; } catch (_) { header = {}; }
    const isJwe = !!header.enc;
    return {
      type: "client-side",
      label: "Client-side (JWT)",
      detail:
        (isJwe
          ? "Encrypted JWT (JWE) session serialized into the cookie. "
          : "Signed JWT (JWS) session serialized into the cookie. ") +
        "Session state lives entirely in the cookie (AM wraps it in the C66 SSO envelope); " +
        "AM keeps only a denylist (when session blacklisting is enabled) to honour logout/revocation. " +
        "Cookie length " + token.length + ".",
    };
  }
  return {
    type: "server-side",
    label: "Server-side (CTS)",
    detail:
      "Opaque reference SSO token (no embedded JWT). Session state is stored server-side in the " +
      "Core Token Service (CTS/DS). Centrally revocable, supports session quotas, and AM/IG can " +
      "validate or terminate it instantly. Cookie length " + token.length + ".",
  };
}

// Logout the user's own session: the token to kill goes in the header.
async function amLogout(token) {
  if (!token) return { ok: false, error: "no token" };
  try {
    const response = await fetch(`${sessionsUrl}?_action=logout`, {
      method: "POST",
      headers: {
        iPlanetDirectoryPro: token,
        "Accept-API-Version": "resource=5.1, protocol=1.0",
        "Content-Type": "application/json",
      },
      body: "{}",
    });
    const payload = await response.json().catch(() => null);
    return { ok: response.ok, status: response.status, raw: payload };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

// User-wide invalidation: log out every session for the given user (admin token).
async function amLogoutByUser(username) {
  try {
    const admin = await getAdminToken();
    const response = await fetch(`${sessionsUrl}?_action=logoutByUser`, {
      method: "POST",
      headers: {
        iPlanetDirectoryPro: admin,
        "Accept-API-Version": "resource=5.1, protocol=1.0",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ username }),
    });
    const payload = await response.json().catch(() => null);
    return { ok: response.ok, status: response.status, raw: payload };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

// ---------------------------------------------------------------------------
// OIDC / OAuth2 helpers
// ---------------------------------------------------------------------------
let metadataCache = { value: null, expiresAt: 0 };
const metadataCacheTtlMs = Number.parseInt(process.env.OIDC_METADATA_CACHE_TTL_MS || "300000", 10);

async function getOidcMetadata() {
  const now = Date.now();
  if (metadataCache.value && metadataCache.expiresAt > now) return metadataCache.value;
  const response = await fetch(oidcMetadataUrl, { headers: { accept: "application/json" } });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload) {
    const error = new Error("OIDC discovery failed");
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  metadataCache = { value: payload, expiresAt: now + metadataCacheTtlMs };
  return payload;
}

function clientAuthHeaders(rp, params) {
  const headers = { "content-type": "application/x-www-form-urlencoded" };
  if (rp.clientSecret) {
    const basic = Buffer.from(
      `${encodeURIComponent(rp.clientId)}:${encodeURIComponent(rp.clientSecret)}`,
    ).toString("base64");
    headers.authorization = `Basic ${basic}`;
  } else {
    params.set("client_id", rp.clientId);
  }
  return headers;
}

async function exchangeCode(rp, code, codeVerifier) {
  const metadata = await getOidcMetadata();
  const params = new URLSearchParams({
    grant_type: "authorization_code",
    redirect_uri: redirectUri(rp.key),
    code: String(code),
    code_verifier: codeVerifier,
  });
  const headers = clientAuthHeaders(rp, params);
  const response = await fetch(metadata.token_endpoint, { method: "POST", headers, body: params });
  const payload = await response.json().catch(() => null);
  return { ok: response.ok, status: response.status, payload };
}

async function refreshTokens(rp, refreshToken) {
  const metadata = await getOidcMetadata();
  const params = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: refreshToken,
    scope,
  });
  const headers = clientAuthHeaders(rp, params);
  const response = await fetch(metadata.token_endpoint, { method: "POST", headers, body: params });
  const payload = await response.json().catch(() => null);
  return { ok: response.ok, status: response.status, payload };
}

// Introspection and revocation use RP C's confidential credentials by default.
async function introspectToken(token) {
  const rp = RPS.c;
  const metadata = await getOidcMetadata();
  const endpoint = metadata.introspection_endpoint || `${issuerUrl}/introspect`;
  const params = new URLSearchParams({ token });
  const headers = clientAuthHeaders(rp, params);
  const response = await fetch(endpoint, { method: "POST", headers, body: params });
  const payload = await response.json().catch(() => null);
  return { ok: response.ok, status: response.status, payload };
}

async function revokeToken(token) {
  const rp = RPS.c;
  const metadata = await getOidcMetadata();
  const endpoint = metadata.revocation_endpoint || `${issuerUrl}/token/revoke`;
  const params = new URLSearchParams({ token });
  const headers = clientAuthHeaders(rp, params);
  const response = await fetch(endpoint, { method: "POST", headers, body: params });
  const text = await response.text().catch(() => "");
  return { ok: response.ok, status: response.status, body: text };
}

// JWKS-based RS256 verification for API E "local JWT" mode, using only Node's
// built-in crypto (JWK -> KeyObject). Demonstrates that a self-contained JWT is
// accepted until exp even after AM logout (T3 residual risk).
let jwksCache = { value: null, expiresAt: 0 };
async function getJwks() {
  const now = Date.now();
  if (jwksCache.value && jwksCache.expiresAt > now) return jwksCache.value;
  const metadata = await getOidcMetadata();
  const response = await fetch(metadata.jwks_uri, { headers: { accept: "application/json" } });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload || !Array.isArray(payload.keys)) {
    throw new Error("JWKS fetch failed");
  }
  jwksCache = { value: payload, expiresAt: now + 3_600_000 };
  return payload;
}

function verifyJwtRs256(token, jwks) {
  const header = decodeJwtHeader(token);
  const claims = decodeJwtClaims(token);
  if (!header || !claims) return { valid: false, reason: "not a JWT" };
  const jwk = jwks.keys.find((k) => k.kid === header.kid) || jwks.keys[0];
  if (!jwk) return { valid: false, reason: "no matching JWK" };
  let signatureValid = false;
  try {
    const key = crypto.createPublicKey({ key: jwk, format: "jwk" });
    const parts = String(token).split(".");
    const signingInput = `${parts[0]}.${parts[1]}`;
    const signature = Buffer.from(parts[2].replace(/-/g, "+").replace(/_/g, "/"), "base64");
    signatureValid = crypto.verify(
      "RSA-SHA256",
      Buffer.from(signingInput),
      key,
      signature,
    );
  } catch (error) {
    return { valid: false, reason: `verify error: ${error}`, claims };
  }
  const nowSec = Math.floor(Date.now() / 1000);
  const expired = typeof claims.exp === "number" && claims.exp < nowSec;
  return {
    valid: signatureValid && !expired,
    signatureValid,
    expired,
    exp: claims.exp,
    nowSec,
    claims,
  };
}

// ---------------------------------------------------------------------------
// RP local session stores (custom, so the back-channel collector can look up
// and invalidate sessions by iss+sid / iss+sub without a DB).
// ---------------------------------------------------------------------------
const rpStore = { c: new Map(), d: new Map() };

function rpGet(key, req) {
  const id = parseCookies(req)[RPS[key].cookie];
  if (!id) return null;
  return rpStore[key].get(id) || null;
}
function rpNew(key, res) {
  const id = randomString(24);
  const rec = {
    id,
    tokens: null,
    idClaims: null,
    sid: null,
    sub: null,
    loggedOut: false,
    loginState: null,
    lastReason: null,
    createdAt: new Date().toISOString(),
  };
  rpStore[key].set(id, rec);
  res.append(
    "Set-Cookie",
    `${RPS[key].cookie}=${id}; Path=/; HttpOnly; SameSite=Lax`,
  );
  return rec;
}
function rpEnsure(key, req, res) {
  return rpGet(key, req) || rpNew(key, res);
}

// Back-channel logout: clear every local session whose iss+sid or iss+sub
// matches the logout token. Returns the number of sessions invalidated.
function invalidateBySidSub(key, sid, sub, reason) {
  let count = 0;
  for (const rec of rpStore[key].values()) {
    if ((sid && rec.sid === sid) || (sub && rec.sub === sub)) {
      rec.loggedOut = true;
      rec.tokens = null;
      rec.lastReason = reason || rec.lastReason || "BACK_CHANNEL_LOGOUT";
      count += 1;
    }
  }
  return count;
}

// ---------------------------------------------------------------------------
// Server-sent events feed (back-channel logout + notable probe results)
// ---------------------------------------------------------------------------
const sseClients = new Set();
const eventLog = [];
function pushEvent(evt) {
  const enriched = { ...evt, ts: new Date().toISOString() };
  eventLog.push(enriched);
  if (eventLog.length > 200) eventLog.shift();
  const data = `data: ${JSON.stringify(enriched)}\n\n`;
  for (const res of sseClients) {
    try {
      res.write(data);
    } catch {
      // drop broken clients on next close
    }
  }
}

// ---------------------------------------------------------------------------
// Test matrix (rendered client-side from window.MATRIX). Each scenario lists
// the action ids it exercises; the client renders a button per action.
// ---------------------------------------------------------------------------
const MATRIX = [
  {
    id: "A",
    title: "A. AM server-side session timeout",
    blurb:
      "Server-side sessions in the timeout-test realm. AM is the source of truth and terminates sessions on idle/max/logout.",
    scenarios: [
      {
        id: "S1",
        title: "AM idle expires first",
        setup: "AM_IDLE < APP_IDLE, AT_TTL, RT_TTL, AM_MAX. Log into App A/B + RP C/D, then stay idle.",
        expected:
          "AM session invalid. IG App A/B redirect to login. RP C/D cleared via back-channel logout. prompt=none fails with login_required.",
        actions: ["login-a", "login-b", "login-c", "login-d", "am-session-info", "am-validate", "prompt-none-c", "status-c", "status-d"],
      },
      {
        id: "S2",
        title: "AM max expires first (active user)",
        setup: "AM_MAX < AM_IDLE. Keep hitting a protected app to avoid idle timeout.",
        expected: "At AM_MAX, AM invalidates the session despite activity. All IG apps and OIDC RPs become invalid.",
        actions: ["login-a", "am-session-info", "am-validate", "status-c", "status-d"],
      },
      {
        id: "S3",
        title: "AM idle expires while only RP active",
        setup: "AM_IDLE short. Use RP C local app only; do not touch AM/IG.",
        expected: "AM idles out. RP cleared by back-channel logout. Local RP activity must not keep the AM session alive.",
        actions: ["login-c", "status-c", "am-validate", "am-session-info"],
      },
      {
        id: "S4",
        title: "AM idle refreshed by IG",
        setup: "Enable sessionIdleRefresh for server-side sessions; use App A repeatedly through SingleSignOnFilter.",
        expected: "AM idle is refreshed by IG activity, preventing unintended idle timeout while the user works through IG.",
        actions: ["login-a", "am-session-info", "am-validate"],
      },
      {
        id: "S5",
        title: "Equal AM idle and app idle",
        setup: "AM_IDLE = APP_IDLE. No activity.",
        expected: "AM invalidation dominates. SSO re-entry must not silently re-login from the (also invalid) AM session.",
        actions: ["login-a", "login-c", "am-validate", "prompt-none-c"],
      },
    ],
  },
  {
    id: "B",
    title: "B. AM client-side / stateless sessions (optional)",
    blurb:
      "Only relevant if client-side AM sessions are in scope. AM does not auto-terminate idle client-side sessions; IG idle tracking + denylisting are required.",
    scenarios: [
      {
        id: "C1",
        title: "Client-side AM idle without IG idle filter",
        setup: "Client-side AM session, no AmSessionIdleTimeoutFilter.",
        expected: "Negative control: AM does NOT terminate the client-side session on idle alone. Proves the need for IG idle tracking/denylisting.",
        actions: ["am-validate", "am-session-info"],
      },
      {
        id: "C2",
        title: "IG idle filter expires first",
        setup: "IG_IDLE < AM_MAX, denylisting enabled, filter before SingleSignOnFilter.",
        expected: "IG forces AM session revocation, expires the tracking cookie, and every protected app/RP becomes invalid.",
        actions: ["login-a", "am-validate", "status-c"],
      },
      {
        id: "C3",
        title: "Multiple IG routes share activity tracker",
        setup: "Same tracking cookie across App A and App B.",
        expected: "Activity in either route updates shared activity; lack of activity across all routes causes global invalidation.",
        actions: ["login-a", "login-b", "am-validate"],
      },
      {
        id: "C4",
        title: "Different IG idle policies",
        setup: "App A IG_IDLE=2m, App B IG_IDLE=5m; test idleTimeoutUpdate ALWAYS / INCREASE_ONLY / DECREASE_ONLY.",
        expected: "DECREASE_ONLY enforces the shortest idle policy; INCREASE_ONLY the longest.",
        actions: ["login-a", "login-b", "am-session-info"],
      },
      {
        id: "C5",
        title: "Lost logout cookie but denylist enabled",
        setup: "Simulate browser not accepting logout Set-Cookie; keep old iPlanetDirectoryPro.",
        expected: "AM denylist still rejects the logged-out client-side session.",
        actions: ["am-validate"],
      },
    ],
  },
  {
    id: "C",
    title: "C. IG cache & gateway-session",
    blurb:
      "App A is behind a no-cache AmService (revalidates every request). App B is behind a cached AmService (CACHE_TTL=1m). WebSocket notifications are OFF by default so the gateway always boots; with notifications off, App B can serve stale content for up to CACHE_TTL after AM logout (the G3 stale-cache behavior). Enable notifications + restart the gateway after bootstrap to demo instant eviction (G2).",
    scenarios: [
      {
        id: "G1",
        title: "IG session cache disabled (App A)",
        setup: "No sessionCache on the App A route.",
        expected: "Every request validates against AM. After AM invalidation, IG immediately redirects/rejects.",
        actions: ["login-a", "am-validate"],
      },
      {
        id: "G2",
        title: "IG cache + WebSocket notifications (App B)",
        setup: "sessionCache on, notifications on (App B route). Requires setting notifications.enabled=true on AmServiceCached AFTER bootstrap, then restarting the gateway.",
        expected: "AM logout/timeout evicts the cached entry almost immediately; subsequent App B requests with the old cookie are rejected.",
        actions: ["login-b", "am-rest-logout", "am-validate"],
      },
      {
        id: "G3",
        title: "IG cache enabled, WebSocket off (boot-tolerant default)",
        setup: "sessionCache on, notifications OFF (default). CACHE_TTL=1m.",
        expected: "After AM logout/timeout, App B may serve stale content until the cache entry expires (up to CACHE_TTL). This is the stale-cache risk; bound it with a short CACHE_TTL or enable notifications.",
        actions: ["login-b", "am-rest-logout", "am-validate"],
      },
      {
        id: "G4",
        title: "IG route logout (/logout)",
        setup: "logoutExpression matches only /logout with a defaultLogoutLandingPage.",
        expected: "Visiting /logout revokes the AM session and does not allow protected-app bypass.",
        actions: ["ig-logout", "am-validate"],
      },
    ],
  },
  {
    id: "D",
    title: "D. OIDC session & logout",
    blurb:
      "RP C and RP D each hold a local session, ID/access/refresh tokens, sid and sub, and a back-channel logout URI registered in AM.",
    scenarios: [
      {
        id: "O1",
        title: "RP-initiated logout from RP C",
        setup: "RP C calls AM end-session with id_token_hint + registered post_logout_redirect_uri.",
        expected: "AM session invalid. RP C local session cleared. RP D receives back-channel logout. IG A/B reject the old AM session.",
        actions: ["login-c", "login-d", "rp-init-logout-c", "status-c", "status-d", "am-validate"],
      },
      {
        id: "O2",
        title: "AM explicit logout while RPs active",
        setup: "Use AM REST logout (or AM UI logout).",
        expected: "RP C/D receive back-channel logout tokens and clear local sessions. IG A/B reject.",
        actions: ["login-c", "login-d", "am-rest-logout", "status-c", "status-d", "am-validate"],
      },
      {
        id: "O3",
        title: "AM idle timeout while RPs active",
        setup: "AM_IDLE first.",
        expected: "Back-channel logout to RP C/D with SESSION_IDLE_TIMEOUT where available. Both RPs clear local sessions.",
        actions: ["login-c", "login-d", "am-session-info", "status-c", "status-d"],
      },
      {
        id: "O4",
        title: "AM max timeout while RPs active",
        setup: "AM_MAX first.",
        expected: "Back-channel logout to RP C/D with SESSION_MAX_TIMEOUT where available. Both RPs clear local sessions.",
        actions: ["login-c", "login-d", "am-session-info", "status-c", "status-d"],
      },
      {
        id: "O5",
        title: "OIDC session check only",
        setup: "RP relies on hidden iframe/session check, no back-channel.",
        expected: "RP detects the invalid OP session and clears local session. Less deterministic; secondary mechanism.",
        actions: ["login-c", "prompt-none-c", "status-c"],
      },
      {
        id: "O6",
        title: "RP local logout only (negative control)",
        setup: "Clear RP C local session but do NOT call AM end-session.",
        expected: "AM session remains valid; IG A/B and RP D stay logged in. Local app logout is not global logout.",
        actions: ["login-c", "login-d", "local-logout-c", "status-c", "status-d", "am-validate"],
      },
      {
        id: "O7",
        title: "Back-channel endpoint down",
        setup: "RP D back-channel endpoint returns 500 / unreachable.",
        expected: "AM logs delivery failure; RP D may remain locally logged in. Operational risk, not a pass.",
        actions: ["login-d", "am-rest-logout", "status-d"],
      },
      {
        id: "O8",
        title: "Multiple browser/device sessions",
        setup: "Same user logs in from two browsers.",
        expected: "RP-initiated logout with Browser 1's ID token kills Browser 1's AM session only. Browser 2 survives unless logoutByUser is used.",
        actions: ["login-c", "rp-init-logout-c", "am-logout-by-user", "am-validate"],
      },
    ],
  },
  {
    id: "E",
    title: "E. OAuth / OIDC token behavior",
    blurb:
      "API E validates a bearer token two ways: AM introspection (revocation-aware) and local JWT (exp-only). Capture a pre-logout token to replay it.",
    scenarios: [
      {
        id: "T1",
        title: "Access token expires before AM session",
        setup: "AT_TTL < AM_IDLE, AM_MAX.",
        expected: "API rejects the expired AT, but the user stays logged in to AM/RPs. RP can refresh if RT valid. Not global logout.",
        actions: ["login-c", "capture-c-at", "api-e-introspect", "refresh-c"],
      },
      {
        id: "T2",
        title: "Refresh token expires before AM session",
        setup: "RT_TTL < AM_IDLE, AM_MAX.",
        expected: "RP cannot refresh. Because the AM session is still valid, an interactive/silent flow may mint new tokens unless policy prevents it.",
        actions: ["login-c", "refresh-c", "am-validate", "prompt-none-c"],
      },
      {
        id: "T3",
        title: "AM logout while AT still unexpired",
        setup: "Long AT_TTL. Capture AT, then log out, then call API E both ways.",
        expected: "Introspection rejects (revoked). Local-JWT validation may accept until exp - documented residual risk.",
        actions: ["login-c", "capture-c-at", "am-rest-logout", "api-e-captured-introspect", "api-e-captured-jwt"],
      },
      {
        id: "T4",
        title: "AM logout while RT still unexpired",
        setup: "Long RT_TTL.",
        expected: "Refresh must fail if logout revokes the grant/token. Verify by attempting a refresh after logout.",
        actions: ["login-c", "am-rest-logout", "refresh-c"],
      },
      {
        id: "T5",
        title: "Revoke known refresh token",
        setup: "Call the revocation endpoint for the RT.",
        expected: "Revoking the RT revokes tokens from the same grant. Old RT fails; AT behavior matches your introspection/JWT strategy.",
        actions: ["login-c", "capture-c-at", "revoke-c-rt", "refresh-c", "api-e-captured-introspect"],
      },
      {
        id: "T6",
        title: "User-wide token / session invalidation",
        setup: "Use AM user-wide invalidation (logoutByUser) for account-wide / compromise response.",
        expected: "All active sessions for the user are removed across browsers/devices.",
        actions: ["login-c", "am-logout-by-user", "am-validate", "status-c"],
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// Client config + dashboard HTML
// ---------------------------------------------------------------------------
const CFG = {
  appBaseUrl,
  amBaseUrl,
  issuer: issuerUrl,
  realmName,
  realmPath,
  amXuiLogin,
  protectedA: "/protected/a",
  protectedB: "/protected/b",
  igLogout: "/logout",
  demoUser,
  rpC: RPS.c.clientId,
  rpD: RPS.d.clientId,
};

const PAGE_CSS = `
  :root {
    --bg: #0f1720; --panel: #16212e; --panel2: #1d2a39; --ink: #e8f1ef;
    --muted: #93a4b3; --accent: #2dd4bf; --accent-ink: #06262b;
    --ok: #34d399; --bad: #f87171; --warn: #fbbf24; --border: #2a3a4b;
    --info: #60a5fa;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: "IBM Plex Sans","Segoe UI",sans-serif;
    background: radial-gradient(circle at top right,#13202c 0,transparent 40%),var(--bg); color: var(--ink); }
  a { color: var(--accent); }
  header.top { position: sticky; top: 0; z-index: 50; backdrop-filter: blur(6px);
    background: rgba(15,23,32,0.9); border-bottom: 1px solid var(--border); padding: 14px 24px; }
  .eyebrow { display:inline-block; padding:4px 10px; border-radius:999px; background:#0b3b3b;
    color: var(--accent); font-size:12px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; }
  h1 { margin: 8px 0 4px; font-size: 1.5rem; }
  .sub { color: var(--muted); font-size: .85rem; margin: 0; }
  .strip { display:flex; flex-wrap:wrap; gap:10px; margin-top:12px; font-size:.8rem; }
  .chip { padding:6px 10px; border-radius:8px; background: var(--panel2); border:1px solid var(--border);
    font-family:"IBM Plex Mono",monospace; }
  .chip b { color: var(--accent); }
  .chip.ok b { color: var(--ok); } .chip.bad b { color: var(--bad); } .chip.warn b { color: var(--warn); }
  .chip.info b { color: var(--info); } .chip[title] { cursor: help; }
  .legend { display:flex; flex-wrap:wrap; gap:14px; margin-top:10px; font-size:.74rem; color: var(--muted); }
  .legend b { color: var(--ink); font-weight:700; }
  .legend .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:5px; vertical-align:middle; }
  .legend .dot.server { background: var(--info); } .legend .dot.client { background: var(--warn); } .legend .dot.memory { background: var(--muted); }
  main { max-width: 1180px; margin: 0 auto; padding: 20px 24px 80px; }
  .toolbar { display:flex; flex-wrap:wrap; gap:8px; margin: 16px 0 24px; }
  button.act { appearance:none; cursor:pointer; border:1px solid var(--border); background: var(--panel2);
    color: var(--ink); padding:7px 12px; border-radius:8px; font:600 .82rem "IBM Plex Sans",sans-serif; }
  button.act:hover { border-color: var(--accent); }
  button.act.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
  button.act:disabled { opacity:.5; cursor:default; }
  section.group { margin-bottom: 28px; }
  section.group > h2 { font-size: 1.1rem; margin: 0 0 4px; }
  section.group > p.blurb { color: var(--muted); margin: 0 0 14px; max-width: 90ch; font-size:.88rem; }
  .cards { display:grid; grid-template-columns: repeat(auto-fill,minmax(340px,1fr)); gap:14px; }
  .card { background: var(--panel); border:1px solid var(--border); border-radius:12px; padding:14px; display:flex; flex-direction:column; gap:8px; }
  .card h3 { margin:0; font-size:.98rem; }
  .card .rid { font-family:"IBM Plex Mono",monospace; color: var(--accent); font-size:.78rem; }
  .card .meta { font-size:.8rem; color: var(--muted); margin:0; }
  .card .meta b { color:#cdd9e3; }
  .card .btns { display:flex; flex-wrap:wrap; gap:6px; margin-top:2px; }
  .card pre { white-space:pre-wrap; word-break:break-word; background:#0a121a; color:#c8e9e2; margin:0;
    padding:10px; border-radius:8px; font-size:11.5px; max-height:240px; overflow:auto; border:1px solid var(--border); }
  .feed { position: fixed; right: 16px; bottom: 16px; width: 340px; max-height: 42vh; overflow:auto;
    background: var(--panel); border:1px solid var(--border); border-radius:12px; padding:12px; box-shadow:0 12px 40px rgba(0,0,0,.5); }
  .feed h4 { margin:0 0 8px; font-size:.85rem; display:flex; justify-content:space-between; }
  .feed .ev { font-family:"IBM Plex Mono",monospace; font-size:11px; border-bottom:1px solid var(--border); padding:6px 0; }
  .feed .ev .t { color: var(--muted); }
  .feed .ev .k { color: var(--accent); }
  .badge { font-size:.7rem; padding:2px 6px; border-radius:6px; background:#0b3b3b; color:var(--accent); }
`;

const CLIENT_JS = `
(function () {
  var CFG = window.CFG, MATRIX = window.MATRIX;
  var captured = { accessToken: null, source: null };

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function openTab(url) { window.open(url, "_blank", "noopener"); }
  function fmt(obj) {
    try { return JSON.stringify(obj, null, 2); } catch (e) { return String(obj); }
  }
  function postJson(path, body) {
    return fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().catch(function () { return { status: r.status }; }); });
  }
  function getJson(path) {
    return fetch(path, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json().catch(function () { return { status: r.status }; }); });
  }

  var ACTIONS = {
    "login-a": { label: "Open IG App A", run: function () { openTab(CFG.protectedA); return Promise.resolve({ opened: CFG.protectedA }); } },
    "login-b": { label: "Open IG App B (cached)", run: function () { openTab(CFG.protectedB); return Promise.resolve({ opened: CFG.protectedB }); } },
    "login-c": { label: "Login RP C", run: function () { openTab("/rp/c/login"); return Promise.resolve({ opened: "/rp/c/login" }); } },
    "login-d": { label: "Login RP D", run: function () { openTab("/rp/d/login"); return Promise.resolve({ opened: "/rp/d/login" }); } },
    "status-c": { label: "RP C status", run: function () { return getJson("/rp/c/status"); } },
    "status-d": { label: "RP D status", run: function () { return getJson("/rp/d/status"); } },
    "am-validate": { label: "AM validate (refresh=false)", run: function () { return postJson("/probe/am-validate"); } },
    "am-session-info": { label: "AM session info", run: function () { return postJson("/probe/am-session-info"); } },
    "am-rest-logout": { label: "AM REST logout", run: function () { return postJson("/probe/am-rest-logout"); } },
    "am-logout-by-user": { label: "AM logoutByUser", run: function () { return postJson("/probe/am-logout-by-user"); } },
    "rp-init-logout-c": { label: "RP C end-session (RP-initiated)", run: function () { openTab("/rp/c/rp-initiated-logout"); return Promise.resolve({ opened: "/rp/c/rp-initiated-logout" }); } },
    "local-logout-c": { label: "RP C local logout", run: function () { return postJson("/rp/c/logout"); } },
    "local-logout-d": { label: "RP D local logout", run: function () { return postJson("/rp/d/logout"); } },
    "prompt-none-c": { label: "RP C prompt=none check", run: function () { openTab("/rp/c/login?prompt=none"); return Promise.resolve({ opened: "/rp/c/login?prompt=none" }); } },
    "ig-logout": { label: "IG /logout route", run: function () { openTab(CFG.igLogout); return Promise.resolve({ opened: CFG.igLogout }); } },
    "refresh-c": { label: "RP C refresh-token reuse", run: function () { return postJson("/probe/refresh", { rp: "c" }); } },
    "revoke-c-rt": { label: "Revoke RP C refresh token", run: function () { return postJson("/probe/revoke", { rp: "c", type: "refresh" }); } },
    "capture-c-at": { label: "Capture RP C access token", run: function () {
      return getJson("/rp/c/status").then(function (s) {
        var at = s && s.tokens && s.tokens.access_token;
        captured.accessToken = at || null; captured.source = "RP C";
        return { capturedAccessToken: at ? (at.slice(0, 24) + "...") : null, note: at ? "Captured. Replay it with the API E captured buttons after logout." : "No access token in RP C session - log in first." };
      });
    } },
    "api-e-introspect": { label: "API E (introspect, live token)", run: function () { return postJson("/probe/api-e", { rp: "c", mode: "introspect" }); } },
    "api-e-captured-introspect": { label: "API E (introspect, captured token)", run: function () {
      if (!captured.accessToken) return Promise.resolve({ error: "No captured token. Use 'Capture RP C access token' first." });
      return postJson("/probe/api-e", { token: captured.accessToken, mode: "introspect" });
    } },
    "api-e-captured-jwt": { label: "API E (local JWT, captured token)", run: function () {
      if (!captured.accessToken) return Promise.resolve({ error: "No captured token. Use 'Capture RP C access token' first." });
      return postJson("/probe/api-e", { token: captured.accessToken, mode: "jwt" });
    } }
  };

  function renderStrip(state) {
    var strip = document.getElementById("strip");
    strip.innerHTML = "";
    function chip(label, value, kind, title) {
      var c = el("span", "chip " + (kind || ""), label + ": <b>" + value + "</b>");
      if (title) c.title = title;
      strip.appendChild(c);
    }
    chip("AM cookie", state.amCookiePresent ? "present" : "absent", state.amCookiePresent ? "ok" : "bad");
    chip("AM session", state.amValid ? "VALID" : "invalid", state.amValid ? "ok" : "bad");
    if (state.amSessionType && state.amSessionType.type !== "none") {
      var st = state.amSessionType;
      var stKind = st.type === "server-side" ? "info" : (st.type === "client-side" ? "warn" : "bad");
      chip("AM type", st.label, stKind, st.detail + " (hover for details)");
    }
    if (state.sessionInfo) {
      chip("idle left", state.sessionInfo.idleLeftSec != null ? state.sessionInfo.idleLeftSec + "s" : "?", "warn");
      chip("max left", state.sessionInfo.maxLeftSec != null ? state.sessionInfo.maxLeftSec + "s" : "?", "warn");
    }
    chip("RP C", state.rpC && state.rpC.authenticated ? "in" : (state.rpC && state.rpC.loggedOut ? "logged out" : "out"), state.rpC && state.rpC.authenticated ? "ok" : "bad");
    chip("RP D", state.rpD && state.rpD.authenticated ? "in" : (state.rpD && state.rpD.loggedOut ? "logged out" : "out"), state.rpD && state.rpD.authenticated ? "ok" : "bad");
  }
  function pollState() {
    getJson("/probe/state").then(renderStrip).catch(function () {});
  }

  function buildCard(sc) {
    var card = el("div", "card");
    card.appendChild(el("div", "rid", sc.id));
    card.appendChild(el("h3", null, sc.title));
    card.appendChild(el("p", "meta", "<b>Setup:</b> " + sc.setup));
    card.appendChild(el("p", "meta", "<b>Expected:</b> " + sc.expected));
    var btns = el("div", "btns");
    var out = el("pre", null, "Ready.");
    (sc.actions || []).forEach(function (id) {
      var a = ACTIONS[id];
      if (!a) return;
      var b = el("button", "act", a.label);
      b.addEventListener("click", function () {
        b.disabled = true;
        out.textContent = "Running " + a.label + " ...";
        Promise.resolve(a.run()).then(function (res) {
          out.textContent = fmt(res);
          b.disabled = false;
          pollState();
        }).catch(function (e) { out.textContent = "Error: " + e; b.disabled = false; });
      });
      btns.appendChild(b);
    });
    card.appendChild(btns);
    card.appendChild(out);
    return card;
  }

  function render() {
    var root = document.getElementById("matrix");
    MATRIX.forEach(function (group) {
      var sec = el("section", "group");
      sec.appendChild(el("h2", null, group.title));
      sec.appendChild(el("p", "blurb", group.blurb));
      var cards = el("div", "cards");
      group.scenarios.forEach(function (sc) { cards.appendChild(buildCard(sc)); });
      sec.appendChild(cards);
      root.appendChild(sec);
    });
  }

  function wireToolbar() {
    document.getElementById("tb-state").addEventListener("click", pollState);
    document.getElementById("tb-validate").addEventListener("click", function () {
      postJson("/probe/am-validate").then(function (r) { alert("AM validate: " + (r.result && r.result.valid ? "VALID" : "invalid")); pollState(); });
    });
    document.getElementById("tb-amlogin").addEventListener("click", function () { openTab(CFG.amXuiLogin); });
    document.getElementById("tb-reset").addEventListener("click", function () {
      if (!confirm("Reset: AM logout + clear local RP C/D sessions for this browser?")) return;
      postJson("/probe/reset").then(function (r) { pollState(); alert("Reset done: " + fmt(r)); });
    });
  }

  function connectEvents() {
    var list = document.getElementById("feed-list");
    try {
      var es = new EventSource("/events");
      es.onmessage = function (m) {
        var evt; try { evt = JSON.parse(m.data); } catch (e) { return; }
        var row = el("div", "ev");
        var t = new Date(evt.ts).toLocaleTimeString();
        row.innerHTML = "<span class='t'>" + t + "</span> <span class='k'>" + (evt.type || "event") + "</span><br>" +
          (evt.rp ? ("rp=" + evt.rp + " ") : "") +
          (evt.reason ? ("reason=" + evt.reason + " ") : "") +
          (evt.sub ? ("sub=" + evt.sub + " ") : "") +
          (evt.sid ? ("sid=" + String(evt.sid).slice(0, 12) + "... ") : "") +
          (evt.cleared != null ? ("cleared=" + evt.cleared) : (evt.detail || ""));
        list.insertBefore(row, list.firstChild);
      };
    } catch (e) { /* SSE unsupported */ }
  }

  render();
  wireToolbar();
  connectEvents();
  pollState();
  setInterval(pollState, 5000);
})();
`;

function renderPage() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Session Timeout / Logout / OIDC SLO Test Console</title>
<style>${PAGE_CSS}</style>
</head>
<body>
<header class="top">
  <span class="eyebrow">app6.jrsz.org &middot; timeout-test realm</span>
  <h1>Session Timeout / Logout / OIDC SLO Test Console</h1>
  <p class="sub">AM is the source of truth. Once the AM session is invalid, every IG-protected app must reject it, every OIDC RP must clear local state, and tokens must behave per design.</p>
  <div id="strip" class="strip"></div>
  <div class="legend">
    <span><b>AM session types:</b></span>
    <span><span class="dot server"></span><b>Server-side (CTS)</b> &mdash; opaque SSO token; state in the Core Token Service (DS). Centrally revocable.</span>
    <span><span class="dot client"></span><b>Client-side (JWT)</b> &mdash; signed/encrypted JWT in the cookie; only a denylist server-side.</span>
    <span><span class="dot memory"></span><b>In-memory</b> &mdash; transient journey/auth session during login (not the SSO cookie).</span>
  </div>
</header>
<main>
  <div class="toolbar">
    <button id="tb-amlogin" class="act primary">Open AM login (timeout-test)</button>
    <button id="tb-state" class="act">Refresh instrumentation</button>
    <button id="tb-validate" class="act">Quick AM validate</button>
    <button id="tb-reset" class="act">Reset this browser</button>
  </div>
  <div id="matrix"></div>
</main>
<aside class="feed">
  <h4>Back-channel logout events <span class="badge">SSE</span></h4>
  <div id="feed-list"></div>
</aside>
<script>window.CFG=${JSON.stringify(CFG)};window.MATRIX=${JSON.stringify(MATRIX)};</script>
<script>${CLIENT_JS}</script>
</body>
</html>`;
}

// ---------------------------------------------------------------------------
// API E resource-server validation (shared by /api/e/resource and /probe/api-e)
// ---------------------------------------------------------------------------
async function apiEValidate(token, mode) {
  if (!token) return { accepted: false, reason: "no bearer token" };
  if (mode === "jwt") {
    const header = decodeJwtHeader(token);
    if (!header) {
      return {
        accepted: false,
        mode: "jwt",
        reason:
          "Presented token is not a JWT (AM opaque access token). Local JWT validation is impossible; use introspection.",
      };
    }
    try {
      const jwks = await getJwks();
      const result = verifyJwtRs256(token, jwks);
      return {
        accepted: !!result.valid,
        mode: "jwt",
        note:
          "Local JWT validation only checks signature + exp. A self-contained JWT stays accepted until exp even after AM logout (T3 residual risk).",
        ...result,
      };
    } catch (error) {
      return { accepted: false, mode: "jwt", reason: String(error) };
    }
  }
  // default: introspection (revocation/session aware)
  const result = await introspectToken(token);
  const active = !!(result.payload && result.payload.active);
  return {
    accepted: active,
    mode: "introspect",
    status: result.status,
    note: "Introspection reflects live AM state - rejects revoked/expired/logged-out tokens.",
    introspection: result.payload,
  };
}

// ---------------------------------------------------------------------------
// Routes: dashboard
// ---------------------------------------------------------------------------
app.get("/", (req, res) => {
  res.set("Cache-Control", "no-store");
  res.set("Content-Type", "text/html; charset=UTF-8");
  res.send(renderPage());
});

app.get("/healthz", (req, res) => res.json({ ok: true }));

// ---------------------------------------------------------------------------
// Routes: IG-protected App A / App B backends (reached only via SingleSignOnFilter)
// ---------------------------------------------------------------------------
function protectedPage(name, note) {
  return `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>${htmlEscape(name)}</title>
<style>body{font-family:"IBM Plex Sans",sans-serif;background:#0f1720;color:#e8f1ef;margin:0;padding:48px}
.box{max-width:680px;margin:0 auto;background:#16212e;border:1px solid #2a3a4b;border-radius:14px;padding:28px}
h1{margin:0 0 6px}.pill{display:inline-block;background:#0b3b3b;color:#2dd4bf;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700}
a{color:#2dd4bf}code{font-family:"IBM Plex Mono",monospace}</style></head>
<body><div class="box"><span class="pill">${htmlEscape(name)}</span>
<h1>Protected content delivered</h1>
<p>You only see this because PingGateway's <code>SingleSignOnFilter</code> validated a live AM session in the <code>${htmlEscape(realmName)}</code> realm. ${note}</p>
<p>If the AM session becomes invalid (idle/max/logout), IG must stop returning this page and redirect to AM login.</p>
<p><a href="/">Back to the test console</a></p></div></body></html>`;
}
app.get("/protected/a", (req, res) => {
  res.set("Cache-Control", "no-store");
  res.send(protectedPage("IG App A", "App A is behind a <b>no-cache</b> AmService - every request revalidates against AM."));
});
app.get("/protected/b", (req, res) => {
  res.set("Cache-Control", "no-store");
  res.send(protectedPage("IG App B", "App B is behind a <b>cached</b> AmService (CACHE_TTL=1m). With WebSocket notifications off (the boot-tolerant default) cache entries expire on TTL; enable notifications after bootstrap for instant eviction."));
});

// ---------------------------------------------------------------------------
// Routes: OIDC RP C / RP D
// ---------------------------------------------------------------------------
function renderRpPage(rp, rec) {
  const authed = !!(rec && rec.tokens && !rec.loggedOut);
  const body = {
    authenticated: authed,
    loggedOut: rec ? rec.loggedOut : false,
    sub: rec ? rec.sub : null,
    sid: rec ? rec.sid : null,
    lastReason: rec ? rec.lastReason : null,
    tokens: rec ? rec.tokens : null,
    idClaims: rec ? rec.idClaims : null,
  };
  return `<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>${htmlEscape(rp.label)}</title>
<style>body{font-family:"IBM Plex Sans",sans-serif;background:#0f1720;color:#e8f1ef;margin:0;padding:40px}
.box{max-width:820px;margin:0 auto}h1{margin:0 0 4px}.pill{display:inline-block;background:#0b3b3b;color:#2dd4bf;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700}
a.btn{display:inline-block;margin:4px 6px 0 0;background:#1d2a39;border:1px solid #2a3a4b;color:#e8f1ef;padding:8px 12px;border-radius:8px;text-decoration:none;font-weight:600;font-size:.85rem}
pre{white-space:pre-wrap;word-break:break-word;background:#0a121a;color:#c8e9e2;padding:14px;border-radius:10px;border:1px solid #2a3a4b;font-size:12px}
.state{font-weight:700}.in{color:#34d399}.out{color:#f87171}</style></head>
<body><div class="box"><span class="pill">${htmlEscape(rp.label)} &middot; ${htmlEscape(rp.clientId)}</span>
<h1>OIDC Relying Party ${htmlEscape(rp.key.toUpperCase())}</h1>
<p class="state ${authed ? "in" : "out"}">${authed ? "Signed in" : "Signed out"}${rec && rec.loggedOut ? " (logged out: " + htmlEscape(rec.lastReason || "back-channel") + ")" : ""}</p>
<p>
  <a class="btn" href="/rp/${rp.key}/login">Login</a>
  <a class="btn" href="/rp/${rp.key}/login?prompt=none">prompt=none check</a>
  <a class="btn" href="/rp/${rp.key}/rp-initiated-logout">RP-initiated logout (end-session)</a>
  <a class="btn" href="/">Test console</a>
</p>
<pre>${htmlEscape(JSON.stringify(body, null, 2))}</pre>
</div></body></html>`;
}

app.get("/rp/:key/login", async (req, res) => {
  const rp = RPS[req.params.key];
  if (!rp) return res.status(404).send("unknown RP");
  try {
    const metadata = await getOidcMetadata();
    const rec = rpEnsure(rp.key, req, res);
    const state = randomString();
    const nonce = randomString();
    const codeVerifier = randomString(48);
    rec.loginState = { state, nonce, codeVerifier };
    rec.loggedOut = false;
    const url = new URL(metadata.authorization_endpoint);
    url.searchParams.set("client_id", rp.clientId);
    url.searchParams.set("redirect_uri", redirectUri(rp.key));
    url.searchParams.set("response_type", "code");
    url.searchParams.set("scope", scope);
    url.searchParams.set("state", state);
    url.searchParams.set("nonce", nonce);
    url.searchParams.set("code_challenge", base64Url(sha256(codeVerifier)));
    url.searchParams.set("code_challenge_method", "S256");
    if (req.query.prompt) url.searchParams.set("prompt", String(req.query.prompt));
    res.redirect(url.toString());
  } catch (error) {
    res.status(error.status || 500).json({ error: "discovery failed", detail: error.payload || String(error) });
  }
});

app.get("/rp/:key/callback", async (req, res) => {
  const rp = RPS[req.params.key];
  if (!rp) return res.status(404).send("unknown RP");
  const rec = rpGet(rp.key, req);
  const { code, state, error, error_description: errDesc } = req.query;
  if (error) {
    // e.g. login_required from a prompt=none check once the AM session is dead.
    return res
      .status(200)
      .set("Cache-Control", "no-store")
      .send(
        `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${htmlEscape(rp.label)} error</title>
<style>body{font-family:"IBM Plex Sans",sans-serif;background:#0f1720;color:#e8f1ef;padding:40px}pre{background:#0a121a;padding:14px;border-radius:10px;border:1px solid #2a3a4b}</style></head>
<body><h1>${htmlEscape(rp.label)} authorization response</h1>
<pre>${htmlEscape(JSON.stringify({ error, error_description: errDesc }, null, 2))}</pre>
<p>For a <code>prompt=none</code> check, <b>login_required</b> proves the AM session is no longer valid.</p>
<p><a style="color:#2dd4bf" href="/rp/${rp.key}/">Back to ${htmlEscape(rp.label)}</a></p></body></html>`,
      );
  }
  if (!code || !state || !rec || !rec.loginState || state !== rec.loginState.state) {
    return res.status(400).send("state validation failed");
  }
  const result = await exchangeCode(rp, code, rec.loginState.codeVerifier);
  if (!result.ok) {
    return res.status(result.status || 400).set("Content-Type", "text/html").send(
      `<pre style="white-space:pre-wrap">${htmlEscape(JSON.stringify(result.payload, null, 2))}</pre>`,
    );
  }
  rec.tokens = result.payload;
  rec.idClaims = decodeJwtClaims(result.payload.id_token);
  rec.sid = rec.idClaims ? rec.idClaims.sid || null : null;
  rec.sub = rec.idClaims ? rec.idClaims.sub || null : null;
  rec.loggedOut = false;
  rec.lastReason = null;
  rec.loginState = null;
  pushEvent({ type: "rp-login", rp: rp.key, sub: rec.sub, sid: rec.sid });
  res.redirect(`/rp/${rp.key}/`);
});

app.get("/rp/:key/", (req, res) => {
  const rp = RPS[req.params.key];
  if (!rp) return res.status(404).send("unknown RP");
  res.set("Cache-Control", "no-store").set("Content-Type", "text/html; charset=UTF-8");
  res.send(renderRpPage(rp, rpGet(rp.key, req)));
});

app.get("/rp/:key/status", (req, res) => {
  const rp = RPS[req.params.key];
  if (!rp) return res.status(404).json({ error: "unknown RP" });
  const rec = rpGet(rp.key, req);
  res.set("Cache-Control", "no-store");
  res.json({
    key: rp.key,
    label: rp.label,
    authenticated: !!(rec && rec.tokens && !rec.loggedOut),
    loggedOut: rec ? rec.loggedOut : false,
    sub: rec ? rec.sub : null,
    sid: rec ? rec.sid : null,
    lastReason: rec ? rec.lastReason : null,
    hasRefreshToken: !!(rec && rec.tokens && rec.tokens.refresh_token),
    tokens: rec ? rec.tokens : null,
    idClaims: rec ? rec.idClaims : null,
  });
});

// Local logout only (negative control O6): does NOT touch the AM session.
app.post("/rp/:key/logout", (req, res) => {
  const rp = RPS[req.params.key];
  if (!rp) return res.status(404).json({ error: "unknown RP" });
  const rec = rpGet(rp.key, req);
  if (rec) {
    rec.tokens = null;
    rec.loggedOut = true;
    rec.lastReason = "LOCAL_LOGOUT";
  }
  pushEvent({ type: "local-logout", rp: rp.key, detail: "local only; AM session untouched" });
  res.set("Cache-Control", "no-store");
  res.json({ loggedOut: true, scope: "local only", note: "AM session and other RPs are unaffected (O6 negative control)." });
});

// RP-initiated logout (O1): redirect the browser to AM end-session.
app.get("/rp/:key/rp-initiated-logout", async (req, res) => {
  const rp = RPS[req.params.key];
  if (!rp) return res.status(404).send("unknown RP");
  const rec = rpGet(rp.key, req);
  if (!rec || !rec.tokens || !rec.tokens.id_token) {
    return res.redirect(`/rp/${rp.key}/`);
  }
  try {
    const metadata = await getOidcMetadata();
    const endpoint = metadata.end_session_endpoint || `${issuerUrl}/connect/endSession`;
    const url = new URL(endpoint);
    url.searchParams.set("id_token_hint", rec.tokens.id_token);
    url.searchParams.set("post_logout_redirect_uri", postLogoutUri(rp.key));
    url.searchParams.set("client_id", rp.clientId);
    pushEvent({ type: "rp-initiated-logout", rp: rp.key, sub: rec.sub, sid: rec.sid });
    res.redirect(url.toString());
  } catch (error) {
    res.status(500).send("end-session discovery failed: " + error);
  }
});

app.get("/rp/:key/post-logout", (req, res) => {
  const rp = RPS[req.params.key];
  if (!rp) return res.status(404).send("unknown RP");
  const rec = rpGet(rp.key, req);
  if (rec) {
    rec.tokens = null;
    rec.loggedOut = true;
    rec.lastReason = rec.lastReason || "RP_INITIATED_LOGOUT";
  }
  res.set("Cache-Control", "no-store").set("Content-Type", "text/html; charset=UTF-8");
  res.send(
    `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${htmlEscape(rp.label)} logged out</title>
<style>body{font-family:"IBM Plex Sans",sans-serif;background:#0f1720;color:#e8f1ef;padding:48px}a{color:#2dd4bf}</style></head>
<body><h1>${htmlEscape(rp.label)}: RP-initiated logout complete</h1>
<p>AM deleted the authenticated session and redirected back to this post-logout URI. Other RPs should receive a back-channel logout.</p>
<p><a href="/rp/${rp.key}/">Back to ${htmlEscape(rp.label)}</a> &middot; <a href="/">Test console</a></p></body></html>`,
  );
});

// Back-channel logout collector (server-to-server; no browser cookie present).
app.post("/rp/:key/backchannel", (req, res) => {
  const rp = RPS[req.params.key];
  if (!rp) return res.status(404).json({ error: "unknown RP" });
  const logoutToken = (req.body && req.body.logout_token) || null;
  const claims = decodeJwtClaims(logoutToken);
  if (!claims) {
    return res.status(400).set("Cache-Control", "no-store").json({ error: "invalid logout_token" });
  }
  const sid = claims.sid || null;
  const sub = claims.sub || null;
  // AM may convey the trigger in a custom claim; capture whatever is present.
  const reason =
    claims.reason ||
    (claims.events && Object.keys(claims.events).find((k) => /logout/i.test(k))) ||
    "BACK_CHANNEL_LOGOUT";
  const cleared = invalidateBySidSub(rp.key, sid, sub, reason);
  pushEvent({ type: "back-channel-logout", rp: rp.key, sid, sub, reason, cleared, claims });
  res.status(200).set("Cache-Control", "no-store").json({ ok: true, cleared });
});

// ---------------------------------------------------------------------------
// Routes: API E resource server
// ---------------------------------------------------------------------------
app.get("/api/e/resource", async (req, res) => {
  const auth = req.headers.authorization || "";
  const bearer = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : null;
  const token = bearer || (req.query.access_token ? String(req.query.access_token) : null);
  const mode = req.query.mode === "jwt" ? "jwt" : "introspect";
  const result = await apiEValidate(token, mode);
  res.set("Cache-Control", "no-store");
  res.status(result.accepted ? 200 : 401).json({
    resource: "api-e",
    accepted: result.accepted,
    validation: result,
  });
});

// ---------------------------------------------------------------------------
// Routes: probes (called by the dashboard)
// ---------------------------------------------------------------------------
app.post("/probe/am-validate", async (req, res) => {
  const token = getAmToken(req);
  const result = await amValidate(token);
  res.set("Cache-Control", "no-store");
  res.json({ amCookiePresent: !!token, result });
});

app.post("/probe/am-session-info", async (req, res) => {
  const token = getAmToken(req);
  const info = await amSessionInfo(token);
  res.set("Cache-Control", "no-store");
  res.json({ amCookiePresent: !!token, sessionType: detectAmSessionType(token), info });
});

app.post("/probe/am-rest-logout", async (req, res) => {
  const token = getAmToken(req);
  const result = await amLogout(token);
  res.append("Set-Cookie", clearAmCookieHeader());
  res.set("Cache-Control", "no-store");
  pushEvent({ type: "am-rest-logout", detail: "AM REST _action=logout", cleared: result.ok });
  res.json({ loggedOut: true, result });
});

app.post("/probe/am-logout-by-user", async (req, res) => {
  const username = (req.body && req.body.username) || demoUser;
  const result = await amLogoutByUser(username);
  res.set("Cache-Control", "no-store");
  pushEvent({ type: "am-logout-by-user", detail: "user-wide invalidation: " + username });
  res.json({ username, result });
});

app.post("/probe/refresh", async (req, res) => {
  const key = (req.body && req.body.rp) || "c";
  const rp = RPS[key];
  if (!rp) return res.status(400).json({ error: "unknown rp" });
  const rec = rpGet(key, req);
  const refreshToken = rec && rec.tokens && rec.tokens.refresh_token;
  if (!refreshToken) {
    return res.json({ ok: false, error: "no refresh token in RP " + key.toUpperCase() + " session - log in first" });
  }
  const result = await refreshTokens(rp, refreshToken);
  if (result.ok && rec) {
    rec.tokens = { ...rec.tokens, ...result.payload };
  }
  res.set("Cache-Control", "no-store");
  res.json({ ok: result.ok, status: result.status, payload: result.payload });
});

app.post("/probe/revoke", async (req, res) => {
  const key = (req.body && req.body.rp) || "c";
  const type = (req.body && req.body.type) || "refresh";
  const rec = rpGet(key, req);
  const token = rec && rec.tokens && (type === "access" ? rec.tokens.access_token : rec.tokens.refresh_token);
  if (!token) {
    return res.json({ ok: false, error: "no " + type + " token in RP " + key.toUpperCase() + " session" });
  }
  const result = await revokeToken(token);
  res.set("Cache-Control", "no-store");
  pushEvent({ type: "token-revoke", rp: key, detail: "revoked " + type + " token" });
  res.json(result);
});

app.post("/probe/api-e", async (req, res) => {
  let token = (req.body && req.body.token) || null;
  if (!token && req.body && req.body.rp) {
    const rec = rpGet(req.body.rp, req);
    token = rec && rec.tokens && rec.tokens.access_token;
  }
  const mode = req.body && req.body.mode === "jwt" ? "jwt" : "introspect";
  const result = await apiEValidate(token, mode);
  res.set("Cache-Control", "no-store");
  res.json(result);
});

app.get("/probe/state", async (req, res) => {
  const token = getAmToken(req);
  const validate = await amValidate(token);
  let sessionInfo = null;
  if (validate.valid) {
    const info = await amSessionInfo(token);
    const raw = info.raw || {};
    const now = Date.now();
    const idleExp = raw.maxIdleExpirationTime ? Date.parse(raw.maxIdleExpirationTime) : null;
    const maxExp = raw.maxSessionExpirationTime ? Date.parse(raw.maxSessionExpirationTime) : null;
    sessionInfo = {
      idleLeftSec: idleExp ? Math.max(0, Math.round((idleExp - now) / 1000)) : null,
      maxLeftSec: maxExp ? Math.max(0, Math.round((maxExp - now) / 1000)) : null,
    };
  }
  const recC = rpGet("c", req);
  const recD = rpGet("d", req);
  res.set("Cache-Control", "no-store");
  res.json({
    amCookiePresent: !!token,
    amValid: validate.valid,
    amSessionType: detectAmSessionType(token),
    sessionInfo,
    rpC: { authenticated: !!(recC && recC.tokens && !recC.loggedOut), loggedOut: recC ? recC.loggedOut : false },
    rpD: { authenticated: !!(recD && recD.tokens && !recD.loggedOut), loggedOut: recD ? recD.loggedOut : false },
  });
});

app.post("/probe/reset", async (req, res) => {
  const token = getAmToken(req);
  const logout = await amLogout(token);
  for (const key of ["c", "d"]) {
    const id = parseCookies(req)[RPS[key].cookie];
    if (id) rpStore[key].delete(id);
    res.append("Set-Cookie", `${RPS[key].cookie}=; Path=/; Max-Age=0; HttpOnly`);
  }
  res.append("Set-Cookie", clearAmCookieHeader());
  res.set("Cache-Control", "no-store");
  pushEvent({ type: "reset", detail: "browser reset: AM logout + local RP sessions cleared" });
  res.json({ reset: true, amLogout: logout.ok });
});

// ---------------------------------------------------------------------------
// Routes: SSE event feed
// ---------------------------------------------------------------------------
app.get("/events", (req, res) => {
  res.set({
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-store",
    Connection: "keep-alive",
  });
  res.write("retry: 5000\n\n");
  for (const evt of eventLog.slice(-30)) {
    res.write(`data: ${JSON.stringify(evt)}\n\n`);
  }
  sseClients.add(res);
  const keepAlive = setInterval(() => {
    try {
      res.write(": ping\n\n");
    } catch {
      clearInterval(keepAlive);
    }
  }, 25_000);
  req.on("close", () => {
    clearInterval(keepAlive);
    sseClients.delete(res);
  });
});

app.listen(port, () => {
  console.log(`app6 session-timeout test console listening on ${port}`);
  console.log(`  AM:     ${amBaseUrl}`);
  console.log(`  realm:  ${realmName} (${realmPath})`);
  console.log(`  issuer: ${issuerUrl}`);
});
