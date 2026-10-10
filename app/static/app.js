const $ = (id) => document.getElementById(id);
const SEV = {5: "Disqualifying", 4: "Hard gate", 3: "Scored", 2: "Administrative", 1: "Minor"};
const CAT = {
  bid_security: "Bid bond", insurance: "Insurance", turnover: "Turnover",
  certification: "Certification", experience: "Experience", personnel: "Key personnel",
  litigation: "Litigation", subcontracting: "Subcontracting", language: "Language",
  pricing: "Pricing", format: "Format"
};
const VCOLOR = {bid: "#0f7b46", conditional: "#9a6400", no_bid: "#a11b1b"};
const VLABEL = {bid: "BID", conditional: "CONDITIONAL", no_bid: "NO BID"};

let pollTimer = null;
let statusState = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function table(headers, rows) {
  if (!rows.length) return "";
  return `<table><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>${rows.join("")}</table>`;
}

async function api(url, options) {
  const r = await fetch(url, options);
  let data = {};
  try { data = await r.json(); } catch { /* non-json error body */ }
  if (!r.ok) {
    const err = new Error(data.detail || `Request failed (${r.status})`);
    err.status = r.status;
    throw err;
  }
  return data;
}

async function loadStatus() {
  try {
    statusState = await api("/api/status");
  } catch {
    window.location.href = "/login";
    return;
  }
  if (!statusState.user) { window.location.href = "/login"; return; }

  const u = statusState.usage;
  $("plan").textContent = u.plan === "pro" ? "PRO" : "FREE";
  $("plan").className = "badge" + (u.plan === "pro" ? " on" : "");
  $("usage").textContent = u.unlimited
    ? `${u.used} this month - unlimited`
    : `${u.used} of ${u.quota} documents this month - up to ${u.max_pages} pages`;
  $("upgrade").hidden = u.plan === "pro";

  const note = $("engineNote");
  if (!statusState.llm.enabled) {
    note.hidden = false;
    note.textContent = "Smart extraction is off on this server, so reports run on the deterministic engine only. Coverage is narrower. See the pricing page for what changes on Pro.";
  } else {
    note.hidden = true;
  }
  loadProfiles();
}

async function loadProfiles() {
  const rows = await api("/api/profiles").catch(() => []);
  const sel = $("profileName");
  const keep = sel.value;
  sel.innerHTML = '<option value="">new profile</option>' +
    rows.map((r) => `<option value="${esc(r.name)}">${esc(r.name)}</option>`).join("");
  if (keep) sel.value = keep;
}

async function loadHistory() {
  const rows = await api("/api/analyses").catch(() => []);
  const ul = $("history");
  if (!rows.length) { ul.innerHTML = '<li class="muted">none yet</li>'; return; }
  ul.innerHTML = rows.map((row) => `
    <li>
      <a href="#" data-id="${esc(row.id)}">${esc(row.filename)}</a>
      <span>${row.pages ? row.pages + " pages" : ""}</span>
      <span class="pill ${esc(row.status)}">${esc(row.status)}</span>
    </li>`).join("");
  ul.querySelectorAll("a[data-id]").forEach((a) =>
    a.addEventListener("click", (ev) => { ev.preventDefault(); openAnalysis(a.dataset.id); }));
}

function renderVerdict(v) {
  if (!v || !v.available) {
    return `<div class="verdict"><div class="muted">${esc(v ? v.note : "No verdict.")}</div></div>`;
  }
  const cov = v.coverage || {};
  const blockers = (v.blockers || []).map((b) =>
    `<li><b>p.${esc(b.page)}</b> ${esc(b.requirement)}<br><span class="muted">${esc(b.why)}</span></li>`).join("");
  const actions = (v.next_actions || []).map((a) => `<li>${esc(a)}</li>`).join("");
  return `<div class="verdict" style="border-left-color:${VCOLOR[v.verdict] || "#999"}">
    <div class="verdict-label" style="color:${VCOLOR[v.verdict] || "#555"}">${esc(VLABEL[v.verdict] || v.verdict)}</div>
    <p>${esc(v.headline)}</p>
    <div class="coverage">
      <span>Met <b>${esc(cov.met ?? 0)}</b></span>
      <span>At risk <b>${esc(cov.risk ?? 0)}</b></span>
      <span>Gap <b>${esc(cov.gap ?? 0)}</b></span>
      <span>Unknown <b>${esc(cov.unknown ?? 0)}</b></span>
      <span>Confidence <b>${esc(v.confidence ?? 0)}%</b></span>
    </div>
    ${blockers ? `<h3>Blockers</h3><ul>${blockers}</ul>` : ""}
    ${actions ? `<h3>Next actions</h3><ol>${actions}</ol>` : ""}
  </div>`;
}

function renderResult(result) {
  $("verdict").innerHTML = renderVerdict(result.verdict);
  const r = result.requirements || [];
  const gates = r.filter((x) => x.severity >= 4);
  const docs = r.filter((x) => x.document_required);

  const deadlineRows = (result.deadlines || []).slice(0, 15).map((d) =>
    `<tr><td>${esc(d.page)}</td><td class="date">${esc((d.dates || []).join(", "))}</td><td>${esc(d.sentence)}</td></tr>`);

  const gateRows = gates.slice(0, 60).map((x) =>
    `<tr><td>${esc(x.page)}</td>
     <td><span class="sev sev${esc(x.severity)}">${esc(SEV[x.severity] || x.severity)}</span></td>
     <td>${esc(x.requirement)}</td>
     <td>${esc(x.kind)}</td></tr>`);

  const docRows = docs.slice(0, 60).map((x) =>
    `<tr><td>${esc(x.page)}</td><td>${esc(x.document_required)}</td><td class="muted">${esc(x.requirement)}</td></tr>`);

  const checkRows = (result.special_checks || []).map((c) =>
    `<tr><td>${esc(c.page)}</td><td>${esc(CAT[c.check] || c.check)}</td><td>${esc(c.matched)}</td></tr>`);

  const moneyRows = (result.financial || []).slice(0, 20).map((m) =>
    `<tr><td>${esc(m.page)}</td><td>${esc(m.sentence)}</td></tr>`);

  const st = result.stats || {};
  $("tables").innerHTML = `
    <p class="muted">${esc(result.document.filename)} &middot; ${esc(result.document.pages)} pages &middot;
      ${esc(st.requirements || 0)} requirements &middot; ${esc(st.mandatory || 0)} mandatory &middot;
      quote verification ${esc(result.engine.verified_ratio)}</p>
    ${deadlineRows.length ? "<h3>Deadlines</h3>" + table(["Page", "Date", "Context"], deadlineRows) : ""}
    ${gateRows.length ? "<h3>Hard gates and disqualifying clauses</h3>" + table(["Page", "Severity", "Requirement", "Kind"], gateRows) : ""}
    ${docRows.length ? "<h3>Documents to supply</h3>" + table(["Page", "Document", "Requirement"], docRows) : ""}
    ${checkRows.length ? "<h3>Compliance checkpoints</h3>" + table(["Page", "Area", "Evidence in document"], checkRows) : ""}
    ${moneyRows.length ? "<h3>Monetary thresholds</h3>" + table(["Page", "Context"], moneyRows) : ""}`;
}

async function openAnalysis(id) {
  $("resultCard").hidden = false;
  $("verdict").innerHTML = '<div class="verdict muted">loading...</div>';
  $("tables").innerHTML = "";
  let row;
  try { row = await api(`/api/analyses/${id}`); }
  catch (err) { $("verdict").innerHTML = `<div class="verdict"><span class="muted">${esc(err.message)}</span></div>`; return; }

  if (row.status !== "done") {
    $("verdict").innerHTML =
      `<div class="verdict"><span class="pill ${esc(row.status)}">${esc(row.status)}</span>` +
      `<p class="muted">${esc(row.error || "")}</p></div>`;
    if (row.status === "queued" || row.status === "running") startPolling(id);
    return;
  }
  renderResult(row.result);
  $("expMd").href = `/api/analyses/${id}/report.md`;
  $("expHtml").href = `/api/analyses/${id}/report.html`;
  $("expSrc").href = `/api/analyses/${id}/source.pdf`;
  $("resultCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

function startPolling(id) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const row = await api(`/api/analyses/${id}`);
      if (row.status === "done" || row.status === "error") {
        clearInterval(pollTimer);
        openAnalysis(id);
        loadHistory();
      }
    } catch { clearInterval(pollTimer); }
  }, 2500);
}

$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const file = $("file").files[0];
  if (!file) { $("state").textContent = "choose a PDF first"; return; }
  const body = new FormData();
  body.append("file", file);
  body.append("profile", $("profile").value);
  body.append("profile_name", $("profileName").value);
  $("submit").disabled = true;
  $("state").textContent = "uploading, analysis runs in background...";
  try {
    const data = await api("/api/analyze", { method: "POST", body });
    $("state").textContent = `queued: ${data.id}`;
    loadHistory();
    loadStatus();
    startPolling(data.id);
  } catch (err) {
    $("state").textContent = err.status === 402 ? `${err.message} See pricing.` : `error: ${err.message}`;
    loadStatus();
  } finally {
    $("submit").disabled = false;
  }
});

$("saveProfile").addEventListener("click", async () => {
  const name = $("profileName").value.trim() || $("profile").value.slice(0, 24);
  if (!name) return;
  const body = new FormData();
  body.append("name", name);
  body.append("body", $("profile").value);
  try {
    await api("/api/profiles", { method: "POST", body });
    $("profileName").value = name;
    await loadProfiles();
    $("profileName").value = name;
    $("state").textContent = `profile "${name}" saved`;
  } catch (err) { $("state").textContent = `error: ${err.message}`; }
});

$("profileName").addEventListener("change", async () => {
  const name = $("profileName").value;
  if (!name) return;
  try {
    const row = await api(`/api/profiles/${encodeURIComponent(name)}`);
    $("profile").value = row.body;
  } catch { /* ignore */ }
});

$("upgrade").addEventListener("click", async () => {
  if (statusState && statusState.billing && statusState.billing.dev) {
    try {
      await api("/api/billing/dev-upgrade", { method: "POST" });
      $("state").textContent = "dev upgrade applied";
      loadStatus();
    } catch (err) { $("state").textContent = `error: ${err.message}`; }
    return;
  }
  const body = new FormData();
  body.append("plan_key", "monthly");
  try {
    const data = await api("/api/billing/checkout", { method: "POST", body });
    window.location.href = data.url;
  } catch (err) {
    $("state").textContent = err.status === 503
      ? "Payments are not configured yet. Set LS_API_KEY and LS_STORE_ID."
      : `error: ${err.message}`;
  }
});

$("logout").addEventListener("click", async (ev) => {
  ev.preventDefault();
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/";
});

const drop = $("drop");
drop.addEventListener("click", () => $("file").click());
$("file").addEventListener("change", () => {
  const f = $("file").files[0];
  if (f) $("filename").textContent = `${f.name} - ${(f.size / 1048576).toFixed(2)} MB`;
});
["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => {
  ev.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => {
  ev.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", (ev) => {
  const f = ev.dataTransfer.files[0];
  if (!f) return;
  const dt = new DataTransfer();
  dt.items.add(f);
  $("file").files = dt.files;
  $("filename").textContent = `${f.name} - ${(f.size / 1048576).toFixed(2)} MB`;
});

loadStatus();
loadHistory();
setInterval(loadStatus, 60000);