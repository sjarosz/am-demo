import '@forgerock/login-widget/widget.css';
import './widget-overrides.css';
import './app.css';
import Widget, { component, configuration, journey, user } from '@forgerock/login-widget';

const amBaseUrl = import.meta.env.VITE_AM_BASE_URL || 'https://am.jrsz.org:8443/am';
const clientId = import.meta.env.VITE_CLIENT_ID || 'sdkPublicClient';
const journeyName = import.meta.env.VITE_JOURNEY || 'sdkUsernamePasswordJourney';
const realmPath = 'alpha';
const redirectUri = `${window.location.origin}${window.location.pathname}`;

const root = document.getElementById('root');
root.innerHTML = `
  <div class="page-shell">
    <aside class="login-dock" aria-label="Account controls">
      <p class="dock-eyebrow">Account</p>
      <p id="sessionState" class="dock-session">Checking session&hellip;</p>
      <div class="actions">
        <button id="loginButton" class="btn btn-primary" type="button">Sign in</button>
        <button id="logoutButton" class="btn btn-secondary" type="button" hidden>Sign out</button>
      </div>
      <pre id="userInfo" class="output-block">Not signed in.</pre>
      <pre id="status" class="output-block"></pre>
    </aside>
    <main class="hero-panel">
      <p class="eyebrow">PingAM Login Widget</p>
      <h1>app5 demo</h1>
      <p class="lead">
        This app follows the
        <a href="https://developer.pingidentity.com/login-widget/login-widget/tutorial/01-install.html">Login Widget tutorial</a>
        against <code>${amBaseUrl}</code> in the <code>/${realmPath}</code> realm.
      </p>
      <p class="meta">
        Client <code>${clientId}</code> &middot; journey <code>${journeyName}</code>
      </p>
    </main>
  </div>
`;

const userInfoEl = document.getElementById('userInfo');
const statusEl = document.getElementById('status');

function renderUserInfo(info) {
  userInfoEl.textContent = info ? JSON.stringify(info, null, 2) : 'Not signed in.';
}

function renderStatus(message) {
  if (!message) {
    statusEl.style.display = 'none';
    statusEl.textContent = '';
    return;
  }
  statusEl.style.display = 'block';
  statusEl.textContent = message;
}

let currentUserInfo = null;

const loginButton = document.getElementById('loginButton');
const logoutButton = document.getElementById('logoutButton');
const sessionStateEl = document.getElementById('sessionState');
let signedIn = null;

// Drives the smart button from the shared AM SSO session reported by the
// same-origin /__slo/status endpoint (served by IG). This keeps app5 in lockstep
// with app1-app4: when the AM session is ended anywhere, app5 flips to "Sign in".
function applySessionState(authenticated, username) {
  signedIn = authenticated;
  if (authenticated) {
    sessionStateEl.textContent = username ? `Signed in as ${username}` : 'Signed in';
    sessionStateEl.style.color = '#0a7d54';
    loginButton.hidden = true;
    logoutButton.hidden = false;
  } else {
    sessionStateEl.textContent = 'Signed out';
    sessionStateEl.style.color = '#a23b2d';
    loginButton.hidden = false;
    logoutButton.hidden = true;
    if (currentUserInfo) {
      currentUserInfo = null;
      renderUserInfo(null);
    }
  }
}

async function pollSession() {
  try {
    const res = await fetch('/__slo/status', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    const data = await res.json();
    applySessionState(!!data.authenticated, data.username);
  } catch (_) {
    // keep last known state
  }
}

const myConfig = configuration();
myConfig.set({
  forgerock: {
    serverConfig: {
      baseUrl: amBaseUrl,
      timeout: 10000,
    },
    realmPath,
    clientId,
    redirectUri,
    scope: 'openid profile email address',
    tree: journeyName,
    logLevel: 'warn',
  },
  style: {
    labels: 'floating',
    sections: {
      header: false,
    },
  },
});

const widgetRootEl = document.getElementById('widget-root');
const widget = new Widget({ target: widgetRootEl });

const componentEvents = component();
const journeyEvents = journey();

journeyEvents.subscribe((event) => {
  if (event.oauth?.error) {
    renderStatus(`OAuth error: ${event.oauth.error.message}`);
  } else if (event.user?.error) {
    renderStatus(`Userinfo error: ${event.user.error.message}`);
  } else if (event.journey?.error) {
    renderStatus(`Journey error: ${event.journey.error.message}`);
  } else {
    renderStatus(null);
  }

  if (event.user?.response && event.user.response !== currentUserInfo) {
    currentUserInfo = event.user.response;
    renderUserInfo(currentUserInfo);
    const r = event.user.response;
    applySessionState(true, r.preferred_username || r.name || r.sub || null);
  }
});

loginButton.addEventListener('click', () => {
  componentEvents.open();
  journeyEvents.start({ journey: journeyName });
});

logoutButton.addEventListener('click', async () => {
  logoutButton.disabled = true;
  sessionStateEl.textContent = 'Signing out\u2026';
  // SDK logout revokes the OAuth tokens and ends the AM session (global SLO);
  // the IG endpoint guarantees the shared AM cookie is cleared across jrsz.org.
  try {
    await user.logout();
  } catch (_) {
    // session may already be gone
  }
  try {
    await fetch('/__slo/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
  } catch (_) {
    // best effort
  }
  logoutButton.disabled = false;
  currentUserInfo = null;
  renderUserInfo(null);
  renderStatus(null);
  applySessionState(false, null);
});

pollSession();
setInterval(pollSession, 5000);
window.addEventListener('focus', pollSession);

window.addEventListener('beforeunload', () => {
  widget.$destroy();
});
