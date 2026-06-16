const crypto = require("crypto");
const express = require("express");
const session = require("express-session");

const app = express();

const port = Number.parseInt(process.env.PORT || "3000", 10);
const demoBaseUrl = process.env.DEMO_BASE_URL || "https://app4.jrsz.org";
const amBaseUrl = process.env.AM_BASE_URL || "https://am.jrsz.org:8443/am";
const amCookieDomain = process.env.AM_COOKIE_DOMAIN || "jrsz.org";
const amRealmPath =
  process.env.AM_REALM_PATH || "realms/root/realms/alpha";
const issuerUrl = (
  process.env.OIDC_ISSUER_URL || `${amBaseUrl}/oauth2/${amRealmPath}`
).replace(/\/+$/, "");
const amSessionsUrl = `${amBaseUrl.replace(/\/+$/, "")}/json/${amRealmPath}/sessions`;
const clientId = process.env.CLIENT_ID || "demo-pkce-app";
const clientSecret = process.env.CLIENT_SECRET || "";
const sessionSecret =
  process.env.SESSION_SECRET || "changeit-changeit-changeit";
const scope = process.env.SCOPE || "openid profile email";
const oidcMetadataUrl = `${issuerUrl}/.well-known/openid-configuration`;
const tokenAuthMethod = clientSecret
  ? "client_secret_basic + PKCE"
  : "public (PKCE only)";
const metadataCacheTtlMs = Number.parseInt(
  process.env.OIDC_METADATA_CACHE_TTL_MS || "300000",
  10,
);
let metadataCache = {
  expiresAt: 0,
  value: null,
};

app.use(
  session({
    name: "app4.sid",
    secret: sessionSecret,
    resave: false,
    saveUninitialized: false,
    cookie: {
      httpOnly: true,
      sameSite: "lax",
      secure: false,
    },
  }),
);

function base64Url(input) {
  return input
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function randomString(byteLength = 32) {
  return base64Url(crypto.randomBytes(byteLength));
}

function sha256(input) {
  return crypto.createHash("sha256").update(input).digest();
}

function decodeJwtClaims(token) {
  if (!token) {
    return null;
  }

  const parts = token.split(".");
  if (parts.length < 2) {
    return null;
  }

  const padded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const remainder = padded.length % 4;
  const normalized =
    remainder === 0 ? padded : padded + "=".repeat(4 - remainder);

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

// Server-side check of the shared AM SSO session. The browser sends the
// iPlanetDirectoryPro cookie (domain jrsz.org) to this app host, so app4 can
// confirm whether the AM session is still alive without any CORS config.
async function amGetSessionInfo(token) {
  if (!token) {
    return { valid: false, username: null };
  }
  try {
    const response = await fetch(`${amSessionsUrl}?_action=getSessionInfo`, {
      method: "POST",
      headers: {
        iPlanetDirectoryPro: token,
        "Accept-API-Version": "resource=5.1, protocol=1.0",
        "Content-Type": "application/json",
      },
      body: "{}",
    });
    if (!response.ok) {
      return { valid: false, username: null };
    }
    const info = await response.json().catch(() => null);
    return { valid: true, username: info && info.username ? info.username : null };
  } catch {
    return { valid: false, username: null };
  }
}

// Global Single Logout: invoke AM's session logout service for the shared SSO
// token so app1-app3 are logged out everywhere too.
async function amLogout(token) {
  if (!token) {
    return;
  }
  try {
    await fetch(`${amSessionsUrl}?_action=logout`, {
      method: "POST",
      headers: {
        iPlanetDirectoryPro: token,
        "Accept-API-Version": "resource=5.1, protocol=1.0",
        "Content-Type": "application/json",
      },
      body: "{}",
    });
  } catch {
    // best effort; the cookie is cleared regardless
  }
}

const SLO_WIDGET_STYLES = `
    .slo-widget {
      position: fixed; top: 16px; right: 16px; z-index: 9999;
      display: flex; align-items: center; gap: 10px;
      padding: 10px 14px; border-radius: 999px;
      background: #fffdf5; border: 1px solid #cfd7d1;
      box-shadow: 0 8px 24px rgba(24, 32, 36, 0.12);
      font: 600 0.9rem/1.2 "IBM Plex Sans", "Segoe UI", sans-serif;
    }
    .slo-widget .slo-status { color: #5a6168; }
    .slo-widget.slo-in .slo-status { color: #0a7d54; }
    .slo-widget.slo-out .slo-status { color: #a23b2d; }
    .slo-btn {
      appearance: none; border: 1px solid #005f73; background: #005f73; color: #fff;
      padding: 7px 14px; border-radius: 999px; font: inherit; cursor: pointer;
    }
    .slo-widget.slo-out .slo-btn { background: #fff; color: #005f73; }
    .slo-btn:disabled { opacity: 0.6; cursor: default; }`;

const SLO_WIDGET_MARKUP = `
  <div id="slo-widget" class="slo-widget slo-loading" aria-live="polite">
    <span class="slo-status">Checking session&hellip;</span>
    <button type="button" class="slo-btn" hidden></button>
  </div>`;

const SLO_WIDGET_SCRIPT = `
  <script>
    (function () {
      var widget = document.getElementById('slo-widget');
      var statusEl = widget.querySelector('.slo-status');
      var btn = widget.querySelector('.slo-btn');

      function render(state) {
        widget.classList.remove('slo-loading', 'slo-in', 'slo-out');
        if (state && state.authenticated) {
          widget.classList.add('slo-in');
          statusEl.textContent = state.username ? ('Signed in as ' + state.username) : 'Signed in';
          btn.hidden = false; btn.textContent = 'Log out'; btn.dataset.action = 'logout';
        } else {
          widget.classList.add('slo-out');
          statusEl.textContent = 'Signed out';
          btn.hidden = false; btn.textContent = 'Log in'; btn.dataset.action = 'login';
        }
      }

      function poll() {
        fetch('/__slo/status', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.json(); })
          .then(render)
          .catch(function () {});
      }

      btn.addEventListener('click', function () {
        if (btn.dataset.action === 'login') {
          window.location.href = '/__slo/login';
          return;
        }
        btn.disabled = true;
        statusEl.textContent = 'Logging out\\u2026';
        fetch('/__slo/logout', { method: 'POST', credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.json(); })
          .then(function () { btn.disabled = false; render({ authenticated: false }); })
          .catch(function () { btn.disabled = false; poll(); });
      });

      poll();
      setInterval(poll, 5000);
      window.addEventListener('focus', poll);
    })();
  </script>`;

function renderPage({ title, body, subtitle = "" }) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${htmlEscape(title)}</title>
  <style>
    :root {
      --bg: #f3f4ee;
      --panel: #fffdf5;
      --ink: #182024;
      --accent: #005f73;
      --accent-soft: #d7eef2;
      --border: #cfd7d1;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, #e9f8f4 0, transparent 35%),
        linear-gradient(180deg, #f9faf5 0%, var(--bg) 100%);
      color: var(--ink);
    }
    main {
      max-width: 960px;
      margin: 0 auto;
      padding: 48px 24px 64px;
    }
    .eyebrow {
      display: inline-block;
      padding: 6px 12px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    h1 {
      margin: 18px 0 8px;
      font-size: clamp(2rem, 4vw, 3.5rem);
      line-height: 1.05;
    }
    p {
      line-height: 1.6;
      max-width: 70ch;
    }
    .actions {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin: 24px 0 32px;
    }
    .button {
      display: inline-block;
      padding: 12px 18px;
      border-radius: 12px;
      text-decoration: none;
      font-weight: 700;
      border: 1px solid var(--border);
      color: var(--ink);
      background: white;
    }
    .button.primary {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 18px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 10px 30px rgba(24, 32, 36, 0.06);
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #0f1720;
      color: #e8f1ef;
      padding: 16px;
      border-radius: 12px;
      overflow-x: auto;
      font-size: 13px;
    }
    code {
      font-family: "IBM Plex Mono", "SFMono-Regular", monospace;
    }
${SLO_WIDGET_STYLES}
  </style>
</head>
<body>
${SLO_WIDGET_MARKUP}
  <main>
    <span class="eyebrow">App 4 PKCE Demo</span>
    <h1>${htmlEscape(title)}</h1>
    ${subtitle ? `<p>${subtitle}</p>` : ""}
    ${body}
  </main>
${SLO_WIDGET_SCRIPT}
</body>
</html>`;
}

async function getOidcMetadata() {
  const now = Date.now();
  if (metadataCache.value && metadataCache.expiresAt > now) {
    return metadataCache.value;
  }

  const response = await fetch(oidcMetadataUrl, {
    headers: {
      accept: "application/json",
    },
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error("OIDC discovery failed");
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  metadataCache = {
    value: payload,
    expiresAt: now + metadataCacheTtlMs,
  };

  return payload;
}

app.get("/", async (req, res) => {
  const tokens = req.session.tokens || null;
  const idTokenClaims = decodeJwtClaims(tokens?.id_token);
  const accessTokenClaims = decodeJwtClaims(tokens?.access_token);
  const loginState = req.session.loginState || null;
  let oidcMetadata = null;
  let discoveryError = null;

  try {
    oidcMetadata = await getOidcMetadata();
  } catch (error) {
    discoveryError = {
      status: error.status || 500,
      payload: error.payload || { message: error.message },
    };
  }

  const discoverySection = discoveryError
    ? `<section class="card">
        <h2>OIDC Discovery Error</h2>
        <pre>${htmlEscape(JSON.stringify(discoveryError, null, 2))}</pre>
      </section>`
    : `<section class="card">
        <h2>OIDC Discovery</h2>
        <p><strong>Metadata URL:</strong> <code>${htmlEscape(oidcMetadataUrl)}</code></p>
        <p><strong>Authorization Endpoint:</strong> <code>${htmlEscape(
          oidcMetadata.authorization_endpoint,
        )}</code></p>
        <p><strong>Token Endpoint:</strong> <code>${htmlEscape(
          oidcMetadata.token_endpoint,
        )}</code></p>
      </section>`;

  const body = `
    <p>This additive demo exercises OpenID Connect Authorization Code with PKCE against the existing AM realm. The original app1-app3 flow remains unchanged.</p>
    <div class="actions">
      <a class="button primary" href="/login">Start PKCE Login</a>
      <a class="button" href="/logout">Clear Local Session</a>
    </div>
    <div class="grid">
      <section class="card">
        <h2>Configuration</h2>
        <p><strong>Issuer:</strong> <code>${htmlEscape(issuerUrl)}</code></p>
        <p><strong>Client ID:</strong> <code>${htmlEscape(clientId)}</code></p>
        <p><strong>Token auth:</strong> <code>${htmlEscape(tokenAuthMethod)}</code></p>
        <p><strong>Redirect URI:</strong> <code>${htmlEscape(
          `${demoBaseUrl}/callback`,
        )}</code></p>
      </section>
      ${discoverySection}
      <section class="card">
        <h2>PKCE Runtime State</h2>
        <pre>${htmlEscape(
          JSON.stringify(
            loginState
              ? {
                  state: loginState.state,
                  nonce: loginState.nonce,
                  scope,
                }
              : { state: null, nonce: null, scope },
            null,
            2,
          ),
        )}</pre>
      </section>
    </div>
    <section class="card">
      <h2>Token Response</h2>
      <pre>${htmlEscape(JSON.stringify(tokens, null, 2) || "null")}</pre>
    </section>
    <section class="card">
      <h2>ID Token Claims</h2>
      <pre>${htmlEscape(JSON.stringify(idTokenClaims, null, 2) || "null")}</pre>
    </section>
    <section class="card">
      <h2>Access Token Claims</h2>
      <pre>${htmlEscape(
        JSON.stringify(accessTokenClaims, null, 2) || "null",
      )}</pre>
    </section>
  `;

  res.send(
    renderPage({
      title: "Hello World via Authorization Code + PKCE",
      subtitle:
        "This app drives the OAuth/OIDC flow itself instead of relying on IG session filters.",
      body,
    }),
  );
});

app.get("/login", (req, res) => {
  getOidcMetadata()
    .then((oidcMetadata) => {
  const state = randomString();
  const nonce = randomString();
  const codeVerifier = randomString(48);
  const codeChallenge = base64Url(sha256(codeVerifier));

  req.session.loginState = {
    state,
    nonce,
    codeVerifier,
  };

  const authorizationUrl = new URL(oidcMetadata.authorization_endpoint);
  authorizationUrl.searchParams.set("client_id", clientId);
  authorizationUrl.searchParams.set("redirect_uri", `${demoBaseUrl}/callback`);
  authorizationUrl.searchParams.set("response_type", "code");
  authorizationUrl.searchParams.set("scope", scope);
  authorizationUrl.searchParams.set("state", state);
  authorizationUrl.searchParams.set("nonce", nonce);
  authorizationUrl.searchParams.set("code_challenge", codeChallenge);
  authorizationUrl.searchParams.set("code_challenge_method", "S256");

      res.redirect(authorizationUrl.toString());
    })
    .catch((error) => {
      res.status(error.status || 500).send(
        renderPage({
          title: "OIDC Discovery Failed",
          body: `<section class="card"><pre>${htmlEscape(
            JSON.stringify(
              {
                metadataUrl: oidcMetadataUrl,
                status: error.status || 500,
                payload: error.payload || { message: error.message },
              },
              null,
              2,
            ),
          )}</pre></section>`,
        }),
      );
    });
});

app.get("/callback", async (req, res) => {
  const { code, state, error, error_description: errorDescription } = req.query;
  const loginState = req.session.loginState;

  if (error) {
    res.status(400).send(
      renderPage({
        title: "Authorization Error",
        body: `<section class="card"><pre>${htmlEscape(
          JSON.stringify({ error, errorDescription }, null, 2),
        )}</pre></section>`,
      }),
    );
    return;
  }

  if (!code || !state || !loginState || state !== loginState.state) {
    res.status(400).send(
      renderPage({
        title: "State Validation Failed",
        body: `<section class="card"><p>The callback is missing the expected authorization code or state.</p></section>`,
      }),
    );
    return;
  }

  let oidcMetadata;
  try {
    oidcMetadata = await getOidcMetadata();
  } catch (error) {
    res.status(error.status || 500).send(
      renderPage({
        title: "OIDC Discovery Failed",
        body: `<section class="card"><pre>${htmlEscape(
          JSON.stringify(
            {
              metadataUrl: oidcMetadataUrl,
              status: error.status || 500,
              payload: error.payload || { message: error.message },
            },
            null,
            2,
          ),
        )}</pre></section>`,
      }),
    );
    return;
  }

  const tokenParams = new URLSearchParams({
    grant_type: "authorization_code",
    redirect_uri: `${demoBaseUrl}/callback`,
    code: String(code),
    code_verifier: loginState.codeVerifier,
  });

  const tokenHeaders = {
    "content-type": "application/x-www-form-urlencoded",
  };

  if (clientSecret) {
    const basic = Buffer.from(
      `${encodeURIComponent(clientId)}:${encodeURIComponent(clientSecret)}`,
    ).toString("base64");
    tokenHeaders.authorization = `Basic ${basic}`;
  } else {
    tokenParams.set("client_id", clientId);
  }

  const tokenResponse = await fetch(oidcMetadata.token_endpoint, {
    method: "POST",
    headers: tokenHeaders,
    body: tokenParams,
  });

  const tokenPayload = await tokenResponse.json();

  if (!tokenResponse.ok) {
    res.status(tokenResponse.status).send(
      renderPage({
        title: "Token Request Failed",
        body: `<section class="card"><pre>${htmlEscape(
          JSON.stringify(tokenPayload, null, 2),
        )}</pre></section>`,
      }),
    );
    return;
  }

  req.session.tokens = tokenPayload;
  res.redirect("/");
});

app.get("/logout", (req, res) => {
  req.session.destroy(() => {
    res.redirect("/");
  });
});

// Smart-button status: app4 is "signed in" only when it holds local OIDC tokens
// AND the shared AM SSO session is still valid. If the AM session was killed
// elsewhere (global SLO), app4 drops its stale local tokens to honor the logout.
app.get("/__slo/status", async (req, res) => {
  const localAuthed = !!(req.session && req.session.tokens);
  const am = await amGetSessionInfo(getAmToken(req));

  if (localAuthed && !am.valid && req.session) {
    req.session.tokens = null;
    req.session.loginState = null;
  }

  const authenticated = localAuthed && am.valid;
  let username = am.username;
  if (!username && authenticated) {
    const claims = decodeJwtClaims(req.session.tokens && req.session.tokens.id_token);
    username = (claims && (claims.preferred_username || claims.sub)) || null;
  }

  res.set("Cache-Control", "no-store");
  res.json({ authenticated, username: authenticated ? username : null });
});

// Global Single Logout from app4: kill the shared AM session, clear the shared
// cookie across jrsz.org, and destroy app4's local session.
app.post("/__slo/logout", async (req, res) => {
  await amLogout(getAmToken(req));

  const finish = () => {
    res.append(
      "Set-Cookie",
      `iPlanetDirectoryPro=; Domain=${amCookieDomain}; Path=/; Max-Age=0; HttpOnly`,
    );
    res.set("Cache-Control", "no-store");
    res.json({ authenticated: false, loggedOut: true });
  };

  if (req.session) {
    req.session.destroy(finish);
  } else {
    finish();
  }
});

app.get("/__slo/login", (req, res) => {
  res.redirect("/login");
});

app.listen(port, () => {
  console.log(`app4 PKCE demo listening on ${port}`);
});
