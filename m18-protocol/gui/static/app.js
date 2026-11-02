"use strict";
/* M18 Akku-Verwaltung – single page app (no framework). */

const view = $("#view");
let SETTINGS = { demo: false };
const ICON = {
  qr: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M1 1h6v6H1zm2 2v2h2V3zM9 1h6v6H9zm2 2v2h2V3zM1 9h6v6H1zm2 2v2h2v-2zM9 9h2v2H9zm2 2h2v2h-2zm2-2h2v2h-2zm0 4h2v2h-2zm-4 0h2v2H9z"/></svg>',
  print: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 6V1.5h8V6M4 12H2V6h12v6h-2"/><rect x="4" y="9.5" width="8" height="5"/></svg>',
  dl: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M8 2v8m-3.5-3.5L8 10l3.5-3.5M2.5 13.5h11"/></svg>',
  plug: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M5 1v4m6-4v4M3 5h10v3a5 5 0 0 1-10 0zm5 8v2"/></svg>',
};

/* ------------------------------------------------------------ routing */
const ROUTES = [
  [/^\/(?:fleet\/?)?$/, () => viewFleet()],
  [/^\/b\/([^/]+)\/?$/, (m) => viewBattery(decodeURIComponent(m[1]))],
  [/^\/read\/?$/, () => viewRead()],
  [/^\/alerts\/?$/, () => viewAlerts()],
  [/^\/settings\/?$/, () => viewSettings()],
];

function navigate(path) {
  if (path !== location.pathname + location.search) history.pushState({}, "", path);
  route();
}

async function route() {
  const path = location.pathname;
  const top = path.split("/")[1] || "fleet";
  $$("nav a").forEach((a) => a.classList.toggle("active",
    a.dataset.nav === (top === "b" ? "fleet" : top)));
  if (readTimer) { clearInterval(readTimer); readTimer = null; }
  for (const [re, fn] of ROUTES) {
    const m = path.match(re);
    if (m) {
      try { await fn(m); } catch (e) { view.innerHTML = `<div class="card empty">Fehler: ${h(e.message)}</div>`; }
      refreshAlertCount();
      return;
    }
  }
  view.innerHTML = `<div class="card empty">Seite nicht gefunden. <a href="/fleet" data-link>Zur Flotte</a></div>`;
}

document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link]");
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  navigate(a.getAttribute("href"));
});
window.addEventListener("popstate", route);

async function refreshAlertCount() {
  const d = await api("/api/alerts?open=1");
  const n = (d.alerts || []).filter((a) => a.severity !== "info").length;
  const el = $("#alertcount");
  el.hidden = n === 0;
  el.textContent = n;
}

/* --------------------------------------------------------- search/scan */
async function resolveAndGo(q) {
  q = (q || "").trim();
  if (!q) return;
  const d = await api("/api/resolve?q=" + encodeURIComponent(q));
  if (d.battery) { navigate("/b/" + encodeURIComponent(d.battery.eserial)); $("#searchq").value = ""; }
  else toast(`Kein Akku zu „${q}“ gefunden`, "err");
}
$("#search").addEventListener("submit", (e) => { e.preventDefault(); resolveAndGo($("#searchq").value); });

// USB/Bluetooth barcode scanners "type" the QR content followed by Enter.
// Capture fast keystrokes outside of input fields and treat them as a scan.
(() => {
  let buf = "", last = 0;
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select"].includes(tag) || e.ctrlKey || e.metaKey || e.altKey) return;
    const now = Date.now();
    if (now - last > 120) buf = "";
    last = now;
    if (e.key === "Enter") { if (buf.length >= 5) { e.preventDefault(); resolveAndGo(buf); } buf = ""; }
    else if (e.key.length === 1) buf += e.key;
  });
})();

/* --------------------------------------------------------------- shared */
function alertItem(a, withBattery) {
  return `<div class="alert-item ${a.acknowledged ? "acked" : ""}" data-id="${a.id}">
    <span><span class="sev ${a.severity}">${SEVERITY[a.severity] || a.severity}</span><br>
      <span class="when">${fmtDate(a.ts)}</span></span>
    <span>${withBattery ? `<a class="who" href="/b/${encodeURIComponent(a.eserial)}" data-link>${h(a.name || a.eserial)}</a> · ` : ""}${h(a.message)}</span>
    <button class="btn small ghost" data-ack="${a.id}" data-val="${a.acknowledged ? 0 : 1}">${a.acknowledged ? "Wieder öffnen" : "Quittieren"}</button>
  </div>`;
}

function wireAcks(root, after) {
  $$("[data-ack]", root).forEach((b) => b.onclick = async () => {
    await api(`/api/alerts/${b.dataset.ack}/${b.dataset.val === "1" ? "ack" : "unack"}`, "POST");
    after();
  });
}

/* ================================================================ FLEET */
const fleetState = Object.assign({ sort: "status", asc: true, q: "", loc: "", status: "", retired: false },
  (() => { try { return JSON.parse(localStorage.getItem("m18.fleet") || "{}"); } catch { return {}; } })());
function saveFleetState() { try { localStorage.setItem("m18.fleet", JSON.stringify(fleetState)); } catch { /* ignore */ } }
const STATUS_ORDER = { critical: 0, weak: 1, watch: 2, good: 3, unknown: 4 };

async function viewFleet() {
  const data = await api("/api/batteries");
  const bats = data.batteries || [];
  const active = bats.filter((b) => !b.retired);
  const withH = active.filter((b) => b.latest && b.latest.health != null);
  const avg = withH.length ? withH.reduce((s, b) => s + b.latest.health, 0) / withH.length : null;
  const bad = active.filter((b) => b.status === "critical" || b.status === "weak").length;
  const openAlerts = active.reduce((s, b) => s + (b.alerts_open.crit || 0) + (b.alerts_open.warn || 0), 0);
  const stale = active.filter((b) => b.stale).length;

  view.innerHTML = `
    <div class="page-head">
      <div><h1>Flotte</h1><div class="sub">${active.length} aktive Akkus${bats.length - active.length ? ` · ${bats.length - active.length} ausgemustert` : ""}</div></div>
      <div class="actions">
        <a class="btn" href="/export/fleet.csv" title="CSV für Excel (Semikolon, Dezimalkomma)">${ICON.dl} CSV</a>
        <a class="btn" id="printlabels" href="/label" target="_blank">${ICON.qr} Etiketten drucken</a>
        <a class="btn primary" href="/read" data-link>${ICON.plug} Akku auslesen</a>
      </div>
    </div>
    <div class="tiles">
      <div class="tile"><div class="k">Aktive Akkus</div><div class="v">${active.length}</div></div>
      <div class="tile"><div class="k">Ø Zustand</div><div class="v c-${healthClass(avg)}">${avg == null ? "–" : NF0.format(avg)}<small> / 100</small></div></div>
      <div class="tile ${bad ? "alert" : ""}"><div class="k">Schwach / kritisch</div><div class="v">${bad}</div></div>
      <div class="tile ${openAlerts ? "alert" : ""}"><div class="k">Offene Warnungen</div><div class="v">${openAlerts}</div></div>
      <div class="tile ${stale ? "warnish" : ""}"><div class="k">Messung fällig <span class="faint">(&gt; ${SETTINGS.thresholds?.stale_days ?? 90} Tage)</span></div><div class="v">${stale}</div></div>
    </div>
    <div class="card">
      <div class="filters">
        <input type="search" id="f-q" placeholder="Filtern: Name, Seriennr., Kommentar …" value="${h(fleetState.q)}">
        <select id="f-loc"><option value="">Alle Standorte</option>${(data.locations || []).map((l) =>
          `<option ${l === fleetState.loc ? "selected" : ""}>${h(l)}</option>`).join("")}</select>
        <select id="f-status"><option value="">Alle Zustände</option>${Object.entries(STATUS).map(([k, s]) =>
          `<option value="${k}" ${k === fleetState.status ? "selected" : ""}>${s.label}</option>`).join("")}</select>
        <label class="check"><input type="checkbox" id="f-ret" ${fleetState.retired ? "checked" : ""}> Ausgemusterte zeigen</label>
        <span class="spacer" style="flex:1"></span>
        <span class="muted" id="f-count"></span>
      </div>
      <div class="table-wrap"><table class="list" id="fleet">
        <thead><tr>
          <th data-sort="status">Status</th><th data-sort="name">Akku</th><th data-sort="model">Modell</th>
          <th data-sort="location">Standort</th><th data-sort="health" style="min-width:130px">Zustand</th>
          <th>Verlauf</th><th data-sort="trend" class="num">Trend/Jahr</th><th data-sort="imb" class="num">Imbalance</th>
          <th data-sort="efc" class="num">Zyklen</th><th data-sort="last">Letzte Messung</th><th data-sort="alerts" class="num">Warn.</th>
        </tr></thead><tbody></tbody></table></div>
    </div>`;

  const key = {
    status: (b) => STATUS_ORDER[b.status] * 1000 + (b.latest?.health ?? 999),
    name: (b) => displayName(b).toLowerCase(), model: (b) => b.model || "",
    location: (b) => (b.location || "~").toLowerCase(), health: (b) => b.latest?.health ?? -1,
    trend: (b) => b.trend?.slope_per_year ?? 0, imb: (b) => b.latest?.imbalance_mv ?? -1,
    efc: (b) => efc(b.latest?.counters, b.capacity_ah) ?? -1, last: (b) => b.days_since_reading ?? 1e9,
    alerts: (b) => (b.alerts_open.crit || 0) * 100 + (b.alerts_open.warn || 0),
  };

  function render() {
    const q = fleetState.q.toLowerCase();
    let rows = bats.filter((b) => (fleetState.retired || !b.retired)
      && (!fleetState.loc || b.location === fleetState.loc)
      && (!fleetState.status || b.status === fleetState.status)
      && (!q || [b.name, b.eserial, b.case_serial, b.comment, b.location, b.model].join(" ").toLowerCase().includes(q)));
    const k = key[fleetState.sort] || key.status;
    rows.sort((a, b) => { const x = k(a), y = k(b); return (x < y ? -1 : x > y ? 1 : 0) * (fleetState.asc ? 1 : -1); });
    $("#f-count").textContent = `${rows.length} angezeigt`;
    $("#printlabels").href = "/label?ids=" + rows.map((b) => b.eserial).join(",");
    $$("#fleet th[data-sort]").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === fleetState.sort);
      th.classList.toggle("asc", fleetState.asc);
    });
    const tb = $("#fleet tbody");
    if (!rows.length) {
      tb.innerHTML = `<tr><td colspan="11" class="empty">${bats.length ? "Keine Akkus für diesen Filter." :
        'Noch keine Akkus erfasst. <a href="/read" data-link>Jetzt den ersten Akku auslesen →</a>'}</td></tr>`;
      return;
    }
    tb.innerHTML = rows.map((b) => {
      const l = b.latest || {}, t = b.trend, ao = b.alerts_open;
      const cyc = efc(l.counters, b.capacity_ah);
      const slope = t ? t.slope_per_year : null;
      return `<tr class="click ${b.retired ? "retired" : ""}" data-es="${h(b.eserial)}">
        <td>${statusBadge(b.status)}</td>
        <td class="name-cell"><b>${h(displayName(b))}</b><span>${h(b.eserial)}</span></td>
        <td class="nw">${h(b.model || "–")}</td>
        <td>${h(b.location || "–")}</td>
        <td>${healthBar(l.health)}</td>
        <td class="c-${healthClass(l.health)}">${sparkline(b.health_series.map((p) => p[1]), { minSpan: 20 })}</td>
        <td class="num ${slope != null && slope <= -15 ? "c-crit" : slope != null && slope <= -8 ? "c-watch" : ""}">${slope == null ? "–" : fmtSigned(slope, NF1)}</td>
        <td class="num ${l.imbalance_mv >= 100 ? "c-crit" : l.imbalance_mv >= 50 ? "c-watch" : ""}">${l.imbalance_mv == null ? "–" : l.imbalance_mv + " mV"}</td>
        <td class="num">${num(cyc)}</td>
        <td class="nw ${b.stale ? "c-watch" : ""}" title="${fmtDateTime(l.ts)}">${fmtAgo(b.days_since_reading)}</td>
        <td class="num nw">${ao.crit ? `<span class="badge crit plain">${ao.crit}</span> ` : ""}${ao.warn ? `<span class="badge watch plain">${ao.warn}</span>` : ""}${!ao.crit && !ao.warn ? '<span class="faint">–</span>' : ""}</td>
      </tr>`;
    }).join("");
    $$("#fleet tbody tr.click").forEach((tr) => tr.onclick = () => navigate("/b/" + encodeURIComponent(tr.dataset.es)));
  }
  $("#f-q").oninput = (e) => { fleetState.q = e.target.value; saveFleetState(); render(); };
  $("#f-loc").onchange = (e) => { fleetState.loc = e.target.value; saveFleetState(); render(); };
  $("#f-status").onchange = (e) => { fleetState.status = e.target.value; saveFleetState(); render(); };
  $("#f-ret").onchange = (e) => { fleetState.retired = e.target.checked; saveFleetState(); render(); };
  $$("#fleet th[data-sort]").forEach((th) => th.onclick = () => {
    if (fleetState.sort === th.dataset.sort) fleetState.asc = !fleetState.asc;
    else { fleetState.sort = th.dataset.sort; fleetState.asc = !["health", "imb", "efc", "alerts", "last"].includes(th.dataset.sort) || th.dataset.sort === "health"; }
    saveFleetState(); render();
  });
  render();
}

/* ============================================================== BATTERY */
async function viewBattery(id) {
  const b = await api("/api/batteries/" + encodeURIComponent(id));
  if (b.ok === false) {
    view.innerHTML = `<div class="card empty"><h2>Akku „${h(id)}“ nicht gefunden</h2>
      <p>Dieser Akku ist noch nicht in der Datenbank. <a href="/read" data-link>Jetzt auslesen und anlegen →</a></p></div>`;
    return;
  }
  const rs = b.readings, last = rs[rs.length - 1], first = rs[0];
  const c = last ? last.counters : {};
  const sf = b.since_first;
  const locs = (await api("/api/batteries")).locations || [];
  const es = encodeURIComponent(b.eserial);
  const openAlerts = b.alerts.filter((a) => !a.acknowledged);

  const deltaTag = (v, unit, goodIfUp) => v == null || v === 0 ? "" :
    `<span class="delta ${(v > 0) === goodIfUp ? "up" : "down"}">${fmtSigned(v, NF1)}${unit} seit ${fmtDate(first.ts)}</span>`;

  view.innerHTML = `
    <div class="page-head">
      <div>
        <div class="sub"><a href="/fleet" data-link class="muted">Flotte</a> / ${h(b.eserial)}</div>
        <h1>${h(displayName(b))} ${statusBadge(b.status)} ${b.retired ? '<span class="badge unknown plain">ausgemustert</span>' : ""}</h1>
        <div class="sub">${h(b.model || "Unbekanntes Modell")}${b.capacity_ah ? ` · ${NF1.format(b.capacity_ah)} Ah` : ""} · E-Seriennr. <span class="mono">${h(b.eserial)}</span>${b.location ? ` · 📍 ${h(b.location)}` : ""}</div>
      </div>
      <div class="actions">
        <a class="btn" href="/label/${es}" target="_blank">${ICON.qr} QR-Etikett</a>
        <a class="btn" href="/report/${es}" target="_blank">${ICON.print} Bericht</a>
        <a class="btn" href="/export/battery/${es}.csv">${ICON.dl} CSV</a>
        <a class="btn primary" href="/read" data-link>${ICON.plug} Neu auslesen</a>
      </div>
    </div>

    <div class="grid g-side">
      <div class="stack">
        <div class="card">
          <h2>Zustand <span class="right faint">Schätzung</span></h2>
          <div class="hero">${gauge(last?.health)}<div class="parts">${partsList(last?.health_parts)}</div></div>
          <p class="hint" style="margin-top:10px">Der Akku meldet keinen echten State-of-Health. Der Wert wird aus den
            Verschleiß-Zählern geschätzt; entscheidend ist der <b>Verlauf</b>.</p>
        </div>
        <div class="card">
          <h2>Stammdaten</h2>
          <form class="form" id="meta">
            <label class="field">Name / Etikett<input name="name" value="${h(b.name)}" placeholder="z. B. A-07"></label>
            <label class="field">Standort<input name="location" list="locs" value="${h(b.location)}" placeholder="z. B. Transporter 1"></label>
            <datalist id="locs">${locs.map((l) => `<option value="${h(l)}">`).join("")}</datalist>
            <div class="row2">
              <label class="field">Gehäuse-Seriennr.<input name="case_serial" value="${h(b.case_serial)}" placeholder="vom Typenschild"></label>
              <label class="field">Nennkapazität (Ah)<input name="capacity_ah" type="number" step="0.5" min="0" value="${b.capacity_ah ?? ""}"></label>
            </div>
            <label class="field">Kommentar<textarea name="comment" rows="3" placeholder="Einsatz, Auffälligkeiten …">${h(b.comment)}</textarea></label>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <button class="btn primary" type="submit">Speichern</button>
              <button class="btn ghost" type="button" id="retire">${b.retired ? "Wieder aktivieren" : "Ausmustern"}</button>
              <button class="btn ghost danger" type="button" id="del">Löschen</button>
            </div>
          </form>
        </div>
        <div class="card">
          <h2>QR-Code</h2>
          <div class="qr-mini"><img src="/qr/${es}.svg" alt="QR-Code">
            <div><div class="mono" style="word-break:break-all">${h(b.qr_url)}</div>
              <div class="hint" style="margin:6px 0">Basis-URL in den <a href="/settings" data-link>Einstellungen</a> änderbar.</div>
              <a class="btn small" href="/label/${es}" target="_blank">Etikett drucken</a>
              <a class="btn small" href="/qr/${es}.png?scale=12" download="qr-${h(b.eserial)}.png">PNG</a></div></div>
        </div>
      </div>

      <div class="stack">
        <div class="card">
          <h2>Kennzahlen <span class="right faint">${last ? "Stand " + fmtDateTime(last.ts) : ""}</span></h2>
          ${last ? `<div class="stats">
            <div class="stat"><div class="k">Zell-Imbalance</div><div class="v ${last.imbalance_mv >= 100 ? "c-crit" : last.imbalance_mv >= 50 ? "c-watch" : ""}">${num(last.imbalance_mv)}<small> mV</small></div><div class="d">${sf ? deltaTag(sf.imbalance_mv, " mV", false) : ""}</div></div>
            <div class="stat"><div class="k">Vollzyklen (äquiv.)</div><div class="v">${num(efc(c, b.capacity_ah))}</div><div class="d muted">${num(counterAh(c))} Ah entladen</div></div>
            <div class="stat"><div class="k">Ladungen</div><div class="v">${num(c.total_charges)}</div><div class="d muted">${num(c.redlink_charges)} Redlink · ${num(c.dumb_charges)} einfach</div></div>
            <div class="stat"><div class="k">Überhitzung</div><div class="v ${c.overheat ? "c-watch" : ""}">${num(c.overheat)}</div></div>
            <div class="stat"><div class="k">Unterspannung</div><div class="v">${num(c.lowvolt)}</div></div>
            <div class="stat"><div class="k">Ladung Zelle &lt; 2,5 V</div><div class="v ${c.lowv_charges ? "c-crit" : ""}">${num(c.lowv_charges)}</div></div>
            <div class="stat"><div class="k">Hergestellt</div><div class="v" style="font-size:16px">${fmtDate(b.manufactured)}</div><div class="d muted">${b.manufactured ? NF1.format((Date.now() - new Date(b.manufactured)) / 864e5 / 365.25) + " Jahre" : ""}</div></div>
            <div class="stat"><div class="k">Messungen</div><div class="v">${rs.length}</div><div class="d muted">seit ${fmtDate(first.ts)}</div></div>
          </div>` : '<p class="muted">Noch keine Messung gespeichert.</p>'}
        </div>
        <div class="card">
          <h2>Alterung – Zustand über Zeit</h2>
          <div class="trendline">${trendText(b.trend)}</div>
          ${healthChart(rs, b.trend)}
        </div>
        <div class="grid g2">
          <div class="card"><h2>Zell-Imbalance</h2>${imbChart(rs)}</div>
          <div class="card"><h2>Ereignisse (kumuliert)</h2>${eventChart(rs)}</div>
        </div>
        <div class="grid g2">
          <div class="card"><h2>Zellspannungen <span class="right faint">${last ? fmtDate(last.ts) : ""}</span></h2>${cellBars(last?.cells)}
            ${last ? `<p class="hint" style="margin-top:10px">Pack ${NF2.format(last.pack_v)} V · ${last.temp_c != null ? NF1.format(last.temp_c) + " °C" : "Temp. –"}</p>` : ""}</div>
          <div class="card"><h2>Entladung kumuliert</h2>${usageChart(rs)}</div>
        </div>
        <div class="card">
          <h2>Warnungen <span class="right">${openAlerts.length ? `<button class="btn small" id="ackall">Alle quittieren</button>` : ""}</span></h2>
          <div id="alerts"><div class="alert-list">${openAlerts.length ? openAlerts.map((a) => alertItem(a, false)).join("") : '<p class="muted">Keine offenen Warnungen.</p>'}</div>
          ${b.alerts.length > openAlerts.length ? `<details style="margin-top:8px"><summary>${b.alerts.length - openAlerts.length} quittierte Warnungen</summary>
            <div class="alert-list">${b.alerts.filter((a) => a.acknowledged).map((a) => alertItem(a, false)).join("")}</div></details>` : ""}</div>
        </div>
        <div class="card">
          <h2>Messverlauf <span class="right faint">${rs.length} Messungen</span></h2>
          <div class="table-wrap"><table class="list">
            <thead><tr><th>Zeitpunkt</th><th class="num">Zustand</th><th class="num">Δ</th><th class="num">Imbalance</th>
              <th class="num">Pack</th><th class="num">Temp.</th><th class="num">Entladen</th><th class="num">Ladungen</th>
              <th class="num">Überh.</th><th class="num">Untersp.</th><th>Quelle</th><th></th></tr></thead>
            <tbody>${rs.slice().reverse().map((r, i, arr) => {
              const prev = arr[i + 1];
              const d = prev && r.health != null && prev.health != null ? r.health - prev.health : null;
              return `<tr><td>${fmtDateTime(r.ts)}</td><td class="num c-${healthClass(r.health)}">${num(r.health)}</td>
                <td class="num ${d < 0 ? "c-weak" : ""}">${d == null ? "" : fmtSigned(d)}</td>
                <td class="num">${num(r.imbalance_mv)} mV</td><td class="num">${r.pack_v != null ? NF2.format(r.pack_v) + " V" : "–"}</td>
                <td class="num">${r.temp_c != null ? NF1.format(r.temp_c) + " °C" : "–"}</td>
                <td class="num">${num(counterAh(r.counters))} Ah</td><td class="num">${num(r.counters.total_charges)}</td>
                <td class="num">${num(r.counters.overheat)}</td><td class="num">${num(r.counters.lowvolt)}</td>
                <td class="faint">${h(r.source)}</td>
                <td><button class="btn small ghost" data-delr="${r.id}" title="Messung löschen">✕</button></td></tr>`;
            }).join("")}</tbody></table></div>
        </div>
      </div>
    </div>`;

  const reload = () => viewBattery(b.eserial).then(refreshAlertCount);
  $("#meta").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target).entries());
    const r = await api("/api/batteries/" + es, "POST", f);
    if (r.ok) { toast("Gespeichert"); reload(); } else toast(r.error || "Fehler", "err");
  };
  $("#retire").onclick = async () => {
    await api("/api/batteries/" + es, "POST", { retired: !b.retired });
    toast(b.retired ? "Wieder aktiviert" : "Ausgemustert"); reload();
  };
  $("#del").onclick = async () => {
    if (!confirm(`Akku „${displayName(b)}“ mit ${rs.length} Messungen endgültig löschen?`)) return;
    await api("/api/batteries/" + es, "DELETE");
    toast("Gelöscht"); navigate("/fleet");
  };
  if ($("#ackall")) $("#ackall").onclick = async () => { await api("/api/alerts/ack_all", "POST", { battery_id: b.id }); reload(); };
  wireAcks($("#alerts"), reload);
  $$("[data-delr]").forEach((btn) => btn.onclick = async () => {
    if (!confirm("Diese Messung löschen?")) return;
    await api("/api/readings/" + btn.dataset.delr, "DELETE");
    reload();
  });
}

/* ================================================================= READ */
let readTimer = null;
let lastRead = null;

async function viewRead() {
  const [st, ports, demo] = await Promise.all([api("/api/status"), api("/api/ports"), api("/api/demo")]);
  view.innerHTML = `
    <div class="page-head"><div><h1>Akku auslesen</h1>
      <div class="sub">Akku an den Adapter anschließen, verbinden, auslesen – die Messung wird mit Zeitstempel gespeichert.</div></div></div>
    <div class="card">
      <h2>Verbindung</h2>
      <div class="conn">
        <span class="dot ${st.connected ? "on" : ""}" id="cdot"></span>
        ${demo.demo ? "" : `<select id="kind" title="Wie ist der Akku angeschlossen?">
          <option value="direct">USB-Seriell-Adapter</option>
          <option value="esp">ESP32-Brücke (USB)</option>
          <option value="wifi">ESP32-Brücke (WLAN)</option></select>
          <input id="host" placeholder="m18-bridge.local oder IP" style="width:220px" hidden>`}
        <select id="port">${(ports.ports || []).map((p) => `<option value="${h(p.device)}" ${p.device === st.port ? "selected" : ""}>${h(p.device)}${p.desc ? " – " + h(p.desc) : ""}</option>`).join("") || '<option value="">(keine seriellen Ports gefunden)</option>'}</select>
        <button class="btn" id="rescan" title="Ports neu suchen">⟳</button>
        <button class="btn ${st.connected ? "" : "primary"}" id="connect">${st.connected ? "Trennen" : "Verbinden"}</button>
        <button class="btn ghost" id="idle" ${st.connected ? "" : "disabled"} title="TX auf Low setzen (Akku schlafen lassen)">Idle</button>
        ${demo.demo ? `<span class="spacer" style="flex:1"></span><label class="check">Simulierter Akku am Adapter:
          <select id="plug">${demo.options.map((o) => `<option value="${o.key}" ${o.key === demo.plugged ? "selected" : ""}>${h(o.label)}</option>`).join("")}</select></label>` : ""}
      </div>
      ${!demo.demo && !(ports.ports || []).length ? `<p class="hint" id="noports" style="margin-top:10px">Kein serieller Port gefunden. USB-Seriell-Adapter oder ESP32-Brücke anschließen und ⟳ drücken (ist <span class="code">pyserial</span> installiert?) – oder „ESP32-Brücke (WLAN)“ wählen.</p>` : ""}
    </div>
    <div class="card">
      <div class="readbox">
        <button class="btn primary big" id="read" ${st.connected ? "" : "disabled"}>Akku auslesen</button>
        <span class="hint" id="readhint">${st.connected ? "Das Auslesen dauert ca. 10–20 Sekunden." : "Zuerst verbinden."}</span>
      </div>
    </div>
    <div id="readresult" style="margin-top:16px"></div>
    <div class="grid g2" style="margin-top:16px">
      <details class="card"><summary>Protokoll</summary><pre class="log" id="log"></pre></details>
      <details class="card"><summary>Erweitert: Notiz in den Akku schreiben</summary>
        <p class="hint" style="margin-bottom:8px">Schreibt bis zu 20 Zeichen in den Notizspeicher des Akkus (Register 0x0023).</p>
        <div class="conn"><input id="notetext" maxlength="20" placeholder="z. B. A-07 Firma" style="flex:1">
        <button class="btn" id="writenote" ${st.connected ? "" : "disabled"}>Schreiben</button></div></details>
    </div>`;

  const setConn = (on) => {
    $("#cdot").classList.toggle("on", on);
    $("#connect").textContent = on ? "Trennen" : "Verbinden";
    $("#connect").classList.toggle("primary", !on);
    ["#read", "#idle", "#writenote"].forEach((s) => $(s).disabled = !on);
    $("#readhint").textContent = on ? "Das Auslesen dauert ca. 10–20 Sekunden." : "Zuerst verbinden.";
  };
  const refreshLog = async () => {
    const d = await api("/api/log");
    const el = $("#log");
    if (!el) return;
    el.textContent = (d.lines || []).join("\n");
    el.scrollTop = el.scrollHeight;
  };
  // Adapter type: direct USB-serial, ESP32 bridge via USB, ESP32 bridge via Wi-Fi
  const kindPref = (() => { try { return JSON.parse(localStorage.getItem("m18.conn") || "{}"); } catch { return {}; } })();
  const syncKind = () => {
    if (!$("#kind")) return;
    const wifi = $("#kind").value === "wifi";
    $("#host").hidden = !wifi;
    $("#port").hidden = wifi;
    $("#rescan").hidden = wifi;
    if ($("#noports")) $("#noports").hidden = wifi;
  };
  if ($("#kind")) {
    const cur = st.port || "";
    $("#kind").value = cur.startsWith("esp:") ? "esp" : /^(https?:\/\/|.*\.local$|\d+\.\d+\.\d+\.\d+)/.test(cur) ? "wifi" : (kindPref.kind || "direct");
    $("#host").value = $("#kind").value === "wifi" && cur ? cur : (kindPref.host || "m18-bridge.local");
    if (cur.startsWith("esp:")) $("#port").value = cur.slice(4);
    $("#kind").onchange = syncKind;
    syncKind();
  }
  const target = () => {
    const kind = $("#kind") ? $("#kind").value : "direct";
    try { localStorage.setItem("m18.conn", JSON.stringify({ kind, host: $("#host") ? $("#host").value : "" })); } catch { /* ignore */ }
    if (kind === "wifi") return $("#host").value.trim();
    return (kind === "esp" ? "esp:" : "") + $("#port").value;
  };
  $("#rescan").onclick = () => viewRead();
  $("#connect").onclick = async () => {
    const on = $("#cdot").classList.contains("on");
    const r = on ? await api("/api/disconnect", "POST") : await api("/api/connect", "POST", { port: target() });
    if (!on && !r.ok) toast("Verbinden fehlgeschlagen: " + (r.error || "?"), "err");
    else if (!on && r.ack === false) toast("Verbunden, aber keine Antwort vom Akku (kein ACK)", "err");
    setConn(!on && r.ok);
    refreshLog();
  };
  $("#idle").onclick = async () => { await api("/api/idle", "POST"); refreshLog(); };
  $("#writenote").onclick = async () => {
    const r = await api("/api/note", "POST", { text: $("#notetext").value });
    toast(r.ok ? "Notiz geschrieben" : "Fehler: " + r.error, r.ok ? "" : "err"); refreshLog();
  };
  if ($("#plug")) $("#plug").onchange = async (e) => { await api("/api/demo/plug", "POST", { key: e.target.value }); };
  $("#read").onclick = async () => {
    const btn = $("#read");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Lese Akku …';
    try {
      const r = await api("/api/read", "POST");
      if (!r.ok) { toast("Auslesen fehlgeschlagen: " + (r.error || "?"), "err"); return; }
      lastRead = r;
      renderReadResult(r);
    } finally {
      btn.disabled = false; btn.textContent = "Akku auslesen"; refreshLog();
    }
  };
  if (lastRead) renderReadResult(lastRead, lastRead._saved);
  refreshLog();
  readTimer = setInterval(refreshLog, 4000);
}

function cmpRow(label, prev, cur, unit = "", nf = NF0, higherIsBetter = null) {
  const d = prev != null && cur != null ? cur - prev : null;
  let cls = "";
  if (d && higherIsBetter !== null) cls = (d > 0) === higherIsBetter ? "up" : "down";
  return `<tr><td>${label}</td><td>${num(prev, nf)}${prev != null ? unit : ""}</td><td><b>${num(cur, nf)}${cur != null ? unit : ""}</b></td>
    <td class="delta ${cls}">${d == null ? "" : fmtSigned(d, nf)}</td></tr>`;
}

function renderReadResult(r, saved) {
  const b = r.battery, p = r.preview, prev = p.previous, c = r.counters;
  const pc = prev ? prev.counters : {};
  const cap = b ? b.capacity_ah : r.capacity_ah;
  const alertsHtml = (list) => list.length ? `<div class="callout ${list.some((a) => a.severity === "crit") ? "crit" : "warn"}" style="margin-top:12px">
      <h3>${list.length} Auffälligkeit${list.length > 1 ? "en" : ""} gegenüber der letzten Messung</h3>
      ${list.map((a) => `<div><span class="sev ${a.severity}">${SEVERITY[a.severity]}</span> ${h(a.message)}</div>`).join("")}</div>` : "";

  let right;
  if (saved) {
    right = `<div class="callout known"><h3>✓ Messung gespeichert</h3>
        <div>${h(displayName(saved.battery))} · Zustand ${num(saved.reading.health)} · ${fmtDateTime(saved.reading.ts)}</div></div>
      ${alertsHtml(saved.alerts)}
      <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">
        <a class="btn primary" href="/b/${encodeURIComponent(saved.battery.eserial)}" data-link>Zum Akku →</a>
        <a class="btn" href="/label/${encodeURIComponent(saved.battery.eserial)}" target="_blank">${ICON.qr} QR-Etikett drucken</a>
      </div>
      ${saved.created ? '<p class="hint" style="margin-top:10px">Tipp: Etikett jetzt drucken und auf den Akku kleben – dann genügt künftig ein Scan.</p>' : ""}`;
  } else if (b) {
    right = `<div class="callout known"><h3>Bekannter Akku: ${h(displayName(b))}</h3>
        <div class="muted">${h(r.model)} · <span class="mono">${h(r.eserial)}</span>${b.location ? " · 📍 " + h(b.location) : ""}</div>
        ${prev ? `<div class="muted">Letzte Messung: ${fmtDateTime(prev.ts)}</div>` : ""}</div>
      ${prev ? `<table class="cmp" style="margin-top:12px"><thead><tr><th></th><th>Letzte</th><th>Jetzt</th><th>Δ</th></tr></thead><tbody>
        ${cmpRow("Zustand", prev.health, p.health, "", NF0, true)}
        ${cmpRow("Imbalance", prev.imbalance_mv, r.imbalance_mv, " mV", NF0, false)}
        ${cmpRow("Entladen", counterAh(pc), counterAh(c), " Ah", NF0, null)}
        ${cmpRow("Vollzyklen", efc(pc, cap), efc(c, cap), "", NF1, null)}
        ${cmpRow("Ladungen", pc.total_charges, c.total_charges, "", NF0, null)}
        ${cmpRow("Überhitzung", pc.overheat, c.overheat, "", NF0, false)}
        ${cmpRow("Unterspannung", pc.lowvolt, c.lowvolt, "", NF0, false)}
        ${cmpRow("Ladung Zelle < 2,5 V", pc.lowv_charges, c.lowv_charges, "", NF0, false)}
      </tbody></table>` : ""}
      ${alertsHtml(p.alerts)}
      <form class="form" id="saveform" style="margin-top:14px">
        <label class="field">Standort (optional aktualisieren)<input name="location" value="${h(b.location)}"></label>
        <button class="btn primary big" type="submit">Messung speichern</button>
      </form>`;
  } else {
    right = `<div class="callout new"><h3>Neuer Akku erkannt</h3>
        <div>${h(r.model)}${r.capacity_ah ? "" : " – Typ unbekannt"} · E-Seriennr. <span class="mono">${h(r.eserial)}</span></div>
        <div class="muted">Hergestellt ${fmtDate(r.manufactured)}${r.note ? ` · Notiz im Akku: „${h(r.note)}“` : ""}</div></div>
      ${alertsHtml(p.alerts)}
      <form class="form" id="saveform" style="margin-top:14px">
        <div class="row2">
          <label class="field">Name / Etikett<input name="name" placeholder="z. B. A-07" value="${h(r.note || "")}" autofocus></label>
          <label class="field">Gehäuse-Seriennr.<input name="case_serial" placeholder="vom Typenschild"></label>
        </div>
        <label class="field">Standort<input name="location" placeholder="z. B. Werkstatt"></label>
        <label class="field">Kommentar<textarea name="comment" rows="2"></textarea></label>
        <button class="btn primary big" type="submit">Akku anlegen &amp; Messung speichern</button>
      </form>`;
  }

  $("#readresult").innerHTML = `<div class="grid g-read">
    <div class="card"><h2>Messwerte <span class="right faint">${h(r.eserial)}</span></h2>
      <div class="hero">${gauge(p.health)}<div class="parts">${partsList(p.health_parts)}</div></div>
      <div class="stats" style="margin:14px 0">
        <div class="stat"><div class="k">Pack</div><div class="v">${r.pack_v != null ? NF2.format(r.pack_v) : "–"}<small> V</small></div></div>
        <div class="stat"><div class="k">Temperatur</div><div class="v">${r.temp != null ? NF1.format(r.temp) : "–"}<small> °C</small></div></div>
        <div class="stat"><div class="k">Imbalance</div><div class="v ${r.imbalance_mv >= 100 ? "c-crit" : r.imbalance_mv >= 50 ? "c-watch" : ""}">${num(r.imbalance_mv)}<small> mV</small></div></div>
        <div class="stat"><div class="k">Vollzyklen</div><div class="v">${num(efc(c, cap))}</div></div>
      </div>
      ${cellBars(r.cells)}
    </div>
    <div class="card"><h2>Zuordnung</h2>${right}</div>
  </div>`;

  const form = $("#saveform");
  if (form) form.onsubmit = async (e) => {
    e.preventDefault();
    const fields = Object.fromEntries(new FormData(form).entries());
    const res = await api("/api/save", "POST", { token: r.token, fields });
    if (!res.ok) { toast(res.error || "Speichern fehlgeschlagen", "err"); return; }
    toast(res.created ? "Akku angelegt und Messung gespeichert" : "Messung gespeichert");
    r._saved = res;
    renderReadResult(r, res);
    refreshAlertCount();
  };
}

/* =============================================================== ALERTS */
let showAcked = false;
async function viewAlerts() {
  const d = await api("/api/alerts" + (showAcked ? "" : "?open=1"));
  const list = d.alerts || [];
  view.innerHTML = `
    <div class="page-head"><div><h1>Warnungen</h1>
      <div class="sub">Entstehen automatisch beim Speichern einer Messung, wenn sich ein Akku gegenüber der vorigen Messung verschlechtert.</div></div>
      <div class="actions"><label class="check"><input type="checkbox" id="showacked" ${showAcked ? "checked" : ""}> Quittierte zeigen</label>
      ${list.some((a) => !a.acknowledged) ? '<button class="btn" id="ackall">Alle quittieren</button>' : ""}</div></div>
    <div class="card"><div class="alert-list" id="alerts">${list.length ? list.map((a) => alertItem(a, true)).join("")
      : '<p class="empty">Keine offenen Warnungen – alles im grünen Bereich.</p>'}</div></div>
    <div class="card"><h2>Wann wird gewarnt?</h2><div class="hint" id="rules"></div></div>`;
  const t = SETTINGS.thresholds || {};
  $("#rules").innerHTML = `<ul style="margin-left:18px;line-height:1.8">
    <li>Zustand fällt gegenüber der letzten Messung um ≥ ${t.health_drop_warn} Punkte (Warnung) bzw. ≥ ${t.health_drop_crit} (kritisch)</li>
    <li>Zell-Imbalance erreicht ${t.imb_warn_mv} mV (Warnung) bzw. ${t.imb_crit_mv} mV (kritisch) oder steigt um ≥ ${t.imb_rise_warn_mv} mV</li>
    <li>Neue Überhitzungs- oder Unterspannungs-Ereignisse (Warnung), neue Ladung mit einer Zelle &lt; 2,5 V (kritisch)</li>
    <li>Eine Zelle liegt beim Auslesen unter ${NF1.format(t.cell_low_warn_v)} V (Warnung) bzw. ${NF1.format(t.cell_low_crit_v)} V (kritisch)</li></ul>`;
  $("#showacked").onchange = (e) => { showAcked = e.target.checked; viewAlerts(); };
  if ($("#ackall")) $("#ackall").onclick = async () => { await api("/api/alerts/ack_all", "POST", {}); viewAlerts(); refreshAlertCount(); };
  wireAcks($("#alerts"), () => { viewAlerts(); refreshAlertCount(); });
}

/* ============================================================= SETTINGS */
async function viewSettings() {
  const s = await api("/api/settings");
  SETTINGS = s;
  const t = s.thresholds;
  view.innerHTML = `
    <div class="page-head"><div><h1>Einstellungen</h1></div></div>
    <div class="grid g2">
      <div class="stack">
        <div class="card"><h2>QR-Codes &amp; Etiketten</h2>
          <form class="form" id="sform">
            <label class="field">Basis-URL im QR-Code
              <input name="base_url" value="${h(s.base_url)}" placeholder="${h(s.effective_base_url)}"></label>
            <p class="hint">Aktuell im QR: <span class="code">${h(s.effective_base_url)}/b/&lt;Seriennr.&gt;</span>.
              Leer lassen = Adresse, mit der dieser Browser die App aufruft.
              ${s.suggested_base_url ? `Damit ein Handy-Scan im WLAN funktioniert, z. B. <a href="#" id="usesugg" class="code">${h(s.suggested_base_url)}</a> eintragen und die App mit <span class="code">--host 0.0.0.0</span> starten.` : ""}
              Der QR enthält immer auch die Seriennummer – ein USB-Scanner oder das Suchfeld findet den Akku auch ohne Netzwerk.</p>
            <label class="field">Firma / Inventar-Zusatz auf Etiketten &amp; Berichten
              <input name="company" value="${h(s.company)}" placeholder="optional"></label>
            <div><button class="btn primary" type="submit">Speichern</button></div>
          </form></div>
        <div class="card"><h2>Daten</h2>
          <div class="kv"><div class="k">Datenbank</div><div class="v mono" style="word-break:break-all">${h(s.db_path)}</div>
            <div class="k">Modus</div><div class="v">${s.demo ? "Demo (temporäre Datenbank)" : "Live"}</div>
            <div class="k">Server lauscht auf</div><div class="v mono">${h(s.listen_host)}</div></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
            <a class="btn" href="/export/database">${ICON.dl} Datenbank-Backup (.db)</a>
            <a class="btn" href="/export/fleet.csv">${ICON.dl} Flotte als CSV (Excel)</a>
            <a class="btn" href="/export/fleet.csv?format=intl">${ICON.dl} CSV (international)</a>
          </div></div>
      </div>
      <div class="card"><h2>Bewertung &amp; Warnschwellen</h2>
        <div class="kv">
          <div class="k">Zustand „gut“</div><div class="v">≥ ${t.health_good}</div>
          <div class="k">Zustand „schwach“</div><div class="v">&lt; ${t.health_weak}</div>
          <div class="k">Warnung bei Zustandsabfall</div><div class="v">≥ ${t.health_drop_warn} / kritisch ≥ ${t.health_drop_crit} Punkte</div>
          <div class="k">Imbalance</div><div class="v">≥ ${t.imb_warn_mv} mV / kritisch ≥ ${t.imb_crit_mv} mV</div>
          <div class="k">Imbalance-Anstieg</div><div class="v">≥ ${t.imb_rise_warn_mv} mV pro Messung</div>
          <div class="k">Niedrige Zelle</div><div class="v">&lt; ${NF1.format(t.cell_low_warn_v)} V / kritisch &lt; ${NF1.format(t.cell_low_crit_v)} V</div>
          <div class="k">Messung fällig nach</div><div class="v">${t.stale_days} Tagen</div>
        </div>
        <h2 style="margin-top:18px">So wird der Zustand geschätzt</h2>
        <div class="hint" style="line-height:1.7">100 Punkte minus Abzüge für:
          <ul style="margin-left:18px">
            <li><b>Zyklenverschleiß</b>: 0,04 je äquivalentem Vollzyklus (Entladung Ah ÷ Nennkapazität), max. 45</li>
            <li><b>Zell-Imbalance</b>: 0,35 je mV über 20 mV, max. 40</li>
            <li><b>Ladung mit Zelle &lt; 2,5 V</b>: 10 je Ereignis, max. 30</li>
            <li><b>Überhitzung</b>: 1,5 je Ereignis, max. 15</li>
            <li><b>Kalenderalter</b>: 2 je Jahr seit Herstellung, max. 15</li></ul>
          Die Imbalance hängt vom Ladezustand ab – am aussagekräftigsten sind Messungen bei ähnlichem Ladestand (z. B. immer voll geladen).
          Die Schwellen stehen in <span class="code">gui/fleet.py</span>.</div>
      </div>
    </div>`;
  if ($("#usesugg")) $("#usesugg").onclick = (e) => { e.preventDefault(); $("#sform [name=base_url]").value = s.suggested_base_url; };
  $("#sform").onsubmit = async (e) => {
    e.preventDefault();
    const r = await api("/api/settings", "POST", Object.fromEntries(new FormData(e.target).entries()));
    toast(r.ok ? "Gespeichert" : "Fehler", r.ok ? "" : "err");
    viewSettings();
  };
}

/* ================================================================= boot */
(async function boot() {
  SETTINGS = await api("/api/settings");
  $("#demopill").hidden = !SETTINGS.demo;
  if (location.pathname === "/") history.replaceState({}, "", "/fleet");
  route();
})();
