/* llrpkit dashboard — vanilla JS, no build step.
   Charts are hand-rolled SVG following the project's dataviz rules:
   single blue series (no legend needed), 2px lines, hairline grid,
   muted axis ink, crosshair + tooltip on hover, status colors reserved
   for state and always paired with an icon + label. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  demo: false,
  readers: [],
  current: null,          // active reader id
  rate: {},               // readerId -> [{t, v}], capped
  health: {},             // readerId -> {antenna: {...}}
  stats: {},              // readerId -> latest stats event
  tags: [],               // rows for the current reader, newest first
  alerts: [],             // [{at, reader, kind, antenna, message}]
  modes: [],              // annotated modes of current reader
  spark: {},              // readerId -> {antenna: [values]}
};

const RATE_CAP = 90;
const TAG_CAP = 200;

/* ---------------------------- helpers --------------------------------- */

async function api(path, options = {}) {
  if (options.body !== undefined) {
    options.headers = { "content-type": "application/json", ...(options.headers || {}) };
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

const fmt = {
  rssi: (v) => (v == null ? "–" : v.toFixed(2)),
  time: (t) => new Date(t * 1000).toLocaleTimeString([], { hour12: false }),
  num: (v) => (v == null ? "–" : v.toLocaleString()),
};

function currentReader() {
  return state.readers.find((r) => r.id === state.current) || null;
}

/* ---------------------------- websocket -------------------------------- */

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => $("#ws-dot").classList.add("on");
  ws.onclose = () => {
    $("#ws-dot").classList.remove("on");
    setTimeout(connectWS, 1500);
  };
  ws.onmessage = (event) => handleEvent(JSON.parse(event.data));
}

function handleEvent(msg) {
  switch (msg.type) {
    case "state":
      state.demo = msg.demo;
      setReaders(msg.readers);
      break;
    case "readers":
      setReaders(msg.items);
      break;
    case "tags":
      if (msg.reader === state.current) {
        for (const row of msg.items) state.tags.unshift(row);
        state.tags.length = Math.min(state.tags.length, TAG_CAP);
        renderTagTable();
      }
      break;
    case "stats": {
      state.stats[msg.reader] = msg;
      const series = (state.rate[msg.reader] ||= []);
      series.push({ t: Date.now() / 1000, v: msg.reads_per_sec });
      if (series.length > RATE_CAP) series.shift();
      if (msg.reader === state.current) {
        renderTiles();
        renderRateCharts();
      }
      break;
    }
    case "health": {
      state.health[msg.reader] = msg.antennas;
      const sparks = (state.spark[msg.reader] ||= {});
      for (const [ant, snap] of Object.entries(msg.antennas)) {
        const arr = (sparks[ant] ||= []);
        arr.push(snap.reads_per_sec ?? 0);
        if (arr.length > 60) arr.shift();
      }
      if (msg.reader === state.current) renderAntennaCards();
      break;
    }
    case "alert":
      state.alerts.unshift(msg);
      state.alerts.length = Math.min(state.alerts.length, 80);
      toast(msg);
      renderAlertLog();
      if (msg.reader === state.current) renderAntennaCards();
      break;
  }
}

/* ---------------------------- readers ---------------------------------- */

function setReaders(list) {
  state.readers = list;
  if (!currentReader()) state.current = list.length ? list[0].id : null;
  $("#demo-badge").hidden = !state.demo;
  renderReaderPicker();
  renderReaderCards();
  if (state.current) {
    populateTuningForm();
    loadModes();
  }
}

function renderReaderPicker() {
  const picker = $("#reader-picker");
  picker.innerHTML = "";
  for (const r of state.readers) {
    const opt = document.createElement("option");
    opt.value = r.id;
    opt.textContent = `${r.host}:${r.port}` + (r.connected ? "" : " (offline)");
    picker.appendChild(opt);
  }
  if (state.current) picker.value = state.current;
  picker.disabled = state.readers.length <= 1;
}

$("#reader-picker").addEventListener("change", (e) => {
  state.current = e.target.value;
  state.tags = [];
  renderAll();
  populateTuningForm();
  loadModes();
});

async function renderReaderCards() {
  const host = $("#reader-cards");
  host.innerHTML = "";
  if (!state.readers.length) {
    host.innerHTML = '<p class="empty-note">No readers yet — connect one above.</p>';
    return;
  }
  for (const r of state.readers) {
    const card = document.createElement("div");
    card.className = "reader-card";
    const status = r.connected
      ? '<span class="status-line status-good"><span class="icon">●</span> connected</span>'
      : '<span class="status-line status-critical"><span class="icon">✕</span> offline</span>';
    card.innerHTML = `
      <h3>${r.host}:${r.port}</h3>
      ${status}
      <div class="kv"><span>Model</span><b>${r.model_number ?? "–"}</b></div>
      <div class="kv"><span>Firmware</span><b>${r.firmware ?? "–"}</b></div>
      <div class="kv"><span>Antenna ports</span><b>${r.max_antennas ?? "–"}</b></div>
      <div class="kv"><span>Octane</span><b>${r.is_impinj ? "yes" : "no"}</b></div>
      <div class="kv"><span>Temperature</span><b class="temp">–</b></div>
      <div class="row">
        <button class="btn" data-remove="${r.id}">Remove</button>
      </div>`;
    host.appendChild(card);
    api(`/api/readers/${r.id}/temperature`)
      .then((t) => {
        card.querySelector(".temp").textContent = t.celsius == null ? "–" : `${t.celsius} °C`;
      })
      .catch(() => {});
  }
  host.querySelectorAll("[data-remove]").forEach((btn) =>
    btn.addEventListener("click", () => api(`/api/readers/${btn.dataset.remove}`, { method: "DELETE" }).catch(alertError))
  );
}

$("#add-reader-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#add-error").textContent = "";
  try {
    await api("/api/readers", {
      method: "POST",
      body: { host: $("#add-host").value.trim(), port: Number($("#add-port").value) },
    });
    $("#add-host").value = "";
  } catch (err) {
    $("#add-error").textContent = err.message;
  }
});

/* ---------------------------- live panel -------------------------------- */

function renderTiles() {
  const s = state.stats[state.current] || {};
  $("#tile-rate").textContent = s.reads_per_sec != null ? s.reads_per_sec.toFixed(1) : "–";
  $("#tile-unique").textContent = fmt.num(s.unique);
  $("#tile-total").textContent = fmt.num(s.total);
  const running = $("#tile-running");
  running.innerHTML = s.running
    ? '<span class="status-good">▶ running</span>'
    : '<span class="status-warning">⏸ stopped</span>';
}

function renderTagTable() {
  const epcNeedle = $("#epc-filter").value.trim().toLowerCase();
  const antNeedle = $("#antenna-filter").value;
  const body = $("#tag-rows");
  const rows = state.tags
    .filter((t) => (!epcNeedle || t.epc.includes(epcNeedle)) && (!antNeedle || String(t.antenna) === antNeedle))
    .slice(0, 30);
  body.innerHTML = rows
    .map(
      (t) => `<tr>
        <td class="epc">${t.epc}</td>
        <td class="num">${t.antenna ?? "–"}</td>
        <td class="num">${fmt.rssi(t.rssi)}</td>
        <td class="num">${t.phase != null ? t.phase.toFixed(1) + "°" : "–"}</td>
        <td class="num">${t.channel ?? "–"}</td>
        <td class="num">${fmt.time(t.at)}</td>
      </tr>`
    )
    .join("");
  const ants = [...new Set(state.tags.map((t) => t.antenna).filter((a) => a != null))].sort((a, b) => a - b);
  const filterSel = $("#antenna-filter");
  const existing = filterSel.value;
  filterSel.innerHTML =
    '<option value="">all antennas</option>' + ants.map((a) => `<option value="${a}">antenna ${a}</option>`).join("");
  filterSel.value = existing;
}
$("#epc-filter").addEventListener("input", renderTagTable);
$("#antenna-filter").addEventListener("change", renderTagTable);

/* ---------------------------- charts ------------------------------------ */

function lineChart(container, points, { height = 180 } = {}) {
  const width = 640;
  const padL = 44, padR = 10, padT = 10, padB = 22;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  if (!points.length) {
    container.innerHTML = '<p class="empty-note">waiting for data…</p>';
    return;
  }
  const vMax = Math.max(1, ...points.map((p) => p.v)) * 1.15;
  const t0 = points[0].t, t1 = points[points.length - 1].t || t0 + 1;
  const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * plotW;
  const y = (v) => padT + plotH - (v / vMax) * plotH;
  const ticks = 4;
  let grid = "", labels = "";
  for (let i = 0; i <= ticks; i++) {
    const v = (vMax / ticks) * i;
    const gy = y(v);
    grid += `<line x1="${padL}" x2="${width - padR}" y1="${gy}" y2="${gy}" class="gridline"/>`;
    labels += `<text x="${padL - 7}" y="${gy + 3.5}" class="axis-label" text-anchor="end">${Math.round(v)}</text>`;
  }
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="line chart">
      <style>
        .gridline { stroke: var(--grid); stroke-width: 1; }
        .axis-label { fill: var(--text-muted); font-size: 10px; font-variant-numeric: tabular-nums; }
        .series { stroke: var(--series-1); stroke-width: 2; fill: none; stroke-linejoin: round; }
        .baseline { stroke: var(--baseline); stroke-width: 1; }
        .crosshair { stroke: var(--baseline); stroke-width: 1; stroke-dasharray: 3 3; }
        .hover-dot { fill: var(--series-1); stroke: var(--surface-1); stroke-width: 2; }
      </style>
      ${grid}${labels}
      <line x1="${padL}" x2="${width - padR}" y1="${padT + plotH}" y2="${padT + plotH}" class="baseline"/>
      <path class="series" d="${path}"/>
      <line class="crosshair" y1="${padT}" y2="${padT + plotH}" x1="-10" x2="-10" data-role="crosshair" visibility="hidden"/>
      <circle class="hover-dot" r="4.5" cx="-10" cy="-10" data-role="dot" visibility="hidden"/>
      <rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent" data-role="hover"/>
    </svg>`;
  const svg = container.querySelector("svg");
  const hover = svg.querySelector('[data-role="hover"]');
  const crosshair = svg.querySelector('[data-role="crosshair"]');
  const dot = svg.querySelector('[data-role="dot"]');
  const tooltip = $("#tooltip");
  hover.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * width;
    let best = points[0], bd = Infinity;
    for (const p of points) {
      const d = Math.abs(x(p.t) - mx);
      if (d < bd) { bd = d; best = p; }
    }
    crosshair.setAttribute("x1", x(best.t));
    crosshair.setAttribute("x2", x(best.t));
    crosshair.removeAttribute("visibility");
    dot.setAttribute("cx", x(best.t));
    dot.setAttribute("cy", y(best.v));
    dot.removeAttribute("visibility");
    tooltip.hidden = false;
    tooltip.innerHTML = `<span class="tip-label">${fmt.time(best.t)}</span><br><b>${best.v.toFixed(1)}</b> reads/s`;
    tooltip.style.left = `${e.clientX + 14}px`;
    tooltip.style.top = `${e.clientY - 10}px`;
  });
  hover.addEventListener("mouseleave", () => {
    tooltip.hidden = true;
    crosshair.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
  });
}

function sparkline(container, values) {
  const width = 200, height = 36, pad = 3;
  if (!values.length) { container.innerHTML = ""; return; }
  const vMax = Math.max(1, ...values);
  const x = (i) => pad + (i / Math.max(1, values.length - 1)) * (width - 2 * pad);
  const y = (v) => height - pad - (v / vMax) * (height - 2 * pad);
  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const last = values[values.length - 1];
  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="reads per second, last minute">
      <title>${last.toFixed(1)} reads/s now (last 60 s)</title>
      <path d="${path}" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round"/>
      <circle cx="${x(values.length - 1)}" cy="${y(last)}" r="3" fill="var(--series-1)"/>
    </svg>`;
}

function renderRateCharts() {
  const points = state.rate[state.current] || [];
  lineChart($("#rate-chart"), points);
  lineChart($("#tuning-chart"), points, { height: 150 });
}

/* ---------------------------- antennas ---------------------------------- */

function statusFor(snap) {
  if (!snap.connected) return ["status-critical", "✕", "disconnected"];
  if (snap.quiet_alert_active) return ["status-warning", "⚠", "quiet — was reading, now silent"];
  if (snap.reads > 0) return ["status-good", "●", "reading"];
  return ["status-good", "○", "connected, no reads yet"];
}

function renderAntennaCards() {
  const host = $("#antenna-cards");
  const health = state.health[state.current] || {};
  const reader = currentReader();
  const portCount = reader?.max_antennas || 0;
  const ports = new Set([
    ...Object.keys(health).map(Number),
    ...Array.from({ length: portCount }, (_, i) => i + 1),
  ]);
  host.innerHTML = "";
  for (const port of [...ports].sort((a, b) => a - b)) {
    const snap = health[port] || { connected: true, reads: 0 };
    const [cls, icon, label] = statusFor(snap);
    const card = document.createElement("div");
    card.className = "antenna-card";
    card.innerHTML = `
      <h3>Antenna ${port}</h3>
      <div class="status-line ${cls}"><span class="icon">${icon}</span> ${label}</div>
      <div class="kv"><span>Reads/sec</span><b>${(snap.reads_per_sec ?? 0).toFixed(1)}</b></div>
      <div class="kv"><span>Total reads</span><b>${fmt.num(snap.reads ?? 0)}</b></div>
      <div class="kv"><span>Unique tags</span><b>${fmt.num(snap.unique_epcs ?? 0)}</b></div>
      <div class="kv"><span>RSSI last / mean</span><b>${fmt.rssi(snap.rssi_last_dbm)} / ${fmt.rssi(snap.rssi_mean_dbm)}</b></div>
      <div class="spark"></div>`;
    host.appendChild(card);
    sparkline(card.querySelector(".spark"), (state.spark[state.current] || {})[port] || []);
  }
}

function alertIcon(kind) {
  return { quiet: "⚠", disconnected: "✕", connected: "✓", recovered: "✓", exception: "⚠" }[kind] || "•";
}

function renderAlertLog() {
  $("#alert-log").innerHTML = state.alerts
    .slice(0, 30)
    .map(
      (a) =>
        `<li><time>${fmt.time(a.at)}</time><span class="icon">${alertIcon(a.kind)}</span>[${a.kind}] ${a.message}</li>`
    )
    .join("") || '<li class="empty-note">no alerts yet — that is a good sign</li>';
}

function toast(alert) {
  if (!["quiet", "disconnected", "exception", "recovered", "connected"].includes(alert.kind)) return;
  const good = ["recovered", "connected"].includes(alert.kind);
  const el = document.createElement("div");
  el.className = `toast ${alert.kind === "disconnected" || alert.kind === "exception" ? "kind-critical" : ""} ${good ? "kind-good" : ""}`;
  el.innerHTML = `${alertIcon(alert.kind)} <b>${alert.kind}</b> — ${alert.message}`;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

/* ---------------------------- tuning ------------------------------------ */

function populateTuningForm() {
  const reader = currentReader();
  if (!reader) return;
  const s = reader.settings || {};
  $("#set-antennas").value = (s.antennas || []).join(",");
  $("#set-session").value = String(s.session ?? 1);
  $("#set-search").value = s.search_mode == null ? "" : String(s.search_mode);
  $("#set-population").value = s.tag_population ?? 32;
  const slider = $("#set-power");
  const useDefault = $("#power-default");
  if (reader.power_min_dbm != null && reader.power_max_dbm != null) {
    slider.min = reader.power_min_dbm;
    slider.max = reader.power_max_dbm;
    slider.step = 0.25;
    slider.value = s.tx_power_dbm ?? reader.power_max_dbm;
  }
  useDefault.checked = s.tx_power_dbm == null;
  slider.disabled = useDefault.checked;
  updatePowerLabel();
}

function updatePowerLabel() {
  $("#power-value").textContent = $("#power-default").checked
    ? "reader default"
    : `${Number($("#set-power").value).toFixed(2)} dBm`;
}
$("#set-power").addEventListener("input", updatePowerLabel);
$("#power-default").addEventListener("change", () => {
  $("#set-power").disabled = $("#power-default").checked;
  updatePowerLabel();
});

async function loadModes() {
  if (!state.current) return;
  try {
    const data = await api(`/api/readers/${state.current}/modes`);
    state.modes = data.modes;
    const sel = $("#set-mode");
    const currentSetting = currentReader()?.settings?.mode_index;
    sel.innerHTML =
      '<option value="">reader default</option>' +
      data.modes.map((m) => `<option value="${m.mode_id}">${m.mode_id} — ${m.name}</option>`).join("");
    sel.value = currentSetting == null ? "" : String(currentSetting);
    updateModeSummary();
  } catch { /* reader gone */ }
}

function updateModeSummary() {
  const id = $("#set-mode").value;
  const mode = state.modes.find((m) => String(m.mode_id) === id);
  $("#mode-summary").textContent = mode ? mode.summary : "Let the reader use its configured default mode.";
}
$("#set-mode").addEventListener("change", updateModeSummary);

function settingsFromForm() {
  const antennas = $("#set-antennas").value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0);
  return {
    antennas,
    session: Number($("#set-session").value),
    search_mode: $("#set-search").value === "" ? null : Number($("#set-search").value),
    mode_index: $("#set-mode").value === "" ? null : Number($("#set-mode").value),
    tx_power_dbm: $("#power-default").checked ? null : Number($("#set-power").value),
    tag_population: Number($("#set-population").value) || 32,
    include_phase: true,
  };
}

$("#tuning-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.current) return;
  try {
    await api(`/api/readers/${state.current}/inventory/start`, { method: "POST", body: settingsFromForm() });
  } catch (err) { alertError(err); }
});

$("#btn-stop").addEventListener("click", async () => {
  if (!state.current) return;
  try { await api(`/api/readers/${state.current}/inventory/stop`, { method: "POST" }); } catch (err) { alertError(err); }
});

$("#btn-suggest").addEventListener("click", async (e) => {
  e.preventDefault();
  if (!state.current) return;
  const dense = $("#sugg-dense").checked, fast = $("#sugg-fast").checked;
  const data = await api(`/api/readers/${state.current}/modes?dense=${dense}&fast=${fast}`);
  $("#suggestion-text").textContent = `mode ${data.suggestion.mode_id} (${data.suggestion.name}) — ${data.suggestion.reason}`;
  $("#btn-use-suggestion").hidden = false;
  $("#btn-use-suggestion").dataset.mode = data.suggestion.mode_id;
});

$("#btn-use-suggestion").addEventListener("click", (e) => {
  $("#set-mode").value = String(e.target.dataset.mode);
  updateModeSummary();
});

/* ---------------------------- profiles ---------------------------------- */

async function loadProfiles() {
  const profiles = await api("/api/profiles").catch(() => []);
  const sel = $("#profile-picker");
  sel.innerHTML =
    '<option value="">— load a profile —</option>' +
    profiles.map((p, i) => `<option value="${i}">${p.name}</option>`).join("");
  sel.onchange = () => {
    const p = profiles[Number(sel.value)];
    if (!p) return;
    $("#set-antennas").value = (p.antennas || []).join(",");
    $("#set-session").value = String(p.session ?? 1);
    $("#set-search").value = p.search_mode == null ? "" : String(p.search_mode);
    $("#set-mode").value = p.mode_index == null ? "" : String(p.mode_index);
    $("#set-population").value = p.tag_population ?? 32;
    $("#power-default").checked = p.tx_power_dbm == null;
    if (p.tx_power_dbm != null) $("#set-power").value = p.tx_power_dbm;
    $("#set-power").disabled = $("#power-default").checked;
    updatePowerLabel();
    updateModeSummary();
  };
}

$("#btn-save-profile").addEventListener("click", async (e) => {
  e.preventDefault();
  const name = $("#profile-name").value.trim() || "profile";
  try {
    await api("/api/profiles", { method: "POST", body: { name, ...settingsFromForm() } });
    await loadProfiles();
    $("#profile-name").value = "";
  } catch (err) { alertError(err); }
});

/* ---------------------------- shell ------------------------------------- */

function alertError(err) {
  toast({ kind: "exception", message: err.message || String(err) });
}

$$(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${tab.dataset.panel}`));
  })
);

function renderAll() {
  renderTiles();
  renderRateCharts();
  renderTagTable();
  renderAntennaCards();
  renderAlertLog();
  renderReaderCards();
}

(async function init() {
  try {
    const s = await api("/api/state");
    state.demo = s.demo;
    setReaders(s.readers);
  } catch { /* server not ready yet */ }
  await loadProfiles();
  renderAll();
  connectWS();
})();
