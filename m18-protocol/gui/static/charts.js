"use strict";
/* Tiny dependency-free SVG charts (time series + sparkline). */

function niceTicks(lo, hi, n = 5) {
  if (hi === lo) { hi = lo + 1; }
  const raw = (hi - lo) / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => raw <= s) || 10 * mag;
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}

function timeTicks(t0, t1) {
  const out = [];
  const months = (t1 - t0) / (30.4 * 864e5);
  const every = months > 30 ? 6 : months > 14 ? 3 : months > 5 ? 2 : 1;
  const d = new Date(t0);
  d.setDate(1); d.setHours(0, 0, 0, 0);
  d.setMonth(d.getMonth() + 1);
  while (d.getMonth() % every !== 0) d.setMonth(d.getMonth() + 1);
  while (d <= t1) {
    out.push(new Date(d));
    d.setMonth(d.getMonth() + every);
  }
  if (out.length === 0) out.push(new Date((t0.getTime() + t1.getTime()) / 2));
  return out;
}

/**
 * series: [{label, color, points: [[Date|string, number|null], ...], dashed, dots, area}]
 * bands:  [{from, to, color}]   hlines: [{y, label, color}]
 * vline:  {x: Date, label}
 */
function lineChart(o) {
  const W = o.width || 640, H = o.height || 220;
  const m = { l: 42, r: 14, t: 12, b: 26 };
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const series = (o.series || []).map((s) => ({
    ...s, points: s.points.map(([t, v]) => [t instanceof Date ? t : new Date(t), v])
      .filter(([t, v]) => !isNaN(t) && v !== null && v !== undefined),
  }));
  const all = series.flatMap((s) => s.points);
  if (all.length === 0) {
    return `<svg viewBox="0 0 ${W} ${H}" class="chart"><text x="${W / 2}" y="${H / 2}"
      text-anchor="middle" class="ch-empty">Noch keine Messwerte</text></svg>`;
  }
  let t0 = o.xMin ? new Date(o.xMin) : new Date(Math.min(...all.map((p) => p[0])));
  let t1 = o.xMax ? new Date(o.xMax) : new Date(Math.max(...all.map((p) => p[0])));
  if (o.vline && o.vline.x > t1) t1 = new Date(o.vline.x);
  if (t1 - t0 < 864e5 * 14) { t0 = new Date(t0 - 864e5 * 7); t1 = new Date(+t1 + 864e5 * 7); }
  const vals = all.map((p) => p[1]).concat((o.hlines || []).map((l) => l.y));
  let y0 = o.yMin !== undefined ? o.yMin : Math.min(...vals);
  let y1 = o.yMax !== undefined ? o.yMax : Math.max(...vals);
  if (o.yMin === undefined || o.yMax === undefined) {
    const pad = (y1 - y0) * 0.1 || Math.abs(y1) * 0.1 || 1;
    if (o.yMin === undefined) y0 = Math.max(o.yFloor ?? -Infinity, y0 - pad);
    if (o.yMax === undefined) y1 = y1 + pad;
  }
  const ticks = niceTicks(y0, y1, o.yTicks || 4);
  if (o.yMin === undefined) y0 = Math.min(y0, ticks[0]);
  if (o.yMax === undefined) y1 = Math.max(y1, ticks[ticks.length - 1]);
  const X = (t) => m.l + ((t - t0) / (t1 - t0)) * pw;
  const Y = (v) => m.t + ph - ((v - y0) / (y1 - y0)) * ph;
  const fmtY = o.fmtY || ((v) => NF1.format(v));
  let s = `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img"${o.title ? ` aria-label="${h(o.title)}"` : ""}>`;
  for (const b of o.bands || []) {
    const a = Y(Math.min(y1, b.to)), z = Y(Math.max(y0, b.from));
    if (z > a) s += `<rect x="${m.l}" y="${a}" width="${pw}" height="${z - a}" style="fill:${b.color}"/>`;
  }
  for (const v of ticks) {
    if (v < y0 - 1e-9 || v > y1 + 1e-9) continue;
    s += `<line x1="${m.l}" x2="${m.l + pw}" y1="${Y(v)}" y2="${Y(v)}" class="ch-grid"/>`;
    s += `<text x="${m.l - 6}" y="${Y(v) + 4}" text-anchor="end" class="ch-tick">${fmtY(v)}</text>`;
  }
  for (const d of timeTicks(t0, t1)) {
    const x = X(d);
    if (x < m.l || x > m.l + pw) continue;
    s += `<line x1="${x}" x2="${x}" y1="${m.t}" y2="${m.t + ph}" class="ch-grid v"/>`;
    const lab = d.getMonth() === 0 ? String(d.getFullYear())
      : d.toLocaleDateString("de-DE", { month: "short" }).replace(".", "");
    s += `<text x="${x}" y="${H - 8}" text-anchor="middle" class="ch-tick">${lab}</text>`;
  }
  for (const l of o.hlines || []) {
    if (l.y < y0 || l.y > y1) continue;
    s += `<line x1="${m.l}" x2="${m.l + pw}" y1="${Y(l.y)}" y2="${Y(l.y)}" class="ch-hline" style="stroke:${l.color}"/>`;
    if (l.label) s += `<text x="${m.l + pw - 4}" y="${Y(l.y) - 4}" text-anchor="end" class="ch-hlabel" style="fill:${l.color}">${h(l.label)}</text>`;
  }
  if (o.vline) {
    const x = X(new Date(o.vline.x));
    s += `<line x1="${x}" x2="${x}" y1="${m.t}" y2="${m.t + ph}" class="ch-hline" style="stroke:${o.vline.color || "currentColor"}"/>`;
    if (o.vline.label) s += `<text x="${x - 4}" y="${m.t + 12}" text-anchor="end" class="ch-hlabel" style="fill:${o.vline.color || "currentColor"}">${h(o.vline.label)}</text>`;
  }
  for (const se of series) {
    if (se.points.length === 0) continue;
    const pts = se.points.map(([t, v]) => [X(t), Y(v)]);
    let d = "";
    if (se.step) {
      pts.forEach(([x, y], i) => { d += i === 0 ? `M${x},${y}` : `H${x}V${y}`; });
    } else {
      d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
    }
    if (se.area && pts.length > 1) {
      s += `<path d="${d}L${pts[pts.length - 1][0]},${m.t + ph}L${pts[0][0]},${m.t + ph}Z" style="fill:${se.color};opacity:.12"/>`;
    }
    s += `<path d="${d}" class="ch-line${se.dashed ? " dashed" : ""}" style="stroke:${se.color}"/>`;
    if (se.dots !== false) {
      se.points.forEach(([t, v], i) => {
        s += `<circle cx="${pts[i][0]}" cy="${pts[i][1]}" r="${se.points.length > 40 ? 2 : 3}" class="ch-dot" style="fill:${se.color}">` +
          `<title>${h(se.label || "")} ${t.toLocaleDateString("de-DE")}: ${fmtY(v)}${o.unit || ""}</title></circle>`;
      });
    }
  }
  s += `<line x1="${m.l}" x2="${m.l + pw}" y1="${m.t + ph}" y2="${m.t + ph}" class="ch-axis"/>`;
  s += "</svg>";
  if (series.filter((x) => x.label).length > 1) {
    s += `<div class="ch-legend">${series.filter((x) => x.label).map((x) =>
      `<span><i style="background:${x.color}"></i>${h(x.label)}</span>`).join("")}</div>`;
  }
  return s;
}

function sparkline(values, opts = {}) {
  const W = opts.width || 110, H = opts.height || 26;
  const v = values.filter((x) => x !== null && x !== undefined);
  if (v.length < 2) return `<svg viewBox="0 0 ${W} ${H}" class="spark"></svg>`;
  let lo = opts.min ?? Math.min(...v), hi = opts.max ?? Math.max(...v);
  if (opts.minSpan && hi - lo < opts.minSpan) { const mid = (hi + lo) / 2; lo = mid - opts.minSpan / 2; hi = mid + opts.minSpan / 2; }
  const span = hi - lo || 1;
  const pts = v.map((y, i) => [2 + (i / (v.length - 1)) * (W - 4), H - 3 - ((y - lo) / span) * (H - 6)]);
  const color = opts.color || "currentColor";
  const last = pts[pts.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" class="spark"><path d="${pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("")}" style="stroke:${color}"/>` +
    `<circle cx="${last[0]}" cy="${last[1]}" r="2.4" style="fill:${color}"/></svg>`;
}
