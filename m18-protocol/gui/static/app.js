"use strict";

const $ = (id) => document.getElementById(id);
let connected = false;
let autoTimer = null;
let lastSnapshot = null;

async function api(path, method = "GET", body) {
  const opt = { method };
  if (body !== undefined) {
    opt.headers = { "Content-Type": "application/json" };
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  return r.json();
}

async function loadPorts() {
  const data = await api("/api/ports");
  const sel = $("port");
  sel.innerHTML = "";
  (data.ports || []).forEach((p) => {
    const o = document.createElement("option");
    o.value = p.device;
    o.textContent = p.desc ? `${p.device} — ${p.desc}` : p.device;
    sel.appendChild(o);
  });
  if (!data.ports || data.ports.length === 0) {
    const o = document.createElement("option");
    o.textContent = "(no serial ports found)";
    o.value = "";
    sel.appendChild(o);
  }
}

function setConnected(on, port) {
  connected = on;
  $("dot").classList.toggle("off", !on);
  const st = $("status");
  st.classList.toggle("off", !on);
  st.textContent = on ? `Connected · ${port}` : "Not connected";
  $("connect").textContent = on ? "Disconnect" : "Connect";
  $("connect").classList.toggle("green", !on);
  ["idle", "read", "raw", "writenote", "export"].forEach((id) => {
    $(id).disabled = !on;
  });
}

async function doConnect() {
  if (connected) {
    await api("/api/disconnect", "POST");
    setConnected(false);
    stopAuto();
    await refreshLog();
    return;
  }
  const port = $("port").value;
  const res = await api("/api/connect", "POST", { port });
  await refreshLog();
  if (res.ok) {
    setConnected(true, port);
    await doRead();
  } else {
    alert("Connect failed: " + (res.error || "unknown"));
  }
}

function fillPct(v) {
  // Map a Li-ion cell 3.0–4.2 V onto 0–100% of the bar.
  return Math.max(4, Math.min(100, ((v - 3.0) / 1.2) * 100));
}

function renderCells(cells) {
  const box = $("cells");
  box.innerHTML = "";
  if (!cells || cells.length === 0) {
    box.innerHTML = '<div class="kv"><span class="k">no cell data</span></div>';
    return;
  }
  cells.forEach((v, i) => {
    const row = document.createElement("div");
    row.className = "cell";
    row.innerHTML =
      `<span class="lab">Cell ${i + 1}</span>` +
      `<span class="track"><span class="fill" style="width:${fillPct(v)}%"></span></span>` +
      `<span class="cv">${v.toFixed(3)} V</span>`;
    box.appendChild(row);
  });
}

function num(v) { return (v === null || v === undefined) ? "--" : v; }

function render(s) {
  if (!s || !s.ok) return;
  lastSnapshot = s;
  $("health").textContent = s.health === null ? "--" : s.health;
  const state = $("healthstate");
  if (s.health === null) { state.textContent = "—"; state.className = "state"; }
  else if (s.health >= 90) { state.textContent = "● Good"; state.className = "state"; }
  else if (s.health >= 70) { state.textContent = "● Fair"; state.className = "state warn"; }
  else { state.textContent = "● Weak"; state.className = "state bad"; }

  $("packv").textContent = s.pack_v != null ? s.pack_v.toFixed(2) : "--";
  $("temp").textContent = s.temp != null ? s.temp.toFixed(1) : "--";
  $("imbalance").textContent = num(s.imbalance_mv);

  renderCells(s.cells);

  $("serial").textContent = s.serial || "--";
  $("celltype").textContent = num(s.cell_type);
  $("manufactured").textContent = (s.manufactured || "--").split(" ")[0];
  $("firstcharge").textContent = (s.first_charge || "--").split(" ")[0];
  $("lastcharge").textContent = (s.last_charge || "--").split(" ")[0];
  $("note").textContent = s.note || "--";

  const c = s.counters || {};
  $("total_charges").textContent = num(c.total_charges);
  $("redlink_charges").textContent = num(c.redlink_charges);
  $("dumb_charges").textContent = num(c.dumb_charges);
  $("overheat").textContent = num(c.overheat);
  $("lowvolt").textContent = num(c.lowvolt);
  const ws = c.discharge_ws;
  $("discharge").textContent = ws != null ? (ws / 3600).toFixed(0) + " Wh" : "--";
}

async function refreshLog() {
  const data = await api("/api/log");
  const el = $("log");
  el.textContent = (data.lines || []).join("\n");
  el.scrollTop = el.scrollHeight;
}

async function doRead() {
  const s = await api("/api/read");
  render(s);
  await refreshLog();
}

async function doRaw() {
  const data = await api("/api/raw");
  $("rawtext").textContent = data.text || data.error || "(no data)";
  $("rawdlg").showModal();
}

async function doIdle() {
  await api("/api/idle", "POST");
  await refreshLog();
}

async function doWriteNote() {
  const text = prompt("Note to write to the battery (max 20 chars):",
                      lastSnapshot ? lastSnapshot.note : "");
  if (text === null) return;
  const res = await api("/api/note", "POST", { text });
  if (!res.ok) alert("Write failed: " + (res.error || "unknown"));
  await refreshLog();
  await doRead();
}

function exportCsv() {
  if (!lastSnapshot) return;
  const s = lastSnapshot, c = s.counters || {};
  const rows = [
    ["field", "value"],
    ["serial", s.serial], ["cell_type", s.cell_type],
    ["manufactured", s.manufactured], ["first_charge", s.first_charge],
    ["last_charge", s.last_charge], ["note", s.note],
    ["pack_v", s.pack_v], ["temp_c", s.temp], ["health", s.health],
    ["imbalance_mv", s.imbalance_mv],
  ];
  (s.cells || []).forEach((v, i) => rows.push([`cell_${i + 1}_v`, v]));
  Object.entries(c).forEach(([k, v]) => rows.push([k, v]));
  const csv = rows.map((r) => r.map((x) => `"${x ?? ""}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "m18-" + (s.serial || "battery").replace(/[^\w-]/g, "_") + ".csv";
  a.click();
}

function startAuto() { stopAuto(); autoTimer = setInterval(doRead, 3000); }
function stopAuto() { if (autoTimer) clearInterval(autoTimer); autoTimer = null; }

function wire() {
  $("connect").onclick = doConnect;
  $("rescan").onclick = loadPorts;
  $("read").onclick = doRead;
  $("raw").onclick = doRaw;
  $("idle").onclick = doIdle;
  $("writenote").onclick = doWriteNote;
  $("export").onclick = exportCsv;
  $("autorefresh").onchange = (e) => {
    if (e.target.checked && connected) startAuto(); else stopAuto();
  };
}

async function init() {
  wire();
  await loadPorts();
  const st = await api("/api/status");
  if (st.connected) { setConnected(true, st.port); await doRead(); }
  await refreshLog();
}

init();
