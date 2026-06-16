const crypto = require("crypto");
const express = require("express");
const session = require("express-session");

// ---------------------------------------------------------------------------
// app8 - OAuth2/OIDC custom-script tester.
//
// A confidential OIDC relying party that drives an authorization-code (+ PKCE)
// flow against the isolated AM `/scriptlab` realm. That realm's OAuth2 provider
// wires six PingAM sample scripts; each tags its custom output with a leading
// star emoji. This console decodes the id_token + access token, then calls
// userinfo, tokeninfo and introspect, and HIGHLIGHTS every star-tagged element
// so the script customizations are instantly visible as the proof point.
// ---------------------------------------------------------------------------

const STAR = "\u2B50"; // the star emoji used as the custom-element marker

const app = express();
const port = Number.parseInt(process.env.PORT || "3000", 10);

const appBaseUrl = (process.env.APP8_BASE_URL || "https://app8.jrsz.org").replace(/\/+$/, "");
const amBaseUrl = (process.env.AM_BASE_URL || "https://am.jrsz.org:8443/am").replace(/\/+$/, "");
const amRealmPath = process.env.AM_REALM_PATH || "realms/root/realms/scriptlab";
const issuerUrl = (process.env.OIDC_ISSUER_URL || `${amBaseUrl}/oauth2/${amRealmPath}`).replace(/\/+$/, "");
const oidcMetadataUrl = `${issuerUrl}/.well-known/openid-configuration`;
// Legacy tokeninfo endpoint - exercises the OAUTH2_EVALUATE_SCOPE script.
const tokenInfoUrl = `${amBaseUrl}/oauth2/${amRealmPath}/tokeninfo`;

const clientId = process.env.CLIENT_ID || "scriptlab-rp";
const clientSecret = process.env.CLIENT_SECRET || "scriptlab-secret-changeit";
const scope = process.env.SCOPE || "openid profile email phone";
const badScope = process.env.BAD_SCOPE || "openid profile bogus.scope.notallowed";
const sessionSecret = process.env.SESSION_SECRET || "scriptlab-changeit-changeit";
const demoUser = process.env.DEMO_USER_NAME || "demo-user";

const redirectUri = `${appBaseUrl}/callback`;
const metadataCacheTtlMs = Number.parseInt(process.env.OIDC_METADATA_CACHE_TTL_MS || "300000", 10);
let metadataCache = { value: null, expiresAt: 0 };

app.use(
  session({
    name: "app8.sid",
    secret: sessionSecret,
    resave: false,
    saveUninitialized: false,
    cookie: { httpOnly: true, sameSite: "lax", secure: false },
  }),
);

// ---------------------------------------------------------------------------
// Helpers
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
  const parts = token.split(".");
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
function htmlEscape(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Render a JSON object as escaped, pretty-printed HTML where any line carrying
// the star marker is wrapped in a highlight span (the proof point).
function highlightJson(obj) {
  let text;
  try {
    text = JSON.stringify(obj, null, 2);
  } catch {
    text = String(obj);
  }
  if (text == null) return "null";
  return text
    .split("\n")
    .map((line) => {
      const escaped = htmlEscape(line);
      return line.includes(STAR) ? `<span class="star-line">${escaped}</span>` : escaped;
    })
    .join("\n");
}

// Count star-tagged keys in an object (one level + nested) for the summary chip.
function countStarKeys(obj) {
  let n = 0;
  function walk(o) {
    if (!o || typeof o !== "object") return;
    for (const k of Object.keys(o)) {
      if (k.includes(STAR)) n += 1;
      walk(o[k]);
    }
  }
  walk(obj);
  return n;
}

function basicAuthHeader() {
  const basic = Buffer.from(`${encodeURIComponent(clientId)}:${encodeURIComponent(clientSecret)}`).toString("base64");
  return `Basic ${basic}`;
}

async function getOidcMetadata() {
  const now = Date.now();
  if (metadataCache.value && metadataCache.expiresAt > now) return metadataCache.value;
  const response = await fetch(oidcMetadataUrl, { headers: { accept: "application/json" } });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error("OIDC discovery failed");
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  metadataCache = { value: payload, expiresAt: now + metadataCacheTtlMs };
  return payload;
}

async function fetchUserinfo(meta, accessToken) {
  if (!meta.userinfo_endpoint || !accessToken) return { ok: false, error: "no userinfo_endpoint or token" };
  try {
    const r = await fetch(meta.userinfo_endpoint, { headers: { authorization: `Bearer ${accessToken}`, accept: "application/json" } });
    const body = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, body };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function fetchTokeninfo(accessToken) {
  if (!accessToken) return { ok: false, error: "no token" };
  try {
    const r = await fetch(`${tokenInfoUrl}?access_token=${encodeURIComponent(accessToken)}`, { headers: { accept: "application/json" } });
    const body = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, body };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function fetchIntrospect(meta, accessToken) {
  if (!meta.introspection_endpoint || !accessToken) return { ok: false, error: "no introspection_endpoint or token" };
  try {
    const r = await fetch(meta.introspection_endpoint, {
      method: "POST",
      headers: {
        authorization: basicAuthHeader(),
        "content-type": "application/x-www-form-urlencoded",
        accept: "application/json",
      },
      body: new URLSearchParams({ token: accessToken }),
    });
    const body = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, body };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

// ---------------------------------------------------------------------------
// Page rendering
// ---------------------------------------------------------------------------
const PAGE_CSS = `
  :root {
    --bg:#f3f4ee; --panel:#fffdf5; --ink:#182024; --accent:#005f73; --accent-soft:#d7eef2;
    --border:#cfd7d1; --muted:#5a6168; --star:#b45309; --star-bg:#fff3cd; --ok:#0a7d54; --bad:#a23b2d;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"IBM Plex Sans","Segoe UI",sans-serif;
    background:radial-gradient(circle at top left,#e9f8f4 0,transparent 35%),linear-gradient(180deg,#f9faf5 0%,var(--bg) 100%); color:var(--ink); }
  main { max-width:1080px; margin:0 auto; padding:40px 24px 72px; }
  .eyebrow { display:inline-block; padding:6px 12px; border-radius:999px; background:var(--accent-soft); color:var(--accent);
    font-size:13px; font-weight:700; letter-spacing:.04em; text-transform:uppercase; }
  h1 { margin:16px 0 6px; font-size:clamp(1.8rem,4vw,2.8rem); line-height:1.05; }
  h2 { margin:30px 0 6px; font-size:1.3rem; }
  h3 { margin:0 0 6px; font-size:1.05rem; }
  p.lead { max-width:80ch; line-height:1.6; color:#2c3338; }
  p.section { max-width:80ch; line-height:1.55; color:var(--muted); margin-top:0; }
  .strip { display:flex; flex-wrap:wrap; gap:8px; margin:14px 0; font-size:.82rem; }
  .chip { padding:6px 10px; border-radius:8px; background:#fff; border:1px solid var(--border); font-family:"IBM Plex Mono",monospace; }
  .chip b { color:var(--accent); }
  .chip.ok b { color:var(--ok); } .chip.bad b { color:var(--bad); } .chip.star b { color:var(--star); }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; margin-top:12px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:16px; padding:18px;
    box-shadow:0 8px 24px rgba(24,32,36,.05); display:flex; flex-direction:column; gap:10px; }
  .card p { margin:0; font-size:.9rem; color:#3b4248; line-height:1.5; }
  .card .meta { font-size:.76rem; color:var(--muted); font-family:"IBM Plex Mono",monospace; }
  .button { display:inline-block; align-self:flex-start; margin-top:auto; padding:10px 16px; border-radius:10px;
    text-decoration:none; font-weight:700; font-size:.9rem; background:var(--accent); color:#fff; border:1px solid var(--accent); }
  .button.outline { background:#fff; color:var(--accent); }
  .button.warn { background:#fff7ed; color:var(--star); border-color:var(--star); }
  pre { white-space:pre-wrap; word-break:break-word; background:#0f1720; color:#e8f1ef; padding:14px; border-radius:12px;
    overflow:auto; font-size:12.5px; max-height:420px; border:1px solid #1d2a39; }
  pre .star-line { display:inline-block; width:100%; background:var(--star-bg); color:#7c2d12; font-weight:700; border-radius:4px; }
  code { font-family:"IBM Plex Mono","SFMono-Regular",monospace; }
  .legend { display:inline-block; padding:2px 8px; border-radius:6px; background:var(--star-bg); color:#7c2d12; font-weight:700; font-size:.8rem; }
  .err { border-color:var(--bad); }
  .err h3 { color:var(--bad); }
`;

function shell(bodyHtml) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OAuth2/OIDC Custom-Script Tester (app8)</title>
<style>${PAGE_CSS}</style>
</head>
<body>
<main>
  <span class="eyebrow">app8 &middot; scriptlab realm</span>
  <h1>OAuth2/OIDC Custom-Script Tester</h1>
  <p class="lead">A confidential OIDC client against the isolated AM <code>/scriptlab</code> realm. Its OAuth2 provider wires six PingAM sample scripts; every custom token element they add is named with a <span class="legend">${STAR} star</span> so it is instantly visible below.</p>
  ${bodyHtml}
</main>
</body>
</html>`;
}

const SCRIPT_TABLE = `
  <h2>The six wired scripts</h2>
  <p class="section">Each script tags its output with <span class="legend">${STAR}</span>. Proof surfaces are noted per row.</p>
  <div class="grid">
    <div class="card"><h3>OIDC Claims</h3><p>Injects <code>${STAR}dept</code>, <code>${STAR}script</code>, <code>${STAR}source</code>, <code>${STAR}realm</code> into the <b>id_token</b> and <b>userinfo</b>.</p></div>
    <div class="card"><h3>Access Token Modification</h3><p>Adds <code>${STAR}mail</code>, <code>${STAR}dept</code>, <code>${STAR}script</code> (and <code>${STAR}loginHost</code>) to the <b>access token</b> (JWT) &amp; introspection.</p></div>
    <div class="card"><h3>Evaluate Scope</h3><p>Populates the legacy <b>tokeninfo</b> response and adds <code>${STAR}evaluatedBy</code>.</p></div>
    <div class="card"><h3>Validate Scope</h3><p>Enforces allowed scopes &mdash; proof is the <b>rejection</b> of a deliberately bad scope.</p></div>
    <div class="card"><h3>Authorize Endpoint Data Provider</h3><p>Returns <code>${STAR}authData</code> &amp; friends at the <b>/authorize</b> endpoint (surfaced on the callback when AM propagates it; always in AM debug).</p></div>
    <div class="card"><h3>May Act</h3><p>Adds a <b>may_act</b> claim with <code>${STAR}delegate</code> / <code>${STAR}script</code> to the access &amp; id tokens.</p></div>
  </div>`;

function renderHome(session) {
  const result = session.lastResult || null;
  const cards = `
  <h2>Use cases</h2>
  <div class="grid">
    <div class="card">
      <h3>Standard login</h3>
      <p>Authorization code + PKCE as <code>${htmlEscape(demoUser)}</code>. Proves OIDC claims, access-token fields and the <code>may_act</code> claim.</p>
      <p class="meta">scope: ${htmlEscape(scope)}</p>
      <a class="button" href="/login">Run standard login</a>
    </div>
    <div class="card">
      <h3>Introspect / tokeninfo</h3>
      <p>After a standard login, the callback also calls <code>userinfo</code>, <code>tokeninfo</code> (evaluate-scope) and <code>introspect</code> &mdash; all rendered with ${STAR} highlighted.</p>
      <p class="meta">run a standard login first</p>
      <a class="button outline" href="/login">Login + inspect</a>
    </div>
    <div class="card">
      <h3>Disallowed scope (validate-scope)</h3>
      <p>Requests a bogus scope so the <b>Validate Scope</b> script rejects it. Proof is an <code>invalid_scope</code> error on the callback.</p>
      <p class="meta">scope: ${htmlEscape(badScope)}</p>
      <a class="button warn" href="/login?demo=badscope">Request bad scope</a>
    </div>
  </div>`;

  const resultHtml = result ? renderResult(result) : `
  <h2>Results</h2>
  <p class="section">No flow run yet. Start with <b>Standard login</b> above.</p>`;

  return shell(`
    <div class="strip">
      <span class="chip">issuer: <b>${htmlEscape(issuerUrl)}</b></span>
      <span class="chip">client: <b>${htmlEscape(clientId)}</b></span>
      <span class="chip">redirect: <b>${htmlEscape(redirectUri)}</b></span>
    </div>
    ${cards}
    ${resultHtml}
    ${SCRIPT_TABLE}
    <div class="grid" style="margin-top:16px;">
      <div class="card"><h3>Reset</h3><p>Clear this app's local session.</p><a class="button outline" href="/logout">Clear local session</a></div>
    </div>
  `);
}

function section(title, payload, opts = {}) {
  const cls = opts.error ? "card err" : "card";
  const stars = opts.error ? 0 : countStarKeys(payload);
  const chip = stars > 0 ? `<span class="chip star">${STAR} custom keys: <b>${stars}</b></span>` : "";
  return `<section class="${cls}">
    <h3>${htmlEscape(title)} ${chip}</h3>
    <pre>${highlightJson(payload)}</pre>
  </section>`;
}

function renderResult(result) {
  if (result.error) {
    return `<h2>Results</h2>
    <div class="grid">
      <section class="card err">
        <h3>Authorization error (validate-scope proof if scope was bogus)</h3>
        <pre>${highlightJson(result.error)}</pre>
      </section>
    </div>`;
  }
  const blocks = [];
  blocks.push(section("Token response", result.tokens));
  blocks.push(section("ID token claims", result.idTokenClaims));
  blocks.push(section("Access token claims (JWT)", result.accessTokenClaims));
  blocks.push(section("userinfo", result.userinfo && result.userinfo.body ? result.userinfo.body : result.userinfo, { error: !(result.userinfo && result.userinfo.ok) }));
  blocks.push(section("tokeninfo (evaluate-scope)", result.tokeninfo && result.tokeninfo.body ? result.tokeninfo.body : result.tokeninfo, { error: !(result.tokeninfo && result.tokeninfo.ok) }));
  blocks.push(section("introspect", result.introspect && result.introspect.body ? result.introspect.body : result.introspect, { error: !(result.introspect && result.introspect.ok) }));
  if (result.authorizeData && Object.keys(result.authorizeData).length > 0) {
    blocks.push(section("Authorize-endpoint data on callback", result.authorizeData));
  }
  return `<h2>Results <span class="legend">${STAR} highlighted</span></h2>
  <div class="grid">${blocks.join("\n")}</div>`;
}

function renderError(title, payload, status = 500) {
  return { status, html: shell(`<h2>${htmlEscape(title)}</h2><section class="card err"><pre>${highlightJson(payload)}</pre></section><p><a class="button outline" href="/">Back</a></p>`) };
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------
app.get("/", (req, res) => {
  res.set("Cache-Control", "no-store");
  res.send(renderHome(req.session));
});

app.get("/healthz", (req, res) => res.json({ ok: true }));

app.get("/login", async (req, res) => {
  let meta;
  try {
    meta = await getOidcMetadata();
  } catch (error) {
    const r = renderError("OIDC discovery failed", { metadataUrl: oidcMetadataUrl, status: error.status, payload: error.payload || String(error) }, error.status || 500);
    return res.status(r.status).send(r.html);
  }
  const useBad = req.query.demo === "badscope";
  const effectiveScope = useBad ? badScope : scope;
  const state = randomString();
  const nonce = randomString();
  const codeVerifier = randomString(48);
  const codeChallenge = base64Url(sha256(codeVerifier));
  req.session.loginState = { state, nonce, codeVerifier, scope: effectiveScope, demo: useBad ? "badscope" : "standard" };

  const authorizationUrl = new URL(meta.authorization_endpoint);
  authorizationUrl.searchParams.set("client_id", clientId);
  authorizationUrl.searchParams.set("redirect_uri", redirectUri);
  authorizationUrl.searchParams.set("response_type", "code");
  authorizationUrl.searchParams.set("scope", effectiveScope);
  authorizationUrl.searchParams.set("state", state);
  authorizationUrl.searchParams.set("nonce", nonce);
  authorizationUrl.searchParams.set("code_challenge", codeChallenge);
  authorizationUrl.searchParams.set("code_challenge_method", "S256");
  res.redirect(authorizationUrl.toString());
});

app.get("/callback", async (req, res) => {
  const { code, state, error, error_description: errorDescription } = req.query;
  const loginState = req.session.loginState || null;

  // Capture any extra (non-standard) callback params - e.g. star-tagged data
  // contributed by the Authorize Endpoint Data Provider script.
  const standardParams = new Set(["code", "state", "error", "error_description", "iss", "scope", "session_state"]);
  const authorizeData = {};
  for (const [k, v] of Object.entries(req.query)) {
    if (!standardParams.has(k)) authorizeData[k] = v;
  }

  if (error) {
    req.session.lastResult = { error: { error, error_description: errorDescription || null, demo: loginState && loginState.demo, requestedScope: loginState && loginState.scope } };
    return res.redirect("/");
  }
  if (!code || !state || !loginState || state !== loginState.state) {
    const r = renderError("State validation failed", { message: "Missing/again mismatched code or state.", haveCode: !!code, haveState: !!state }, 400);
    return res.status(r.status).send(r.html);
  }

  let meta;
  try {
    meta = await getOidcMetadata();
  } catch (e) {
    const r = renderError("OIDC discovery failed", { payload: e.payload || String(e) }, e.status || 500);
    return res.status(r.status).send(r.html);
  }

  const tokenParams = new URLSearchParams({
    grant_type: "authorization_code",
    redirect_uri: redirectUri,
    code: String(code),
    code_verifier: loginState.codeVerifier,
  });
  const tokenResponse = await fetch(meta.token_endpoint, {
    method: "POST",
    headers: { authorization: basicAuthHeader(), "content-type": "application/x-www-form-urlencoded" },
    body: tokenParams,
  });
  const tokenPayload = await tokenResponse.json().catch(() => null);
  if (!tokenResponse.ok) {
    req.session.lastResult = { error: { stage: "token", status: tokenResponse.status, payload: tokenPayload } };
    return res.redirect("/");
  }

  const accessToken = tokenPayload && tokenPayload.access_token;
  const [userinfo, tokeninfo, introspect] = await Promise.all([
    fetchUserinfo(meta, accessToken),
    fetchTokeninfo(accessToken),
    fetchIntrospect(meta, accessToken),
  ]);

  req.session.lastResult = {
    tokens: tokenPayload,
    idTokenClaims: decodeJwtClaims(tokenPayload && tokenPayload.id_token),
    accessTokenClaims: decodeJwtClaims(accessToken),
    userinfo,
    tokeninfo,
    introspect,
    authorizeData,
  };
  res.redirect("/");
});

app.get("/logout", (req, res) => {
  req.session.destroy(() => res.redirect("/"));
});

app.listen(port, () => {
  console.log(`app8 OAuth2/OIDC script tester listening on ${port}`);
  console.log(`  issuer:   ${issuerUrl}`);
  console.log(`  client:   ${clientId}`);
  console.log(`  redirect: ${redirectUri}`);
});
