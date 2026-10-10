// AggTool web client. No build step, no dependencies.
"use strict";

const CFG = window.AGGTOOL_CONFIG;
const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "aggtool.tokens";
const VERIFIER_KEY = "aggtool.pkce";
const REASON_LABELS = {
  empty_code: "Blank short code",
  email_address: "Email address instead of a code",
  non_numeric_code: "Code is not numeric",
  invalid_code_length: "Not a 5 or 6 digit code",
  duplicate_code: "Duplicate code",
  missing_content_provider: "No content provider",
};

// ------------------------------------------------------------------ auth (PKCE)

function redirectUri() {
  return window.location.origin + "/";
}

function b64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function signIn() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  sessionStorage.setItem(VERIFIER_KEY, JSON.stringify({ verifier, state }));
  const params = new URLSearchParams({
    response_type: "code",
    client_id: CFG.auth.clientId,
    redirect_uri: redirectUri(),
    scope: "openid email",
    code_challenge_method: "S256",
    code_challenge: challenge,
    state,
  });
  window.location.assign(`${CFG.auth.domain}/oauth2/authorize?${params}`);
}

function signOut() {
  sessionStorage.removeItem(TOKEN_KEY);
  const params = new URLSearchParams({ client_id: CFG.auth.clientId, logout_uri: redirectUri() });
  window.location.assign(`${CFG.auth.domain}/logout?${params}`);
}

async function completeSignIn() {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("code");
  if (!code) return;
  const saved = JSON.parse(sessionStorage.getItem(VERIFIER_KEY) || "{}");
  sessionStorage.removeItem(VERIFIER_KEY);
  window.history.replaceState({}, "", redirectUri());
  if (!saved.verifier || saved.state !== url.searchParams.get("state")) {
    throw new Error("Sign-in could not be verified. Please try again.");
  }
  const resp = await fetch(`${CFG.auth.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: CFG.auth.clientId,
      code,
      redirect_uri: redirectUri(),
      code_verifier: saved.verifier,
    }),
  });
  if (!resp.ok) throw new Error("Sign-in failed. Please try again.");
  const tokens = await resp.json();
  const claims = JSON.parse(atob(tokens.id_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  sessionStorage.setItem(TOKEN_KEY, JSON.stringify({
    access: tokens.access_token,
    email: claims.email,
    exp: Date.now() + tokens.expires_in * 1000,
  }));
}

function session() {
  if (!CFG.auth.enabled) return { access: null, email: "Local mode" };
  const s = JSON.parse(sessionStorage.getItem(TOKEN_KEY) || "null");
  return s && s.exp > Date.now() + 30000 ? s : null;
}

// ------------------------------------------------------------------------- API

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  const s = session();
  if (CFG.auth.enabled) {
    if (!s) { showSignedOut(); throw new Error("Your session expired. Sign in again."); }
    headers.Authorization = `Bearer ${s.access}`;
  }
  const resp = await fetch(`${CFG.apiBase}${path}`, { ...options, headers });
  if (resp.status === 401) { sessionStorage.removeItem(TOKEN_KEY); showSignedOut(); }
  const body = resp.status === 204 ? null : await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail = body && body.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${resp.status})`);
  }
  return body;
}

async function upload(target, file) {
  const form = new FormData();
  Object.entries(target.fields).forEach(([k, v]) => form.append(k, v));
  form.append("file", file);
  const resp = await fetch(target.url, { method: "POST", body: form });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text.includes("EntityTooLarge") ? `${file.name} is too large.` : `Upload of ${file.name} failed.`);
  }
}

// -------------------------------------------------------------------------- UI

let currentJobId = null;

function setStep(name, state) {
  const steps = [...$("steps").children];
  const idx = steps.findIndex((li) => li.dataset.step === name);
  steps.forEach((li, i) => {
    li.className = i < idx ? "done" : i === idx ? state : "";
  });
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

function showSignedOut() {
  $("signed-out").hidden = false;
  $("app").hidden = true;
  $("sign-out").hidden = true;
  $("user-email").textContent = "";
}

function tile(value, label, warn) {
  const div = document.createElement("div");
  div.className = "tile" + (warn && value > 0 ? " warn" : "");
  const v = document.createElement("div");
  v.className = "value";
  v.textContent = Number(value).toLocaleString();
  const l = document.createElement("div");
  l.className = "label";
  l.textContent = label;
  div.append(v, l);
  return div;
}

function renderResult(job) {
  const s = job.summary;
  currentJobId = job.job_id;
  $("tiles").replaceChildren(
    tile(s.output_rows, "Codes in combined list"),
    tile(s.matched, "Matched to a lease holder"),
    tile(s.no_lease_holder, "On carrier, no CSCA lease", true),
    tile(s.not_on_carrier, "In CSCA, not on carrier", true),
    tile(s.registry_conflicts, "Conflicting registry rows", true),
    tile(s.dropped_rows, "Rows removed by rules"),
  );
  const reasons = Object.entries(s.dropped_by_reason || {});
  $("reasons").replaceChildren(...(reasons.length ? reasons : [["none", 0]]).map(([k, n]) => {
    const li = document.createElement("li");
    li.textContent = k === "none" ? "No rows removed." : `${REASON_LABELS[k] || k}: ${n}`;
    return li;
  }));
  $("result").hidden = false;
}

async function download(jobId, artifact) {
  try {
    const { url } = await api(`/jobs/${jobId}/download?artifact=${artifact}`);
    window.location.assign(url);
  } catch (e) { showError(e.message); }
}

async function loadHistory() {
  const jobs = await api("/jobs");
  const rows = jobs.map((j) => {
    const tr = document.createElement("tr");
    const cells = [
      new Date(j.created_at).toLocaleString(),
      j.filenames.primary,
      j.filenames.lookup,
    ].map((t) => { const td = document.createElement("td"); td.textContent = t; return td; });
    const status = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `badge ${j.status}`;
    badge.textContent = j.status.replace("_", " ").toLowerCase();
    if (j.error) badge.title = j.error;
    status.append(badge);
    const count = document.createElement("td");
    count.className = "num";
    count.textContent = j.summary ? j.summary.output_rows : "";
    const action = document.createElement("td");
    if (j.status === "SUCCEEDED") {
      const b = document.createElement("button");
      b.className = "ghost";
      b.textContent = "View";
      b.addEventListener("click", () => { renderResult(j); window.scrollTo({ top: 0, behavior: "smooth" }); });
      action.append(b);
    }
    tr.append(...cells, status, count, action);
    return tr;
  });
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "muted";
    td.textContent = "No jobs yet.";
    tr.append(td);
    rows.push(tr);
  }
  $("history").replaceChildren(...rows);
}

async function loadProfiles() {
  const profiles = await api("/profiles");
  $("profile").replaceChildren(...profiles.map((p) => {
    const o = document.createElement("option");
    o.value = p.name;
    o.textContent = p.name;
    o.title = p.description;
    return o;
  }));
  const sync = () => {
    const p = profiles.find((x) => x.name === $("profile").value);
    if (p) {
      $("primary-label").textContent = `${p.primary} (.csv or .xlsx)`;
      $("lookup-label").textContent = `${p.lookup} (.csv or .xlsx)`;
    }
  };
  $("profile").addEventListener("change", sync);
  sync();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function runJob(event) {
  event.preventDefault();
  const primary = $("primary-file").files[0];
  const lookup = $("lookup-file").files[0];
  if (!primary || !lookup) return;
  showError("");
  $("result").hidden = true;
  $("run").disabled = true;
  $("steps").hidden = false;
  try {
    setStep("create", "active");
    const created = await api("/jobs", {
      method: "POST",
      body: JSON.stringify({
        profile: $("profile").value,
        primary_filename: primary.name,
        lookup_filename: lookup.name,
      }),
    });
    const jobId = created.job.job_id;
    setStep("upload", "active");
    await Promise.all([upload(created.uploads.primary, primary), upload(created.uploads.lookup, lookup)]);
    setStep("process", "active");
    await api(`/jobs/${jobId}/submit`, { method: "POST" });
    let job;
    for (let delay = 500; ; delay = Math.min(delay * 1.5, 4000)) {
      job = await api(`/jobs/${jobId}`);
      if (job.status === "SUCCEEDED" || job.status === "FAILED") break;
      await sleep(delay);
    }
    if (job.status === "FAILED") {
      setStep("process", "failed");
      showError(job.error || "The job failed.");
    } else {
      setStep("done", "done");
      renderResult(job);
    }
  } catch (e) {
    showError(e.message);
    $("steps").querySelector(".active")?.classList.replace("active", "failed");
  } finally {
    $("run").disabled = false;
    loadHistory().catch(() => {});
  }
}

async function main() {
  $("sign-in").addEventListener("click", signIn);
  $("sign-out").addEventListener("click", signOut);
  $("job-form").addEventListener("submit", runJob);
  $("refresh").addEventListener("click", () => loadHistory().catch((e) => showError(e.message)));
  document.querySelectorAll("[data-artifact]").forEach((b) => {
    b.addEventListener("click", () => currentJobId && download(currentJobId, b.dataset.artifact));
  });

  if (CFG.auth.enabled) {
    try { await completeSignIn(); } catch (e) { showError(e.message); }
  }
  const s = session();
  if (!s) { showSignedOut(); return; }
  $("signed-out").hidden = true;
  $("app").hidden = false;
  $("user-email").textContent = s.email || "";
  $("sign-out").hidden = !CFG.auth.enabled;
  try {
    await Promise.all([loadProfiles(), loadHistory()]);
  } catch (e) { showError(e.message); }
}

main();
