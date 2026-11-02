"use strict";
/* Printable per-battery report: /report/<eserial> */

(async function () {
  const id = decodeURIComponent(location.pathname.split("/").filter(Boolean)[1] || "");
  const b = await api("/api/batteries/" + encodeURIComponent(id));
  const page = $("#page");
  if (b.ok === false) { page.innerHTML = `<p class="empty">Akku „${h(id)}“ nicht gefunden.</p>`; return; }
  const es = encodeURIComponent(b.eserial);
  $("#csv").href = `/export/battery/${es}.csv`;
  document.title = `Akku-Bericht ${displayName(b)} (${b.eserial})`;

  const rs = b.readings, last = rs[rs.length - 1], first = rs[0];
  const c = last ? last.counters : {};
  const st = STATUS[b.status] || STATUS.unknown;

  page.innerHTML = `
    <div class="rh">
      <div>
        <div class="meta">${b.company ? h(b.company) + " · " : ""}M18 Akku-Bericht · erstellt ${new Date().toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" })}</div>
        <h1>${h(displayName(b))} ${statusBadge(b.status)}${b.retired ? ' <span class="badge unknown plain">ausgemustert</span>' : ""}</h1>
        <div class="meta">${h(b.model || "Unbekanntes Modell")} · E-Seriennr. ${h(b.eserial)}</div>
      </div>
      <img src="/qr/${es}.svg?border=1" alt="QR">
    </div>

    <div class="two">
      <div class="sec" style="margin-top:0"><h2>Stammdaten</h2>
        <div class="kv">
          <div class="k">E-Seriennr.</div><div class="v mono">${h(b.eserial)}</div>
          <div class="k">Gehäuse-Seriennr.</div><div class="v">${h(b.case_serial || "–")}</div>
          <div class="k">Modell</div><div class="v">${h(b.model || "–")}</div>
          <div class="k">Nennkapazität</div><div class="v">${b.capacity_ah ? NF1.format(b.capacity_ah) + " Ah" : "–"}</div>
          <div class="k">Standort</div><div class="v">${h(b.location || "–")}</div>
          <div class="k">Hergestellt</div><div class="v">${fmtDate(b.manufactured)}</div>
          <div class="k">Erfasst seit</div><div class="v">${fmtDate(b.created_at)}</div>
          <div class="k">Messungen</div><div class="v">${rs.length}${rs.length ? ` (${fmtDate(first.ts)} – ${fmtDate(last.ts)})` : ""}</div>
        </div>
        ${b.comment ? `<p style="margin-top:8px;font-size:12.5px"><b>Kommentar:</b> ${h(b.comment)}</p>` : ""}
      </div>
      <div class="sec" style="margin-top:0"><h2>Zustand (Schätzung) – ${st.label}</h2>
        <div class="hero">${gauge(last?.health, 110)}<div class="parts" style="font-size:12.5px">${partsList(last?.health_parts)}</div></div>
        <div class="trendline" style="margin-top:8px">${trendText(b.trend)}</div>
      </div>
    </div>

    ${last ? `<div class="sec"><h2>Kennzahlen (letzte Messung ${fmtDateTime(last.ts)})</h2>
      <div class="stats">
        <div class="stat"><div class="k">Imbalance</div><div class="v">${num(last.imbalance_mv)} <small>mV</small></div></div>
        <div class="stat"><div class="k">Pack</div><div class="v">${last.pack_v != null ? NF2.format(last.pack_v) : "–"} <small>V</small></div></div>
        <div class="stat"><div class="k">Vollzyklen</div><div class="v">${num(efc(c, b.capacity_ah))}</div></div>
        <div class="stat"><div class="k">Entladen</div><div class="v">${num(counterAh(c))} <small>Ah</small></div></div>
        <div class="stat"><div class="k">Ladungen</div><div class="v">${num(c.total_charges)}</div></div>
        <div class="stat"><div class="k">Überhitzung</div><div class="v">${num(c.overheat)}</div></div>
        <div class="stat"><div class="k">Unterspannung</div><div class="v">${num(c.lowvolt)}</div></div>
        <div class="stat"><div class="k">Ladung &lt; 2,5 V</div><div class="v">${num(c.lowv_charges)}</div></div>
      </div></div>` : ""}

    <div class="sec"><h2>Alterung – Zustand über Zeit</h2>${healthChart(rs, b.trend, { height: 210 })}</div>
    <div class="two">
      <div class="sec"><h2>Zell-Imbalance</h2>${imbChart(rs, 200)}</div>
      <div class="sec"><h2>Ereignisse (kumuliert)</h2>${eventChart(rs, 200)}</div>
    </div>
    <div class="two">
      <div class="sec"><h2>Zellspannungen ${last ? "(" + fmtDate(last.ts) + ")" : ""}</h2>${cellBars(last?.cells)}</div>
      <div class="sec"><h2>Warnungen</h2>
        <div class="alert-list">${b.alerts.length ? b.alerts.slice(0, 12).map((a) => alertItem(a)).join("") : '<p class="muted">Keine.</p>'}</div>
        ${b.alerts.length > 12 ? `<p class="hint">… und ${b.alerts.length - 12} ältere</p>` : ""}</div>
    </div>

    <div class="sec" style="break-inside:auto"><h2>Messverlauf</h2>
      <table class="list"><thead><tr><th>Datum</th><th class="num">Zustand</th><th class="num">Imbalance</th><th class="num">Pack</th>
        <th class="num">Temp.</th><th class="num">Entladen</th><th class="num">Zyklen</th><th class="num">Ladungen</th>
        <th class="num">Überh.</th><th class="num">Untersp.</th><th class="num">&lt;2,5 V</th></tr></thead>
      <tbody>${rs.slice().reverse().map((r) => `<tr><td>${fmtDate(r.ts)}</td><td class="num">${num(r.health)}</td>
        <td class="num">${num(r.imbalance_mv)} mV</td><td class="num">${r.pack_v != null ? NF2.format(r.pack_v) : "–"} V</td>
        <td class="num">${r.temp_c != null ? NF1.format(r.temp_c) : "–"} °C</td><td class="num">${num(counterAh(r.counters))} Ah</td>
        <td class="num">${num(efc(r.counters, b.capacity_ah))}</td><td class="num">${num(r.counters.total_charges)}</td>
        <td class="num">${num(r.counters.overheat)}</td><td class="num">${num(r.counters.lowvolt)}</td>
        <td class="num">${num(r.counters.lowv_charges)}</td></tr>`).join("")}</tbody></table>
    </div>
    <div class="foot">Der Zustand ist eine Schätzung aus den im Akku gespeicherten Verschleißzählern (Entladung ÷ Nennkapazität, Zell-Imbalance,
      Tiefentlade-Ladungen, Überhitzung, Alter) – kein gemessener Kapazitätstest. Daten ausgelesen mit m18-protocol.</div>`;
})();

function alertItem(a) {
  return `<div class="alert-item ${a.acknowledged ? "acked" : ""}"><span><span class="sev ${a.severity}">${SEVERITY[a.severity] || a.severity}</span><br>
    <span class="when">${fmtDate(a.ts)}</span></span><span>${h(a.message)}</span></div>`;
}
