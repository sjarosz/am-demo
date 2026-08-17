import org.forgerock.http.protocol.Response
import org.forgerock.http.protocol.Status

def html = '''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <base target="_blank" rel="noopener">
  <title>AM Standalone Lab Launchpad</title>
  <style>
    :root {
      --bg: #ecebf7;
      --panel: #ffffff;
      --ink: #1c1733;
      --accent: #6d28d9;
      --accent-soft: #e7ddfb;
      --border: #d8d3ec;
      --muted: #5d5680;
      --grad-spot: #efe6fb;
      --grad-top: #f6f3fd;
      --lead: #2c2640;
      --card-ink: #3a3357;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, var(--grad-spot) 0, transparent 35%),
        linear-gradient(180deg, var(--grad-top) 0%, var(--bg) 100%);
      color: var(--ink);
    }
    main { max-width: 1080px; margin: 0 auto; padding: 48px 24px 64px; }
    .eyebrow {
      display: inline-block; padding: 6px 12px; border-radius: 999px;
      background: var(--accent-soft); color: var(--accent);
      font-size: 13px; font-weight: 700; letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    h1 { margin: 18px 0 8px; font-size: clamp(2rem, 4vw, 3.25rem); line-height: 1.05; }
    h2 { margin: 36px 0 6px; font-size: 1.35rem; }
    p.lead { max-width: 72ch; line-height: 1.6; color: var(--lead); }
    p.section { max-width: 72ch; line-height: 1.55; color: var(--muted); margin-top: 0; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
      margin-top: 12px;
    }
    .card {
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 16px; padding: 20px;
      box-shadow: 0 8px 24px rgba(24, 32, 36, 0.05);
      display: flex; flex-direction: column; gap: 10px;
    }
    .card h3 { margin: 0; font-size: 1.05rem; }
    .card p { margin: 0; font-size: 0.92rem; color: var(--card-ink); line-height: 1.5; }
    .card .meta {
      font-size: 0.78rem; color: var(--muted);
      font-family: "IBM Plex Mono", "SFMono-Regular", monospace;
      word-break: break-all;
    }
    .button {
      display: inline-block; align-self: flex-start; margin-top: 4px;
      padding: 10px 16px; border-radius: 10px;
      text-decoration: none; font-weight: 700; font-size: 0.92rem;
      background: var(--accent); color: white; border: 1px solid var(--accent);
    }
    .button.outline { background: white; color: var(--accent); }
    code { font-family: "IBM Plex Mono", "SFMono-Regular", monospace; }
    .session-strip {
      display: none; align-items: center; justify-content: flex-end; gap: 12px;
      margin: 0 0 4px; font-size: 0.9rem; color: var(--muted);
    }
    .session-strip.show { display: flex; }
    .session-strip .who strong { color: var(--ink); }
    .session-strip .button {
      margin-top: 0; padding: 7px 14px; font-size: 0.82rem;
    }
    /* Thicker bold line with rounded ends */
hr {
  border: none;
  height: 6px;
  background-color: black;
  border-radius: 3px; /* Smooths out sharp edges */
}
  </style>
</head>
<body>
  <main>
    <div id="session-strip" class="session-strip">
      <span class="who">Signed in as <strong id="session-user">&hellip;</strong></span>
      <button id="logout-btn" class="button outline" type="button">Single Logout</button>
    </div>
    <span class="eyebrow">jrsz.net lab</span>
    <h1>Launchpad</h1>
    <p class="lead">Every demo flow available in this lab. The gateway-protected apps trigger AM session SSO; the OIDC buttons exercise the federation paths configured in the <code>/alpha</code> realm.</p>

    <h2>Gateway-protected session SSO</h2>
    <p class="section">PingGateway intercepts the request and redirects to AM when no session is present. After login, the request is proxied to the backend app.</p>
    <div class="grid">
      <div class="card">
        <h3>app1</h3>
        <p class="meta">https://app1.jrsz.net:8444/</p>
        <p>Session-protected static demo backend.</p>
        <a class="button" href="https://app1.jrsz.net:8444/">Launch app1</a>
      </div>
      <div class="card">
        <h3>app2</h3>
        <p class="meta">https://app2.jrsz.net:8444/</p>
        <p>Session-protected static demo backend.</p>
        <a class="button" href="https://app2.jrsz.net:8444/">Launch app2</a>
      </div>
      <div class="card">
        <h3>app3</h3>
        <p class="meta">https://app3.jrsz.net:8444/</p>
        <p>Session-protected static demo backend.</p>
        <a class="button" href="https://app3.jrsz.net:8444/">Launch app3</a>
      </div>
    </div>

    <h2>OIDC Authorization Code + PKCE</h2>
    <p class="section">app4 drives the OIDC handshake itself against the <code>/alpha</code> OAuth2 provider. Public client, S256 PKCE.</p>
    <div class="grid">
      <div class="card">
        <h3>app4</h3>
        <p class="meta">https://app4.jrsz.net:8444/ &middot; client demo-pkce-app</p>
        <p>OIDC discovery, PKCE login, token + claim inspection on the callback page.</p>
        <a class="button" href="https://app4.jrsz.net:8444/">Open app4</a>
      </div>
    </div>

    <h2>PingAM Login Widget</h2>
    <p class="section">app5 embeds <code>@forgerock/login-widget</code> against the <code>/alpha</code> realm using client <code>sdkPublicClient</code> and journey <code>sdkUsernamePasswordJourney</code>.</p>
    <div class="grid">
      <div class="card">
        <h3>app5</h3>
        <p class="meta">https://app5.jrsz.net:8444/ &middot; client sdkPublicClient</p>
        <p>Modal Login Widget flow with OAuth redirect to <code>/callback.html</code> and userinfo display after sign-in.</p>
        <a class="button" href="https://app5.jrsz.net:8444/">Open app5</a>
      </div>
    </div>

    <h2>OAuth2/OIDC custom-script tester</h2>
    <p class="section">app8 is a confidential OIDC client against the isolated <code>/scriptlab</code> realm whose OAuth2 provider wires six PingAM sample scripts. Every custom token element is named with a star emoji so the customization is instantly visible in the decoded tokens.</p>
    <div class="grid">
      <div class="card">
        <h3>app8</h3>
        <p class="meta">https://app8.jrsz.net:8444/ &middot; realm /scriptlab &middot; client scriptlab-rp</p>
        <p>Runs OIDC claims, access-token modification, evaluate-scope, validate-scope, authorize-endpoint data and may_act &mdash; with &#11088;-tagged elements highlighted across id_token, userinfo, tokeninfo and introspect.</p>
        <a class="button" href="https://app8.jrsz.net:8444/">Open app8</a>
      </div>
    </div>

    <h2>OIDC external IDP login (cross-AM)</h2>
    <p class="section">Each AM stack is the other's social (OpenID Connect) identity provider. The <code>SocialLogin</code> journey in <code>/alpha</code> offers a single "Log in with the partner" button: it redirects to the partner AM to authenticate (sign in there as <code>demo-user</code>), then finds or auto-provisions the federated user in this realm and returns an authenticated session. First login auto-creates the account; subsequent logins reuse it via the social alias.</p>
    <div class="grid">
      <div class="card">
        <h3>OIDC external IDP login</h3>
        <p class="meta">tree SocialLogin &middot; realm /alpha</p>
        <p>Select Identity Provider (social only) &rarr; Social Provider Handler (OIDC back-channel to the partner AM over the shared network) &rarr; existing account or Provision Dynamic Account. Proves cross-AM OIDC federation in both directions.</p>
        <a class="button" href="https://am.jrsz.net:9443/am/XUI/?realm=/alpha&amp;authIndexType=service&amp;authIndexValue=SocialLogin#login/">Open OIDC external IDP login</a>
      </div>
    </div>
<p>&nbsp;</p>
    <hr>

    <h2>External SAML IDP login (integrated mode, cross-AM)</h2>
    <p class="section">SAML 2.0 federation driven from inside an authentication tree. The <code>SamlLogin</code> journey in <code>/alpha</code> uses a SAML2 Authentication node on the hosted SP <code>org-integrated-sp</code> (AuthConsumer endpoints) to SP-init an AuthnRequest to the partner AM's IdP. After the partner authenticates the user, the assertion returns to this SP and the journey finds or auto-provisions the federated user. Auto-federation on <code>uid</code> resolves the existing <code>demo-user</code>; genuinely new users go through Provision Dynamic Account. This is separate from, and leaves untouched, the standalone app7 federation below.</p>
    <div class="grid">
      <div class="card">
        <h3>External SAML IDP login</h3>
        <p class="meta">tree SamlLogin &middot; realm /alpha</p>
        <p>SAML2 Authentication node (SP-init, SP <code>org-integrated-sp</code> &rarr; partner IdP <code>jrsz-com</code>) &rarr; Account Exists or Provision Dynamic Account. Proves SAML integrated-mode federation inside a journey.</p>
        <a class="button" href="https://am.jrsz.net:9443/am/XUI/?realm=/alpha&amp;authIndexType=service&amp;authIndexValue=SamlLogin#login/">Open external SAML IDP login</a>
      </div>
    </div>
<p>&nbsp;</p>
    <hr>

    <h2>SAML v2.0 cross-domain federation console</h2>
    <p class="section">app7 federates the jrsz.net and jrsz.net AM stacks. Each <code>/alpha</code> realm hosts one dual-role (IDP + SP) entity in the <code>jrsz-federation</code> circle of trust, so AM is both the IDP and the SP. The console launches all four IDP/SP-init permutations and shows the resulting federated AM session (auto-federation on <code>uid</code> resolves to <code>demo-user</code>).</p>
    <div class="grid">
      <div class="card">
        <h3>app7 federation console</h3>
        <p class="meta">https://app7.jrsz.net:8444/ &middot; realm /alpha</p>
        <p>Launch org&harr;com IDP-init and SP-init flows. The partner console at <code>https://app7.jrsz.net:8444/</code> shows sessions that land on the com side.</p>
        <a class="button" href="https://app7.jrsz.net:8444/">Open federation console</a>
      </div>
    </div>

    <h2>SAML v2.0 custom-script tester</h2>
    <p class="section">app9 demonstrates three PingAM sample SAML2 scripts in a genuine cross-AM federation isolated in the <code>/samllab</code> realm (org hosts the IdP, com hosts the SP, joined by the <code>samllab-cot</code> circle of trust). The <b>IDP Attribute Mapper</b> and <b>NameID Mapper</b> build a star-tagged assertion at the org IdP, and the <b>SP Adapter</b> stashes those elements into the com SP session. Every custom element is named with a star emoji for instant identification.</p>
    <div class="grid">
      <div class="card">
        <h3>app9 SAML script console</h3>
        <p class="meta">https://app9.jrsz.net:8444/ &middot; realm /samllab</p>
        <p>Launch the org IdP &rarr; com SP flow (IdP-init or SP-init). The session lands on the com stack &mdash; the partner console at <code>https://app9.jrsz.net:8444/</code> reads it back and highlights the &#11088;-tagged assertion attributes and NameID.</p>
        <a class="button" href="https://app9.jrsz.net:8444/">Open SAML script console</a>
      </div>
    </div>

    <h2>AM XUI authentication trees</h2>
    <p class="section">These journeys live in the <code>/</code> (root) realm, not <code>/alpha</code>. Each link opens the AM End User UI with <code>authIndexType=service</code> and the tree name as <code>authIndexValue</code>. Use the lab demo user (<code>demo-user</code>) where username/password is collected first.</p>
    <div class="grid">
      <div class="card">
        <h3>MFA</h3>
        <p class="meta">tree MFA &middot; realm /</p>
        <p>Page login (username + password), data-store decision, then OATH registration and OATH token verification for MFA enrollment and step-up.</p>
        <a class="button" href="https://am.jrsz.net:9443/am/XUI/?realm=/alpha&amp;authIndexType=service&amp;authIndexValue=MFA#login/">Open MFA tree</a>
      </div>
      <div class="card">
        <h3>TOTP</h3>
        <p class="meta">tree TOTP &middot; realm /</p>
        <p>Username collector followed by OATH token verifier only &mdash; for users who already have a registered authenticator app.</p>
        <a class="button" href="https://am.jrsz.net:9443/am/XUI/?realm=/alpha&amp;authIndexType=service&amp;authIndexValue=TOTP#login/">Open TOTP tree</a>
      </div>
      <div class="card">
        <h3>Passkeys</h3>
        <p class="meta">tree Passkeys &middot; realm /</p>
        <p>Page node with WebAuthn registration and WebAuthn authentication nodes for passkey enrollment and sign-in.</p>
        <a class="button outline" href="https://am.jrsz.net:9443/am/XUI/?realm=/alpha&amp;authIndexType=service&amp;authIndexValue=Passkeys#login/">Open Passkeys tree</a>
      </div>
      <div class="card">
        <h3>Passwordless</h3>
        <p class="meta">tree Passwordless &middot; realm /</p>
        <p>Username collector then WebAuthn authentication &mdash; no password step in the tree.</p>
        <a class="button outline" href="https://am.jrsz.net:9443/am/XUI/?realm=/alpha&amp;authIndexType=service&amp;authIndexValue=Passwordless#login/">Open Passwordless tree</a>
      </div>
    </div>

    <h2>Session timeout, logout &amp; OIDC SLO test console</h2>
    <p class="section">app6 is a full test lab for session timeout, inactivity, explicit logout and OIDC single logout. It runs against the dedicated <code>/timeout-test</code> realm with short session/token lifetimes, two OIDC RPs (RP C confidential, RP D public PKCE) wired for back-channel logout, an API E resource server, IG App A (no cache) / App B (cached + WebSocket), and live AM/token probes.</p>
    <div class="grid">
      <div class="card">
        <h3>app6 test console</h3>
        <p class="meta">https://app6.jrsz.net:8444/ &middot; realm /timeout-test</p>
        <p>Runnable S/C/G/O/T matrix with live pass/fail evidence: AM validate (refresh=false), REST/RP-initiated/IG logout, back-channel logout feed, introspection, refresh reuse and API E.</p>
        <a class="button" href="https://app6.jrsz.net:8444/">Open test console</a>
      </div>
    </div>

  </main>
  <script>
    (function () {
      var strip = document.getElementById('session-strip');
      var userEl = document.getElementById('session-user');
      var btn = document.getElementById('logout-btn');
      function refresh() {
        fetch('/__slo/status', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d && d.authenticated) {
              userEl.textContent = d.username || 'authenticated';
              strip.classList.add('show');
            } else {
              strip.classList.remove('show');
            }
          })
          .catch(function () { strip.classList.remove('show'); });
      }
      btn.addEventListener('click', function () {
        btn.disabled = true;
        btn.textContent = 'Signing out...';
        fetch('/__slo/logout', { method: 'POST', credentials: 'same-origin' })
          .then(function () { window.location.reload(); })
          .catch(function () { window.location.reload(); });
      });
      refresh();
    })();
  </script>
</body>
</html>'''

def response = new Response(Status.OK)
response.headers.put("Content-Type", "text/html; charset=UTF-8")
response.headers.put("Cache-Control", "no-store")
response.entity.setString(html)
return response
