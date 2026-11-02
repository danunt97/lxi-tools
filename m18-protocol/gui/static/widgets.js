"use strict";
/* Rendering widgets shared by the app and the report page. */

function gauge(v, size = 132) {
  const r = 52, c = 2 * Math.PI * r, cls = healthClass(v);
  const frac = v === null || v === undefined ? 0 : Math.max(0, Math.min(100, v)) / 100;
  return `<svg class="gauge" viewBox="0 0 132 132" style="width:${size}px;height:${size}px">
    <circle cx="66" cy="66" r="${r}" fill="none" stroke-width="11" class="bgc"/>
    <circle cx="66" cy="66" r="${r}" fill="none" stroke-width="11" stroke-linecap="round"
      class="c-${cls}" style="stroke:currentColor" stroke-dasharray="${c * frac} ${c}"
      transform="rotate(-90 66 66)"/>
    <text x="66" y="72" text-anchor="middle" class="v">${v ?? "–"}</text>
    <text x="66" y="90" text-anchor="middle" class="u">von 100</text></svg>`;
}

function partsList(parts) {
  if (!parts || !parts.length) return '<p class="muted">Keine Daten</p>';
  return parts.map((p) => `<div class="row"><span>${h(p.label)}</span>
    <span class="p">${p.penalty > 0 ? "−" + NF1.format(p.penalty) : "0"}</span>
    <span class="d">${h(p.detail)}</span></div>`).join("");
}

function healthBar(v) {
  const cls = healthClass(v);
  return `<div class="hbar c-${cls}"><span class="track"><span class="fill" style="width:${v ?? 0}%"></span></span><b>${v ?? "–"}</b></div>`;
}

function cellBars(cells) {
  if (!cells || !cells.length) return '<p class="muted">Keine Zelldaten</p>';
  const lo = Math.min(...cells), hi = Math.max(...cells);
  return `<div class="cells">${cells.map((v, i) => {
    const pct = Math.max(4, Math.min(100, ((v - 3.0) / 1.2) * 100));
    const cls = [v < 3.0 ? "low" : "", v === lo ? "min" : "", v === hi ? "max" : ""].join(" ");
    return `<div class="cell ${cls}"><span class="lab">Zelle ${i + 1}</span>
      <span class="track"><span class="fill" style="width:${pct}%"></span></span>
      <span class="cv">${NF3.format(v)} V</span></div>`;
  }).join("")}</div>`;
}

function healthChart(rs, trend, opts = {}) {
  const series = [{ label: "Zustand", color: "var(--c1)", points: rs.map((r) => [r.ts, r.health]) }];
  let vline;
  if (trend && trend.fit) {
    series.push({ label: "Trend", color: "var(--muted)", dashed: true, dots: false, points: trend.fit });
    if (trend.projection) {
      series.push({ label: "Prognose", color: "var(--weak)", dashed: true, dots: false, points: trend.projection });
      vline = { x: new Date(trend.projected_date), label: "< 60", color: "var(--weak)" };
    }
  }
  return lineChart({
    series, yMin: 0, yMax: 100, unit: " %", height: opts.height || 230, vline,
    bands: [{ from: 80, to: 100, color: "var(--band-good)" }, { from: 60, to: 80, color: "var(--band-watch)" },
      { from: 0, to: 60, color: "var(--band-weak)" }],
  });
}
function imbChart(rs, height = 230) {
  return lineChart({
    series: [{ label: "Imbalance", color: "var(--c2)", points: rs.map((r) => [r.ts, r.imbalance_mv]) }],
    yMin: 0, unit: " mV", width: 420, height, fmtY: (v) => NF0.format(v),
    hlines: [{ y: 50, label: "Warnung 50 mV", color: "var(--watch)" }, { y: 100, label: "Kritisch 100 mV", color: "var(--crit)" }],
  });
}
function eventChart(rs, height = 230) {
  const s = (k) => rs.map((r) => [r.ts, r.counters[k]]);
  return lineChart({
    series: [
      { label: "Überhitzung", color: "var(--c3)", step: true, points: s("overheat") },
      { label: "Unterspannung", color: "var(--c1)", step: true, points: s("lowvolt") },
      { label: "Ladung Zelle < 2,5 V", color: "var(--crit)", step: true, points: s("lowv_charges") },
    ], yMin: 0, width: 420, height, fmtY: (v) => NF0.format(v),
  });
}
function usageChart(rs, height = 230) {
  return lineChart({
    series: [{ label: "Entladung", color: "var(--good)", area: true, points: rs.map((r) => [r.ts, counterAh(r.counters)]) }],
    yMin: 0, width: 420, height, unit: " Ah", fmtY: (v) => NF0.format(v),
  });
}

function trendText(t) {
  if (!t) return '<span>Trend: <b>zu wenige Messungen</b> (mind. 3)</span>';
  const proj = t.projected_date ? (new Date(t.projected_date) <= new Date() ? "<b class='c-weak'>bereits erreicht</b>"
    : `<b>ca. ${new Date(t.projected_date).toLocaleDateString("de-DE", { month: "long", year: "numeric" })}</b>`) : "<b>nicht absehbar</b>";
  return `<span>Trend: <b class="${t.slope_per_year <= -15 ? "c-crit" : t.slope_per_year <= -8 ? "c-watch" : ""}">${fmtSigned(t.slope_per_year, NF1)} Punkte/Jahr</b></span>
    <span>Unter 60 (schwach): ${proj}</span><span class="faint">aus ${t.n} Messungen</span>`;
}

