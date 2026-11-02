"use strict";
/* QR label sheet: /label/<eserial> or /label?ids=a,b,c (empty = all active). */

const DEFAULT_LEN = { 12: 36, 18: 46, 24: 56, 36: 70, 62: 90 };
let BATS = [];

function prefs() {
  try { return JSON.parse(localStorage.getItem("m18.label") || "{}"); } catch { return {}; }
}
function savePrefs(p) { try { localStorage.setItem("m18.label", JSON.stringify(p)); } catch { /* ignore */ } }

function layout() {
  const W = +$("#tape").value;                       // tape width = label height (mm)
  const L = +$("#len").value || DEFAULT_LEN[W];      // label length (mm)
  const pad = W <= 12 ? 0.8 : W <= 18 ? 1.2 : 2;     // printable margin (mm)
  const qr = W - 2 * pad;
  const fs = Math.max(1.9, Math.min(5.2, W * 0.16)); // base font size (mm)
  return { W, L, pad, qr, fs, small: W <= 12 };
}

function render() {
  const o = layout();
  const showModel = $("#o-model").checked && !o.small, showCo = $("#o-company").checked, showLoc = $("#o-loc").checked;
  savePrefs({ tape: o.W, len: $("#len").value, model: $("#o-model").checked, company: showCo, loc: showLoc });
  $("#pagestyle").textContent = `@page { size: ${o.L}mm ${o.W}mm; margin: 0; }`;
  $("#sheet").innerHTML = BATS.map((b) => {
    const lines = [];
    lines.push(`<div class="n" style="font-size:${o.fs * 1.25}mm">${h(displayName(b))}</div>`);
    if (b.name) lines.push(`<div class="s" style="font-size:${o.fs * 0.78}mm">${h(b.eserial)}</div>`);
    if (showModel && b.model) lines.push(`<div class="m" style="font-size:${o.fs * 0.78}mm">${h(b.model)}</div>`);
    if (showLoc && b.location && !o.small) lines.push(`<div class="c" style="font-size:${o.fs * 0.72}mm">${h(b.location)}</div>`);
    if (showCo && b.company) lines.push(`<div class="c" style="font-size:${o.fs * 0.72}mm;font-weight:600">${h(b.company)}</div>`);
    return `<div class="label" style="width:${o.L}mm;height:${o.W}mm;padding:${o.pad}mm;gap:${o.pad * 1.2}mm">
      <img src="/qr/${encodeURIComponent(b.eserial)}.svg?border=1" style="width:${o.qr}mm;height:${o.qr}mm" alt="">
      <div class="txt">${lines.join("")}</div></div>`;
  }).join("") || '<p class="empty">Keine Akkus ausgewählt.</p>';
}

/* Render each label into a 360-dpi PNG (P-touch Editor friendly). */
async function downloadPng() {
  const o = layout();
  const dpmm = 360 / 25.4;
  for (const [i, el] of $$(".label").entries()) {
    const b = BATS[i];
    const c = document.createElement("canvas");
    c.width = Math.round(o.L * dpmm); c.height = Math.round(o.W * dpmm);
    const g = c.getContext("2d");
    g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
    const img = new Image();
    img.src = `/qr/${encodeURIComponent(b.eserial)}.png?scale=20&border=1`;
    await img.decode();
    g.imageSmoothingEnabled = false;
    const px = (mm) => mm * dpmm;
    g.drawImage(img, px(o.pad), px(o.pad), px(o.qr), px(o.qr));
    // reuse the on-screen text layout
    const tx = px(o.pad + o.qr + o.pad * 1.2);
    const lines = $$(".txt > div", el);
    const scale = c.height / el.getBoundingClientRect().height;
    g.fillStyle = "#000"; g.textBaseline = "top";
    for (const ln of lines) {
      const r = ln.getBoundingClientRect(), cs = getComputedStyle(ln);
      g.font = `${cs.fontWeight} ${parseFloat(cs.fontSize) * scale}px ${cs.fontFamily}`;
      g.fillText(ln.textContent, tx, (r.top - el.getBoundingClientRect().top) * scale, c.width - tx - px(o.pad));
    }
    const a = document.createElement("a");
    a.href = c.toDataURL("image/png");
    a.download = `etikett-${b.eserial}-${o.W}mm.png`;
    a.click();
    await new Promise((r) => setTimeout(r, 250));
  }
}

(async function init() {
  const p = prefs();
  if (p.tape) $("#tape").value = String(p.tape);
  $("#len").value = p.len || DEFAULT_LEN[+$("#tape").value];
  if (p.model !== undefined) $("#o-model").checked = p.model;
  if (p.company !== undefined) $("#o-company").checked = p.company;
  if (p.loc !== undefined) $("#o-loc").checked = p.loc;

  const parts = location.pathname.split("/").filter(Boolean);
  let ids = parts.length > 1 ? [decodeURIComponent(parts[1])] : [];
  const q = new URLSearchParams(location.search).get("ids");
  if (q !== null) ids = q.split(",").filter(Boolean);
  const settings = await api("/api/settings");
  if (ids.length === 0) {
    const all = await api("/api/batteries");
    BATS = (all.batteries || []).filter((b) => !b.retired);
  } else {
    const list = await api("/api/batteries");
    const by = Object.fromEntries((list.batteries || []).map((b) => [b.eserial, b]));
    BATS = ids.map((i) => by[i]).filter(Boolean);
  }
  BATS.forEach((b) => b.company = settings.company);
  document.title = BATS.length === 1 ? `Etikett ${displayName(BATS[0])}` : `QR-Etiketten (${BATS.length})`;

  $("#tape").onchange = () => { $("#len").value = DEFAULT_LEN[+$("#tape").value]; render(); };
  ["#len", "#o-model", "#o-company", "#o-loc"].forEach((s) => $(s).oninput = render);
  $("#print").onclick = () => window.print();
  $("#png").onclick = downloadPng;
  render();
})();
