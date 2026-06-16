const express = require("express");

const app = express();
app.use(express.urlencoded({ extended: false }));
app.use(express.json());

// ---------------------------------------------------------------------------
// Configuration
//
// app7 is a SAML v2.0 cross-domain federation test console. Two AM stacks
// (jrsz.org and jrsz.com) are federated: each /alpha realm hosts a single
// dual-role (IDP + SP) entity and imports the other AM's entity as a remote
// provider, joined by the `jrsz-federation` circle of trust. AM is BOTH the
// IDP and the SP; this console only launches AM's built-in SAML SSO-init
// endpoints (the four IDP/SP-init permutations) and reports the resulting
// federated AM session on whichever side it is co-located with.
// ---------------------------------------------------------------------------
const port = Number.parseInt(process.env.PORT || "3000", 10);

// Which stack this console instance is deployed on ("org" or "com").
const localSide = (process.env.LOCAL_SIDE || "org").toLowerCase() === "com" ? "com" : "org";
const appBaseUrl = (
  process.env.APP7_BASE_URL || (localSide === "com" ? "https://app7.jrsz.com:8444" : "https://app7.jrsz.org")
).replace(/\/+$/, "");

// Local (co-located) AM used for server-to-server session inspection. This is
// the same published URL the browser uses, so it doubles as a browser target.
const amBaseUrl = (
  process.env.AM_BASE_URL ||
  (localSide === "com" ? "https://am.jrsz.com:9443/am" : "https://am.jrsz.org:8443/am")
).replace(/\/+$/, "");
const amCookieDomain = process.env.AM_COOKIE_DOMAIN || (localSide === "com" ? "jrsz.com" : "jrsz.org");
const amAdminUser = process.env.AM_ADMIN_USER || "amadmin";
const amAdminPassword = process.env.AM_ADMIN_PASSWORD || "changeit";
const demoUser = process.env.DEMO_USER_NAME || "demo-user";

const realmName = process.env.AM_REALM || "/alpha";
const realmPath = process.env.AM_REALM_PATH || "realms/root/realms/alpha";
const authenticateUrl = `${amBaseUrl}/json/realms/root/authenticate`;
const sessionsUrl = `${amBaseUrl}/json/${realmPath}/sessions`;

// Browser-facing AM base URLs for BOTH stacks (used to build the four flow
// launch URLs regardless of which side this console runs on).
const orgAmBaseUrl = (process.env.ORG_AM_BASE_URL || "https://am.jrsz.org:8443/am").replace(/\/+$/, "");
const comAmBaseUrl = (process.env.COM_AM_BASE_URL || "https://am.jrsz.com:9443/am").replace(/\/+$/, "");

// SAML entity ids and per-role metaAliases (must match the AM federation config).
const orgEntityId = process.env.ORG_ENTITY_ID || "https://am.jrsz.org:8443/am/jrsz-org";
const comEntityId = process.env.COM_ENTITY_ID || "https://am.jrsz.com:9443/am/jrsz-com";
const orgIdpMetaAlias = process.env.ORG_IDP_METAALIAS || "/alpha/idp-org";
const orgSpMetaAlias = process.env.ORG_SP_METAALIAS || "/alpha/sp-org";
const comIdpMetaAlias = process.env.COM_IDP_METAALIAS || "/alpha/idp-com";
const comSpMetaAlias = process.env.COM_SP_METAALIAS || "/alpha/sp-com";

const POST_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST";
// SAML Single Logout is initiated over the front-channel HTTP-Redirect binding.
const REDIRECT_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect";

// IDP-init SSO starts at the IDP's idpssoinit servlet; SP-init SSO starts at the
// SP's spssoinit servlet. RelayState sends the browser to the SP-side app7 so
// the resulting federated session is visible immediately.
function idpInitUrl(idpAm, idpMetaAlias, spEntityId, relayState) {
  const u = new URL(`${idpAm}/idpssoinit`);
  u.searchParams.set("metaAlias", idpMetaAlias);
  u.searchParams.set("spEntityID", spEntityId);
  u.searchParams.set("binding", POST_BINDING);
  if (relayState) u.searchParams.set("RelayState", relayState);
  return u.toString();
}
function spInitUrl(spAm, spMetaAlias, idpEntityId, relayState) {
  const u = new URL(`${spAm}/spssoinit`);
  u.searchParams.set("metaAlias", spMetaAlias);
  u.searchParams.set("idpEntityID", idpEntityId);
  u.searchParams.set("binding", POST_BINDING);
  if (relayState) u.searchParams.set("RelayState", relayState);
  return u.toString();
}

const orgApp7 = "https://app7.jrsz.org";
const comApp7 = "https://app7.jrsz.com:8444";

// The four IDP/SP-init permutations. `landsOn` is the SP side (where the
// federated session is created), so the user should inspect that side's console.
const FLOWS = [
  {
    id: "org-idp_com-sp_idp-init",
    title: "org IDP &rarr; com SP (IDP-init)",
    init: "IDP-initiated",
    idp: "org",
    sp: "com",
    landsOn: "com",
    startAt: "am.jrsz.org",
    url: idpInitUrl(orgAmBaseUrl, orgIdpMetaAlias, comEntityId, `${comApp7}/?flow=org-idp_com-sp_idp-init`),
    blurb:
      "Start unauthenticated at the org IDP. After login as the demo user, AM (org) builds an assertion and HTTP-POSTs it to the com SP's ACS. com auto-federates on uid and creates a session for the user.",
  },
  {
    id: "org-idp_com-sp_sp-init",
    title: "org IDP &rarr; com SP (SP-init)",
    init: "SP-initiated",
    idp: "org",
    sp: "com",
    landsOn: "com",
    startAt: "am.jrsz.com",
    url: spInitUrl(comAmBaseUrl, comSpMetaAlias, orgEntityId, `${comApp7}/?flow=org-idp_com-sp_sp-init`),
    blurb:
      "Start at the com SP. com sends an AuthnRequest to the org IDP, the user authenticates at org, org POSTs the assertion back to com, and com creates the federated session.",
  },
  {
    id: "com-idp_org-sp_idp-init",
    title: "com IDP &rarr; org SP (IDP-init)",
    init: "IDP-initiated",
    idp: "com",
    sp: "org",
    landsOn: "org",
    startAt: "am.jrsz.com",
    url: idpInitUrl(comAmBaseUrl, comIdpMetaAlias, orgEntityId, `${orgApp7}/?flow=com-idp_org-sp_idp-init`),
    blurb:
      "Start unauthenticated at the com IDP. After login as the demo user, AM (com) builds an assertion and HTTP-POSTs it to the org SP's ACS. org auto-federates on uid and creates a session for the user.",
  },
  {
    id: "com-idp_org-sp_sp-init",
    title: "com IDP &rarr; org SP (SP-init)",
    init: "SP-initiated",
    idp: "com",
    sp: "org",
    landsOn: "org",
    startAt: "am.jrsz.org",
    url: spInitUrl(orgAmBaseUrl, orgSpMetaAlias, comEntityId, `${orgApp7}/?flow=com-idp_org-sp_sp-init`),
    blurb:
      "Start at the org SP. org sends an AuthnRequest to the com IDP, the user authenticates at com, com POSTs the assertion back to org, and org creates the federated session.",
  },
];

// SAML Single Logout (SLO) init endpoints. The metaAlias is NOT passed on the
// query string: AM's IDPSloInit / SPSloInit servlets resolve it from the SAML
// session properties (IDP_META_ALIAS / SP_METAALIAS), so the button only works
// against a live federated session on that entity. IDP-init SLO terminates the
// IDP session and propagates LogoutRequests to every SP in the session; SP-init
// SLO sends a LogoutRequest to the IDP which then cascades. RelayState returns
// the browser to that side's console (allow-listed on both roles).
function idpSloUrl(am, relayState) {
  const u = new URL(`${am}/IDPSloInit`);
  u.searchParams.set("binding", REDIRECT_BINDING);
  if (relayState) u.searchParams.set("RelayState", relayState);
  return u.toString();
}
function spSloUrl(am, relayState) {
  const u = new URL(`${am}/SPSloInit`);
  u.searchParams.set("binding", REDIRECT_BINDING);
  if (relayState) u.searchParams.set("RelayState", relayState);
  return u.toString();
}

// One SLO card per federated entity. Each dual-role entity can single-logout
// from either role; the canonical "single logout" is IDP-initiated.
const SLO_ENTITIES = [
  {
    id: "org",
    side: "org",
    label: "jrsz-org",
    entityId: orgEntityId,
    startAt: "am.jrsz.org",
    idpSlo: idpSloUrl(orgAmBaseUrl, `${orgApp7}/?slo=org-idp`),
    spSlo: spSloUrl(orgAmBaseUrl, `${orgApp7}/?slo=org-sp`),
  },
  {
    id: "com",
    side: "com",
    label: "jrsz-com",
    entityId: comEntityId,
    startAt: "am.jrsz.com",
    idpSlo: idpSloUrl(comAmBaseUrl, `${comApp7}/?slo=com-idp`),
    spSlo: spSloUrl(comAmBaseUrl, `${comApp7}/?slo=com-sp`),
  },
];

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
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
    if (idx > -1) out[part.slice(0, idx).trim()] = decodeURIComponent(part.slice(idx + 1).trim());
  });
  return out;
}
function getAmToken(req) {
  return parseCookies(req)["iPlanetDirectoryPro"] || null;
}
function clearAmCookieHeader() {
  return `iPlanetDirectoryPro=; Domain=${amCookieDomain}; Path=/; Max-Age=0; HttpOnly`;
}

// Classify the AM SSO token (server-side opaque reference vs client-side JWT).
function detectAmSessionType(token) {
  if (!token) return { type: "none", label: "no AM session" };
  if (token.match(/eyJ[A-Za-z0-9_-]{16,}/)) {
    return { type: "client-side", label: "Client-side (JWT)" };
  }
  return { type: "server-side", label: "Server-side (CTS)" };
}

// ---------------------------------------------------------------------------
// AM REST helpers (against the co-located AM)
// ---------------------------------------------------------------------------
let adminTokenCache = { value: null, expiresAt: 0 };

async function getAdminToken(force = false) {
  const now = Date.now();
  if (!force && adminTokenCache.value && adminTokenCache.expiresAt > now) return adminTokenCache.value;
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
  if (!response.ok || !payload || !payload.tokenId) throw new Error("AM admin authentication failed");
  adminTokenCache = { value: payload.tokenId, expiresAt: now + 60_000 };
  return payload.tokenId;
}

async function amSessionAction(token, action, refresh) {
  if (!token) return { ok: false, raw: null };
  const admin = await getAdminToken();
  const q = action === "validate" && refresh === false ? "&refresh=false" : "";
  const response = await fetch(`${sessionsUrl}?_action=${action}${q}`, {
    method: "POST",
    headers: {
      iPlanetDirectoryPro: admin,
      "Accept-API-Version": "resource=5.1, protocol=1.0",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ tokenId: token }),
  });
  const raw = await response.json().catch(() => null);
  return { ok: response.ok, status: response.status, raw };
}

async function amValidate(token) {
  if (!token) return { valid: false };
  try {
    const r = await amSessionAction(token, "validate", false);
    return { valid: !!(r.raw && r.raw.valid), raw: r.raw };
  } catch (error) {
    return { valid: false, error: String(error) };
  }
}

async function amSessionInfo(token) {
  if (!token) return { ok: false };
  try {
    return await amSessionAction(token, "getSessionInfo");
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

// Logout the user's own session (token goes in the header).
async function amLogout(token) {
  if (!token) return { ok: false };
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
    return { ok: response.ok, status: response.status };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

// Read the SAML2 entities + circle of trust from the co-located AM (admin) so
// the console can display the federation configuration as evidence.
async function readFederationConfig() {
  try {
    const admin = await getAdminToken();
    const base = `${amBaseUrl}/json/${realmPath}/realm-config`;
    const headers = { iPlanetDirectoryPro: admin, "Accept-API-Version": "protocol=2.1,resource=1.0" };
    const cotHeaders = { iPlanetDirectoryPro: admin, "Accept-API-Version": "resource=2.0" };
    const [entRes, cotRes] = await Promise.all([
      fetch(`${base}/saml2?_queryFilter=true`, { headers }),
      fetch(`${base}/federation/circlesoftrust?_queryFilter=true`, { headers: cotHeaders }),
    ]);
    const ent = await entRes.json().catch(() => null);
    const cot = await cotRes.json().catch(() => null);
    return {
      ok: entRes.ok && cotRes.ok,
      entities: (ent && ent.result ? ent.result : []).map((e) => ({
        entityId: e.entityId,
        location: e.location,
        roles: e.roles,
      })),
      circlesOfTrust: (cot && cot.result ? cot.result : []).map((c) => ({
        _id: c._id,
        status: c.status,
        trustedProviders: c.trustedProviders,
      })),
    };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

// ---------------------------------------------------------------------------
// Client config + page
// ---------------------------------------------------------------------------
const CFG = {
  appBaseUrl,
  localSide,
  amBaseUrl,
  realmName,
  demoUser,
  amXuiLogin: `${amBaseUrl}/XUI/?realm=${realmName}#login/`,
  orgApp7,
  comApp7,
  flows: FLOWS,
  sloEntities: SLO_ENTITIES,
};

const PAGE_CSS = `
  :root {
    --bg:#0f1720; --panel:#16212e; --panel2:#1d2a39; --ink:#e8f1ef; --muted:#93a4b3;
    --accent:#2dd4bf; --accent-ink:#06262b; --ok:#34d399; --bad:#f87171; --warn:#fbbf24;
    --border:#2a3a4b; --info:#60a5fa; --org:#2dd4bf; --com:#a78bfa;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"IBM Plex Sans","Segoe UI",sans-serif;
    background:radial-gradient(circle at top right,#13202c 0,transparent 40%),var(--bg); color:var(--ink); }
  a { color:var(--accent); }
  header.top { position:sticky; top:0; z-index:50; backdrop-filter:blur(6px);
    background:rgba(15,23,32,.9); border-bottom:1px solid var(--border); padding:14px 24px; }
  .eyebrow { display:inline-block; padding:4px 10px; border-radius:999px; background:#0b3b3b;
    color:var(--accent); font-size:12px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; }
  .eyebrow.com { background:#241b46; color:var(--com); }
  h1 { margin:8px 0 4px; font-size:1.5rem; }
  .sub { color:var(--muted); font-size:.85rem; margin:0; max-width:90ch; }
  .strip { display:flex; flex-wrap:wrap; gap:10px; margin-top:12px; font-size:.8rem; }
  .chip { padding:6px 10px; border-radius:8px; background:var(--panel2); border:1px solid var(--border);
    font-family:"IBM Plex Mono",monospace; }
  .chip b { color:var(--accent); }
  .chip.ok b { color:var(--ok); } .chip.bad b { color:var(--bad); } .chip.warn b { color:var(--warn); }
  .chip.info b { color:var(--info); }
  main { max-width:1180px; margin:0 auto; padding:20px 24px 80px; }
  .toolbar { display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 8px; }
  button.act, a.act { appearance:none; cursor:pointer; border:1px solid var(--border); background:var(--panel2);
    color:var(--ink); padding:7px 12px; border-radius:8px; font:600 .82rem "IBM Plex Sans",sans-serif; text-decoration:none; }
  button.act:hover, a.act:hover { border-color:var(--accent); }
  button.act.primary { background:var(--accent); color:var(--accent-ink); border-color:var(--accent); }
  section.group { margin-bottom:24px; }
  section.group > h2 { font-size:1.1rem; margin:18px 0 4px; }
  section.group > p.blurb { color:var(--muted); margin:0 0 14px; max-width:95ch; font-size:.88rem; }
  .cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:14px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:16px;
    display:flex; flex-direction:column; gap:10px; }
  .card.here { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }
  .card h3 { margin:0; font-size:1rem; }
  .card .tags { display:flex; flex-wrap:wrap; gap:6px; }
  .tag { font-size:.68rem; padding:2px 8px; border-radius:999px; background:var(--panel2); border:1px solid var(--border);
    color:var(--muted); font-family:"IBM Plex Mono",monospace; }
  .tag.idp { color:var(--info); } .tag.sp { color:var(--warn); } .tag.here { color:var(--accent); border-color:var(--accent); }
  .card p.meta { font-size:.82rem; color:var(--muted); margin:0; line-height:1.5; }
  .card .btns { display:flex; flex-wrap:wrap; gap:8px; margin-top:auto; }
  pre { white-space:pre-wrap; word-break:break-word; background:#0a121a; color:#c8e9e2; margin:0; padding:12px;
    border-radius:8px; font-size:11.5px; max-height:280px; overflow:auto; border:1px solid var(--border); }
  .banner { margin-top:12px; padding:10px 14px; border-radius:10px; border:1px solid var(--border);
    background:var(--panel2); font-size:.85rem; display:none; }
  .banner.show { display:block; }
  .card.slo .btns { gap:8px; }
  .card .ent { font-size:.72rem; color:var(--muted); font-family:"IBM Plex Mono",monospace; word-break:break-all; margin:0; }
`;

const CLIENT_JS = `
(function () {
  var CFG = window.CFG;
  function el(t,c,h){var e=document.createElement(t); if(c)e.className=c; if(h!=null)e.innerHTML=h; return e;}
  function fmt(o){try{return JSON.stringify(o,null,2);}catch(e){return String(o);}}
  function getJson(p){return fetch(p,{credentials:"same-origin",headers:{Accept:"application/json"}}).then(function(r){return r.json();});}
  function postJson(p,b){return fetch(p,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json",Accept:"application/json"},body:JSON.stringify(b||{})}).then(function(r){return r.json();});}

  function renderStrip(s){
    var strip=document.getElementById("strip"); strip.innerHTML="";
    function chip(l,v,k){var c=el("span","chip "+(k||""),l+": <b>"+v+"</b>"); strip.appendChild(c);}
    chip("this side", CFG.localSide.toUpperCase(), "info");
    chip("realm", s.realm || CFG.realmName, "");
    chip("AM cookie", s.amCookiePresent?"present":"absent", s.amCookiePresent?"ok":"bad");
    chip("AM session", s.amValid?"VALID":"invalid", s.amValid?"ok":"bad");
    if (s.amValid && s.username) chip("federated user", s.username, s.username===CFG.demoUser?"ok":"warn");
    if (s.amSessionType && s.amSessionType.type!=="none") chip("session type", s.amSessionType.label, "info");
  }
  function pollState(){ return getJson("/probe/state").then(renderStrip).catch(function(){}); }

  function renderFlows(){
    var root=document.getElementById("flows");
    CFG.flows.forEach(function(f){
      var here = f.landsOn===CFG.localSide;
      var card=el("div","card"+(here?" here":""));
      card.appendChild(el("h3",null,f.title));
      var tags=el("div","tags");
      tags.appendChild(el("span","tag",f.init));
      tags.appendChild(el("span","tag idp","IDP: "+f.idp));
      tags.appendChild(el("span","tag sp","SP: "+f.sp));
      tags.appendChild(el("span","tag"+(here?" here":""),"lands on "+f.sp+(here?" (this side)":"")));
      card.appendChild(tags);
      card.appendChild(el("p","meta",f.blurb));
      card.appendChild(el("p","meta","Starts at <b>"+f.startAt+"</b>. Session is created on the <b>"+f.sp+"</b> stack \u2014 view the "+f.sp+" console to see it."));
      var btns=el("div","btns");
      var go=el("a","act primary","Launch flow"); go.href=f.url; go.target="_blank"; go.rel="noopener";
      btns.appendChild(go);
      var other = el("a","act", (here?"This console":(f.sp==="com"?"Open com console":"Open org console")));
      other.href = f.sp==="com"?CFG.comApp7:CFG.orgApp7; other.target = here?"_self":"_blank";
      btns.appendChild(other);
      card.appendChild(btns);
      root.appendChild(card);
    });
  }

  function renderConfig(){
    getJson("/probe/config").then(function(c){
      document.getElementById("cfg").textContent = fmt(c);
    }).catch(function(e){ document.getElementById("cfg").textContent = "Error: "+e; });
  }

  function renderSlo(){
    var root=document.getElementById("slo");
    CFG.sloEntities.forEach(function(en){
      var here = en.side===CFG.localSide;
      var card=el("div","card slo"+(here?" here":""));
      card.appendChild(el("h3",null,"SAML SLO &middot; "+en.label+(here?" (this side)":"")));
      var tags=el("div","tags");
      tags.appendChild(el("span","tag"+(here?" here":""),"entity: "+en.side));
      tags.appendChild(el("span","tag","init at "+en.startAt));
      card.appendChild(tags);
      card.appendChild(el("p","ent",en.entityId));
      card.appendChild(el("p","meta","Terminates the live SAML session for this entity and propagates LogoutRequests to its federation partner. Requires an active federated session (the metaAlias is read from the session)."));
      var btns=el("div","btns");
      var idp=el("a","act primary","IDP-initiated SLO"); idp.href=en.idpSlo; idp.target=here?"_self":"_blank"; idp.rel="noopener";
      var sp=el("a","act","SP-initiated SLO"); sp.href=en.spSlo; sp.target=here?"_self":"_blank"; sp.rel="noopener";
      btns.appendChild(idp); btns.appendChild(sp);
      card.appendChild(btns);
      root.appendChild(card);
    });
  }

  function wire(){
    document.getElementById("tb-refresh").addEventListener("click", function(){ pollState(); });
    document.getElementById("tb-amlogin").addEventListener("click", function(){ window.open(CFG.amXuiLogin,"_blank","noopener"); });
    document.getElementById("tb-logout").addEventListener("click", function(){
      if(!confirm("Log out the local AM session on this ("+CFG.localSide+") stack? This is a plain REST logout (no SAML propagation).")) return;
      postJson("/probe/am-logout").then(function(){ pollState(); });
    });
    var qs=new URLSearchParams(window.location.search);
    if (qs.get("flow")) {
      var b=document.getElementById("banner");
      b.innerHTML="Returned from flow <b>"+qs.get("flow")+"</b>. The federated session below should now show <b>"+CFG.demoUser+"</b> on the <b>"+CFG.localSide+"</b> stack.";
      b.classList.add("show");
    } else if (qs.get("slo")) {
      var sb=document.getElementById("banner");
      sb.innerHTML="Returned from SAML Single Logout <b>"+qs.get("slo")+"</b>. The session state below should now show <b>no AM session</b> on the <b>"+CFG.localSide+"</b> stack.";
      sb.classList.add("show");
    }
  }

  renderFlows(); renderSlo(); renderConfig(); wire(); pollState();
  setInterval(pollState, 5000);
})();
`;

function renderPage() {
  const eyebrowClass = localSide === "com" ? "eyebrow com" : "eyebrow";
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SAML Federation Test Console (${htmlEscape(localSide)})</title>
<style>${PAGE_CSS}</style>
</head>
<body>
<header class="top">
  <span class="${eyebrowClass}">app7 &middot; ${htmlEscape(appBaseUrl)} &middot; realm ${htmlEscape(realmName)}</span>
  <h1>SAML v2.0 Cross-Domain Federation Console</h1>
  <p class="sub">jrsz.org and jrsz.com are federated: each <code>/alpha</code> realm hosts one dual-role (IDP + SP) entity in the <code>jrsz-federation</code> circle of trust. AM is both the IDP and the SP. Launch any of the four IDP/SP-init permutations below; the federated session is created on the SP side and resolves to <code>${htmlEscape(demoUser)}</code> via auto-federation on <code>uid</code>.</p>
  <div id="strip" class="strip"></div>
  <div id="banner" class="banner"></div>
</header>
<main>
  <div class="toolbar">
    <button id="tb-amlogin" class="act primary">Open AM login (${htmlEscape(realmName)})</button>
    <button id="tb-refresh" class="act">Refresh session state</button>
    <button id="tb-logout" class="act">Log out local AM session</button>
  </div>

  <section class="group">
    <h2>Federation flows (all four permutations)</h2>
    <p class="blurb">Highlighted cards land their federated session on <b>this</b> (${htmlEscape(localSide)}) stack, so you can verify them here. The other two land on the partner stack &mdash; open its console to confirm. Tip: log out both stacks between IDP-init tests to see a fresh login at the IDP.</p>
    <div id="flows" class="cards"></div>
  </section>

  <section class="group">
    <h2>Single Logout (SAML SLO)</h2>
    <p class="blurb">Proper SAML 2.0 Single Logout for both federated entities. <b>IDP-initiated</b> SLO ends the IDP session and posts a LogoutRequest to every SP in the session; <b>SP-initiated</b> SLO asks the IDP to terminate and cascade. The metaAlias is taken from the live session, so a button only acts when that entity currently has a federated session. SLO for <b>${htmlEscape(localSide)}</b> opens in this tab and returns here; the partner opens in a new tab.</p>
    <div id="slo" class="cards"></div>
  </section>

  <section class="group">
    <h2>Federation configuration on this (${htmlEscape(localSide)}) AM</h2>
    <p class="blurb">Read live from <code>${htmlEscape(amBaseUrl)}</code>: the SAML2 entities (hosted + remote) and the circle of trust backing these flows.</p>
    <pre id="cfg">Loading...</pre>
  </section>
</main>
<script>window.CFG=${JSON.stringify(CFG)};</script>
<script>${CLIENT_JS}</script>
</body>
</html>`;
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------
app.get("/", (req, res) => {
  res.set("Cache-Control", "no-store");
  res.set("Content-Type", "text/html; charset=UTF-8");
  res.send(renderPage());
});

app.get("/healthz", (req, res) => res.json({ ok: true }));

app.get("/probe/state", async (req, res) => {
  const token = getAmToken(req);
  const validate = await amValidate(token);
  let username = null;
  let realm = null;
  if (validate.valid) {
    const info = await amSessionInfo(token);
    if (info.raw) {
      username = info.raw.username || null;
      realm = info.raw.realm || null;
    }
  }
  res.set("Cache-Control", "no-store");
  res.json({
    side: localSide,
    realm: realm || realmName,
    amCookiePresent: !!token,
    amValid: validate.valid,
    amSessionType: detectAmSessionType(token),
    username,
  });
});

app.post("/probe/am-logout", async (req, res) => {
  const token = getAmToken(req);
  const result = await amLogout(token);
  res.append("Set-Cookie", clearAmCookieHeader());
  res.set("Cache-Control", "no-store");
  res.json({ loggedOut: true, result });
});

app.get("/probe/config", async (req, res) => {
  const cfg = await readFederationConfig();
  res.set("Cache-Control", "no-store");
  res.json(cfg);
});

app.listen(port, () => {
  console.log(`app7 SAML federation console listening on ${port}`);
  console.log(`  side:  ${localSide}`);
  console.log(`  AM:    ${amBaseUrl} (realm ${realmName})`);
  console.log(`  org entity: ${orgEntityId}`);
  console.log(`  com entity: ${comEntityId}`);
});
