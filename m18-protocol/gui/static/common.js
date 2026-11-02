"use strict";
/* Shared helpers for the app, label and report pages. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function api(path, method = "GET", body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  let data;
  try { data = await r.json(); } catch { data = { ok: false, error: `HTTP ${r.status}` }; }
  if (!r.ok && data.ok === undefined) data.ok = false;
  return data;
}

/* HTML-escape for template strings. */
function h(v) {
  if (v === null || v === undefined) return "";
  return String(v).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const NF1 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
const NF2 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const NF3 = new Intl.NumberFormat("de-DE", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const NF0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

function num(v, nf = NF0, dash = "–") { return v === null || v === undefined || Number.isNaN(v) ? dash : nf.format(v); }
function toDate(s) { return s ? new Date(s) : null; }
function fmtDate(s) { const d = toDate(s); return d ? d.toLocaleDateString("de-DE") : "–"; }
function fmtDateTime(s) {
  const d = toDate(s);
  return d ? d.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" }) : "–";
}
function fmtAgo(days) {
  if (days === null || days === undefined) return "nie";
  if (days < 1) return "heute";
  if (days < 2) return "gestern";
  if (days < 60) return `vor ${days} Tagen`;
  if (days < 730) return `vor ${Math.round(days / 30)} Monaten`;
  return `vor ${NF1.format(days / 365)} Jahren`;
}
function fmtSigned(v, nf = NF0) {
  if (v === null || v === undefined) return "–";
  return (v > 0 ? "+" : v < 0 ? "−" : "±") + nf.format(Math.abs(v));
}

const STATUS = {
  good: { label: "Gut", cls: "good" },
  watch: { label: "Beobachten", cls: "watch" },
  weak: { label: "Schwach", cls: "weak" },
  critical: { label: "Kritisch", cls: "crit" },
  unknown: { label: "Keine Messung", cls: "unknown" },
};
const SEVERITY = { crit: "Kritisch", warn: "Warnung", info: "Info" };

function healthClass(v) {
  if (v === null || v === undefined) return "unknown";
  return v >= 80 ? "good" : v >= 60 ? "watch" : "weak";
}
function statusBadge(status) {
  const s = STATUS[status] || STATUS.unknown;
  return `<span class="badge ${s.cls}">${s.label}</span>`;
}
function displayName(b) { return b.name || b.eserial; }
function counterAh(c) { return c && c.discharge_as != null ? c.discharge_as / 3600 : null; }
function efc(c, cap) { const ah = counterAh(c); return ah != null && cap ? ah / cap : null; }

function toast(msg, kind = "") {
  let box = $("#toasts");
  if (!box) { box = document.createElement("div"); box.id = "toasts"; document.body.appendChild(box); }
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = msg;
  box.appendChild(t);
  setTimeout(() => t.classList.add("out"), 3200);
  setTimeout(() => t.remove(), 3800);
}
