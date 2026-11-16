"use strict";
// video label: setup, videos, and the labeling screen (docs/design.md).

const $ = (sel) => document.querySelector(sel);
const SPEEDS = [0.5, 1, 1.5, 2, 3, 4];
const MAX_WATCH_SPEED = 4;          // faster playback does not count as watched
const CONTEXT_BEFORE_S = 2.0;       // causal-2s: one window length before a candidate
const CONTEXT_AFTER_S = 1.0;
const ZOOM_HALF_S = 5;              // the zoom timeline shows +-5 s around the playhead

let STATE = null;                   // /api/state

async function api(path, opts = {}) {
  const headers = { "X-Video-Label": "1", ...(opts.body ? { "Content-Type": "application/json" } : {}) };
  const r = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data?.detail || data || r.statusText);
  return data;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });

function fmtTime(s) {
  if (s == null) return "";
  const m = Math.floor(s / 60), sec = s - m * 60;
  return `${m}:${sec.toFixed(1).padStart(4, "0")}`;
}

// ---------------------------------------------------------------- routing

// The Prepare prelabel and Label tabs belong to the selected video and show only when there is one.
let CUR = null;   // {video_id, file}

function route() {
  let [view, arg] = (location.hash.slice(1) || "jobs").split("/");
  if (view === "prelabel" || view === "label") {
    arg = arg || CUR?.video_id;
    if (!arg) { flash("Choose a video first"); location.hash = "#jobs"; return; }
    selectVideo(arg);
  }
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  document.body.classList.toggle("labeling", view === "label");
  if (view === "setup") loadSetup();
  if (view === "jobs") loadJobs();
  if (view === "prelabel") loadPrelabel(arg);
  if (view === "label") openVideo(arg);
}
window.addEventListener("hashchange", route);

/** Show the video in the top bar and point its two tabs at it. */
async function selectVideo(videoId) {
  if (CUR?.video_id !== videoId) CUR = { video_id: videoId, file: "" };
  for (const v of ["prelabel", "label"]) document.querySelector(`nav a[data-view="${v}"]`).href = `#${v}/${videoId}`;
  $("#cur-video").hidden = false;
  if (!CUR.file) {
    const row = (await api("/api/videos")).find((v) => v.video_id === videoId);
    if (CUR.video_id !== videoId) return;
    CUR.file = row?.file ?? videoId;
  }
  $("#cur-name").textContent = CUR.file;
  $("#cur-name").title = `the selected video: ${videoId} · ${CUR.file}`;
}

async function refreshState() {
  STATE = await api("/api/state");
  $("#worker").textContent = STATE.worker.message === "idle" ? "" : `● ${STATE.worker.message}`;
  const key = JSON.stringify(STATE.settings.labels);
  if (key !== appliedLabels) { appliedLabels = key; applyLabels(); }
  markNextSetting();
}

/** Setup page: a breathing glow on the next setting that is missing, and none when all are set. */
function markNextSetting() {
  const s = STATE.settings;
  const next = !s.labeler ? ($("#labeler").value.trim() ? "#save-labeler" : "#labeler")
    : !s.libraries.length ? "#choose-library" : null;
  for (const sel of ["#labeler", "#save-labeler", "#choose-library"]) $(sel).classList.toggle("needs", sel === next);
}

// ---------------------------------------------------------------- labels (Setup page)

// Values from 1: event labels; 0: background (the fill of watched time); -1: ignore.
// Each label has a keyboard key (Setup page) that sets the label of the selected segment.
let appliedLabels = null;
const labels = () => STATE?.settings.labels ?? [];
const labelName = (v) => labels().find((x) => x.value === v)?.name ?? String(v);
const defaultEvent = () => labels().find((x) => x.value >= 1)?.value ?? 1;   // Accept, P, Start/End
/** The color of a label (Setup page). */
const labelColor = (v) => labels().find((x) => x.value === v)?.color ?? "#868e96";
/** Black or white, whichever reads better on the color `hex`. */
function textOn(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? "#000" : "#fff";
}
/** The first palette color that no label in `list` uses. */
function freeColor(list) {
  const palette = STATE?.label_palette ?? ["#d9480f"];
  const used = new Set(list.map((x) => x.color));
  return palette.find((c) => !used.has(c)) ?? palette[list.length % palette.length];
}
const kbd = (key) => `<kbd>${key === " " ? "Space" : key.toUpperCase()}</kbd>`;
const escapeHtml = (t) => t.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

/** Put the label names into the labeling screen: texts, the label buttons, and the legend. */
function applyLabels() {
  document.querySelectorAll("[data-label]").forEach((el) => {
    const k = el.dataset.label;
    el.textContent = k === "event" || k === "event-name" ? labelName(defaultEvent()) : labelName(k === "0-name" ? 0 : Number(k));
  });
  const btns = $("#set-label-btns"), holds = $("#hold-btns"), help = $("#help-label-keys");
  btns.innerHTML = holds.innerHTML = help.innerHTML = "";
  for (const x of labels()) {
    const name = escapeHtml(x.name);
    const b = document.createElement("button");
    Object.assign(b.dataset, { act: "set-label", value: x.value });
    b.innerHTML = `${name} ${kbd(x.key)}`;
    b.title = `set the label of the selected segment to ${x.name} (key ${x.key.toUpperCase()})`;
    b.style.background = b.style.borderColor = x.color;
    b.style.color = textOn(x.color);
    btns.appendChild(b);

    const h = document.createElement("button");
    Object.assign(h.dataset, { act: "hold", value: x.value });
    const isDefault = x.value === defaultEvent();
    h.innerHTML = `Hold: ${name}` + (isDefault ? ` ${kbd("p")}` : "");
    h.title = `hold down while the video plays to mark ${x.name}` + (isDefault ? " (hold P)" : "");
    h.style.setProperty("--c", x.color);
    h.style.setProperty("--c-fg", textOn(x.color));
    holds.appendChild(h);

    help.insertAdjacentHTML("beforeend", `<tr><td>${kbd(x.key)}</td><td>set the selected segment to ${name}</td></tr>`);
  }
  $("#reserved-keys").textContent = [...(STATE?.reserved_keys ?? "")].map((k) => (k === " " ? "Space" : k.toUpperCase())).join(" ");
  const legend = $("#legend-labels");
  legend.innerHTML = "";
  const swatch = (color, text) => {
    const span = document.createElement("span");
    span.innerHTML = `<i class="sw" style="background: ${color}"></i>`;
    span.append(text);
    legend.appendChild(span);
  };
  for (const x of labels()) {
    swatch(x.color, x.name);
    if (x.value === 0) swatch("var(--rejected)", `${x.name}, rejected suggestion`);
  }
  // The Accept / Reject / Unsure and Start / End buttons take the colors of their labels.
  const root = document.documentElement.style;
  for (const [name, v] of [["event", defaultEvent()], ["bg", 0], ["ignore", -1]]) {
    root.setProperty(`--lbl-${name}`, labelColor(v));
    root.setProperty(`--lbl-${name}-fg`, textOn(labelColor(v)));
  }
  render();
}

let labelDraft = null;   // the label list on the Setup page while it is edited
function renderLabelConfig() {
  const tbody = $("#label-config tbody");
  tbody.innerHTML = "";
  labelDraft.forEach((x, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><input maxlength="1"></td><td><input></td><td><input type="color"></td><td class="small muted"></td><td class="small muted mono"></td><td></td>`;
    const [key, name, color] = tr.querySelectorAll("input");
    color.value = x.color;
    color.title = "click to choose the color of this label";
    color.oninput = () => { x.color = color.value; $("#labels-status").textContent = "Not saved yet."; };
    key.value = x.key.toUpperCase();
    key.oninput = () => { x.key = key.value.toLowerCase(); $("#labels-status").textContent = "Not saved yet."; };
    tr.children[4].textContent = x.value;
    name.value = x.name;
    name.oninput = () => { x.name = name.value; $("#labels-status").textContent = "Not saved yet."; };
    tr.children[3].textContent = x.value === 0 ? "background (fill, reject)" : x.value === -1 ? "ignore" : i === 0 ? "event (default)" : "event";
    if (x.value >= 1) {
      const rm = document.createElement("button");
      rm.textContent = "Remove";
      rm.onclick = () => { labelDraft.splice(i, 1); renderLabelConfig(); $("#labels-status").textContent = "Not saved yet."; };
      tr.children[5].appendChild(rm);
    }
    tbody.appendChild(tr);
  });
}
$("#add-label").onclick = () => {
  // A new value is never reused from a removed label, so old exports keep their meaning.
  const value = Math.max(0, ...labelDraft.map((x) => x.value), ...labels().map((x) => x.value)) + 1;
  if (value > 99) return flash("Too many labels");
  labelDraft.splice(labelDraft.filter((x) => x.value >= 1).length, 0, { value, name: "", key: "", color: freeColor(labelDraft) });
  renderLabelConfig();
};
$("#save-labels").onclick = async () => {
  try {
    labelDraft = (await post("/api/settings/labels", { labels: labelDraft })).map((x) => ({ ...x }));
    await refreshState();
    renderLabelConfig();
    $("#labels-status").textContent = "Saved.";
  } catch (e) { $("#labels-status").textContent = `Not saved: ${e.message}`; }
};
setInterval(() => refreshState().catch(() => {}), 3000);

// ---------------------------------------------------------------- setup

async function loadSetup() {
  await refreshState();
  const s = STATE.settings;
  if (!labelerDirty) $("#labeler").value = s.labeler;   // keep a name that is typed but not saved yet
  $("#labeler-status").textContent = s.labeler ? "" : "Not saved yet.";
  $("#ws-path").textContent = STATE.workspace;
  $("#ffmpeg-version").textContent = STATE.ffmpeg;
  if (!labelDraft || $("#labels-status").textContent !== "Not saved yet.") {
    labelDraft = s.labels.map((x) => ({ ...x }));
    renderLabelConfig();
  }
  const ul = $("#libraries");
  ul.innerHTML = "";
  for (const lib of s.libraries) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="mono grow"></span><button>Remove</button>`;
    li.querySelector("span").textContent = lib;
    li.querySelector("button").onclick = async () => { await post("/api/libraries/remove", { path: lib }); loadSetup(); };
    ul.appendChild(li);
  }
  if (!s.libraries.length) ul.innerHTML = `<li class="muted">No library yet.</li>`;
}

let labelerDirty = false;
async function saveLabeler() {
  const name = $("#labeler").value.trim();
  if (!name) return;
  try {
    await post("/api/settings", { labeler: name });
    labelerDirty = false;
    await refreshState();
    $("#labeler-status").textContent = "Saved.";
  } catch (e) { $("#labeler-status").textContent = `Not saved: ${e.message}`; }
}
$("#labeler").addEventListener("input", () => {
  labelerDirty = true;
  $("#labeler-status").textContent = "Not saved yet.";
  if (STATE) markNextSetting();
});
$("#labeler").addEventListener("change", saveLabeler);   // also saves when the field loses focus
$("#labeler").addEventListener("keydown", (e) => { if (e.key === "Enter") saveLabeler(); });
$("#save-labeler").onclick = saveLabeler;
$("#choose-library").onclick = async () => {
  const { path } = await post("/api/libraries/choose");
  if (path) { await post("/api/libraries", { path }); loadSetup(); }
};
$("#add-library").onclick = async () => {
  try { await post("/api/libraries", { path: $("#library-path").value }); $("#library-path").value = ""; loadSetup(); }
  catch (e) { alertInline(e.message); }
};

function alertInline(msg) { flash(msg); }

// ---------------------------------------------------------------- jobs

let jobsTimer = null;
async function loadJobs() {
  clearTimeout(jobsTimer);
  const rows = await api("/api/videos");
  const tbody = $("#video-table tbody");
  tbody.innerHTML = "";
  for (const v of rows) {
    const tr = document.createElement("tr");
    const ready = ["normalized", "frames", "prelabeled"].includes(v.step);
    tr.innerHTML = `<td class="mono"></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>`;
    const td = tr.children;
    td[0].textContent = v.video_id;
    td[1].textContent = v.file;
    td[1].title = v.library;
    td[2].textContent = fmtTime(v.duration_s);
    td[3].textContent = v.step;
    td[4].textContent = v.status;
    if (v.error) { td[4].className = "err"; td[4].title = v.error; td[4].textContent = `error: ${v.error.slice(0, 80)}`; }
    td[5].textContent = v.candidates ?? "";
    td[6].textContent = v.watched_fraction ? `${Math.round(v.watched_fraction * 100)}%` : "";
    td[7].textContent = v.label_status ?? "";
    if (ready) {
      td[8].innerHTML = `<button class="b-primary">Label</button><button class="b-strong">Export for prelabeling</button>`;
      td[8].children[0].onclick = () => (location.hash = `#label/${v.video_id}`);
      td[8].children[1].onclick = () => (location.hash = `#prelabel/${v.video_id}`);
    }
    tbody.appendChild(tr);
  }
  if (!rows.length) tbody.innerHTML = `<tr><td colspan="9" class="muted">No videos. Add a library on the Setup page.</td></tr>`;
  await loadTrainStats();
  if (!$("#view-jobs").hidden) jobsTimer = setTimeout(loadJobs, 3000);
}

// ---------------------------------------------------------------- training data and export

// All watched time is checked by a person and goes into the export (export.py); skipped videos do not.
async function loadTrainStats() {
  const st = await api("/api/export/stats");
  $("#train-total").textContent = st.seconds
    ? `Available for training: ${st.seconds.toFixed(1)} s (${fmtTime(st.seconds)}) from ${st.videos} video${st.videos === 1 ? "" : "s"}, ${st.clips} part${st.clips === 1 ? "" : "s"}`
    : "No watched time yet: watch and label parts of a video first.";
  const tbody = $("#train-table tbody");
  tbody.innerHTML = "";
  for (const x of st.labels) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><i class="sw"></i><span></span></td><td>${kbd(x.key)}</td><td class="mono num-cell"></td><td class="num-cell"></td><td class="num-cell"></td>`;
    tr.querySelector(".sw").style.background = x.color;
    tr.querySelector("span").textContent = x.name;
    tr.children[2].textContent = x.value;
    tr.children[3].textContent = x.instances;
    tr.children[4].textContent = x.seconds.toFixed(1);
    tbody.appendChild(tr);
  }
  $("#train-note").textContent = st.dropped_short_parts
    ? `${st.dropped_short_parts} watched part${st.dropped_short_parts === 1 ? " is" : "s are"} shorter than one training window (${st.window_s} s) and ${st.dropped_short_parts === 1 ? "is" : "are"} left out.`
    : "";
  if (!$("#export-run").dataset.busy) $("#export-run").disabled = !st.clips;
}

$("#export-run").onclick = async () => {
  const btn = $("#export-run"), status = $("#export-status");
  btn.disabled = true;
  btn.dataset.busy = "1";
  status.className = "small muted";
  status.textContent = "Exporting: cutting the clips and writing the label files…";
  $("#export-result").hidden = true;
  try {
    const r = await post("/api/export");
    status.className = "small ok";
    status.textContent = `Exported ${r.clips} clip${r.clips === 1 ? "" : "s"} from ${r.videos} video${r.videos === 1 ? "" : "s"}.`;
    $("#export-path").textContent = r.path;
    $("#export-reveal").onclick = () => reveal(r.path);
    $("#export-result").hidden = false;
  } catch (e) { status.className = "small err"; status.textContent = e.message; }
  delete btn.dataset.busy;
  btn.disabled = false;
};
$("#run-jobs").onclick = async () => { await post("/api/jobs/run"); loadJobs(); };

// ---------------------------------------------------------------- labeling state

const L = {
  videoId: null, fps: 15, n: 0, duration: 0,
  label: null, cands: [], unusable: [],
  frame: 0, sel: -1, cand: -1, pendingStart: null, holdStart: null, holdLabel: 1, tagMode: false,
  lastWatch: null, playUntil: null, undo: [], saveTimer: null, dirty: false,
};
const player = $("#player");

async function openVideo(videoId) {
  if (!videoId) { $("#label-empty").hidden = false; $("#label-main").hidden = true; return; }
  if (!STATE?.settings.labeler) { location.hash = "#setup"; return flash("Set your labeler name first"); }
  await flushSave();
  let data;
  try { data = await api(`/api/videos/${videoId}`); }
  catch (e) { $("#label-empty").textContent = e.message; $("#label-empty").hidden = false; $("#label-main").hidden = true; return; }
  const v = data.video;
  Object.assign(L, {
    videoId, fps: v.fps, n: v.n_frames, duration: v.n_frames / v.fps, label: data.label,
    cands: data.prelabel?.candidates ?? [], unusable: data.prelabel?.unusable ?? [],
    frame: 0, sel: -1, cand: -1, pendingStart: null, holdStart: null, tagMode: false,
    lastWatch: null, playUntil: null, undo: [], dirty: false,
  });
  if (!L.label.labeler && STATE?.settings.labeler) L.label.labeler = STATE.settings.labeler;
  $("#label-empty").hidden = true;
  $("#label-main").hidden = false;
  $("#lbl-title").textContent = `${videoId} · ${v.rel_path}`;
  $("#note").value = L.label.note || "";
  $("#candidate-info").textContent = data.prelabel
    ? `${L.cands.length} suggested events from ${data.prelabel.model}.\nPress N to play the first one.`
    : "No suggested events for this video yet.\nWatch it and mark events with P or I/O, or prepare pre-label files.";
  player.src = data.media_url;
  player.playbackRate = 1;
  history.replaceState(null, "", `#label/${videoId}`);
  render();
  loadSummary();
}

// ---------------------------------------------------------------- frames and time

const clampFrame = (f) => Math.min(L.n - 1, Math.max(0, f));
const frameOf = (t) => clampFrame(Math.floor(t * L.fps + 1e-6));   // any time inside a frame
// The start time of a presented frame can be a little early (13.33326 for frame 200), so round it.
const frameAtStart = (t) => clampFrame(Math.round(t * L.fps));
const timeOf = (f) => (f + 0.5) / L.fps;   // the middle of the frame: seeking there shows frame f
function seekFrame(f) {
  f = Math.min(L.n - 1, Math.max(0, f));
  L.lastWatch = null;
  player.currentTime = timeOf(f);
  L.frame = f;
}

function onFrame(_now, meta) {
  if (L.label) {
    const f = frameAtStart(meta.mediaTime);
    const rate = player.playbackRate;
    if (!player.paused && rate <= MAX_WATCH_SPEED && document.visibilityState === "visible") {
      const maxGap = Math.max(2, Math.ceil(rate * L.fps * 0.25));
      if (L.lastWatch != null && f >= L.lastWatch && f - L.lastWatch <= maxGap) addWatched(L.lastWatch, f);
      else addWatched(f, f);
      L.lastWatch = f;
    }
    L.frame = f;
    if (L.playUntil != null && meta.mediaTime >= L.playUntil) { player.pause(); L.playUntil = null; }
    render();
  }
  player.requestVideoFrameCallback(onFrame);
}
player.requestVideoFrameCallback(onFrame);
player.addEventListener("seeked", () => { L.frame = frameOf(player.currentTime); render(); });
player.addEventListener("pause", () => { L.lastWatch = null; scheduleSave(); render(); });
document.addEventListener("visibilitychange", () => { L.lastWatch = null; });

function addWatched(a, b) {
  const w = L.label.watched;
  const last = w[w.length - 1];
  if (last && a >= last[0] && a <= last[1] + 1) { if (b > last[1]) { last[1] = b; L.dirty = true; } return; }
  w.push([a, b]);
  w.sort((x, y) => x[0] - y[0]);
  // merge
  const out = [];
  for (const r of w) {
    const p = out[out.length - 1];
    if (p && r[0] <= p[1] + 1) p[1] = Math.max(p[1], r[1]); else out.push([r[0], r[1]]);
  }
  L.label.watched = out;
  L.dirty = true;
  scheduleSave();
}
const isWatched = (f) => L.label.watched.some(([a, b]) => f >= a && f <= b);

// ---------------------------------------------------------------- segments

function snapshot() {
  L.undo.push(JSON.stringify({ s: L.label.segments, d: L.label.decisions, st: L.label.status }));
  if (L.undo.length > 200) L.undo.shift();
}
function undo() {
  const prev = L.undo.pop();
  if (!prev) return flash("Nothing to undo");
  const { s, d, st } = JSON.parse(prev);
  Object.assign(L.label, { segments: s, decisions: d, status: st });
  L.sel = -1;
  changed();
}

/** Put a segment on the timeline. It replaces what it overlaps. Returns its index. */
function paint(seg) {
  const out = [];
  for (const s of L.label.segments) {
    if (s.end_frame < seg.start_frame || s.start_frame > seg.end_frame) { out.push(s); continue; }
    if (s.start_frame < seg.start_frame) out.push({ ...s, end_frame: seg.start_frame - 1 });
    if (s.end_frame > seg.end_frame) out.push({ ...s, start_frame: seg.end_frame + 1 });
  }
  out.push(seg);
  out.sort((a, b) => a.start_frame - b.start_frame);
  L.label.segments = out;
  return out.indexOf(seg);
}

function candFrames(c) {
  return [Math.max(0, Math.round(c.start_s * L.fps)), Math.min(L.n - 1, Math.round(c.end_s * L.fps))];
}

function decide(decision) {
  const c = L.cands[L.cand];
  if (!c) return flash("No candidate. Press N first.");
  snapshot();
  const [a, b] = candFrames(c);
  const label = { accepted: defaultEvent(), rejected: 0, unsure: -1 }[decision];
  const origin = { accepted: "grok_accepted", rejected: "grok_rejected", unsure: "unsure" }[decision];
  // Remove an earlier decision on this candidate before the new one.
  L.label.segments = L.label.segments.filter((s) => s.candidate_id !== c.id);
  L.sel = paint({ start_frame: a, end_frame: b, label, origin, tags: [...c.tags],
                  candidate_id: c.id, candidate_confidence: c.confidence });
  L.label.decisions[c.id] = decision;
  flash(`${c.id}: ${decision}`);
  changed();
}

function setUnsure() {
  if (L.sel >= 0) {
    snapshot();
    const s = L.label.segments[L.sel];
    s.label = -1;
    if (s.candidate_id) L.label.decisions[s.candidate_id] = "unsure";
    changed();
  } else decide("unsure");
}

function setEdge(which) {
  const f = L.frame;
  if (L.sel >= 0) {
    const s = L.label.segments[L.sel];
    const a = which === "start" ? f : s.start_frame, b = which === "end" ? f : s.end_frame;
    if (a > b) return flash("The start must be before the end");
    snapshot();
    L.label.segments.splice(L.sel, 1);
    L.sel = paint({ ...s, start_frame: a, end_frame: b });
    changed();
  } else if (which === "start") {
    L.pendingStart = f;
    flash(`Start at frame ${f}. Press O at the end.`);
    render();
  } else if (L.pendingStart != null) {
    const a = Math.min(L.pendingStart, f), b = Math.max(L.pendingStart, f);
    snapshot();
    L.sel = paint({ start_frame: a, end_frame: b, label: defaultEvent(), origin: "manual", tags: [] });
    L.pendingStart = null;
    changed();
  } else flash("Press I first, or select a segment");
}

function deleteSelected() {
  if (L.sel < 0) return;
  snapshot();
  const [s] = L.label.segments.splice(L.sel, 1);
  if (s.candidate_id) delete L.label.decisions[s.candidate_id];
  L.sel = -1;
  changed();
}

function toggleTag(i) {
  const tag = STATE?.tags[i];
  if (L.sel < 0 || !tag) return;
  snapshot();
  const s = L.label.segments[L.sel];
  s.tags = s.tags.includes(tag) ? s.tags.filter((t) => t !== tag) : [...s.tags, tag].sort();
  changed();
}

function goCandidate(step) {
  if (!L.cands.length) return flash("No candidates");
  let i = L.cand + step;
  if (L.cand < 0 && step < 0) i = L.cands.length - 1;
  if (i < 0 || i >= L.cands.length) return flash(step > 0 ? "Last candidate. Press W for unwatched parts, Enter when done." : "First candidate");
  L.cand = i;
  L.sel = L.label.segments.findIndex((s) => s.candidate_id === L.cands[i].id);
  const c = L.cands[i];
  seekFrame(Math.round(Math.max(0, c.start_s - CONTEXT_BEFORE_S) * L.fps));
  L.playUntil = Math.min(L.duration, c.end_s + CONTEXT_AFTER_S);
  player.play();
  render();
}

function playUnwatched(random) {
  const free = [];
  let start = null;
  for (let f = 0; f <= L.n; f++) {
    const w = f === L.n || isWatched(f);
    if (!w && start == null) start = f;
    if (w && start != null) { free.push([start, f - 1]); start = null; }
  }
  if (!free.length) return flash("All of the video is watched");
  let f;
  if (random) {
    const total = free.reduce((n, [a, b]) => n + b - a + 1, 0);
    let k = Math.floor(Math.random() * total);
    for (const [a, b] of free) { if (k <= b - a) { f = a + k; break; } k -= b - a + 1; }
    L.playUntil = Math.min(L.duration, f / L.fps + 10);
  } else {
    const next = free.find(([, b]) => b >= L.frame) || free[0];
    f = Math.max(next[0], next[0] <= L.frame && L.frame <= next[1] ? L.frame : next[0]);
    L.playUntil = (next[1] + 1) / L.fps;
  }
  seekFrame(f);
  player.play();
}

async function setStatus(status) {
  snapshot();
  L.label.status = status;
  L.label.note = $("#note").value;
  if (status === "skip") L.label.skip_reason = L.label.note || "skipped";
  changed();
  await flushSave();
  if (status === "done" || status === "skip") {
    const rows = await api("/api/videos");
    const ready = rows.filter((v) => ["normalized", "frames", "prelabeled"].includes(v.step));
    const idx = ready.findIndex((v) => v.video_id === L.videoId);
    const next = [...ready.slice(idx + 1), ...ready.slice(0, idx)].find((v) => !v.label_status || v.label_status === "todo");
    if (next) location.hash = `#label/${next.video_id}`;
    else flash("All videos are done or skipped");
  }
}

// ---------------------------------------------------------------- save

function changed() { L.dirty = true; render(); scheduleSave(); }
function scheduleSave() { clearTimeout(L.saveTimer); L.saveTimer = setTimeout(flushSave, 800); }
async function flushSave() {
  clearTimeout(L.saveTimer);
  if (!L.label || !L.dirty) return;
  L.dirty = false;
  L.label.note = $("#note").value;
  try {
    const saved = await api(`/api/labels/${L.videoId}`, { method: "PUT", body: JSON.stringify(L.label) });
    L.label.updated_at = saved.updated_at;
    $("#lbl-saved").textContent = `saved ${new Date().toLocaleTimeString()}`;
    loadSummary();
  } catch (e) {
    L.dirty = true;
    $("#lbl-saved").textContent = `NOT SAVED: ${e.message}`;
  }
}
window.addEventListener("beforeunload", () => {
  if (L.label && L.dirty) {
    fetch(`/api/labels/${L.videoId}`, { method: "PUT", keepalive: true, body: JSON.stringify(L.label),
      headers: { "X-Video-Label": "1", "Content-Type": "application/json" } });
  }
});
$("#note").addEventListener("input", () => { L.dirty = true; scheduleSave(); });
// The debounce never fires during long playback (the watched range grows each frame), so also save every 5 s.
setInterval(() => { if (L.dirty && !player.paused) flushSave(); }, 5000);

let summaryTimer = null;
function loadSummary() {
  clearTimeout(summaryTimer);
  summaryTimer = setTimeout(async () => {
    const s = await api(`/api/summary?video_id=${L.videoId}`);
    const a = s.all, v = s.video;
    const byLabel = labels().filter((x) => x.value >= 1).length > 1
      ? labels().filter((x) => x.value >= 1).map((x) => [`· ${x.name}`, a.events_by_label[x.name] ?? 0, v?.events_by_label[x.name] ?? (v ? 0 : null)])
      : [];
    const rows = [
      ["events", a.events, v?.events, true],
      ...byLabel,
      ["source videos", a.source_videos, null, true],
      ["positive windows", a.positive_windows, v?.positive_windows],
      ["negative windows", a.negative_windows, v?.negative_windows],
      ["hard negatives", a.hard_negatives, v?.hard_negatives],
      ["watched", fmtTime(a.watched_s), fmtTime(v?.watched_s)],
    ];
    $("#sum-body").innerHTML = `<tr class="muted small"><td title="window config ${a.config}">${a.config}</td><td title="done videos + this one">all</td><td>video</td></tr>` +
      rows.map(([k, x, y, key]) => `<tr class="${key ? "key" : ""}"><td>${k}</td><td>${x ?? ""}</td><td>${y ?? ""}</td></tr>`).join("");
  }, 300);
}

// ---------------------------------------------------------------- keys

const KEYMAP = {
  " ": () => togglePlay(),
  ArrowLeft: (e) => { player.pause(); stepFrames(e.shiftKey ? -Math.round(L.fps) : -1); },
  ArrowRight: (e) => { player.pause(); stepFrames(e.shiftKey ? Math.round(L.fps) : 1); },
  "[": () => setSpeed(-1),
  "]": () => setSpeed(1),
  n: (e) => goCandidate(e.shiftKey ? -1 : 1),
  a: () => decide("accepted"),
  r: () => decide("rejected"),
  u: () => setUnsure(),
  i: () => setEdge("start"),
  o: () => setEdge("end"),
  w: () => playUnwatched(false),
  b: () => playUnwatched(true),
  t: () => { if (L.sel < 0) return flash("Select a segment first"); L.tagMode = true; flash("Tag: press 1–8"); },
  s: (e) => (e.shiftKey ? setStatus("skip") : flash("Shift+S skips the video")),
  Enter: () => setStatus("done"),
  Delete: () => deleteSelected(),
  Backspace: () => deleteSelected(),
  Escape: () => { L.sel = -1; L.pendingStart = null; L.tagMode = false; $("#help").hidden = true; render(); },
  "?": () => ($("#help").hidden = !$("#help").hidden),
};

function stepFrames(k) {
  const f = Math.min(L.n - 1, Math.max(0, L.frame + k));
  seekFrame(f);
  if (Math.abs(k) === 1) addWatched(f, f);   // stepping through frames counts as watching them
}
function setSpeed(dir) {
  const i = SPEEDS.indexOf(player.playbackRate);
  player.playbackRate = SPEEDS[Math.min(SPEEDS.length - 1, Math.max(0, (i < 0 ? 1 : i) + dir))];
  render();
}

document.addEventListener("keydown", (e) => {
  if ($("#view-label").hidden || !L.label) return;
  if (e.target.matches("input, textarea, select")) { if (e.key === "Escape") e.target.blur(); return; }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") { e.preventDefault(); return undo(); }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (L.tagMode && /^[1-8]$/.test(e.key)) { L.tagMode = false; e.preventDefault(); return toggleTag(Number(e.key) - 1); }
  L.tagMode = false;
  if (e.key.toLowerCase() === "p") {
    e.preventDefault();
    if (!e.repeat) startHold(defaultEvent());
    return;
  }
  const fn = KEYMAP[e.key] || KEYMAP[e.key.toLowerCase()];
  if (fn) { e.preventDefault(); return fn(e); }
  const lab = labels().find((x) => x.key === e.key.toLowerCase());   // label keys (Setup page)
  if (lab) { e.preventDefault(); setSelectedLabel(lab.value); }
});
document.addEventListener("keyup", (e) => { if (e.key.toLowerCase() === "p") finishHold(); });

/** Hold (P, or a Hold button): mark `value` from the frame where the hold starts to where it ends. */
function startHold(value) {
  if (L.holdStart != null) return;
  L.holdStart = L.frame;
  L.holdLabel = value;
  $(`#hold-btns [data-value="${value}"]`)?.classList.add("down");
  render();
}
function finishHold() {
  document.querySelectorAll("#hold-btns .down").forEach((b) => b.classList.remove("down"));
  if (L.holdStart == null) return;
  const a = Math.min(L.holdStart, L.frame), b = Math.max(L.holdStart, L.frame);
  L.holdStart = null;
  snapshot();
  L.sel = paint({ start_frame: a, end_frame: b, label: L.holdLabel, origin: "manual", tags: [] });
  changed();
}

// ---------------------------------------------------------------- control bar

const togglePlay = () => (player.paused ? player.play() : player.pause());
const ACTIONS = {
  prev: () => goCandidate(-1),
  next: () => goCandidate(1),
  play: togglePlay,
  accept: () => decide("accepted"),
  reject: () => decide("rejected"),
  unsure: () => setUnsure(),
  start: () => setEdge("start"),
  end: () => setEdge("end"),
  delete: () => deleteSelected(),
  undo: () => undo(),
  unwatched: () => playUnwatched(false),
  random: () => playUnwatched(true),
  done: () => setStatus("done"),
  skip: () => setStatus("skip"),
  help: () => ($("#help").hidden = !$("#help").hidden),
  "set-label": (btn) => setSelectedLabel(Number(btn.dataset.value)),
};
const labelMain = $("#label-main");
// Buttons must not take the keyboard focus: a focused button would also react to Space.
labelMain.addEventListener("mousedown", (e) => { if (e.target.closest("button[data-act]")) e.preventDefault(); });
labelMain.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn || !L.label || btn.dataset.act === "hold") return;
  ACTIONS[btn.dataset.act](btn);
});

/** Set the label of the selected segment: an event label (1 to 9), 0 background, or -1 ignore. */
function setSelectedLabel(value) {
  if (L.sel < 0) return flash("Select a segment first (click it on the timeline)");
  snapshot();
  const s = L.label.segments[L.sel];
  s.label = value;
  if (s.candidate_id) L.label.decisions[s.candidate_id] = value >= 1 ? "accepted" : value === 0 ? "rejected" : "unsure";
  changed();
}

const holdBtns = $("#hold-btns");
holdBtns.addEventListener("pointerdown", (e) => {
  const btn = e.target.closest('button[data-act="hold"]');
  if (!btn || !L.label) return;
  try { btn.setPointerCapture(e.pointerId); } catch { /* not an active pointer */ }
  startHold(Number(btn.dataset.value));
});
holdBtns.addEventListener("pointerup", finishHold);
holdBtns.addEventListener("pointercancel", finishHold);

const speedInput = $("#speed");
speedInput.addEventListener("input", () => { player.playbackRate = SPEEDS[Number(speedInput.value)]; render(); });
speedInput.addEventListener("pointerup", () => speedInput.blur());   // give the keys back to the page
speedInput.addEventListener("change", () => speedInput.blur());
player.addEventListener("play", () => render());
$("#help-close").onclick = () => ($("#help").hidden = true);
$("#lbl-status").onclick = () => { if (L.label && L.label.status !== "todo") { setStatus("todo"); flash("Reopened"); } };

// ---------------------------------------------------------------- drawing

let toastTimer = null;
function flash(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 1500);
}

let cssCache = {};
const css = (name) => (cssCache[name] ??= getComputedStyle(document.documentElement).getPropertyValue(name).trim());
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { cssCache = {}; render(); });
const TRACKS = { cand: [4, 12], mine: [22, 20], watched: [46, 6] };   // y, height (canvas is 72 px)
const TICKS_Y = 58;
const TRACK_NAMES = [["suggested", "cand"], ["labels", "mine"], ["watched", "watched"]];

function segColor(s) {
  if (s.label === 0 && s.origin === "grok_rejected") return css("--rejected");
  return labelColor(s.label);
}

function drawTimeline(canvas, t0, t1) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const x = (t) => ((t - t0) / (t1 - t0)) * w;
  const fx = (f) => x(f / L.fps), fw = (a, b) => Math.max(1, fx(b + 1) - fx(a));

  ctx.font = "10px -apple-system, sans-serif";

  for (const u of L.unusable) {
    ctx.fillStyle = css("--unusable");
    ctx.fillRect(x(u.start_s), TRACKS.cand[0] + 13, Math.max(1, x(u.end_s) - x(u.start_s)), 4);
  }
  L.cands.forEach((c, i) => {
    ctx.globalAlpha = 0.35 + 0.65 * c.confidence;
    ctx.fillStyle = css("--cand");
    ctx.fillRect(x(c.start_s), TRACKS.cand[0], Math.max(2, x(c.end_s) - x(c.start_s)), 12);
    ctx.globalAlpha = 1;
    if (i === L.cand) { ctx.strokeStyle = css("--text"); ctx.lineWidth = 2; ctx.strokeRect(x(c.start_s), TRACKS.cand[0], Math.max(2, x(c.end_s) - x(c.start_s)), 12); }
    const d = L.label.decisions[c.id];
    if (d && x(c.end_s) - x(c.start_s) > 10) {
      ctx.fillStyle = css("--text");
      ctx.fillText({ accepted: "✓", rejected: "✗", unsure: "?" }[d], x(c.start_s) + 2, TRACKS.cand[0] + 10);
    }
  });

  L.label.segments.forEach((s, i) => {
    ctx.fillStyle = segColor(s);
    ctx.fillRect(fx(s.start_frame), TRACKS.mine[0], fw(s.start_frame, s.end_frame), TRACKS.mine[1]);
    if (i === L.sel) { ctx.strokeStyle = css("--text"); ctx.lineWidth = 2; ctx.strokeRect(fx(s.start_frame), TRACKS.mine[0], fw(s.start_frame, s.end_frame), TRACKS.mine[1]); }
  });
  for (const [a, b, v] of [[L.pendingStart, L.frame, defaultEvent()], [L.holdStart, L.frame, L.holdLabel]]) {
    if (a == null) continue;
    ctx.fillStyle = labelColor(v);
    ctx.globalAlpha = 0.4;
    ctx.fillRect(fx(Math.min(a, b)), TRACKS.mine[0], fw(Math.min(a, b), Math.max(a, b)), TRACKS.mine[1]);
    ctx.globalAlpha = 1;
  }

  ctx.fillStyle = css("--watched");
  for (const [a, b] of L.label.watched) ctx.fillRect(fx(a), TRACKS.watched[0], fw(a, b), TRACKS.watched[1]);

  // time ticks
  ctx.fillStyle = css("--muted");
  ctx.strokeStyle = css("--border");
  ctx.lineWidth = 1;
  const span = t1 - t0;
  const step = [0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find((s) => span / s <= 14) || 1200;
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
    ctx.beginPath(); ctx.moveTo(x(t), TICKS_Y - 4); ctx.lineTo(x(t), TICKS_Y); ctx.stroke();
    ctx.fillText(step < 1 ? t.toFixed(1) : fmtTime(t).replace(/\.0$/, ""), x(t) + 2, TICKS_Y + 10);
  }
  // frame ticks when zoomed in
  if (span <= 12) {
    ctx.strokeStyle = css("--border");
    const f0 = Math.max(0, Math.floor(t0 * L.fps)), f1 = Math.min(L.n - 1, Math.ceil(t1 * L.fps));
    for (let f = f0; f <= f1; f++) { ctx.beginPath(); ctx.moveTo(fx(f), TRACKS.mine[0] + TRACKS.mine[1]); ctx.lineTo(fx(f), TRACKS.mine[0] + TRACKS.mine[1] + 3); ctx.stroke(); }
  }
  // track names and the timeline name, on a background so they stay readable over the bars
  const label = (text, tx, ty, align = "left") => {
    ctx.textAlign = align;
    const tw = ctx.measureText(text).width;
    ctx.fillStyle = css("--surface");
    ctx.globalAlpha = 0.8;
    ctx.fillRect(align === "left" ? tx - 2 : tx - tw - 2, ty - 9, tw + 4, 12);
    ctx.globalAlpha = 1;
    ctx.fillStyle = css("--muted");
    ctx.fillText(text, tx, ty);
    ctx.textAlign = "left";
  };
  for (const [name, key] of TRACK_NAMES) label(name, 4, TRACKS[key][0] + Math.min(10, TRACKS[key][1]));
  label(canvas.id === "tl-zoom" ? "zoom: 10 s around the playhead" : "whole video", w - 4, TICKS_Y + 11, "right");
  // playhead
  ctx.strokeStyle = css("--playhead");
  ctx.lineWidth = 1.5;
  const px = span <= 12 ? fx(L.frame) + fw(L.frame, L.frame) / 2 : fx(L.frame);
  ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, h); ctx.stroke();
}

function zoomRange() {
  if (L.zoomFrozen) return L.zoomFrozen;
  const t = timeOf(L.frame);
  let t0 = Math.max(0, t - ZOOM_HALF_S), t1 = t0 + 2 * ZOOM_HALF_S;
  if (t1 > L.duration) { t1 = L.duration; t0 = Math.max(0, t1 - 2 * ZOOM_HALF_S); }
  return [t0, t1];
}

function render() {
  if (!L.label || $("#label-main").hidden) return;
  drawTimeline($("#tl-zoom"), ...zoomRange());
  drawTimeline($("#tl-full"), 0, L.duration);
  $("#lbl-frame").textContent = `frame ${L.frame} / ${L.n - 1} · ${fmtTime(L.frame / L.fps)}`;
  $("#speed-val").textContent = `${player.playbackRate}x`;
  if (document.activeElement !== speedInput) speedInput.value = Math.max(0, SPEEDS.indexOf(player.playbackRate));
  $("#btn-play").textContent = player.paused ? "▶ Play" : "❚❚ Pause";
  const watched = L.label.watched.reduce((n, [a, b]) => n + b - a + 1, 0);
  $("#lbl-watched").textContent = `watched ${Math.round((100 * watched) / L.n)}%`;
  $("#lbl-status").textContent = L.label.status;

  const s = L.label.segments[L.sel];
  if (s) {
    const name = labelName(s.label);
    $("#sel-info").textContent = `${name} · frames ${s.start_frame}–${s.end_frame} · ${((s.end_frame - s.start_frame + 1) / L.fps).toFixed(2)} s · ${s.origin}`;
  } else $("#sel-info").textContent = L.pendingStart != null ? `new segment from frame ${L.pendingStart}…` : "nothing selected";
  $("#sel-tags").innerHTML = (STATE?.tags || []).map((t, i) => `<span class="${s?.tags.includes(t) ? "on" : ""}">${i + 1} ${t}</span>`).join("");

  const c = L.cands[L.cand];
  if (c) {
    const d = L.label.decisions[c.id] || "not decided";
    $("#candidate-info").textContent =
      `${c.id} (${L.cand + 1} of ${L.cands.length}) · ${fmtTime(c.start_s)}–${fmtTime(c.end_s)} · confidence ${c.confidence.toFixed(2)}\n` +
      `decision: ${d}\n"${c.evidence}"` + (c.tags.length ? `\ntags: ${c.tags.join(", ")}` : "");
  }
}
window.addEventListener("resize", render);

/** Click to select a bar and move the playhead; drag to scrub. Scrubbing does not count as watching. */
function timelineDrag(canvas, rangeFn, isZoom) {
  let range = null, pending = null;
  const timeAt = (e) => {
    const rect = canvas.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    return range[0] + frac * (range[1] - range[0]);
  };
  // Seek at most once per animation frame while dragging; flush() applies the last position now.
  const flush = () => { if (pending != null) { seekFrame(pending); pending = null; render(); } };
  const scrubTo = (f) => {
    if (pending == null) requestAnimationFrame(flush);
    pending = f;
  };
  canvas.addEventListener("pointerdown", (e) => {
    if (!L.label) return;
    range = rangeFn();
    if (isZoom) L.zoomFrozen = range;   // keep the zoom view still while dragging in it
    try { canvas.setPointerCapture(e.pointerId); } catch { /* the pointer is not active (synthetic event) */ }
    const t = timeAt(e), f = frameOf(t), y = e.clientY - canvas.getBoundingClientRect().top;
    if (y >= TRACKS.cand[0] && y < TRACKS.cand[0] + 17) {
      const i = L.cands.findIndex((c) => t >= c.start_s && t <= c.end_s);
      if (i >= 0) { L.cand = i; L.sel = L.label.segments.findIndex((s) => s.candidate_id === L.cands[i].id); }
    } else if (y >= TRACKS.mine[0] && y < TRACKS.mine[0] + TRACKS.mine[1]) {
      L.sel = L.label.segments.findIndex((s) => f >= s.start_frame && f <= s.end_frame);
    }
    player.pause();
    scrubTo(f);
  });
  // End the drag when no button is held, also if the release happened outside the window.
  const stop = (e) => {
    if (e && canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    if (range == null) return;
    if (e && e.type === "pointerup") pending = frameOf(timeAt(e));
    flush();
    range = null;
    if (isZoom) { L.zoomFrozen = null; render(); }
  };
  canvas.addEventListener("pointermove", (e) => {
    if (range == null) return;
    if (!(e.buttons & 1)) return stop(e);
    scrubTo(frameOf(timeAt(e)));
  });
  canvas.addEventListener("pointerup", stop);
  canvas.addEventListener("pointercancel", stop);
  canvas.addEventListener("lostpointercapture", () => stop());
}
timelineDrag($("#tl-full"), () => [0, L.duration], false);
timelineDrag($("#tl-zoom"), zoomRange, true);

// ---------------------------------------------------------------- prepare prelabel

// A wizard for the selected video (video_label/manual.py): 1. prepare a directory of frames and a
// README.md, 2. paste the instruction into a labeling LLM session, 3. import its prelabels.json.
// Done steps collapse to "again" buttons; another video starts the page fresh.
const READY_STEPS = ["normalized", "frames", "prelabeled"];
let mpDefaults = null, mpTimer = null;
let mpVideoId = null;   // the video that the page shows
let mpOpen = {};        // steps opened again with their button
let mpSet = null;       // the newest prepared directory of the video
const mpModels = {};    // typed model names, by directory

async function loadPrelabel(videoId) {
  clearTimeout(mpTimer);
  videoId = videoId || CUR?.video_id;
  if (videoId !== mpVideoId) {
    mpVideoId = videoId;
    mpOpen = {};
    $("#mp-status").textContent = $("#mp-import-status").textContent = "";
    $("#mp-readme-box").hidden = true;
  }
  const video = (await api("/api/videos")).find((v) => v.video_id === videoId && READY_STEPS.includes(v.step));
  const data = await api(`/api/manual?video_id=${encodeURIComponent(videoId)}`);
  if (!mpDefaults) {
    mpDefaults = data.defaults;
    $("#mp-fps").value = mpDefaults.sample_fps;
    $("#mp-height").value = mpDefaults.height;
    $("#mp-readme").value = mpDefaults.readme;
    $("#mp-placeholders").textContent = mpDefaults.placeholders.join(" ");
  }
  mpSet = video ? data.sets[0] ?? null : null;   // newest first
  mpImages(video);
  mpNotice(video);
  renderWizard(video);
  // Look for prelabels.json again while the model works.
  if (!$("#view-prelabel").hidden) mpTimer = setTimeout(() => loadPrelabel().catch(() => {}), 5000);
}

function renderWizard(video) {
  const set = mpSet, imported = !!set?.imported_run;
  const show = {
    prepare: !set || mpOpen.prepare,
    instruction: !!set && (!imported || mpOpen.instruction),
    import: !!set && (!imported || mpOpen.import),
  };
  const again = { prepare: !!set && !show.prepare, instruction: imported && !show.instruction, import: imported && !show.import };
  $("#mp-step-prepare").hidden = !show.prepare;
  $("#mp-step-instruction").hidden = !show.instruction;
  $("#mp-step-import").hidden = !show.import;
  for (const b of $("#mp-again").querySelectorAll("button")) b.hidden = !again[b.dataset.open];
  $("#mp-again").hidden = !Object.values(again).some(Boolean);
  $("#mp-prepare").disabled = !video;
  if (!video) $("#mp-status").textContent = "This video is not processed yet.";
  if (!set) return;

  $("#mp-summary").textContent = `Samples prepared for prelabel: ${set.n_images} images · ${set.sample_fps} fps`;
  $("#mp-path").textContent = set.path;
  $("#mp-instruction").textContent = set.instruction;
  const a = set.answer;
  $("#mp-answer").className = `small ${a == null ? "muted" : a.ok ? "ok" : "err"}`;
  $("#mp-answer").textContent = a == null ? "Waiting for prelabels.json (the page looks again every 5 s)."
    : a.ok ? `prelabels.json is valid: ${a.candidates} candidates, ${a.unusable} unusable ranges` + (a.model ? `, model ${a.model}.` : ".")
    : `prelabels.json is not valid: ${a.error}`;
  const model = $("#mp-model");
  if (document.activeElement !== model) model.value = mpModels[set.name] ?? (a?.model || "");
  $("#mp-import").disabled = !a?.ok;
}

/** The orange notice: the video already has suggested events from an imported run. */
function mpNotice(video) {
  const box = $("#mp-done");
  box.hidden = !video?.prelabel_run;
  if (box.hidden) return;
  box.innerHTML = "";
  box.append(`This video is already pre-labeled: ${video.candidates ?? 0} suggested events from the run ${video.prelabel_run}. `
    + "If you import another prelabels.json, it replaces them. ");
  const link = document.createElement("a");
  link.href = `#label/${video.video_id}`;
  link.textContent = "Label the video";
  box.appendChild(link);
}

function mpImages(video) {
  const n = video ? Math.ceil(video.duration_s * Number($("#mp-fps").value)) : 0;
  $("#mp-images").textContent = n ? `about ${n} images` : "";
}

const copyText = (text, what) => navigator.clipboard.writeText(text).then(() => flash(`${what} copied`), () => flash("Cannot copy"));
const reveal = (path) => post("/api/export/reveal", { path }).catch((e) => flash(e.message));

$("#mp-again").onclick = (e) => {
  const step = e.target.closest("button")?.dataset.open;
  if (!step) return;
  mpOpen[step] = true;
  loadPrelabel().catch(() => {});
};
$("#mp-edit-readme").onclick = () => {
  $("#mp-readme-box").hidden = !$("#mp-readme-box").hidden;
  $("#mp-edit-readme").textContent = $("#mp-readme-box").hidden ? "Edit README.md" : "Hide README.md";
};
$("#mp-fps").addEventListener("input", () => loadPrelabel());
$("#mp-reset-readme").onclick = () => { if (mpDefaults) $("#mp-readme").value = mpDefaults.readme; };
$("#mp-prepare").onclick = async () => {
  const video_id = CUR?.video_id;
  if (!video_id) return flash("Choose a video");
  $("#mp-status").textContent = "Extracting the frames…";
  $("#mp-prepare").disabled = true;
  try {
    const set = await post("/api/manual/prepare", {
      video_id, sample_fps: Number($("#mp-fps").value), height: Number($("#mp-height").value), readme: $("#mp-readme").value,
    });
    $("#mp-status").textContent = "";
    $("#mp-import-status").textContent = "";
    flash(`Prepared ${set.n_images} images`);
    mpOpen = {};
  } catch (e) { $("#mp-status").textContent = e.message; }
  await loadPrelabel();
};
$("#mp-copy").onclick = () => mpSet && copyText(mpSet.instruction, "Instruction");
$("#mp-copy-path").onclick = () => mpSet && copyText(mpSet.path, "Path");
$("#mp-folder").onclick = () => mpSet && reveal(mpSet.path);
$("#mp-model").oninput = () => { if (mpSet) mpModels[mpSet.name] = $("#mp-model").value; };
$("#mp-import").onclick = async () => {
  if (!mpSet) return;
  try {
    const r = await post(`/api/manual/${mpSet.name}/import`, { model: $("#mp-model").value });
    $("#mp-import-status").textContent = "";
    flash(`Imported ${r.candidates} suggested events`);
    mpOpen = {};
  } catch (e) { $("#mp-import-status").className = "small err"; $("#mp-import-status").textContent = e.message; }
  await loadPrelabel();
};

// ---------------------------------------------------------------- start

refreshState().then(() => {
  // A first start (or a missing setting) opens the Setup page; otherwise the video list.
  if (!(STATE.settings.libraries.length && STATE.settings.labeler)) location.hash = "#setup";
  else if (!location.hash) location.hash = "#jobs";
  route();
});
