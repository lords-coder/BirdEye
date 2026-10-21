/* AI Video Attendance - basic web UI (vanilla JS) */
"use strict";

/* ------------------------------------------------------------- helpers */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDT(s) {
  if (!s) return "—";
  try {
    const d = new Date(String(s).replace(" ", "T"));
    return isNaN(d) ? String(s) : d.toLocaleString();
  } catch { return String(s); }
}

/* Recognition-log timestamps: rendered in Asia/Kolkata from the BACKEND
   event time (the frame-capture moment carried in the payload), never from
   the moment this browser happened to receive the message.  The REST rows
   come back as UTC wall-clock WITHOUT an offset (SQLite drops tzinfo) while
   WebSocket events carry +00:00 - both must render the same instant, so a
   naive value is pinned to UTC before parsing instead of being read as
   browser-local time. */
function fmtIN(s) {
  if (!s) return "—";
  try {
    let raw = String(s).replace(" ", "T");
    if (!/(Z|[+-]\d{2}:?\d{2})$/.test(raw)) raw += "Z";
    const d = new Date(raw);
    if (isNaN(d)) return String(s);
    return d.toLocaleString("en-IN", {
      timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false,
    });
  } catch { return String(s); }
}

function fmtTime(s) {
  if (!s) return "—";
  try {
    const d = new Date(String(s).replace(" ", "T"));
    return isNaN(d) ? String(s) : d.toLocaleTimeString();
  } catch { return String(s); }
}

function fmtDur(sec) {
  if (sec == null) return "—";
  sec = Number(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

function localToday() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = "toast " + (kind === "error" ? "err" : kind === "ok" ? "ok" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}

/* ------------------------------------------------------------- auth/api */
const TOKEN_KEY = "iva_token";
let CURRENT_USER = null; // {username, role, security_level, ...} from /auth/me

const getToken = () => localStorage.getItem(TOKEN_KEY) || "";
const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  CURRENT_USER = null;
  try { if (ws) ws.close(); } catch {}
  // stop background work tied to the session: the reconnect timer would
  // otherwise fire after logout, and the live-view poller would keep
  // hitting the API with a dead token forever.
  clearTimeout(wsRetry);
  clearInterval(wsPing);
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  stopCameraPreview();
  try { monitorDisconnect("signed out"); } catch {}
  logLiveUnmount();
  $("#app-view").hidden = true;
  $("#nav-users").hidden = true;
  $("#nav-teacher").hidden = true;
  showAuthCard("login");
}

/* Show exactly one of the sign-in / register / forgot-password cards. */
function showAuthCard(which) {
  ["login", "register", "forgot"].forEach((n) => {
    const el = $("#" + n + "-view");
    if (el) el.hidden = n !== which;
  });
}

function setAuthMessage(kind, text) {
  const errEl = $("#login-error"), infoEl = $("#login-info");
  errEl.hidden = true; infoEl.hidden = true;
  if (!text) return;
  if (kind === "error") { errEl.textContent = text; errEl.hidden = false; }
  else { infoEl.textContent = text; infoEl.hidden = false; }
}

const authHeaders = () => {
  const h = {};
  const tok = getToken();
  if (tok) h["Authorization"] = "Bearer " + tok;
  return h;
};

async function api(path, opts = {}) {
  const headers = authHeaders();

  let body = opts.body;
  if (opts.form) {
    body = opts.form; // FormData -> browser sets multipart boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }

  const res = await fetch(path, {
    method: opts.method || (body ? "POST" : "GET"),
    headers, body,
  });

  if (res.status === 401) {
    logout();
    throw new Error("Session expired — please log in again");
  }
  const txt = await res.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch { data = { detail: txt }; }
  if (!res.ok) {
    let d = data && data.detail;
    if (Array.isArray(d)) d = d.map((e) => (e && e.msg) || JSON.stringify(e)).join("; ");
    const err = new Error(d || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function errToast(e) { toast(e && e.message ? e.message : String(e), "error"); }

/* ------------------------------------------------------------- login */
async function tryLogin(ev) {
  ev.preventDefault();
  setAuthMessage("error", "");
  try {
    const data = await api("/auth/token", {
      body: {
        username: $("#login-username").value.trim(),
        password: $("#login-password").value,
      },
    });
    setToken(data.access_token);
    enterApp();
  } catch (e) {
    setAuthMessage("error", e.message);
  }
}

async function tryRegister(ev) {
  ev.preventDefault();
  const errEl = $("#register-error");
  errEl.hidden = true;
  const pw = $("#reg-password").value;
  if (pw !== $("#reg-confirm").value) {
    errEl.textContent = "Passwords do not match";
    errEl.hidden = false;
    return;
  }
  try {
    const data = await api("/auth/register", {
      body: {
        username: $("#reg-username").value.trim(),
        password: pw,
        display_name: $("#reg-display").value.trim() || null,
      },
    });
    $("#register-form").reset();
    showAuthCard("login");
    setAuthMessage("info", `${data.detail} (account #${data.id}, status: ${data.status})`);
  } catch (e) {
    errEl.textContent = e.message;
    errEl.hidden = false;
  }
}

async function tryForgot(ev) {
  ev.preventDefault();
  const errEl = $("#forgot-error");
  errEl.hidden = true;
  try {
    const data = await api("/auth/forgot-password", {
      body: {
        username: $("#forgot-username").value.trim(),
        reason: $("#forgot-reason").value.trim() || null,
      },
    });
    $("#forgot-form").reset();
    showAuthCard("login");
    setAuthMessage("info", data.detail);
  } catch (e) {
    errEl.textContent = e.message;
    errEl.hidden = false;
  }
}

async function boot() {
  if (getToken()) { enterApp(); return; }
  // auth may be disabled (AUTH_REQUIRED=false) -> probe an authed endpoint
  try {
    await api("/students?limit=1");
    enterApp();
    return;
  } catch (e) {
    if (e && e.status === 401) { /* needs login */ }
    else if (!(e && e.message && e.message.includes("log in"))) { enterApp(); }
  }
  showAuthCard("login");
}

/* Resolve the signed-in principal so the console can gate sections by role.
   Returns the /auth/me object (or null when auth is disabled). */
async function refreshMe() {
  if (!getToken()) {
    CURRENT_USER = null;
    $("#user-label").textContent = "auth disabled";
    $("#nav-users").hidden = true;
    $("#nav-teacher").hidden = true;
    return null;
  }
  try {
    const me = await api("/auth/me");
    CURRENT_USER = me;
    const lvl = me.security_level ? ` · L${me.security_level}` : "";
    $("#user-label").textContent = `${me.username} · ${me.role}${lvl}`;
    const isSuper = me.role === "super_admin";
    const isAdmin = isSuper || me.role === "admin";
    const isTeacher = me.role === "teacher";
    // Backend enforces every rule below; this only hides dead ends.
    $("#nav-users").hidden = !isSuper;
    $("#nav-students").hidden = !isAdmin;
    $("#nav-cameras").hidden = !isAdmin;
    $("#nav-attendance").hidden = !isAdmin;
    $("#nav-periods").hidden = !isAdmin;
    $("#nav-history").hidden = !isAdmin;
    $("#nav-live").hidden = !isAdmin;
    $("#nav-monitor").hidden = !isAdmin;
    $("#nav-system").hidden = !isAdmin;
    $("#nav-teacher").hidden = !isTeacher;
    // leave a forbidden section immediately
    const active = $(".nav-btn.active");
    const cur = active ? active.dataset.section : "dashboard";
    const allowed = {
      dashboard: isAdmin, students: isAdmin, cameras: isAdmin,
      attendance: isAdmin, periods: isAdmin, history: isAdmin,
      live: isAdmin, monitor: isAdmin, system: isAdmin, users: isSuper, teacher: isTeacher,
    };
    if (allowed[cur] === false) showSection(isTeacher ? "teacher" : "dashboard");
    return me;
  } catch (e) {
    // api() already logged out on 401; anything else is non-fatal here
    if (!getToken()) return null;
    toast(e.message || String(e), "error");
    return null;
  }
}

async function enterApp() {
  // hide every auth card (showAuthCard(null) -> all hidden), otherwise the
  // sign-in card stays on top of the dashboard after a successful login
  showAuthCard(null);
  $("#app-view").hidden = false;
  $("#user-label").textContent = getToken() ? "…" : "auth disabled";
  const me = await refreshMe();
  const isTeacher = me && me.role === "teacher";
  if (isTeacher) {
    // Teacher portal landing: own classes, not the admin dashboard.
    showSection("teacher");
  } else {
    showSection("dashboard");
    wsAuthFailed = false; // fresh credentials: a previous 4401 no longer applies
    connectWS();
  }
}

/* ------------------------------------------------------------- sections */
const SECTION_TITLES = {
  dashboard: "Dashboard", students: "Students", cameras: "Cameras",
  attendance: "Attendance", periods: "Periods & attendance",
  history: "Student entry/exit history", teacher: "My classes",
  live: "Live", monitor: "Monitor", users: "Users", system: "System",
};
let liveTimer = null;

function showSection(name) {
  $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.section === name));
  $$("main section").forEach((s) => { s.hidden = s.id !== "sec-" + name; });
  $("#section-title").textContent = SECTION_TITLES[name] || name;

  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  if (name !== "monitor") monitorDisconnect("section changed");
  // Recognition-log live subscription is scoped to its page: leaving the
  // section removes the WS listener and the fallback poll timer.
  if (name !== "attendance") logLiveUnmount();
  if (name === "dashboard") loadDashboard();
  if (name === "students") loadStudents();
  if (name === "cameras") loadCameras();
  if (name === "attendance") { logLiveMount(); loadAttendance(); }
  if (name === "periods") loadPeriodsSection();
  if (name === "history") loadHistory();
  if (name === "teacher") loadTeacher();
  if (name === "live") {
    loadLive();
    liveTimer = setInterval(loadLive, 5000);
    ensureWS(); // CONNECTING/OPEN = 0/1 (skips a known-rejected token)
  }
  if (name === "monitor") loadMonitor();
  if (name === "users") loadUsers();
  if (name === "system") loadSystem();
}

/* ------------------------------------------------------------- dashboard */
async function loadDashboard() {
  try {
    const h = await api("/system/health");
    const checks = h.checks || {};
    const up = Object.values(checks).filter((v) => v === "up" || v === "online").length;
    $("#st-health").textContent = h.status === "ok" ? "OK" : "Degraded";
    $("#st-health").className = "stat-value " + (h.status === "ok" ? "ok" : "warn");
    $("#st-health-sub").textContent = `${up}/${Object.keys(checks).length} checks up · v${h.version}`;
    $("#version-label").textContent = "v" + h.version;
  } catch (e) { $("#st-health").textContent = "?"; errToast(e); }

  try {
    const g = await api("/system/gpu-status");
    const on = g.service === "online";
    $("#st-inference").textContent = on ? "Online" : "Offline";
    $("#st-inference").className = "stat-value " + (on ? "ok" : "bad");
    $("#st-inference-sub").textContent = on
      ? `${g.provider || "?"} · ${g.device_name || ""}`
      : (g.detail || "not running");
    $("#dash-gpu").innerHTML = on
      ? kvHtml(g)
      : `<span class="muted">Inference service offline — start it (or Docker stack) to enable enrollment &amp; recognition.<br>Detail: <b>${esc(g.detail || "unknown")}</b></span>`;
  } catch (e) { $("#st-inference").textContent = "?"; }

  try {
    const s = await api("/students?limit=1");
    $("#st-students").textContent = s.total;
  } catch { $("#st-students").textContent = "?"; }

  try {
    const cams = await api("/cameras");
    $("#st-cameras").textContent = cams.length;
    const running = cams.filter((c) => c.runtime && c.runtime.state === "running").length;
    $("#st-cameras-sub").textContent = `${running} running`;
  } catch { $("#st-cameras").textContent = "?"; }

  try {
    const sum = await api(`/attendance/summary?date=${localToday()}`);
    $("#st-present").textContent = sum.totals.present_students;
    $("#st-present-sub").textContent =
      `${sum.totals.ongoing_sessions} ongoing · ${sum.totals.completed_sessions} completed`;
  } catch { $("#st-present").textContent = "?"; }
}

function kvHtml(obj) {
  const skip = new Set(["throughput"]);
  return Object.entries(obj)
    .filter(([k, v]) => !skip.has(k) && v !== null && v !== "")
    .map(([k, v]) => `<span>${esc(k)}: <b>${esc(Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : v)}</b></span>`)
    .join("");
}

/* ------------------------------------------------------------- students */
let studentQuery = "";

async function loadStudents() {
  try {
    const data = await api(`/students?limit=200&q=${encodeURIComponent(studentQuery)}`);
    $("#students-count").textContent = `${data.total} total`;
    const body = $("#students-body");
    if (!data.items.length) {
      body.innerHTML = `<tr><td colspan="7" class="muted">No students yet — enroll your first one (3–5 photos).</td></tr>`;
      return;
    }
    body.innerHTML = data.items.map((s) => `
      <tr>
        <td>${s.id}</td>
        <td>${esc(s.name)}</td>
        <td>${esc(s.registration_no)}</td>
        <td>${esc(s.section || "—")}</td>
        <td>${(s.photo_paths || []).length}</td>
        <td>${s.embedding_count ? `<span class="badge green">${s.embedding_count}</span>` : `<span class="badge red">0</span>`}</td>
        <td><div class="row-actions">
          <button class="btn small" data-act="add-photos" data-id="${s.id}">+ photos</button>
          <button class="btn small danger" data-act="del-face" data-id="${s.id}">clear face</button>
          <button class="btn small danger" data-act="del" data-id="${s.id}">delete</button>
        </div></td>
      </tr>`).join("");
  } catch (e) { errToast(e); }
}

async function onStudentAction(ev) {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id, act = btn.dataset.act;
  try {
    if (act === "del") {
      if (!confirm(`Delete student #${id} and all their sessions?`)) return;
      await api(`/students/${id}`, { method: "DELETE" });
      toast("Student deleted", "ok");
      loadStudents();
    } else if (act === "del-face") {
      if (!confirm(`Clear face data for student #${id}?`)) return;
      await api(`/students/${id}/face`, { method: "DELETE" });
      toast("Face data cleared", "ok");
      loadStudents();
    } else if (act === "add-photos") {
      const input = $("#photo-input");
      input.onchange = async () => {
        if (!input.files.length) return;
        const fd = new FormData();
        for (const f of input.files) fd.append("photos", f);
        input.value = "";
        try {
          await api(`/students/${id}/photos`, { form: fd });
          toast(`Added ${fd.getAll("photos").length} photo(s)`, "ok");
          loadStudents();
        } catch (e) { errToast(e); }
      };
      input.click();
    }
  } catch (e) { errToast(e); }
}

async function submitEnrollment(ev) {
  ev.preventDefault();
  const files = $("#s-photos").files;
  if (files.length < 3 || files.length > 5) {
    toast("Enrollment needs 3–5 photos (selected: " + files.length + ")", "error");
    return;
  }
  const fd = new FormData();
  fd.append("name", $("#s-name").value.trim());
  fd.append("registration_no", $("#s-reg").value.trim());
  const section = $("#s-section").value.trim();
  if (section) fd.append("section", section);
  for (const f of files) fd.append("photos", f);

  const btn = $("#enroll-form button[type=submit]");
  btn.disabled = true;
  try {
    const s = await api("/students", { form: fd });
    toast(`Enrolled ${s.name} (${s.embedding_count} embeddings)`, "ok");
    $("#enroll-form").reset();
    $("#s-photos-count").textContent = "no files";
    $("#enroll-form").hidden = true;
    loadStudents();
  } catch (e) { errToast(e); }
  btn.disabled = false;
}

/* ------------------------------------------------------------- cameras */
function camStateBadge(c) {
  const st = (c.runtime && c.runtime.state) || "stopped";
  const cls = st === "running" ? "green" : st === "unhealthy" ? "red"
    : (st === "starting" || st === "stopping") ? "amber" : "gray";
  return `<span class="badge ${cls}">${esc(st)}</span>${c.runtime && c.runtime.slot != null ? ` <span class="muted small">#${c.runtime.slot}</span>` : ""}`;
}

function camSourceLabel(c) {
  // Escape BEFORE any slicing; host/stream_path never contain credentials.
  if (c.host) return esc(`${c.host}:${c.port || 554}${c.stream_path || ""}`);
  if (c.display_url) return esc(c.display_url).slice(0, 42);
  if (c.rtsp_url) return esc(c.rtsp_url).slice(0, 42);
  if (c.file_path) return esc(c.file_path);
  return "—";
}

function camHealthCell(c) {
  const parts = [];
  if (c.last_test_status) {
    const ok = c.last_test_status === "connected";
    parts.push(`<span class="badge ${ok ? "green" : "red"}">${esc(c.last_test_status)}</span>`);
  }
  if (c.last_frame_at) parts.push(`<span class="muted small">frame ${fmtDT(c.last_frame_at)}</span>`);
  else if (c.last_error) parts.push(`<span class="muted small" title="${esc(c.last_error)}">no frames</span>`);
  return parts.length ? parts.join("<br>") : "—";
}

let cameraPreviewTimer = null;

function stopCameraPreview() {
  if (cameraPreviewTimer) { clearInterval(cameraPreviewTimer); cameraPreviewTimer = null; }
  const img = $("#camera-preview-img");
  if (img && img.dataset.url) { try { URL.revokeObjectURL(img.dataset.url); } catch {} }
  if (img) img.removeAttribute("src");
  const card = $("#camera-preview-card");
  if (card) card.hidden = true;
}

async function openCameraPreview(c) {
  $("#camera-preview-card").hidden = false;
  $("#camera-preview-title").textContent = `Preview — ${c.name}`;
  $("#camera-preview-note").textContent = "fetching authenticated snapshot…";
  if (cameraPreviewTimer) clearInterval(cameraPreviewTimer);
  const tick = async () => {
    try {
      const res = await fetch(`/cameras/${c.id}/snapshot`, { headers: authHeaders() });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { msg = (await res.json()).detail || msg; } catch {}
        throw new Error(msg);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const img = $("#camera-preview-img");
      if (img.dataset.url) { try { URL.revokeObjectURL(img.dataset.url); } catch {} }
      img.dataset.url = url;
      img.src = url;
      $("#camera-preview-note").textContent =
        "live snapshot every ~2.5 s — credentials stay on the server";
    } catch (e) {
      $("#camera-preview-note").textContent = "preview failed: " + (e.message || e);
    }
  };
  tick();
  cameraPreviewTimer = setInterval(tick, 2500);
}

async function loadCameras() {
  try {
    const cams = await api("/cameras");
    $("#cameras-count").textContent = `${cams.length} total`;
    const body = $("#cameras-body");
    if (!cams.length) {
      body.innerHTML = `<tr><td colspan="9" class="muted">No cameras yet — register your classroom and doorway cameras.</td></tr>`;
      return;
    }
    body.innerHTML = cams.map((c) => {
      const running = c.runtime && (c.runtime.state === "running" || c.runtime.state === "starting");
      const role = c.camera_role === "classroom_interior" ? `<span class="badge green">interior</span>`
        : c.camera_role === "doorway_exterior" ? `<span class="badge amber">doorway</span>`
        : `<span class="badge gray">${esc(c.type)}</span>`;
      return `<tr>
        <td>${c.id}</td>
        <td>${esc(c.name)}<br><span class="muted small">${esc(c.location || "")}</span></td>
        <td>${esc(c.room || "—")}</td>
        <td>${role}</td>
        <td title="${esc(c.display_url || "")}">${camSourceLabel(c)}</td>
        <td>${camStateBadge(c)}</td>
        <td>${camHealthCell(c)}</td>
        <td>${c.enabled ? `<span class="badge green">on</span>` : `<span class="badge gray">off</span>`}</td>
        <td><div class="row-actions">
          ${running
            ? `<button class="btn small" data-act="stop" data-id="${c.id}">stop</button>`
            : `<button class="btn small primary" data-act="start" data-id="${c.id}" ${c.enabled ? "" : "disabled title='disabled'"}>start</button>`}
          <button class="btn small" data-act="cam-test" data-id="${c.id}">test</button>
          <button class="btn small" data-act="cam-preview" data-id="${c.id}">preview</button>
          <button class="btn small" data-act="cam-edit" data-id="${c.id}">edit</button>
          <button class="btn small" data-act="cam-toggle" data-id="${c.id}">${c.enabled ? "disable" : "enable"}</button>
          <button class="btn small danger" data-act="cam-del" data-id="${c.id}">delete</button>
        </div></td>
      </tr>`;
    }).join("");
  } catch (e) { errToast(e); }
}

async function onCameraAction(ev) {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(btn.dataset.id), act = btn.dataset.act;
  const cams = await api("/cameras").catch(() => null);
  const cam = cams ? cams.find((c) => c.id === id) : null;
  btn.disabled = true;
  try {
    if (act === "start") {
      const r = await api(`/cameras/${id}/start`, { method: "POST", body: {} });
      toast(`Camera #${id}: ${r.state}${r.slot != null ? " (slot " + r.slot + ")" : ""}`, "ok");
    } else if (act === "stop") {
      const r = await api(`/cameras/${id}/stop`, { method: "POST", body: {} });
      toast(`Camera #${id}: ${r.state}`, "ok");
    } else if (act === "cam-test") {
      toast("testing connection…");
      const r = await api(`/cameras/${id}/test`, { method: "POST", body: {} });
      toast(`${r.status}: ${r.detail}`, r.ok ? "ok" : "error");
    } else if (act === "cam-preview") {
      if (cam) openCameraPreview(cam);
    } else if (act === "cam-edit") {
      if (cam) openCameraForm(cam);
      return; // keep the form open - no reload
    } else if (act === "cam-toggle") {
      if (!cam) throw new Error("camera not found");
      await api(`/cameras/${id}`, { method: "PUT", body: { enabled: !cam.enabled } });
      toast(cam.enabled ? "camera disabled" : "camera enabled", "ok");
    } else if (act === "cam-del") {
      if (!confirm(`Delete camera #${id}?`)) return;
      await api(`/cameras/${id}`, { method: "DELETE" });
      toast("Camera deleted", "ok");
    }
    loadCameras();
  } catch (e) { errToast(e); }
  btn.disabled = false;
}

function openCameraForm(c) {
  const editing = !!c;
  $("#camera-form-title").textContent = editing ? `Edit camera #${c.id}` : "Add camera";
  $("#c-id").value = editing ? c.id : "";
  $("#c-name").value = editing ? (c.name || "") : "";
  $("#c-room").value = editing ? (c.room || "") : "";
  $("#c-role").value = editing ? (c.camera_role || "") : "";
  $("#c-type").value = editing ? (c.type || "entry") : "entry";
  $("#c-host").value = editing ? (c.host || "") : "";
  $("#c-port").value = editing && c.port ? c.port : "";
  $("#c-path").value = editing ? (c.stream_path || "") : "";
  $("#c-user").value = editing ? (c.username || "") : "";
  $("#c-pass").value = "";
  $("#c-pass").placeholder = editing && c.has_password
    ? "stored encrypted — leave blank to keep" : "stored encrypted";
  $("#c-rtsp").value = editing ? (c.rtsp_url || "") : "";
  $("#c-file").value = editing ? (c.file_path || "") : "";
  $("#c-rate").value = editing ? (c.sampling_rate || 5) : 5;
  $("#c-location").value = editing ? (c.location || "") : "";
  $("#c-notes").value = editing ? (c.notes || "") : "";
  $("#c-enabled").checked = editing ? !!c.enabled : true;
  $("#c-test-result").textContent = "";
  $("#camera-form").hidden = false;
}

async function submitCamera(ev) {
  ev.preventDefault();
  const id = $("#c-id").value;
  const password = $("#c-pass").value;
  const body = {
    name: $("#c-name").value.trim(),
    type: $("#c-type").value,
    sampling_rate: Number($("#c-rate").value) || 5,
  };
  const loc = $("#c-location").value.trim();
  const room = $("#c-room").value.trim();
  const notes = $("#c-notes").value.trim();
  const role = $("#c-role").value;
  const host = $("#c-host").value.trim();
  const user = $("#c-user").value.trim();
  if (loc) body.location = loc;
  if (room) body.room = room;
  if (notes) body.notes = notes;
  if (role) body.camera_role = role;
  if (host) body.host = host;
  const port = $("#c-port").value;
  if (port) body.port = Number(port);
  const path = $("#c-path").value.trim();
  if (path) body.stream_path = path;
  if (user) body.username = user;
  if (password) body.password = password;
  const rtsp = $("#c-rtsp").value.trim();
  if (rtsp) body.rtsp_url = rtsp;
  const file = $("#c-file").value.trim();
  if (file) body.file_path = file;
  body.enabled = $("#c-enabled").checked;
  try {
    if (id) {
      await api(`/cameras/${id}`, { method: "PUT", body });
      toast(`Camera #${id} updated`, "ok");
    } else {
      const c = await api("/cameras", { body });
      toast(`Camera "${c.name}" created (#${c.id})`, "ok");
    }
    $("#camera-form").reset();
    $("#camera-form").hidden = true;
    loadCameras();
  } catch (e) { errToast(e); }
}

async function testCameraFromForm() {
  const out = $("#c-test-result");
  out.textContent = "testing…";
  try {
    const id = $("#c-id").value;
    let r;
    if (id) {
      r = await api(`/cameras/${id}/test`, { method: "POST", body: {} });
    } else {
      const body = {};
      const host = $("#c-host").value.trim();
      const port = $("#c-port").value;
      const user = $("#c-user").value.trim();
      const pass = $("#c-pass").value;
      const path = $("#c-path").value.trim();
      const rtsp = $("#c-rtsp").value.trim();
      const file = $("#c-file").value.trim();
      if (host) body.host = host;
      if (port) body.port = Number(port);
      if (user) body.username = user;
      if (pass) body.password = pass;
      if (path) body.stream_path = path;
      if (rtsp) body.rtsp_url = rtsp;
      if (file) body.file_path = file;
      if (!Object.keys(body).length) { out.textContent = "enter a host, RTSP URL or file first"; return; }
      r = await api("/cameras/test-connection", { body });
    }
    out.textContent = `${r.status}: ${r.detail}${r.latency_ms != null ? ` (${r.latency_ms} ms)` : ""}`;
    out.className = r.ok ? "muted small ok" : "muted small error";
  } catch (e) {
    out.textContent = e.message || String(e);
    out.className = "muted small error";
  }
}

/* ------------------------------------------------------------- history */
function localToday() {
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

function fmtClock(s) {
  return s ? String(s).slice(0, 5) : s; // "09:30:00" -> "09:30"
}

let historyDefaulted = false;

async function loadHistory() {
  if (!historyDefaulted && !$("#h-from").value && !$("#h-to").value) {
    $("#h-from").value = localToday();
    $("#h-to").value = localToday();
    historyDefaulted = true;
  }
  const q = new URLSearchParams();
  const setIf = (id, key) => { const v = $(id).value; if (v) q.set(key, v); };
  setIf("#h-from", "from");
  setIf("#h-to", "to");
  setIf("#h-student", "student_id");
  setIf("#h-camera", "camera_id");
  const sec = $("#h-section").value.trim();
  if (sec) q.set("section", sec);
  if ($("#h-unresolved").checked) q.set("unresolved", "true");
  const tbody = $("#history-body");
  tbody.innerHTML = `<tr><td colspan="9" class="muted">loading…</td></tr>`;
  try {
    const data = await api("/attendance/history?" + q.toString());
    $("#h-total").textContent = data.total;
    $("#h-exits").textContent = data.totals.exits;
    $("#h-open").textContent = Math.max(0, data.totals.entries - data.totals.exits);
    $("#h-time").textContent = fmtDur(data.totals.total_seconds);
    $("#h-count").textContent = `${data.items.length} of ${data.total} shown`;
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="9" class="muted">No intervals match these filters.</td></tr>`;
      return;
    }
    tbody.innerHTML = data.items.map((h) => `<tr>
      <td>${esc(h.student.name)}<br><span class="muted small">${esc(h.student.registration_no)}</span></td>
      <td>${esc(h.student.section || "—")}</td>
      <td>${fmtDT(h.entry_time)}</td>
      <td>${h.exit_time ? fmtDT(h.exit_time) : `<span class="badge amber">open</span>`}</td>
      <td>${fmtDur(h.duration_seconds)}</td>
      <td>${h.camera_in_name ? esc(h.camera_in_name) : "—"}</td>
      <td>${h.camera_out_name ? esc(h.camera_out_name) : "—"}</td>
      <td>${h.period_name ? esc(h.period_name) : "—"}</td>
      <td>${h.unresolved
        ? `<span class="badge amber">no exit</span>`
        : h.review_status === "confirmed"
          ? `<span class="badge green">confirmed</span>`
          : `<span class="badge ${h.review_status === "needs_review" ? "red" : "gray"}">${esc(h.review_status)}</span>`}</td>
    </tr>`).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="9" class="error">${esc(e.message || e)}</td></tr>`;
  }
}

/* -------------------------------------------------------- periods + records */
let periodsCache = [];
let currentRecords = {}; // record id -> record, for the override dialog

function registerRecords(data) {
  data.records.forEach((r) => { currentRecords[r.id] = r; });
}

function machineBadge(status) {
  const cls = status === "PRESENT" ? "green"
    : status === "ABSENT" ? "red"
    : status === "NEEDS_REVIEW" ? "amber" : "gray";
  return `<span class="badge ${cls}">${esc(status)}</span>`;
}

function effectiveBadge(r) {
  if (!r.effective_status) return `<span class="badge gray">—</span>`;
  const cls = r.effective_status === "PRESENT" ? "green" : "red";
  let html = `<span class="badge ${cls}">${esc(r.effective_status)}</span>`;
  if (r.overridden) {
    html += `<br><span class="muted small" title="${esc(r.override_reason || "")}">by ${esc(r.overridden_by || "?")}</span>`;
  }
  return html;
}

function intervalsCell(r) {
  if (!r.intervals || !r.intervals.length) return `<span class="muted">none</span>`;
  return r.intervals.map((i) =>
    `${fmtTime(i.entry_time)}–${i.exit_time ? fmtTime(i.exit_time) : "open"} (${fmtDur(i.total_duration)})`
  ).join("<br>");
}

function recordRowsHtml(data) {
  registerRecords(data);
  if (!data.records.length) {
    return `<tr><td colspan="7" class="muted">No students match this period's section.</td></tr>`;
  }
  return data.records.map((r) => `<tr>
    <td>${esc(r.student.name)}${r.overridden ? ` <span class="badge amber">manual</span>` : ""}</td>
    <td>${esc(r.student.registration_no)}</td>
    <td>${fmtDur(r.presence_seconds)} <span class="muted small">/ ${fmtDur(r.threshold_seconds)} req.</span></td>
    <td>${machineBadge(r.machine_status)}</td>
    <td>${effectiveBadge(r)}</td>
    <td class="small">${intervalsCell(r)}</td>
    <td><div class="row-actions"><button class="btn small" data-act="ov" data-id="${r.id}">override</button></div></td>
  </tr>`).join("");
}

async function loadPeriodsSection() {
  try {
    const [periods, courses] = await Promise.all([api("/periods"), api("/courses")]);
    periodsCache = periods;
    // course select (period form)
    $("#p-course").innerHTML = `<option value="">— none —</option>` +
      courses.map((c) => `<option value="${c.id}">${esc(c.code)} — ${esc(c.name)}</option>`).join("");
    // period select (records pane), keep previous choice when possible
    const sel = $("#rec-period");
    const prev = sel.value;
    sel.innerHTML = `<option value="">choose…</option>` +
      periods.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    sel.value = periods.some((p) => String(p.id) === prev)
      ? prev : (periods[0] ? String(periods[0].id) : "");
    if (!$("#rec-date").value) $("#rec-date").value = localToday();
    $("#periods-count").textContent = `${periods.length} lecture(s)`;
    const tbody = $("#periods-body");
    if (!periods.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="muted">No periods yet — add a timetable lecture.</td></tr>`;
    } else {
      tbody.innerHTML = periods.map((p) => `<tr>
        <td>${p.id}</td>
        <td>${esc(p.name)}</td>
        <td>${fmtClock(p.start_time)}–${fmtClock(p.end_time)}</td>
        <td>${esc(p.section || "all")}</td>
        <td>${p.course_code ? esc(p.course_code) : "—"}</td>
        <td>${esc(p.teacher_username || "—")}</td>
        <td>${p.required_minutes ? `${p.required_minutes} min` : "default (30)"}${p.required_ratio ? ` / ${(p.required_ratio * 100).toFixed(0)}%` : ""}</td>
        <td><div class="row-actions">
          <button class="btn small" data-act="p-edit" data-id="${p.id}">edit</button>
          <button class="btn small danger" data-act="p-del" data-id="${p.id}">delete</button>
        </div></td>
      </tr>`).join("");
    }
    await loadRecords();
  } catch (e) { errToast(e); }
}

function openPeriodForm(p) {
  const editing = !!p;
  $("#period-form-title").textContent = editing ? `Edit period #${p.id}` : "Add period";
  $("#p-id").value = editing ? p.id : "";
  $("#p-name").value = editing ? p.name : "";
  $("#p-start").value = editing ? fmtClock(p.start_time) : "";
  $("#p-end").value = editing ? fmtClock(p.end_time) : "";
  $("#p-section").value = editing ? (p.section || "") : "";
  $("#p-course").value = editing && p.course_id ? String(p.course_id) : "";
  $("#p-minutes").value = editing && p.required_minutes ? p.required_minutes : "";
  $("#period-form").hidden = false;
}

async function submitPeriod(ev) {
  ev.preventDefault();
  const id = $("#p-id").value;
  const body = {
    name: $("#p-name").value.trim(),
    start_time: $("#p-start").value,
    end_time: $("#p-end").value,
    section: $("#p-section").value.trim() || null,
    required_minutes: $("#p-minutes").value ? Number($("#p-minutes").value) : null,
  };
  const courseId = $("#p-course").value;
  if (courseId) body.course_id = Number(courseId);
  else if (id) body.clear_course = true;
  try {
    if (id) {
      await api(`/periods/${id}`, { method: "PUT", body });
      toast(`Period #${id} updated`, "ok");
    } else {
      const p = await api("/periods", { body });
      toast(`Period "${p.name}" created`, "ok");
    }
    $("#period-form").reset();
    $("#period-form").hidden = true;
    loadPeriodsSection();
  } catch (e) { errToast(e); }
}

async function onPeriodAction(ev) {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(btn.dataset.id), act = btn.dataset.act;
  if (act === "p-edit") {
    const p = periodsCache.find((x) => x.id === id);
    if (p) openPeriodForm(p);
    return;
  }
  if (act === "p-del") {
    if (!confirm(`Delete period #${id}?`)) return;
    try {
      await api(`/periods/${id}`, { method: "DELETE" });
      toast("Period deleted", "ok");
      loadPeriodsSection();
    } catch (e) { errToast(e); }
  }
}

async function loadRecords() {
  const pid = $("#rec-period").value;
  const body = $("#records-body");
  if (!pid) {
    body.innerHTML = `<tr><td colspan="7" class="muted">choose a period and date</td></tr>`;
    $("#rec-window").textContent = "";
    return;
  }
  body.innerHTML = `<tr><td colspan="7" class="muted">computing from presence intervals…</td></tr>`;
  try {
    const date = $("#rec-date").value || localToday();
    const data = await api(`/attendance/period/${pid}/records?date=${encodeURIComponent(date)}`);
    currentRecords = {};
    $("#rec-window").textContent =
      `window ${fmtDT(data.window_start)} → ${fmtDT(data.window_end)}` +
      ` · threshold ${fmtDur(data.threshold_seconds)}` +
      (data.is_running ? " · in progress (provisional)" : data.is_past ? " · finished" : " · not started");
    body.innerHTML = recordRowsHtml(data);
  } catch (e) {
    body.innerHTML = `<tr><td colspan="7" class="error">${esc(e.message || e)}</td></tr>`;
  }
}

/* ------------------------------------------------------------- overrides */
let overrideTarget = null;

function openOverride(record) {
  overrideTarget = record;
  $("#ov-context").textContent =
    `${record.student.name} — ${record.period_name || "period"} · ${record.date}` +
    ` · machine: ${record.machine_status}` +
    ` · presence ${fmtDur(record.presence_seconds)} / ${fmtDur(record.threshold_seconds)}`;
  $("#ov-status").value = record.effective_status || "";
  $("#ov-reason").value = record.override_reason || "";
  $("#ov-error").hidden = true;
  $("#override-modal").hidden = false;
  $("#ov-status").focus();
}

function onRecordAction(ev) {
  const btn = ev.target.closest("button[data-act='ov']");
  if (!btn) return;
  const rec = currentRecords[Number(btn.dataset.id)];
  if (rec) openOverride(rec);
}

async function submitOverride(ev) {
  ev.preventDefault();
  const err = $("#ov-error");
  err.hidden = true;
  const status = $("#ov-status").value;
  const reason = $("#ov-reason").value.trim();
  if (!status) {
    err.textContent = "choose PRESENT or ABSENT";
    err.hidden = false;
    return;
  }
  if (!reason) {
    err.textContent = "a reason is required";
    err.hidden = false;
    return;
  }
  try {
    await api(`/attendance/records/${overrideTarget.id}/override`, { body: { status, reason } });
    toast("Attendance corrected — recorded in the audit history", "ok");
    $("#override-modal").hidden = true;
    if (!$("#sec-periods").hidden) loadRecords();
    else if (!$("#sec-teacher").hidden) loadTeacher();
  } catch (e) {
    err.textContent = e.message || String(e);
    err.hidden = false;
  }
}

/* --------------------------------------------------------------- teacher */
async function loadTeacher() {
  const box = $("#teacher-courses");
  const date = $("#t-date").value || localToday();
  if (!$("#t-date").value) $("#t-date").value = date;
  box.innerHTML = `<div class="card muted">loading…</div>`;
  try {
    const courses = await api("/courses");
    if (!courses.length) {
      box.innerHTML = `<div class="card muted">No courses are assigned to you yet — an administrator assigns courses to your account.</div>`;
      return;
    }
    currentRecords = {};
    // Backend stores dates in Asia/Kolkata; Mon=bit0 … Sun=bit6
    const weekdayBit = 1 << ((new Date().getDay() + 6) % 7);
    const parts = [];
    const targets = [];
    for (const c of courses) {
      const periods = await api(`/courses/${c.id}/periods`);
      parts.push(`<div class="card">
        <div class="card-head"><h3>${esc(c.code)} — ${esc(c.name)}</h3>
        <span class="muted small">section ${esc(c.section || "all")}</span></div>`);
      if (!periods.length) parts.push(`<p class="muted small">no lectures scheduled for this course</p>`);
      for (const p of periods) {
        const scheduled = (p.days_bitmask & weekdayBit) !== 0;
        parts.push(
          `<p class="small"><strong>${esc(p.name)}</strong> · ${fmtClock(p.start_time)}–${fmtClock(p.end_time)}` +
          (scheduled ? "" : ` <span class="muted">(not today)</span>`) + `</p>` +
          `<div id="t-rec-${p.id}"><span class="muted small">loading…</span></div>`
        );
        targets.push({ id: p.id, scheduled });
      }
      parts.push(`</div>`);
    }
    box.innerHTML = parts.join("");
    for (const { id, scheduled } of targets) {
      const el = document.getElementById(`t-rec-${id}`);
      if (!el) continue;
      if (!scheduled) {
        el.innerHTML = `<span class="muted small">not scheduled today</span>`;
        continue;
      }
      try {
        const data = await api(`/attendance/period/${id}/records?date=${encodeURIComponent(date)}`);
        registerRecords(data);
        if (!data.records.length) {
          el.innerHTML = `<span class="muted small">no students for this lecture</span>`;
          continue;
        }
        el.innerHTML = `<table><thead><tr><th>Student</th><th>Presence</th><th>Machine</th><th>Effective</th><th></th></tr></thead>
          <tbody>${data.records.map((r) => `<tr>
            <td>${esc(r.student.name)}<br><span class="muted small">${esc(r.student.registration_no)}</span></td>
            <td>${fmtDur(r.presence_seconds)} <span class="muted small">/ ${fmtDur(r.threshold_seconds)}</span></td>
            <td>${machineBadge(r.machine_status)}</td>
            <td>${effectiveBadge(r)}</td>
            <td><div class="row-actions"><button class="btn small" data-act="ov" data-id="${r.id}">override</button></div></td>
          </tr>`).join("")}</tbody></table>
          <p class="muted small">${data.is_running ? "in progress — provisional" : data.is_past ? "finished" : "not started"} · effective status reflects any correction you or an administrator applied</p>`;
      } catch (e) {
        el.innerHTML = `<span class="error">${esc(e.message || e)}</span>`;
      }
    }
  } catch (e) {
    box.innerHTML = `<div class="card"><p class="error">${esc(e.message || e)}</p></div>`;
  }
}

/* ------------------------------------------------------------- attendance */
async function loadAttendance() {
  const date = $("#att-date").value || localToday();
  const absent = $("#att-absent").checked ? "&include_absent=true" : "";
  try {
    const sum = await api(`/attendance/summary?date=${date}${absent}`);
    $("#att-present").textContent = sum.totals.present_students;
    $("#att-ongoing").textContent = sum.totals.ongoing_sessions;
    $("#att-completed").textContent = sum.totals.completed_sessions;
    $("#att-hours").textContent = fmtDur(sum.totals.total_seconds);
    $("#summary-count").textContent = `${sum.students.length} rows · ${date}`;
    const body = $("#summary-body");
    body.innerHTML = sum.students.length ? sum.students.map((r) => `
      <tr>
        <td>${esc(r.name)}</td>
        <td>${esc(r.registration_no)}</td>
        <td>${esc(r.section || "—")}</td>
        <td>${r.entries}</td>
        <td>${r.exits}</td>
        <td>${fmtDT(r.first_entry)}</td>
        <td>${fmtDT(r.last_exit)}</td>
        <td>${fmtDur(r.total_seconds)}</td>
        <td>${r.ongoing ? `<span class="badge green">inside</span>` : `<span class="badge gray">out</span>`}</td>
      </tr>`).join("")
      : `<tr><td colspan="9" class="muted">No attendance recorded for ${esc(date)}.</td></tr>`;
  } catch (e) { errToast(e); }

  // The recognition-log table is owned by the realtime module below: this
  // same fetch renders the newest page, seeds the id cursor, and the page
  // then keeps itself updated from the WebSocket (cursor poll only as
  // fallback).  Refresh button and section entry share this path.
  await logLiveLoadFull();
}

/* ------------------------------------------------------------- monitor */
/* Live CCTV monitor: video comes ONLY from the running pipeline's shared
   publication (GET /cameras/{id}/live.mjpeg, Authorization header, parsed
   here - no query tokens, no second capture, no substitute clips). */
const mon = {
  cameraId: null, aborter: null, reading: false, statusTimer: null,
  retryTimer: null, retryDelayMs: 3000,
  lastFrameTs: 0, lastPartCam: null, frames: 0, reconnects: 0,
  frameTimes: [], // monotonic arrival times -> real displayed FPS
  // An annotated frame is allowed to replace a slightly newer raw frame so
  // the recognition overlay actually lands on screen.  Bounded, so video
  // never jumps backwards by more than this.
  overlayMaxLagMs: 600,
  present: new Map(), // student_id -> {name, reg, state, ts}
};

function monSetStatus(kind, text) {
  // kind: on | off | warn — text always carries the meaning too (not color-only)
  const dot = $("#mon-dot");
  if (dot) dot.className = "dot " + (kind === "on" ? "on" : kind === "warn" ? "warn" : "off");
  const el = $("#mon-status");
  if (el) el.textContent = text;
}

function monClearVideo(reason) {
  const img = $("#mon-video");
  if (img) {
    if (img.dataset.url) { try { URL.revokeObjectURL(img.dataset.url); } catch {} delete img.dataset.url; }
    img.removeAttribute("src");
  }
  mon.lastFrameTs = 0;
  mon.lastPartCam = null;
  if (reason) {
    const sub = $("#mon-sub");
    if (sub) sub.textContent = reason;
  }
}

function monitorDisconnect(reason) {
  // Hard stop: abort fetch, stop timers, release the object URL so no stale
  // frame survives a camera switch or page change.
  if (mon.aborter) { try { mon.aborter.abort(); } catch {} mon.aborter = null; }
  mon.reading = false;
  if (mon.statusTimer) { clearInterval(mon.statusTimer); mon.statusTimer = null; }
  if (mon.retryTimer) { clearTimeout(mon.retryTimer); mon.retryTimer = null; }
  mon.retryDelayMs = 3000;
  mon.frameTimes = [];
  monClearVideo(reason || null);
}

async function loadMonitor() {
  try {
    const cams = await api("/cameras");
    const sel = $("#mon-camera");
    const prev = sel.value || (mon.cameraId != null ? String(mon.cameraId) : "");
    sel.innerHTML = `<option value="">choose…</option>` + cams.map((c) =>
      `<option value="${c.id}">${esc(c.name)}${c.room ? " · " + esc(c.room) : ""}</option>`).join("");
    sel.value = [...sel.options].some((o) => o.value === prev) ? prev : "";
    mon.cameraId = sel.value ? Number(sel.value) : null;
    const title = $("#mon-title");
    const chosen = cams.find((c) => String(c.id) === sel.value);
    if (title) title.textContent = chosen ? `Monitor — ${chosen.name}` : "Monitor";
    if (mon.cameraId) monitorConnect();
    else { monitorDisconnect(null); monSetStatus("off", "select a camera"); }
  } catch (e) { monSetStatus("off", "cannot load cameras: " + (e.message || e)); }
}

function monitorSelect() {
  const v = $("#mon-camera").value;
  mon.cameraId = v ? Number(v) : null;
  mon.present.clear();
  renderMonPresent();
  const box = $("#mon-events");
  if (box) box.innerHTML = `<div class="muted">waiting for events…</div>`;
  if (mon.cameraId) monitorConnect();
  else { monitorDisconnect(null); monSetStatus("off", "select a camera"); }
}

async function monitorConnect() {
  const id = mon.cameraId;
  if (!id) return;
  monitorDisconnect(null); // never two readers for one page
  monSetStatus("warn", "connecting…");
  const sub = $("#mon-sub");
  if (sub) sub.textContent = "Opening live stream…";
  // Pre-check runtime state so a stopped camera reports WHY (not a spinner).
  try {
    const st = await api(`/cameras/${id}/live-status`);
    if (!st.running) {
      monSetStatus("off", `offline — pipeline is ${st.state || "stopped"} (press Start)`);
      if (sub) sub.textContent = "Camera pipeline is not running. Press Start, then Retry.";
      return;
    }
    if (!st.has_frame) {
      if (sub) sub.textContent = st.error || "Pipeline running, waiting for first frame…";
    }
  } catch (e) {
    // Transient network failure: back off and retry rather than leaving the
    // monitor permanently dead while the pipeline is healthy.
    monSetStatus("warn", "status check failed — retrying…");
    scheduleMonRetry(id, "status check failed: " + (e.message || e));
    return;
  }
  const aborter = new AbortController();
  mon.aborter = aborter;
  mon.reading = true;
  mon.statusTimer = setInterval(() => monitorPollStatus(id), 5000);
  monitorPollStatus(id);
  let res;
  try {
    res = await fetch(`/cameras/${id}/live.mjpeg`, {
      headers: authHeaders(), signal: aborter.signal,
    });
  } catch (e) {
    if (aborter.signal.aborted) return; // intentional disconnect/switch
    monSetStatus("warn", "connection failed — retrying…");
    scheduleMonRetry(id, "fetch failed");
    return;
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { const j = await res.json(); if (j.detail) detail = j.detail; } catch {}
    if (res.status === 401 || res.status === 403) {
      monSetStatus("off", "permission denied — sign in as admin");
      if (sub) sub.textContent = detail;
    } else if (res.status === 409) {
      monSetStatus("off", "offline — " + detail);
      if (sub) sub.textContent = detail;
    } else {
      monSetStatus("warn", detail + " — retrying…");
      scheduleMonRetry(id, detail);
    }
    return;
  }
  const ctype = res.headers.get("content-type") || "";
  if (!ctype.includes("multipart/x-mixed-replace")) {
    monSetStatus("warn", "unexpected content type — retrying…");
    scheduleMonRetry(id, "bad content type: " + ctype);
    return;
  }
  monSetStatus("on", "live");
  if (sub) sub.textContent = "Live — annotated pipeline frames (green = recognized, amber = unverified).";
  monitorReadLoop(id, res.body.getReader(), aborter);
}

function scheduleMonRetry(id, why) {
  // Bounded exponential backoff (3s -> 15s): a flaky network must not hammer
  // the API, and ONE failed reconnect must never leave the monitor dead
  // while the pipeline is still healthy.
  if (mon.cameraId !== id) return; // a different camera owns the view now
  mon.reading = false;
  mon.reconnects += 1;
  if (mon.retryTimer) clearTimeout(mon.retryTimer);
  const delay = mon.retryDelayMs;
  mon.retryDelayMs = Math.min(15000, Math.round(mon.retryDelayMs * 2));
  const sub = $("#mon-sub");
  if (sub) sub.textContent = `Reconnecting in ${Math.round(delay / 1000)}s${why ? " — " + why : ""}`;
  mon.retryTimer = setTimeout(() => {
    mon.retryTimer = null;
    if (mon.cameraId === id && !mon.reading) monitorConnect();
  }, delay);
}

function parseMonPart(buf) {
  // buf: bytes of one "--frame ... \r\n\r\n <jpeg> \r\n" part (boundary stripped by caller)
  const hdrEnd = indexOfBytes(buf, [13, 10, 13, 10]);
  if (hdrEnd < 0) return null;
  const hdr = new TextDecoder().decode(buf.slice(0, hdrEnd)).toLowerCase();
  const mLen = hdr.match(/content-length:\s*(\d+)/);
  const mTs = hdr.match(/x-frame-ts:\s*([0-9.]+)/);
  const mCam = hdr.match(/x-camera-id:\s*(\d+)/);
  const mKind = hdr.match(/x-frame-kind:\s*([a-z]+)/);
  if (!mLen) return null;
  const start = hdrEnd + 4;
  const len = Number(mLen[1]);
  if (buf.length < start + len) return null; // incomplete
  return {
    jpeg: buf.slice(start, start + len),
    ts: mTs ? Number(mTs[1]) : 0,
    cam: mCam ? Number(mCam[1]) : null,
    annotated: mKind ? mKind[1] === "annotated" : true, // legacy = annotated
    total: start + len + 2, // trailing CRLF
  };
}

function indexOfBytes(buf, seq) {
  outer: for (let i = 0; i + seq.length <= buf.length; i++) {
    for (let j = 0; j < seq.length; j++) if (buf[i + j] !== seq[j]) continue outer;
    return i;
  }
  return -1;
}

function monFpsText() {
  // Real displayed FPS from frame-arrival times (not the server's claim).
  const t = mon.frameTimes;
  if (t.length < 3) return "live";
  const fps = (t.length - 1) / ((t[t.length - 1] - t[0]) / 1000);
  return `live — ${fps.toFixed(1)} fps`;
}

async function monitorReadLoop(id, reader, aborter) {
  const boundary = new TextEncoder().encode("--frame");
  let buf = new Uint8Array(0);
  let gotFrame = false;
  const append = (chunk) => {
    const next = new Uint8Array(buf.length + chunk.length);
    next.set(buf); next.set(chunk, buf.length); buf = next;
  };
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (aborter.signal.aborted || mon.cameraId !== id) break;
      append(value);
      // consume all complete parts currently buffered (latest-frame wins: if
      // we fell behind, older parts are skipped by taking the last one)
      let last = null;
      for (;;) {
        const at = indexOfBytes(buf, boundary);
        if (at < 0) { if (buf.length > 2_000_000) buf = buf.slice(-200000); break; }
        buf = buf.slice(at + boundary.length);
        // skip boundary line ending (\r\n or -- end marker)
        if (buf[0] === 45 && buf[1] === 45) break; // closing delimiter
        const part = parseMonPart(buf);
        if (!part) break; // wait for more bytes
        buf = buf.slice(part.total);
        last = part;
      }
      if (last) {
        // stale-switch guards: wrong camera, or older than shown frame
        if (last.cam !== null && last.cam !== id) continue;
        // A fresher frame always wins.  An ANNOTATED frame may additionally
        // replace a marginally newer raw frame (bounded by overlayMaxLagMs)
        // so the recognition overlay actually lands on screen - it is always
        // the very frame its own labels belong to, never a relabelled newer
        // one, and the picture never jumps backwards by more than the bound.
        const newer = !last.ts || last.ts > mon.lastFrameTs;
        const overlay = !!last.annotated && mon.lastFrameTs > 0 && !!last.ts &&
          (mon.lastFrameTs - last.ts) * 1000 <= mon.overlayMaxLagMs;
        if (!newer && !overlay) continue;
        mon.lastFrameTs = Math.max(mon.lastFrameTs, last.ts || 0);
        mon.lastPartCam = last.cam;
        const url = URL.createObjectURL(new Blob([last.jpeg], { type: "image/jpeg" }));
        const img = $("#mon-video");
        if (img.dataset.url) { try { URL.revokeObjectURL(img.dataset.url); } catch {} }
        img.dataset.url = url;
        img.src = url;
        mon.frames += 1;
        mon.retryDelayMs = 3000; // healthy stream: reset the backoff
        mon.frameTimes.push(performance.now());
        if (mon.frameTimes.length > 30) mon.frameTimes.shift();
        gotFrame = true;
        monSetStatus("on", monFpsText());
      }
    }
  } catch (e) {
    if (aborter.signal.aborted || mon.cameraId !== id) return;
  }
  if (mon.cameraId !== id) return; // switched away; a new loop owns the view
  mon.reading = false;
  if (!gotFrame) {
    // Stream ended before any frame: ask the status endpoint WHY (stopped?
    // source died?) instead of spinning.
    monitorPollStatus(id);
    monSetStatus("warn", "stream ended before first frame — retrying…");
  } else {
    monSetStatus("warn", "reconnecting…");
  }
  scheduleMonRetry(id, "stream ended");
}

async function monitorPollStatus(id) {
  if (mon.cameraId !== id) return;
  try {
    const st = await api(`/cameras/${id}/live-status`);
    const v = $("#mon-viewers");
    if (v) v.textContent = st.viewers ? `${st.viewers} viewer(s)` : "";
    if (!st.running) {
      monSetStatus("off", `offline — pipeline is ${st.state || "stopped"}`);
      return;
    }
    if (st.infer_ok === false || st.error) {
      monSetStatus("warn", "live — inference unavailable (" + (st.error || "no detail") + ")");
      return;
    }
    if (!st.has_frame) {
      monSetStatus("warn", "connecting — waiting for first frame…");
      return;
    }
    const age = st.frame_age_s;
    if (age != null && age > 12) {
      monSetStatus("warn", `reconnecting — last frame ${age}s ago`);
      return;
    }
    // Picture and recognition are decoupled now: surface the split whenever
    // they actually differ, so "laggy video" vs "slow inference" is visible.
    const sub = $("#mon-sub");
    if (sub && st.video_age_s != null && st.frame_age_s != null &&
        Math.abs(st.video_age_s - st.frame_age_s) >= 1.0) {
      sub.textContent = `Picture ${st.video_age_s}s old (${st.video_kind || "raw"}) · last recognition ${st.frame_age_s}s old`;
    }
    // don't stomp a healthy "live" set by the frame loop
  } catch (e) { /* status poll is advisory; the frame loop owns state */ }
}

function renderMonPresent() {
  const box = $("#mon-present");
  if (!box) return;
  if (!mon.present.size) {
    box.innerHTML = `<span class="muted">waiting for recognitions…</span>`;
  } else {
    const rows = [...mon.present.values()]
      .sort((a, b) => b.ts - a.ts)
      .map((p) => `<div class="event"><span class="ev-type">${esc(p.name)}</span>
        <span class="ev-time">${esc(p.reg || "")} · ${esc(p.state)} · ${new Date(p.ts).toLocaleTimeString()}</span></div>`)
      .join("");
    box.innerHTML = rows;
  }
  const c = $("#mon-present-count");
  if (c) c.textContent = mon.present.size ? `${mon.present.size} seen` : "";
}

function monPushEvent(ev) {
  if (mon.cameraId == null) return;
  const cam = ev.camera || {};
  if (cam.id !== mon.cameraId) return; // this monitor shows ONE camera
  const box = $("#mon-events");
  if (box && box.querySelector(".muted")) box.innerHTML = "";
  const student = ev.student || {};
  const when = ev.ts ? new Date(ev.ts).toLocaleTimeString() : new Date().toLocaleTimeString();
  let line = null, cls = "event";
  if (ev.type === "recognition") {
    const action = ev.session_action || "";
    line = `<span class="ev-type">RECOGNIZED ${esc(student.name || "")}</span>
      <span class="ev-time">${when} · ${esc(action)}</span>`;
    if (student.id != null) {
      mon.present.set(student.id, {
        name: student.name || `#${student.id}`, reg: student.registration_no || "",
        state: action.includes("exit") ? "EXIT recorded" : "IN CLASS", ts: Date.now(),
      });
      renderMonPresent();
    }
  } else if (ev.type === "presence_state") {
    const to = ev.to_state || "";
    line = `<span class="ev-type">${esc(to)}</span>
      <span class="ev-time">${esc(student.name || "")} · ${when}</span>`;
    if (student.id != null && (to === "INSIDE" || to === "OUTSIDE")) {
      const prev = mon.present.get(student.id);
      if (to === "OUTSIDE") mon.present.delete(student.id);
      else mon.present.set(student.id, {
        name: student.name || `#${student.id}`, reg: student.registration_no || "",
        state: "IN CLASS", ts: Date.now(),
      });
      if (prev || to === "INSIDE") renderMonPresent(); else renderMonPresent();
    }
    if (to === "OUTSIDE" && (ev.session_action || "").includes("exit")) {
      line = `<span class="ev-type">EXIT · ${esc(student.name || "")}</span>
        <span class="ev-time">${when} (recorded event time)</span>`;
    }
  } else if (ev.type === "unknown_face" || (ev.event || "").includes("unknown")) {
    line = `<span class="ev-type">UNVERIFIED face</span><span class="ev-time">${when}</span>`;
  } else if (ev.type === "camera_state" || ev.type === "status") {
    return; // global feed noise, not a camera event
  } else if (ev.type) {
    return; // ignore anything else on the monitor panel
  } else return;
  if (line && box) {
    const div = document.createElement("div");
    div.className = cls;
    div.innerHTML = line;
    box.prepend(div);
    while (box.children.length > 40) box.lastChild.remove();
  }
}

async function monStartStop(start) {
  const id = mon.cameraId;
  if (!id) { toast("Select a camera first", "error"); return; }
  try {
    const r = await api(`/cameras/${id}/${start ? "start" : "stop"}`, { method: "POST", body: {} });
    toast(`Camera #${id}: ${r.state}`, "ok");
    if (start) { mon.reconnects = 0; monitorConnect(); }
    else monitorDisconnect("pipeline stopped — press Start to resume.");
    if (!start) monSetStatus("off", "offline — pipeline stopped");
  } catch (e) { errToast(e); }
}

/* ------------------------------------------------------------- live */
async function loadLive() {
  try {
    const live = await api("/attendance/live");
    $("#live-count").textContent = `${live.count} ongoing`;
    $("#live-body").innerHTML = live.sessions.length ? live.sessions.map((s) => `
      <tr>
        <td>${esc(s.student && (s.student.name || s.student.registration_no)) || "#" + (s.session_id ?? "?")}</td>
        <td>${fmtDT(s.entry_time)}</td>
        <td><span class="badge green">${fmtDur(s.elapsed_seconds)}</span></td>
        <td>${esc(s.camera_in_name || "—")}</td>
      </tr>`).join("")
      : `<tr><td colspan="4" class="muted">Nobody inside right now.</td></tr>`;
  } catch (e) { /* avoid toast spam on 5s timer */ console.warn(e); }
}

/* ------------------------------------- realtime recognition log -------- */
/* The Recognition Logs table follows the database while the page is open:
   every persisted audit row is published on the existing authenticated feed
   as `recognition_log` (log_id = the row primary key) and inserted as it
   arrives.  A single bounded cursor poll (`/attendance/logs?since_id=`)
   covers whatever the socket could not deliver - a closed socket, a Redis
   outage, a slow tab, a reconnect gap.  Dedupe is by row id, so event +
   poll + backlog replay always renders exactly one row.  Pure consumer: no
   camera, no RTSP, no recognition worker is touched by any of this. */

const LOG_POLL_MS = 4000;       // fallback cadence while the socket is down
const LOG_POLL_MAX_MS = 30000;  // backoff ceiling while the API is failing
const LOG_ROWS_MAX = 200;       // rendered rows (same page size as before)
const LOG_SEEN_MAX = 800;       // remembered ids (> rendered rows + backlog)
const LOG_PENDING_MAX = 300;    // buffered rows while the user reads history
const LOG_CATCHUP_MAX = 10;     // cursor pages per reconciliation pass

const logLive = {
  on: false,             // page mounted (listener + loop active)
  cursor: null,          // highest recognition_logs.id already shown
  seen: new Set(),       // ids rendered/queued - dedupe across all paths
  seenOrder: [],         // FIFO of ids for bounded Set trimming
  total: null,           // row count reported by the API ("N of total")
  pending: [],           // rows buffered while the user reads history
  unsub: null,           // WS listener handle
  pollTimer: null,       // the ONE loop (setTimeout chain, cleared on unmount)
  polling: false,        // in-flight guard: never two overlapping requests
  fails: 0,              // consecutive failures -> wider interval
  busDown: false,        // server told us the event bus is degraded
  reconcile: false,      // fill a delivery gap on the next cycle
  refetchQueued: false,  // re-render the authoritative page next cycle
  userRequested: false,  // next cycle's failure deserves a toast
};

function logLiveSetStatus(kind, text) {
  const dot = $("#logs-dot");
  if (dot) dot.className = "dot " + (kind === "on" ? "on" : kind === "warn" ? "warn" : "off");
  const el = $("#logs-status");
  if (el) el.textContent = text;
}

function logLiveRefreshStatus() {
  if (!logLive.on) return;
  if (wsAuthFailed) { logLiveSetStatus("off", "live feed needs an admin login"); return; }
  if (wsIsOpen() && !logLive.busDown) { logLiveSetStatus("on", "connected — live"); return; }
  if (logLive.busDown) { logLiveSetStatus("warn", "event bus unavailable — polling…"); return; }
  logLiveSetStatus("off", logLive.fails ? "disconnected — polling with backoff" : "reconnecting…");
}

function logLiveRemember(id) {
  if (logLive.seen.has(id)) return false;
  logLive.seen.add(id);
  logLive.seenOrder.push(id);
  while (logLive.seenOrder.length > LOG_SEEN_MAX) logLive.seen.delete(logLive.seenOrder.shift());
  return true;
}

/* Row markup shared by the initial page fetch and live inserts, so a
   WebSocket row is pixel-identical to a polled one. */
function logLiveRowHtml(l) {
  const id = Number(l.id);
  return `<tr${Number.isFinite(id) ? ` data-id="${id}"` : ""}>
        <td>${fmtIN(l.timestamp)}</td>
        <td>${esc(l.student_name || (l.student_id != null ? "#" + l.student_id : "—"))}</td>
        <td>${esc(l.camera_name || (l.camera_id != null ? "#" + l.camera_id : "—"))}</td>
        <td>${l.confidence_score != null ? Number(l.confidence_score).toFixed(3) : "—"}</td>
        <td>${l.gpu_inference_time_ms != null ? Number(l.gpu_inference_time_ms).toFixed(1) : "—"}</td>
      </tr>`;
}

function logLiveRowFromEvent(ev) {
  return {
    id: Number(ev.log_id),
    timestamp: ev.ts, // backend frame-capture time, never browser receive time
    student_name: ev.student && ev.student.name,
    student_id: ev.student ? ev.student.id : null,
    camera_name: ev.camera && ev.camera.name,
    camera_id: ev.camera ? ev.camera.id : null,
    confidence_score: ev.confidence,
    gpu_inference_time_ms: ev.inference_ms,
  };
}

/* One row, at its id-ordered position (table is newest-first).  Scanning
   from the top keeps strict order even when two cameras interleave ids or
   an event and a poll race each other. */
function logLiveInsertRow(item) {
  const tbody = $("#logs-body");
  if (!tbody) return;
  tbody.querySelectorAll("tr:not([data-id])").forEach((r) => r.remove()); // placeholders
  const tpl = document.createElement("template");
  tpl.innerHTML = logLiveRowHtml(item).trim();
  const row = tpl.content.firstElementChild;
  if (!row) return;
  let ref = null;
  const id = Number(item.id);
  if (Number.isFinite(id)) {
    for (const r of tbody.rows) {
      const rid = Number(r.dataset.id);
      if (Number.isFinite(rid) && rid < id) { ref = r; break; }
    }
  }
  tbody.insertBefore(row, ref);
  while (tbody.rows.length > LOG_ROWS_MAX) tbody.deleteRow(tbody.rows.length - 1);
}

function logLiveUpdateCount() {
  const body = $("#logs-body");
  const n = body ? body.querySelectorAll("tr[data-id]").length : 0;
  $("#logs-count").textContent = `${n} of ${logLive.total ?? "?"}`;
}

function logLiveShowBadge() {
  const btn = $("#logs-new");
  if (!btn) return;
  if (!logLive.pending.length) { btn.hidden = true; btn.textContent = ""; return; }
  btn.hidden = false;
  btn.textContent = `${logLive.pending.length} new event${logLive.pending.length === 1 ? "" : "s"} — show`;
}

function logLiveFlushPending() {
  logLive.pending.splice(0).forEach((item) => logLiveInsertRow(item));
  logLiveShowBadge();
  logLiveUpdateCount();
}

/* Follow mode: while the newest row is on screen, insert straight away.
   Once the user scrolls up into history, buffer instead - their reading
   position must never be yanked by rows appearing above them. */
function logLiveAtNewest() {
  const tbody = $("#logs-body");
  const first = tbody && tbody.querySelector("tr[data-id]");
  if (!first) return true; // empty table: everything is "newest"
  return first.getBoundingClientRect().top >= 0;
}

function logLiveAdd(item) {
  const id = Number(item.id);
  if (!Number.isFinite(id) || !logLiveRemember(id)) return; // duplicate delivery
  if (logLive.cursor == null || id > logLive.cursor) logLive.cursor = id;
  logLive.total = (logLive.total ?? 0) + 1;
  if (logLive.on && !logLiveAtNewest()) {
    logLive.pending.push(item);
    while (logLive.pending.length > LOG_PENDING_MAX) logLive.pending.shift();
    logLiveShowBadge();
  } else {
    logLiveInsertRow(item);
  }
  logLiveUpdateCount();
}

/* Authoritative page: newest rows, one paint, reseeds cursor/seen/total. */
async function logLiveFetchFull() {
  const data = await api("/attendance/logs?limit=200");
  const items = (data && data.items) || [];
  logLive.seen.clear();
  logLive.seenOrder = [];
  logLive.pending = [];
  logLive.total = data && data.total != null ? Number(data.total) : items.length;
  let max = 0;
  items.forEach((i) => {
    const id = Number(i.id) || 0;
    if (id > max) max = id;
    // Re-seed the dedupe set with exactly what this render put on screen.
    // The WebSocket backlog replays the newest events on every reconnect -
    // without this, rows rendered here would be inserted a SECOND time.
    if (id > 0) logLiveRemember(id);
  });
  logLive.cursor = max; // empty table => 0 => "everything is newer"
  const body = $("#logs-body");
  if (body) {
    body.innerHTML = items.length
      ? items.map((l) => logLiveRowHtml(l)).join("")
      : `<tr><td colspan="5" class="muted">No recognition events yet.</td></tr>`;
  }
  logLiveUpdateCount();
  logLiveShowBadge();
  return items.length;
}

/* Cursor page: only rows the caller has not seen, oldest-first. */
async function logLiveFetchSince() {
  const data = await api(
    `/attendance/logs?since_id=${encodeURIComponent(logLive.cursor ?? 0)}&limit=100`
  );
  const items = (data && data.items) || [];
  for (const it of [...items].reverse()) logLiveAdd(it);
  if (data && data.total != null) logLive.total = Number(data.total); // authoritative
  return { count: items.length, hasMore: !!(data && data.has_more) };
}

/* Single worker for every refresh path (mount, WS event, reconnect,
   fallback tick): decides between "do nothing" (socket healthy), a cursor
   catch-up, or a full re-render.  Always one request in flight. */
async function logLiveCycle() {
  if (!logLive.on) return;
  if (logLive.polling) { logLiveSchedulePoll(250); return; } // never overlap
  const wantFull = logLive.refetchQueued || logLive.cursor == null;
  const wantCatchUp = logLive.reconcile;
  if (!wantFull && !wantCatchUp && wsIsOpen() && !logLive.busDown) {
    // Delivery is healthy: the fallback stays suspended - not one request.
    logLiveSchedulePoll(LOG_POLL_MS);
    return;
  }
  logLive.polling = true;
  try {
    if (wantFull) {
      logLive.refetchQueued = false;
      logLive.reconcile = false;
      await logLiveFetchFull();
    } else {
      logLive.reconcile = false;
      let more = true;
      for (let p = 0; more && p < LOG_CATCHUP_MAX; p++) {
        more = (await logLiveFetchSince()).hasMore;
      }
    }
    logLive.fails = 0;
    logLive.userRequested = false;
    logLiveRefreshStatus();
  } catch (e) {
    logLive.fails = Math.min(logLive.fails + 1, 5);
    const delay = Math.min(LOG_POLL_MS * Math.pow(2, logLive.fails), LOG_POLL_MAX_MS);
    logLiveSetStatus(
      "off",
      (e && (e.status === 401 || e.status === 403)) || (e && /Session expired/.test(e.message || ""))
        ? "live feed needs an admin login"
        : "disconnected — retrying"
    );
    if (logLive.userRequested) { logLive.userRequested = false; errToast(e); }
    logLiveSchedulePoll(delay); // bounded exponential backoff, then retry
    return;
  } finally {
    logLive.polling = false;
  }
  logLiveSchedulePoll(LOG_POLL_MS);
}

function logLiveSchedulePoll(delay) {
  clearTimeout(logLive.pollTimer);
  if (!logLive.on) return;
  logLive.pollTimer = setTimeout(logLiveCycle, delay ?? LOG_POLL_MS);
}

function logLiveWake() {
  logLiveSchedulePoll(0); // run now instead of waiting for the next tick
}

/* Section entry / Refresh button / cursor-less recovery.  Delegates to the
   single cycle so it can never race the fallback poll. */
async function logLiveLoadFull() {
  logLive.refetchQueued = true;
  logLive.userRequested = true;
  logLiveWake();
}

function logLiveOnEvent(ev) {
  if (!logLive.on || !ev) return;
  if (ev.type === "recognition_log") {
    // Validate before touching the DOM: a malformed frame must not inject
    // markup or invent a row.
    const id = Number(ev.log_id);
    if (!Number.isFinite(id) || id <= 0 || !ev.ts) return;
    if (logLive.cursor == null) {
      // No cursor yet (first fetch still running or it failed): let the
      // authoritative page place this row instead of guessing an order.
      logLive.refetchQueued = true;
      logLiveWake();
      return;
    }
    logLiveAdd(logLiveRowFromEvent(ev));
    return;
  }
  if (ev.type === "status") {
    logLive.busDown = !ev.ok;
    logLiveRefreshStatus();
    if (ev.ok) { logLive.reconcile = true; logLiveWake(); } // bus recovered: fill the gap
    return;
  }
  if (ev.type === "__open") {
    logLiveRefreshStatus();
    logLive.reconcile = true; // reconnect: pull anything missed (deduped)
    logLiveWake();
    return;
  }
  if (ev.type === "__close") {
    logLive.busDown = false;
    logLiveRefreshStatus();
  }
}

function logLiveMount() {
  if (logLive.on) logLiveUnmount(); // idempotent: never two listeners/loops
  logLive.on = true;
  logLive.pending = [];
  logLive.fails = 0;
  logLive.busDown = false;
  logLive.reconcile = false;
  logLive.unsub = wsSubscribe(logLiveOnEvent);
  logLiveShowBadge();
  logLiveSetStatus("warn", "connecting…");
  ensureWS(); // reconnect a dead socket (skips a token already refused)
  logLiveRefreshStatus();
  // The authoritative first render is driven by loadAttendance() ->
  // logLiveLoadFull() immediately after this mount.  Scheduling an extra
  // immediate cycle HERE as well made every section entry download the full
  // log page twice (verified on the wire).
  logLiveSchedulePoll(LOG_POLL_MS);
}

function logLiveUnmount() {
  logLive.on = false;
  if (logLive.unsub) { try { logLive.unsub(); } catch {} logLive.unsub = null; }
  clearTimeout(logLive.pollTimer); // the fallback loop stops with the page
  logLive.pollTimer = null;
  logLive.pending = []; // next mount re-fetches; nothing stale survives
}

let logLiveScrollQueued = false;
window.addEventListener("scroll", () => {
  // Came back to the newest entries: flush quietly, without moving anyone.
  if (!logLive.on || logLiveScrollQueued || !logLive.pending.length) return;
  logLiveScrollQueued = true;
  requestAnimationFrame(() => {
    logLiveScrollQueued = false;
    if (logLive.pending.length && logLiveAtNewest()) logLiveFlushPending();
  });
}, { passive: true });

let ws = null;
let wsRetry = null;
let wsPing = null;
let wsAuthFailed = false; // 4401 seen: this token is already known to be rejected

/* One socket, many listeners.  Page components subscribe to the same
   authenticated feed without ever re-assigning onmessage, so a reconnect
   can never stack duplicate handlers, and unmounting a page is a Set.delete
   (no dangling closures, no leaks).  Synthetic "__open"/"__close" frames let
   consumers reconcile after a gap without touching the server protocol. */
const wsSubs = new Set();
function wsSubscribe(fn) { wsSubs.add(fn); return () => { wsSubs.delete(fn); }; }
function wsDispatch(ev) {
  for (const fn of [...wsSubs]) { try { fn(ev); } catch (e) { console.warn(e); } }
}
function wsIsOpen() { return !!ws && ws.readyState === 1; } // 1 = OPEN
function ensureWS() {
  // A 4401 means the current token was already refused server-side: opening
  // another socket with the same token is futile.  The flag clears on the
  // next successful login, so a revoked session never reconnect-storms.
  if (wsAuthFailed) return false;
  if (!ws || ws.readyState > 1) { connectWS(); return true; }
  return true;
}

function setWsStatus(connected, text) {
  $("#ws-dot").className = "dot " + (connected ? "on" : "off");
  $("#ws-status").textContent = text;
}

function connectWS() {
  const tok = getToken();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws/live-feed${tok ? "?token=" + encodeURIComponent(tok) : ""}`;
  try { ws = new WebSocket(url); } catch {
    // Socket construction itself failed: keep the same bounded retry the
    // close path uses instead of going permanently silent.
    setWsStatus(false, "disconnected — retrying…");
    clearTimeout(wsRetry);
    wsRetry = setTimeout(() => { if (getToken()) connectWS(); }, 4000);
    return;
  }

  ws.onopen = () => {
    setWsStatus(true, "connected");
    // Application-level keepalive: an idle socket looks dead to some
    // proxies/firewalls, which then close it "unexpectedly".  The server
    // answers every ping with a pong.
    clearInterval(wsPing);
    wsPing = setInterval(() => { try { ws.send('{"type":"ping"}'); } catch {} }, 25000);
    wsDispatch({ type: "__open" }); // consumers reconcile anything missed
  };
  ws.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch { ev = { raw: m.data }; }
    if (ev.type === "pong") return; // keepalive answer, not an event
    if (ev.type === "status") {
      // Server-side bus state (Redis down / recovered): show it on the
      // status line instead of burying it in the event list.  The
      // recognition log suspends/resumes its fallback poll on this too.
      setWsStatus(!!ev.ok, ev.ok ? "connected" : "event bus unavailable — retrying…");
      wsDispatch(ev);
      return;
    }
    wsDispatch(ev); // recognition log, etc. — before any rendering below
    try { monPushEvent(ev); } catch {} // monitor panel filters by camera; never break the feed
    if (ev.type === "recognition_log") return; // same occurrence as recognition/unknown_face
    const box = $("#events");
    if (box.querySelector(".muted")) box.innerHTML = "";
    const div = document.createElement("div");
    div.className = "event";
    div.innerHTML = `<span class="ev-type">${esc(ev.type || ev.event || "event")}</span>
      <span class="ev-time">${new Date().toLocaleTimeString()}</span>
      <pre>${esc(JSON.stringify(ev, null, 2))}</pre>`;
    box.prepend(div);
    while (box.children.length > 60) box.lastChild.remove();
  };
  ws.onclose = (ev) => {
    clearInterval(wsPing);
    wsDispatch({ type: "__close", code: ev && ev.code });
    if (ev && ev.code === 4401) {
      // Auth rejection (no/foreign token, teacher account): retrying the
      // same credentials is pointless - say so and stay quiet.  Remember it
      // so page visits don't re-open a socket the server already refused.
      wsAuthFailed = true;
      setWsStatus(false, "live feed needs an admin login");
      return;
    }
    setWsStatus(false, "disconnected — retrying…");
    clearTimeout(wsRetry);
    // getToken() returns "" when logged out (never null) - the old
    // `!== null` guard was always true and reconnected after logout.
    wsRetry = setTimeout(() => { if (getToken()) connectWS(); }, 4000);
  };
  ws.onerror = () => { try { ws.close(); } catch {} };
}

/* --------------------------------------------------------------- users */
const USER_BADGE = { active: "green", pending: "amber", rejected: "red", suspended: "gray" };
const STATUS_LABEL = {
  active: "active", pending: "pending approval",
  rejected: "rejected", suspended: "suspended",
};
const badge = (kind, text) => `<span class="badge ${kind}">${esc(text)}</span>`;

async function loadUsers() {
  if (!CURRENT_USER || CURRENT_USER.role !== "super_admin") return;
  try {
    const users = await api("/auth/admin/users");
    const reqs = await api("/auth/admin/password-requests");
    renderUsers(users);
    renderResetRequests(reqs);
  } catch (e) { errToast(e); }
}

function userActions(u) {
  const acts = [];
  if (u.status !== "active") {
    acts.push(`<button class="btn small primary" data-act="approve" data-id="${u.id}">approve</button>`);
  }
  if (u.status === "pending") {
    acts.push(`<button class="btn small danger" data-act="reject" data-id="${u.id}">reject</button>`);
  } else if (u.status === "active") {
    acts.push(`<button class="btn small danger" data-act="suspend" data-id="${u.id}">suspend</button>`);
  }
  acts.push(`<button class="btn small" data-act="edit" data-id="${u.id}">credentials</button>`);
  return acts.join(" ");
}

function renderUsers(list) {
  $("#users-count").textContent = `${list.total} account(s)`;
  if (!list.items.length) {
    $("#users-body").innerHTML = `<tr><td colspan="9" class="muted">no accounts</td></tr>`;
    return;
  }
  $("#users-body").innerHTML = list.items.map((u) => `
    <tr>
      <td>${u.id}</td>
      <td>${esc(u.username)}</td>
      <td>${esc(u.display_name || "—")}</td>
      <td>${u.role === "super_admin" ? badge("amber", "super admin") : badge("gray", "admin")}</td>
      <td>${badge(USER_BADGE[u.status] || "gray", STATUS_LABEL[u.status] || u.status)}</td>
      <td>${u.security_level ? esc(String(u.security_level)) : "—"}</td>
      <td>${fmtDT(u.created_at)}</td>
      <td>${esc(u.approved_by || "—")}</td>
      <td>${userActions(u)}</td>
    </tr>`).join("");
}

function renderResetRequests(list) {
  $("#reset-count").textContent = `${list.total} request(s)`;
  if (!list.items.length) {
    $("#reset-body").innerHTML = `<tr><td colspan="7" class="muted">no requests</td></tr>`;
    return;
  }
  $("#reset-body").innerHTML = list.items.map((r) => `
    <tr>
      <td>${r.id}</td>
      <td>${esc(r.username)}</td>
      <td>${esc(r.reason || "—")}</td>
      <td>${badge(r.status === "resolved" ? "green" : r.status === "pending" ? "amber" : "gray", r.status)}</td>
      <td>${fmtDT(r.created_at)}</td>
      <td>${esc(r.resolved_by || "—")}</td>
      <td>${r.status === "pending"
        ? `<button class="btn small primary" data-act="setpw" data-id="${r.id}" data-username="${esc(r.username)}">set password</button>
           <button class="btn small danger" data-act="reject-req" data-id="${r.id}">reject</button>`
        : ""}</td>
    </tr>`).join("");
}

async function onUserAction(ev) {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(btn.dataset.id);
  const act = btn.dataset.act;
  try {
    if (act === "edit") {
      const row = (await api("/auth/admin/users")).items.find((u) => u.id === id);
      openCredentialForm("user", id, row ? row.username : "");
      return;
    }
    if (act === "approve") {
      const raw = window.prompt("Security level for this account (1 = lowest, 5 = highest):", "3");
      if (raw === null) return;
      const level = Math.min(5, Math.max(1, Number(raw) || 3));
      await api(`/auth/admin/users/${id}/approve`, { body: { security_level: level } });
      toast("account approved", "ok");
    } else if (act === "reject") {
      if (!window.confirm(`Reject account #${id}? It will never be able to sign in.`)) return;
      await api(`/auth/admin/users/${id}/reject`, { body: {} });
      toast("account rejected", "ok");
    } else if (act === "suspend") {
      if (!window.confirm(`Suspend account #${id}? Its sessions stop working immediately.`)) return;
      await api(`/auth/admin/users/${id}/suspend`, { body: {} });
      toast("account suspended", "ok");
    } else {
      return;
    }
    loadUsers();
  } catch (e) { errToast(e); }
}

async function onResetAction(ev) {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(btn.dataset.id);
  try {
    if (btn.dataset.act === "setpw") {
      openCredentialForm("reset", id, btn.dataset.username || "");
      return;
    }
    if (!window.confirm(`Reject reset request #${id}?`)) return;
    await api(`/auth/admin/password-requests/${id}`, { body: { action: "reject" } });
    toast("request rejected", "ok");
    loadUsers();
  } catch (e) { errToast(e); }
}

function openCredentialForm(mode, id, username) {
  $("#cred-mode").value = mode;
  $("#cred-user-id").value = mode === "user" ? id : "";
  $("#cred-request-id").value = mode === "reset" ? id : "";
  $("#cred-password").value = "";
  $("#cred-level").value = "";
  $("#cred-username").value = username || "";
  $("#cred-role").value = "";
  const isReset = mode === "reset";
  $("#cred-username").readOnly = isReset;
  $("#cred-role").disabled = isReset;
  $("#cred-level").disabled = isReset;
  $("#cred-password").placeholder = isReset ? "required" : "leave blank to keep";
  $("#cred-title").textContent = isReset
    ? `Set a new password for '${username}' (request #${id})`
    : `Credentials for account #${id}`;
  $("#credential-form").hidden = false;
}

async function submitCredential(ev) {
  ev.preventDefault();
  const mode = $("#cred-mode").value;
  const password = $("#cred-password").value;
  try {
    if (mode === "reset") {
      if (!password) { toast("a new password is required", "error"); return; }
      await api(`/auth/admin/password-requests/${$("#cred-request-id").value}`, {
        body: { action: "reset", new_password: password },
      });
      toast("password reset — the user can now sign in", "ok");
    } else {
      const body = {};
      const u = $("#cred-username").value.trim();
      const r = $("#cred-role").value;
      const l = $("#cred-level").value;
      if (u) body.username = u;
      if (password) body.password = password;
      if (r) body.role = r;
      if (l) body.security_level = Number(l);
      if (!Object.keys(body).length) { toast("nothing to save", "error"); return; }
      await api(`/auth/admin/users/${$("#cred-user-id").value}`, {
        method: "PATCH", body,
      });
      toast("credentials updated", "ok");
    }
    $("#credential-form").hidden = true;
    $("#credential-form").reset();
    loadUsers();
  } catch (e) { errToast(e); }
}

/* ------------------------------------------------------------- system */
async function loadSystem() {
  try {
    const h = await api("/system/health");
    $("#health-body").textContent = JSON.stringify(h, null, 2);
  } catch (e) { $("#health-body").textContent = String(e.message || e); }
  try {
    const g = await api("/system/gpu-status");
    $("#gpu-body").textContent = JSON.stringify(g, null, 2);
  } catch (e) { $("#gpu-body").textContent = String(e.message || e); }
}

/* ------------------------------------------------------------- wiring */
document.addEventListener("DOMContentLoaded", () => {
  $("#login-form").addEventListener("submit", tryLogin);
  $("#btn-logout").addEventListener("click", logout);

  // register / forgot-password cards
  $("#btn-show-register").addEventListener("click", () => {
    setAuthMessage("info", ""); setAuthMessage("error", "");
    showAuthCard("register");
  });
  $("#btn-show-forgot").addEventListener("click", () => {
    setAuthMessage("error", ""); showAuthCard("forgot");
  });
  $("#btn-register-back").addEventListener("click", () => showAuthCard("login"));
  $("#btn-forgot-back").addEventListener("click", () => showAuthCard("login"));
  $("#register-form").addEventListener("submit", tryRegister);
  $("#forgot-form").addEventListener("submit", tryForgot);

  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => showSection(b.dataset.section)));

  $("#btn-enroll-toggle").addEventListener("click", () => { $("#enroll-form").hidden = !$("#enroll-form").hidden; });
  $("#btn-enroll-cancel").addEventListener("click", () => { $("#enroll-form").hidden = true; });
  $("#enroll-form").addEventListener("submit", submitEnrollment);
  $("#s-photos").addEventListener("change", () => {
    const n = $("#s-photos").files.length;
    $("#s-photos-count").textContent = n ? `${n} file(s) selected` : "no files";
  });
  $("#students-body").addEventListener("click", onStudentAction);
  $("#stu-q").addEventListener("input", (e) => {
    studentQuery = e.target.value.trim();
    clearTimeout(window.__stuTimer);
    window.__stuTimer = setTimeout(loadStudents, 300);
  });

  $("#btn-camera-toggle").addEventListener("click", () => openCameraForm(null));
  $("#btn-camera-cancel").addEventListener("click", () => { $("#camera-form").hidden = true; });
  $("#camera-form").addEventListener("submit", submitCamera);
  $("#btn-cam-test").addEventListener("click", testCameraFromForm);
  $("#btn-preview-close").addEventListener("click", stopCameraPreview);
  $("#cameras-body").addEventListener("click", onCameraAction);

  // history
  $("#btn-h-refresh").addEventListener("click", loadHistory);

  // periods + records + overrides
  $("#btn-period-toggle").addEventListener("click", () => openPeriodForm(null));
  $("#btn-period-cancel").addEventListener("click", () => { $("#period-form").hidden = true; });
  $("#period-form").addEventListener("submit", submitPeriod);
  $("#periods-body").addEventListener("click", onPeriodAction);
  $("#btn-rec-refresh").addEventListener("click", loadRecords);
  $("#rec-period").addEventListener("change", loadRecords);
  $("#rec-date").addEventListener("change", loadRecords);
  $("#records-body").addEventListener("click", onRecordAction);

  // teacher portal
  $("#btn-t-refresh").addEventListener("click", loadTeacher);
  $("#t-date").addEventListener("change", loadTeacher);
  $("#teacher-courses").addEventListener("click", onRecordAction);

  // override dialog
  $("#override-form").addEventListener("submit", submitOverride);
  $("#btn-ov-cancel").addEventListener("click", () => { $("#override-modal").hidden = true; });

  $("#att-date").value = localToday();
  $("#btn-att-refresh").addEventListener("click", loadAttendance);
  $("#logs-new").addEventListener("click", () => {
    // User asks for the buffered rows: show them and return to the top.
    logLiveFlushPending();
    const card = $("#logs-new");
    if (card && card.scrollIntoView) card.scrollIntoView({ block: "nearest" });
  });
  $("#att-date").addEventListener("change", loadAttendance);
  $("#att-absent").addEventListener("change", loadAttendance);

  $("#btn-live-refresh").addEventListener("click", loadLive);

  // monitor
  $("#mon-camera").addEventListener("change", monitorSelect);
  $("#btn-mon-start").addEventListener("click", () => monStartStop(true));
  $("#btn-mon-stop").addEventListener("click", () => monStartStop(false));
  $("#btn-mon-retry").addEventListener("click", () => {
    if (!mon.cameraId) { toast("Select a camera first", "error"); return; }
    mon.reconnects = 0; monitorConnect();
  });
  $("#btn-sys-refresh").addEventListener("click", loadSystem);

  $("#btn-users-refresh").addEventListener("click", loadUsers);
  $("#users-body").addEventListener("click", onUserAction);
  $("#reset-body").addEventListener("click", onResetAction);
  $("#credential-form").addEventListener("submit", submitCredential);
  $("#btn-cred-cancel").addEventListener("click", () => {
    $("#credential-form").hidden = true;
  });

  boot();
});
