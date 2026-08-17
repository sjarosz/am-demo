const express = require("express");

const app = express();
app.use(express.urlencoded({ extended: false }));
app.use(express.json());

// ---------------------------------------------------------------------------
// Configuration
//
// app9 is a SAML v2.0 custom-script test console. It is the SAML analogue of
// app8 (OAuth2/OIDC scripts): it demonstrates three of the PingAM sample SAML2
// scripts in a genuine cross-AM federation, isolated in its own `/samllab`
// realm on BOTH stacks (zero impact on the app7 /alpha federation):
//
//   * org hosts the IdP (samllab-idp) -> IDP Attribute Mapper script injects
//     star-tagged SAML attributes into the assertion.
//   * the IdP's REMOTE view of the com SP carries the NameID Mapper script,
//     which star-tags the assertion Subject NameID.
//   * com hosts the SP (samllab-sp) -> SP Adapter script stashes the star-tagged
//     attributes + NameID into the SP session as the `samllabProof` property.
//
// This console launches AM's built-in SAML SSO-init endpoints, then reads the
// `samllabProof` session property back over REST and highlights every element
// whose name carries the star emoji, tying all three scripts together.
// The federated session lands on the com (SP) stack, so the proof shows there.
// ---------------------------------------------------------------------------
const port = Number.parseInt(process.env.PORT || "3000", 10);
const STAR = "\u2B50";

const localSide = (process.env.LOCAL_SIDE || "org").toLowerCase() === "com" ? "com" : "org";
const appBaseUrl = (
  process.env.APP9_BASE_URL || (localSide === "com" ? "https://app9.jrsz.net:8444" : "https://app9.jrsz.org")
).replace(/\/+$/, "");

// Local (co-located) AM used for server-to-server session/config inspection.
const amBaseUrl = (
  process.env.AM_BASE_URL ||
  (localSide === "com" ? "https://am.jrsz.net:9443/am" : "https://am.jrsz.org:8443/am")
).replace(/\/+$/, "");
const amCookieDomain = process.env.AM_COOKIE_DOMAIN || (localSide === "com" ? "jrsz.net" : "jrsz.org");
const amAdminUser = process.env.AM_ADMIN_USER || "amadmin";
const amAdminPassword = process.env.AM_ADMIN_PASSWORD || "changeit";
const demoUser = process.env.DEMO_USER_NAME || "demo-user";

const realmName = process.env.AM_REALM || "/samllab";
const realmPath = process.env.AM_REALM_PATH || "realms/root/realms/samllab";
const authenticateUrl = `${amBaseUrl}/json/realms/root/authenticate`;
const sessionsUrl = `${amBaseUrl}/json/${realmPath}/sessions`;

// Browser-facing AM base URLs for BOTH stacks (to build the flow launch URLs
// regardless of which side this console runs on).
const orgAmBaseUrl = (process.env.ORG_AM_BASE_URL || "https://am.jrsz.org:8443/am").replace(/\/+$/, "");
const comAmBaseUrl = (process.env.COM_AM_BASE_URL || "https://am.jrsz.net:9443/am").replace(/\/+$/, "");

// SAML entity ids and per-role metaAliases (must match the /samllab federation).
const idpEntityId = process.env.SAMLLAB_IDP_ENTITY_ID || "https://am.jrsz.org:8443/am/samllab-idp";
const spEntityId = process.env.SAMLLAB_SP_ENTITY_ID || "https://am.jrsz.net:9443/am/samllab-sp";
const idpMetaAlias = process.env.SAMLLAB_IDP_METAALIAS || "/samllab/idp";
const spMetaAlias = process.env.SAMLLAB_SP_METAALIAS || "/samllab/sp";

const POST_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST";

const orgApp9 = "https://app9.jrsz.org";
const comApp9 = "https://app9.jrsz.net:8444";

function idpInitUrl(idpAm, metaAlias, spEntity, relayState) {
  const u = new URL(`${idpAm}/idpssoinit`);
  u.searchParams.set("metaAlias", metaAlias);
  u.searchParams.set("spEntityID", spEntity);
  u.searchParams.set("binding", POST_BINDING);
  if (relayState) u.searchParams.set("RelayState", relayState);
  return u.toString();
}
function spInitUrl(spAm, metaAlias, idpEntity, relayState) {
  const u = new URL(`${spAm}/spssoinit`);
  u.searchParams.set("metaAlias", metaAlias);
  u.searchParams.set("idpEntityID", idpEntity);
  u.searchParams.set("binding", POST_BINDING);
  if (relayState) u.searchParams.set("RelayState", relayState);
  return u.toString();
}

// Both flows run the SAME federation (org IdP -> com SP) and land the session on
// com; they differ only in who initiates. Each exercises all three scripts.
const FLOWS = [
  {
    id: "idp-init",
    title: "org IdP &rarr; com SP (IdP-init)",
    init: "IDP-initiated",
    startAt: "am.jrsz.org",
    url: idpInitUrl(orgAmBaseUrl, idpMetaAlias, spEntityId, `${comApp9}/?flow=idp-init`),
    blurb:
      "Start unauthenticated at the org IdP. After login as the demo user, the IDP Attribute Mapper + NameID Mapper scripts build a star-tagged assertion which is HTTP-POSTed to the com SP. The SP Adapter script stashes the star elements into the com session.",
  },
  {
    id: "sp-init",
    title: "org IdP &rarr; com SP (SP-init)",
    init: "SP-initiated",
    startAt: "am.jrsz.net",
    url: spInitUrl(comAmBaseUrl, spMetaAlias, idpEntityId, `${comApp9}/?flow=sp-init`),
    blurb:
      "Start at the com SP. com sends an AuthnRequest to the org IdP; after login the same three scripts run, org POSTs the star-tagged assertion back to com, and the SP Adapter stashes the proof into the com session.",
  },
];

// The three SAML scripts demonstrated, for the explainer cards.
const SCRIPTS = [
  {
    name: "IDP Attribute Mapper",
    context: "SAML2_IDP_ATTRIBUTE_MAPPER",
    where: "org hosted IdP (samllab-idp)",
    side: "org",
    proof: `Injects ${STAR}dept, ${STAR}source, ${STAR}hostedIdp and ${STAR}mail SAML attributes into the assertion (alongside the real uid used for auto-federation).`,
  },
  {
    name: "NameID Mapper",
    context: "SAML2_NAMEID_MAPPER",
    where: "org IdP's remote view of the com SP",
    side: "org",
    proof: `Prefixes the assertion Subject NameID with ${STAR} (the SP auto-federates on the uid attribute, so the marker is cosmetic but visible).`,
  },
  {
    name: "SP Adapter",
    context: "SAML2_SP_ADAPTER",
    where: "com hosted SP (samllab-sp)",
    side: "com",
    proof: `On postSingleSignOnSuccess, reads the assertion and stashes every ${STAR} attribute + the ${STAR} NameID into the com session as the samllabProof property (read back below).`,
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

// Read the SAML2 entities, circle of trust, and the wired samllab scripts from
// the co-located AM (admin), as configuration evidence.
async function readFederationConfig() {
  try {
    const admin = await getAdminToken();
    const base = `${amBaseUrl}/json/${realmPath}/realm-config`;
    const samlHeaders = { iPlanetDirectoryPro: admin, "Accept-API-Version": "protocol=2.1,resource=1.0" };
    const cotHeaders = { iPlanetDirectoryPro: admin, "Accept-API-Version": "resource=2.0" };
    const scriptHeaders = { iPlanetDirectoryPro: admin, "Accept-API-Version": "protocol=2.0,resource=1.0" };
    const [entRes, cotRes, scrRes] = await Promise.all([
      fetch(`${base}/saml2?_queryFilter=true`, { headers: samlHeaders }),
      fetch(`${base}/federation/circlesoftrust?_queryFilter=true`, { headers: cotHeaders }),
      fetch(`${amBaseUrl}/json/${realmPath}/scripts?_queryFilter=true`, { headers: scriptHeaders }),
    ]);
    const ent = await entRes.json().catch(() => null);
    const cot = await cotRes.json().catch(() => null);
    const scr = await scrRes.json().catch(() => null);
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
      scripts: (scr && scr.result ? scr.result : [])
        .filter((s) => String(s.name || "").startsWith("Samllab"))
        .map((s) => ({ name: s.name, context: s.context, language: s.language })),
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
  star: STAR,
  amXuiLogin: `${amBaseUrl}/XUI/?realm=${realmName}#login/`,
  orgApp9,
  comApp9,
  flows: FLOWS,
  scripts: SCRIPTS,
  spEntityId,
  idpEntityId,
};

const PAGE_CSS = `
  :root {
    --bg:#0f1720; --panel:#16212e; --panel2:#1d2a39; --ink:#e8f1ef; --muted:#93a4b3;
    --accent:#2dd4bf; --accent-ink:#06262b; --ok:#34d399; --bad:#f87171; --warn:#fbbf24;
    --border:#2a3a4b; --info:#60a5fa; --star:#fbbf24;
  }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"IBM Plex Sans","Segoe UI",sans-serif;
    background:radial-gradient(circle at top right,#13202c 0,transparent 40%),var(--bg); color:var(--ink); }
  a { color:var(--accent); }
  header.top { position:sticky; top:0; z-index:50; backdrop-filter:blur(6px);
    background:rgba(15,23,32,.9); border-bottom:1px solid var(--border); padding:14px 24px; }
  .eyebrow { display:inline-block; padding:4px 10px; border-radius:999px; background:#0b3b3b;
    color:var(--accent); font-size:12px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; }
  .eyebrow.com { background:#241b46; color:#a78bfa; }
  h1 { margin:8px 0 4px; font-size:1.5rem; }
  .sub { color:var(--muted); font-size:.85rem; margin:0; max-width:96ch; }
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
  section.group > p.blurb { color:var(--muted); margin:0 0 14px; max-width:96ch; font-size:.88rem; }
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
    border-radius:8px; font-size:11.5px; max-height:320px; overflow:auto; border:1px solid var(--border); }
  .banner { margin-top:12px; padding:10px 14px; border-radius:10px; border:1px solid var(--border);
    background:var(--panel2); font-size:.85rem; display:none; }
  .banner.show { display:block; }
  table.proof { width:100%; border-collapse:collapse; font-size:.82rem; }
  table.proof th, table.proof td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--border);
    font-family:"IBM Plex Mono",monospace; vertical-align:top; word-break:break-all; }
  table.proof th { color:var(--muted); font-weight:600; }
  table.proof tr.star td.k { color:var(--star); font-weight:700; }
  table.proof tr.star { background:rgba(251,191,36,.07); }
  .pill { font-size:.7rem; padding:1px 7px; border-radius:999px; border:1px solid var(--border); color:var(--muted); }
  .proof-empty { color:var(--muted); font-size:.85rem; }
`;

const CLIENT_JS = `
(function () {
  var CFG = window.CFG;
  function el(t,c,h){var e=document.createElement(t); if(c)e.className=c; if(h!=null)e.innerHTML=h; return e;}
  function fmt(o){try{return JSON.stringify(o,null,2);}catch(e){return String(o);}}
  function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
  function getJson(p){return fetch(p,{credentials:"same-origin",headers:{Accept:"application/json"}}).then(function(r){return r.json();});}
  function postJson(p,b){return fetch(p,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json",Accept:"application/json"},body:JSON.stringify(b||{})}).then(function(r){return r.json();});}

  function renderStrip(s){
    var strip=document.getElementById("strip"); strip.innerHTML="";
    function chip(l,v,k){strip.appendChild(el("span","chip "+(k||""),l+": <b>"+v+"</b>"));}
    chip("this side", CFG.localSide.toUpperCase(), CFG.localSide==="com"?"info":"");
    chip("role here", CFG.localSide==="com"?"SP (proof lands here)":"IdP (launch flows)", CFG.localSide==="com"?"ok":"warn");
    chip("realm", s.realm || CFG.realmName, "");
    chip("AM session", s.amValid?"VALID":"none", s.amValid?"ok":"bad");
    if (s.amValid && s.username) chip("user", s.username, s.username===CFG.demoUser?"ok":"warn");
  }
  function pollState(){ return getJson("/probe/state").then(renderStrip).catch(function(){}); }

  function renderFlows(){
    var root=document.getElementById("flows");
    CFG.flows.forEach(function(f){
      var card=el("div","card");
      card.appendChild(el("h3",null,f.title));
      var tags=el("div","tags");
      tags.appendChild(el("span","tag",f.init));
      tags.appendChild(el("span","tag idp","IdP: org"));
      tags.appendChild(el("span","tag sp","SP: com"));
      tags.appendChild(el("span","tag","lands on com"));
      card.appendChild(tags);
      card.appendChild(el("p","meta",f.blurb));
      card.appendChild(el("p","meta","Starts at <b>"+f.startAt+"</b>. Session + "+CFG.star+" proof land on the <b>com</b> stack."));
      var btns=el("div","btns");
      var go=el("a","act primary","Launch flow"); go.href=f.url; go.target="_blank"; go.rel="noopener";
      btns.appendChild(go);
      if (CFG.localSide!=="com"){
        var open=el("a","act","Open com console (see proof)"); open.href=CFG.comApp9; open.target="_blank"; open.rel="noopener";
        btns.appendChild(open);
      }
      card.appendChild(btns);
      root.appendChild(card);
    });
  }

  function renderScripts(){
    var root=document.getElementById("scripts");
    CFG.scripts.forEach(function(sc){
      var here = sc.side===CFG.localSide;
      var card=el("div","card"+(here?" here":""));
      card.appendChild(el("h3",null,sc.name));
      var tags=el("div","tags");
      tags.appendChild(el("span","tag",sc.context));
      tags.appendChild(el("span","tag"+(here?" here":""),sc.where));
      card.appendChild(tags);
      card.appendChild(el("p","meta",sc.proof));
      root.appendChild(card);
    });
  }

  function renderProof(){
    var root=document.getElementById("proof");
    root.innerHTML="";
    if (CFG.localSide!=="com"){
      root.appendChild(el("p","proof-empty","The federated session and the "+CFG.star+" proof are created on the <b>com</b> SP stack. Launch a flow above, then open the <a href='"+CFG.comApp9+"' target='_blank' rel='noopener'>com console</a> to see the stashed "+CFG.star+" elements."));
      return;
    }
    getJson("/probe/proof").then(function(p){
      if (!p.amValid){
        root.appendChild(el("p","proof-empty","No federated session on this (com) stack yet. Launch a flow above and return here."));
        return;
      }
      if (!p.present){
        root.appendChild(el("p","proof-empty","Session is present for <b>"+esc(p.username||"?")+"</b> but the samllabProof property is not set. Re-run a flow (the SP Adapter sets it on SSO success)."));
        return;
      }
      var n=0; for (var k in p.proof){ if(p.proof.hasOwnProperty(k)) n++; }
      root.appendChild(el("p","meta","Read from the com SP session of <b>"+esc(p.username)+"</b> via the AM sessions REST endpoint. <span class='pill'>"+n+" entries, all "+CFG.star+"-tagged</span>"));
      var tbl=el("table","proof");
      tbl.innerHTML="<thead><tr><th>property</th><th>value</th></tr></thead>";
      var tb=el("tbody");
      Object.keys(p.proof).forEach(function(k){
        var star = k.indexOf(CFG.star)>=0;
        var tr=el("tr",star?"star":"");
        tr.appendChild(el("td","k",esc(k)));
        tr.appendChild(el("td","v",esc(p.proof[k])));
        tb.appendChild(tr);
      });
      tbl.appendChild(tb);
      root.appendChild(tbl);
    }).catch(function(e){ root.appendChild(el("p","proof-empty","Error reading proof: "+e)); });
  }

  function renderConfig(){
    getJson("/probe/config").then(function(c){
      document.getElementById("cfg").textContent = fmt(c);
    }).catch(function(e){ document.getElementById("cfg").textContent = "Error: "+e; });
  }

  function wire(){
    document.getElementById("tb-refresh").addEventListener("click", function(){ pollState(); renderProof(); });
    document.getElementById("tb-amlogin").addEventListener("click", function(){ window.open(CFG.amXuiLogin,"_blank","noopener"); });
    document.getElementById("tb-logout").addEventListener("click", function(){
      if(!confirm("Log out the local AM session on this ("+CFG.localSide+") stack?")) return;
      postJson("/probe/am-logout").then(function(){ pollState(); renderProof(); });
    });
    var qs=new URLSearchParams(window.location.search);
    if (qs.get("flow")) {
      var b=document.getElementById("banner");
      b.innerHTML="Returned from the <b>"+esc(qs.get("flow"))+"</b> flow. The "+CFG.star+" proof below is read from the resulting com SP session.";
      b.classList.add("show");
    }
  }

  renderFlows(); renderScripts(); renderConfig(); wire(); pollState(); renderProof();
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
<title>SAML Custom-Script Console (${htmlEscape(localSide)})</title>
<style>${PAGE_CSS}</style>
</head>
<body>
<header class="top">
  <span class="${eyebrowClass}">app9 &middot; ${htmlEscape(appBaseUrl)} &middot; realm ${htmlEscape(realmName)}</span>
  <h1>SAML v2.0 Custom-Script Console</h1>
  <p class="sub">A genuine cross-AM federation isolated in the <code>/samllab</code> realm: the org AM hosts the IdP, the com AM hosts the SP. Three PingAM sample SAML2 scripts run during SSO and tag their custom output with the ${STAR} emoji &mdash; the <b>IDP Attribute Mapper</b> and <b>NameID Mapper</b> (org IdP) build a ${STAR}-tagged assertion, and the <b>SP Adapter</b> (com SP) stashes it into the session as <code>samllabProof</code>, shown below.</p>
  <div id="strip" class="strip"></div>
  <div id="banner" class="banner"></div>
</header>
<main>
  <div class="toolbar">
    <button id="tb-amlogin" class="act primary">Open AM login (${htmlEscape(realmName)})</button>
    <button id="tb-refresh" class="act">Refresh</button>
    <button id="tb-logout" class="act">Log out local AM session</button>
  </div>

  <section class="group">
    <h2>Federation flows</h2>
    <p class="blurb">Both flows run org IdP &rarr; com SP and land the session on the <b>com</b> stack, differing only in who initiates. Each exercises all three scripts. Open the <b>com</b> console to read the ${STAR} proof.</p>
    <div id="flows" class="cards"></div>
  </section>

  <section class="group">
    <h2>The three SAML scripts</h2>
    <p class="blurb">Highlighted cards run on <b>this</b> (${htmlEscape(localSide)}) stack. Every custom element these scripts emit is prefixed with ${STAR} for easy identification.</p>
    <div id="scripts" class="cards"></div>
  </section>

  <section class="group">
    <h2>${STAR} Script proof &mdash; com SP session</h2>
    <p class="blurb">The <code>samllabProof</code> session property set by the SP Adapter, read live over REST. Rows whose name carries the ${STAR} emoji are the custom elements injected by the three scripts.</p>
    <div id="proof"></div>
  </section>

  <section class="group">
    <h2>Federation configuration on this (${htmlEscape(localSide)}) AM</h2>
    <p class="blurb">Read live from <code>${htmlEscape(amBaseUrl)}</code>: the SAML2 entities, the <code>samllab-cot</code> circle of trust, and the wired samllab scripts.</p>
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
    username,
  });
});

app.get("/probe/proof", async (req, res) => {
  const token = getAmToken(req);
  const validate = await amValidate(token);
  let username = null;
  let proof = null;
  let present = false;
  if (validate.valid) {
    const info = await amSessionInfo(token);
    if (info.raw) {
      username = info.raw.username || null;
      const props = info.raw.properties || {};
      if (props.samllabProof) {
        try {
          proof = JSON.parse(props.samllabProof);
          present = true;
        } catch (e) {
          proof = { parseError: String(e), raw: props.samllabProof };
          present = true;
        }
      }
    }
  }
  res.set("Cache-Control", "no-store");
  res.json({ side: localSide, amValid: validate.valid, username, present, proof });
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
  console.log(`app9 SAML custom-script console listening on ${port}`);
  console.log(`  side:  ${localSide}`);
  console.log(`  AM:    ${amBaseUrl} (realm ${realmName})`);
  console.log(`  IdP entity: ${idpEntityId}`);
  console.log(`  SP entity:  ${spEntityId}`);
});
