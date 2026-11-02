// Web interface of the M18 bridge (served from flash, no external resources).
// Tabs: Akku (direct read-out), WLAN (setup in the customer's network), Gerät (status, console).
#pragma once

static const char WEB_UI[] PROGMEM = R"HTML(<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>M18 Akku-Brücke</title>
<style>
:root{--bg:#1b1d20;--panel:#23262a;--panel2:#1a1c1f;--raise:#2c3035;--line:#34383e;--text:#eceef0;--muted:#959ba3;--faint:#6b7178;
--accent:#e4002b;--good:#3fcf7f;--watch:#e7b43a;--weak:#f0703c;--crit:#ff4d5e;--info:#5aa2ff;color-scheme:dark}
@media (prefers-color-scheme:light){:root{--bg:#f3f4f6;--panel:#fff;--panel2:#f7f8fa;--raise:#eef0f3;--line:#dde0e5;--text:#17191c;--muted:#5e646c;--faint:#8a9098;
--good:#12935a;--watch:#b07d00;--weak:#c4501c;--crit:#d0142c;--info:#1f6fd6;color-scheme:light}}
*{box-sizing:border-box;margin:0;padding:0}[hidden]{display:none!important}
body{background:var(--bg);color:var(--text);font:14px/1.45 system-ui,"Segoe UI",sans-serif}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;background:var(--panel);border-bottom:1px solid var(--line);flex-wrap:wrap}
.logo{background:var(--accent);color:#fff;font-weight:800;font-size:12px;border-radius:7px;padding:6px 7px}
header h1{font-size:16px;font-weight:650}header small{display:block;color:var(--muted);font-size:11.5px;font-weight:400}
.pill{margin-left:auto;font-size:12px;border-radius:20px;padding:3px 10px;background:var(--raise);color:var(--muted)}
.pill.ok{background:rgba(63,207,127,.14);color:var(--good)}.pill.ap{background:rgba(90,162,255,.15);color:var(--info)}
nav{display:flex;gap:4px;padding:10px 16px 0;max-width:820px;margin:0 auto}
nav button{flex:1;background:transparent;border:0;border-bottom:2px solid transparent;color:var(--muted);padding:9px;font:inherit;cursor:pointer}
nav button.on{color:var(--text);border-bottom-color:var(--accent)}
main{max-width:820px;margin:0 auto;padding:14px 16px 40px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:14px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);margin-bottom:10px}
.btn{display:inline-flex;align-items:center;gap:8px;justify-content:center;background:var(--raise);border:1px solid var(--line);color:var(--text);
border-radius:8px;padding:9px 14px;font:inherit;cursor:pointer;text-decoration:none}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}.btn.big{padding:13px 22px;font-size:15px;width:100%}
.btn:disabled{opacity:.5}
input,select,textarea{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--text);border-radius:7px;padding:9px 10px;font:inherit}
label{display:block;color:var(--muted);font-size:12.5px;margin:10px 0 4px}
.hint{color:var(--muted);font-size:12.5px;margin-top:8px}.mono{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.spin{width:16px;height:16px;border:2px solid rgba(255,255,255,.35);border-top-color:#fff;border-radius:50%;animation:s .8s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
.hero{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.gauge{width:120px;height:120px;flex:none}.gauge .bg{stroke:var(--raise)}
.title b{font-size:18px;display:block}.title span{color:var(--muted)}
.badge{display:inline-block;border-radius:20px;padding:2px 9px;font-size:12px;font-weight:600;margin-top:4px}
.good{color:var(--good)}.watch{color:var(--watch)}.weak{color:var(--weak)}.crit{color:var(--crit)}.unknown{color:var(--muted)}
.badge.good{background:rgba(63,207,127,.13)}.badge.watch{background:rgba(231,180,58,.14)}.badge.weak{background:rgba(240,112,60,.15)}.badge.unknown{background:var(--raise)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:14px}
.stat{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 11px}
.stat .k{color:var(--muted);font-size:12px}.stat .v{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}.stat .v small{font-size:12px;color:var(--muted);font-weight:400}
.cell{display:grid;grid-template-columns:54px 1fr 70px;gap:10px;align-items:center;margin:7px 0}
.track{display:block;height:12px;background:var(--raise);border-radius:6px;overflow:hidden}.fill{display:block;height:100%;background:linear-gradient(90deg,#1f7a3f,var(--good));border-radius:6px}
.cell .cv{text-align:right;font-variant-numeric:tabular-nums}.cell .lab{color:var(--muted)}
.row{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-bottom:1px solid var(--line)}.row:last-child{border:0}.row span:first-child{color:var(--muted)}
.part .p{color:var(--weak);font-weight:600}.part small{display:block;color:var(--faint)}
.nets{display:flex;flex-direction:column;gap:6px;margin-top:10px}
.net{display:flex;justify-content:space-between;align-items:center;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 11px;cursor:pointer}
.net:hover{border-color:var(--faint)}.bars{color:var(--muted);font-size:12px}
.box{border-radius:8px;padding:12px 14px;border:1px solid;margin-top:12px}
.box.ok{background:rgba(63,207,127,.12);border-color:rgba(63,207,127,.4)}.box.err{background:rgba(255,77,94,.12);border-color:rgba(255,77,94,.4)}
.box.info{background:rgba(90,162,255,.12);border-color:rgba(90,162,255,.4)}
.big-url{font-size:17px;font-weight:650;word-break:break-all}
pre{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px;white-space:pre-wrap;word-break:break-all;font-size:12px;max-height:260px;overflow:auto}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:640px){.two{grid-template-columns:1fr}}
</style></head><body>
<header><span class="logo">M18</span><h1>Akku-Brücke<small id="hostline">…</small></h1><span class="pill" id="pill">…</span></header>
<nav><button data-tab="akku" class="on">Akku</button><button data-tab="wlan">WLAN</button><button data-tab="geraet">Gerät</button></nav>
<main>
<section id="tab-akku">
  <div class="card"><button class="btn primary big" id="readbtn">Akku auslesen</button>
    <p class="hint" id="readhint">Akku am Adapter anschließen und auslesen (dauert ca. 7 Sekunden).</p></div>
  <div id="result"></div>
</section>
<section id="tab-wlan" hidden>
  <div class="card"><h2>Verbindung</h2><div id="wifistate">…</div></div>
  <div class="card"><h2>Mit WLAN verbinden</h2>
    <button class="btn" id="scanbtn">Netzwerke suchen</button><div class="nets" id="nets"></div>
    <form id="wifiform"><label>Netzwerk (SSID)</label><input name="ssid" id="ssid" required autocomplete="off">
      <label>Passwort</label><input name="pass" id="pass" type="password" autocomplete="off">
      <label>Gerätename im Netzwerk</label><input name="host" id="host" pattern="[a-zA-Z0-9-]{1,30}">
      <p class="hint">Danach erreichbar unter <b class="mono" id="hostpreview"></b> – die PC-Software findet das Gerät auch automatisch.</p>
      <button class="btn primary big" style="margin-top:12px">Verbinden</button></form>
    <div id="wifiprogress"></div></div>
  <div class="card"><h2>Zurücksetzen</h2><button class="btn" id="forget">WLAN-Daten löschen</button>
    <p class="hint">Alternativ die BOOT-Taste 5 Sekunden gedrückt halten. Danach startet der Einrichtungsmodus (WLAN „M18-Bridge-…“).</p></div>
</section>
<section id="tab-geraet" hidden>
  <div class="two"><div class="card"><h2>Status</h2><div id="devinfo"></div></div>
  <div class="card"><h2>Verbindungstest</h2><button class="btn" id="testbtn">Akku testen</button>
    <p class="hint">Sendet Reset und prüft, ob der Akku mit 0xAA antwortet.</p><div id="testres"></div></div></div>
  <div class="card"><h2>Befehle</h2><textarea id="cmd" rows="4">PING
RESET
READ 0x400A 10
IDLE</textarea><button class="btn" id="cmdbtn" style="margin-top:8px">Senden</button><pre id="cmdres" hidden></pre>
    <p class="hint">PING · RESET · IDLE · READ &lt;addr&gt; &lt;len&gt; · NOTE &lt;addr&gt; &lt;byte&gt; · SCAN · INFO</p></div>
</section>
</main>
<script>
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const esc=v=>v==null?"":String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const nf=(v,d=0)=>v==null?"–":Number(v).toLocaleString("de-DE",{minimumFractionDigits:d,maximumFractionDigits:d});
const fd=s=>s?new Date(s+"T00:00:00Z").toLocaleDateString("de-DE"):"–";
let INFO={};
async function j(url,opt){const r=await fetch(url,opt);return r.json();}
function tab(name){$$("nav button").forEach(b=>b.classList.toggle("on",b.dataset.tab==name));$$("main>section").forEach(s=>s.hidden=s.id!="tab-"+name);
  if(name=="wlan")wifiState();if(name=="geraet")devInfo();}
$$("nav button").forEach(b=>b.onclick=()=>tab(b.dataset.tab));

async function info(){try{INFO=await j("/api/info");}catch(e){return;}
  const w=INFO.wifi,p=$("#pill");
  p.className="pill "+(w=="sta"?"ok":"ap");
  p.textContent=w=="sta"?"WLAN: "+INFO.ssid:"Einrichtungsmodus";
  $("#hostline").textContent=(w=="sta"?"http://"+INFO.host+" · "+INFO.ip:"WLAN „"+INFO.ap+"“ · "+INFO.ap_ip);
  if(!$("#host").value){$("#host").value=INFO.host.replace(".local","");hostPreview();}
  return INFO;}
function hostPreview(){$("#hostpreview").textContent="http://"+($("#host").value||"m18-bridge")+".local";}
$("#host").oninput=hostPreview;

/* ---------- Akku ---------- */
function gauge(v,st){const r=48,c=2*Math.PI*r,f=v==null||v<0?0:Math.min(100,v)/100;
  return `<svg class="gauge" viewBox="0 0 120 120"><circle cx="60" cy="60" r="${r}" fill="none" stroke-width="10" class="bg"/>
  <circle cx="60" cy="60" r="${r}" fill="none" stroke-width="10" stroke-linecap="round" class="${st}" style="stroke:currentColor"
  stroke-dasharray="${c*f} ${c}" transform="rotate(-90 60 60)"/><text x="60" y="66" text-anchor="middle" font-size="28" font-weight="650" fill="currentColor">${v==null||v<0?"–":v}</text>
  <text x="60" y="84" text-anchor="middle" font-size="10" fill="#959ba3">von 100</text></svg>`;}
const STL={good:"Gut",watch:"Beobachten",weak:"Schwach",unknown:"Unbekannt"};
function hms(s){if(s==null)return"–";const h=Math.floor(s/3600),m=Math.floor(s%3600/60);return h+" h "+String(m).padStart(2,"0")+" min";}
function render(d,when){
  if(!d){$("#result").innerHTML="";return;}
  if(!d.ok){$("#result").innerHTML=`<div class="card"><div class="box err"><b>Auslesen fehlgeschlagen:</b> ${esc(d.error||"unbekannt")}<br>
    <span class="hint">Akku richtig aufgesteckt? Verkabelung J1/J2/B− prüfen. (${d.blocks_ok||0}/${d.blocks_total||0} Blöcke)</span></div></div>`;return;}
  const c=d.counters||{},cells=d.cells_mv||[],lo=Math.min(...cells),hi=Math.max(...cells);
  $("#result").innerHTML=`
  <div class="card"><div class="hero">${gauge(d.health,d.status)}
    <div class="title"><b>${esc(d.model)}</b><span class="mono">E-Seriennr. ${esc(d.eserial)}</span>${d.note?`<br><span>Notiz: „${esc(d.note)}“</span>`:""}
    <br><span class="badge ${d.status}">${STL[d.status]||d.status}</span> <span class="hint">Zustand (Schätzung)</span></div></div>
    <div class="stats">
      <div class="stat"><div class="k">Pack</div><div class="v">${nf(d.pack_v,2)}<small> V</small></div></div>
      <div class="stat"><div class="k">Temperatur</div><div class="v">${nf(d.temp_c,1)}<small> °C</small></div></div>
      <div class="stat"><div class="k">Imbalance</div><div class="v ${d.imbalance_mv>=100?"crit":d.imbalance_mv>=50?"watch":""}">${nf(d.imbalance_mv)}<small> mV</small></div></div>
      <div class="stat"><div class="k">Vollzyklen</div><div class="v">${nf(d.cycles)}</div></div>
      <div class="stat"><div class="k">Entladen</div><div class="v">${nf(d.discharge_ah)}<small> Ah</small></div></div>
      <div class="stat"><div class="k">Ladungen</div><div class="v">${nf(c.total_charges)}</div></div>
      <div class="stat"><div class="k">Überhitzung</div><div class="v ${c.overheat?"watch":""}">${nf(c.overheat)}</div></div>
      <div class="stat"><div class="k">Unterspannung</div><div class="v">${nf(c.lowvolt)}</div></div>
      <div class="stat"><div class="k">Ladung Zelle &lt; 2,5 V</div><div class="v ${c.lowv_charges?"crit":""}">${nf(c.lowv_charges)}</div></div>
    </div></div>
  <div class="two">
    <div class="card"><h2>Zellspannungen</h2>${cells.map((v,i)=>`<div class="cell"><span class="lab">Zelle ${i+1}</span>
      <span class="track"><span class="fill" style="width:${Math.max(4,Math.min(100,(v/1000-3)/1.2*100))}%"></span></span>
      <span class="cv ${v==lo||v==hi?"":""}">${nf(v/1000,3)} V</span></div>`).join("")}</div>
    <div class="card"><h2>Abzüge vom Zustand</h2>${(d.health_parts||[]).map(p=>`<div class="row part"><span>${esc(p.label)}<small>${esc(p.detail)}</small></span><span class="p">${p.penalty>0?"−"+nf(p.penalty,1):"0"}</span></div>`).join("")}</div>
  </div>
  <div class="two">
    <div class="card"><h2>Daten</h2>
      <div class="row"><span>Hergestellt</span><span>${fd(d.manufactured)}</span></div>
      <div class="row"><span>Erste Ladung</span><span>${fd(d.first_charge)}</span></div>
      <div class="row"><span>Letzte Ladung</span><span>${fd(d.last_charge)}</span></div>
      <div class="row"><span>Letzte Werkzeugnutzung</span><span>${fd(d.last_tool_use)}</span></div>
      <div class="row"><span>Tage seit Erstladung</span><span>${nf(c.days)}</span></div></div>
    <div class="card"><h2>Nutzung</h2>
      <div class="row"><span>Redlink / einfache Ladungen</span><span>${nf(c.redlink_charges)} / ${nf(c.dumb_charges)}</span></div>
      <div class="row"><span>Ladezeit gesamt</span><span>${hms(c.charge_time_s)}</span></div>
      <div class="row"><span>Zeit am Werkzeug (&gt; 10 A)</span><span>${hms(c.tool_time_s)}</span></div>
      <div class="row"><span>Entladen (Energie)</span><span>${nf(d.discharge_wh)} Wh</span></div>
      <div class="row"><span>Leer gefahren / Überstrom</span><span>${nf(c.discharge_to_empty)} / ${nf(c.overcurrent)}</span></div></div>
  </div>
  <p class="hint">${when?"Ausgelesen "+when+" · ":""}${d.blocks_ok}/${d.blocks_total} Blöcke in ${nf(d.duration_ms/1000,1)} s ·
    <a href="/api/last" download="m18-${esc(d.eserial)}.json" style="color:inherit">JSON</a></p>`;}
$("#readbtn").onclick=async()=>{const b=$("#readbtn");b.disabled=true;b.innerHTML='<span class="spin"></span> Lese Akku …';
  try{const d=await j("/api/read",{method:"POST"});render(d,new Date().toLocaleTimeString("de-DE"));}
  catch(e){$("#result").innerHTML=`<div class="card"><div class="box err">Keine Antwort vom Gerät.</div></div>`;}
  finally{b.disabled=false;b.textContent="Akku erneut auslesen";}};

/* ---------- WLAN ---------- */
async function wifiState(){const s=await j("/api/wifi");let h="";
  if(s.state=="connected"||s.state=="sta")h=`<div class="box ok">Verbunden mit <b>${esc(s.ssid)}</b> (${s.rssi} dBm)<br>
    Adresse: <span class="big-url">http://${esc(s.host)}</span><br><span class="mono">IP ${esc(s.ip)}</span></div>`;
  else if(s.state=="connecting")h=`<div class="box info"><span class="spin" style="display:inline-block;vertical-align:middle"></span> Verbinde mit ${esc(s.ssid)} …</div>`;
  else h=`<div class="box info">Einrichtungsmodus: Dieses Gerät hat ein eigenes WLAN <b>${esc(s.ap)}</b> geöffnet.
    ${s.ssid?`<br>Gespeichertes WLAN „${esc(s.ssid)}“ ist nicht erreichbar.`:"<br>Noch kein WLAN eingerichtet."}</div>`;
  if(s.error)h+=`<div class="box err">${esc(s.error)}</div>`;
  $("#wifistate").innerHTML=h;return s;}
$("#scanbtn").onclick=async()=>{const b=$("#scanbtn");b.disabled=true;b.innerHTML='<span class="spin"></span> Suche …';
  try{const l=await j("/api/networks");$("#nets").innerHTML=l.length?l.map(n=>`<div class="net" data-ssid="${esc(n.ssid)}"><span>${n.secure?"🔒 ":""}${esc(n.ssid)}</span>
    <span class="bars">${n.rssi>-60?"▂▄▆█":n.rssi>-70?"▂▄▆":n.rssi>-80?"▂▄":"▂"} ${n.rssi} dBm</span></div>`).join(""):'<p class="hint">Keine Netzwerke gefunden.</p>';
    $$(".net").forEach(n=>n.onclick=()=>{$("#ssid").value=n.dataset.ssid;$("#pass").focus();});}
  finally{b.disabled=false;b.textContent="Erneut suchen";}};
$("#wifiform").onsubmit=async e=>{e.preventDefault();const body=new URLSearchParams(new FormData(e.target));
  $("#wifiprogress").innerHTML=`<div class="box info"><span class="spin" style="display:inline-block;vertical-align:middle"></span> Verbinde mit ${esc($("#ssid").value)} … (bis zu 20 s)</div>`;
  await fetch("/api/wifi",{method:"POST",body});
  const host=($("#host").value||"m18-bridge")+".local";
  for(let i=0;i<30;i++){await new Promise(r=>setTimeout(r,1000));let s;
    try{s=await j("/api/wifi");}catch(err){continue;}
    if(s.state=="connected"||s.state=="sta"){$("#wifiprogress").innerHTML=`<div class="box ok"><b>✓ Verbunden!</b><br>
      Das Gerät ist jetzt im WLAN „${esc(s.ssid)}“ erreichbar unter<br><span class="big-url">http://${esc(s.host)}</span><br>
      <span class="mono">IP-Adresse: ${esc(s.ip)}</span><br><br>Verbinde dein Handy/Laptop wieder mit deinem normalen WLAN.
      Der Einrichtungs-Hotspot schließt sich in ${s.ap_closes_in||90} Sekunden. Die PC-Software findet die Brücke über „Suchen“.</div>`;info();wifiState();return;}
    if(s.state=="failed"){$("#wifiprogress").innerHTML=`<div class="box err">Verbindung fehlgeschlagen: ${esc(s.error||"")}. Passwort prüfen und erneut versuchen.</div>`;return;}}
  $("#wifiprogress").innerHTML=`<div class="box info">Keine Rückmeldung mehr – vermutlich hat das Gerät den Funkkanal gewechselt.
    Verbinde dich wieder mit deinem WLAN und öffne <span class="big-url">http://${esc(host)}</span>.</div>`;};
$("#forget").onclick=async()=>{if(!confirm("WLAN-Daten löschen und neu starten?"))return;await fetch("/api/wifi/forget",{method:"POST"});
  $("#wifistate").innerHTML='<div class="box info">Gelöscht – das Gerät startet im Einrichtungsmodus neu.</div>';};

/* ---------- Gerät ---------- */
async function devInfo(){const i=await info();if(!i)return;
  $("#devinfo").innerHTML=[["Firmware",i.version],["Gerätename",i.host],["IP",i.ip||"–"],["WLAN",i.wifi=="sta"?i.ssid+" ("+i.rssi+" dBm)":"Einrichtungsmodus ("+i.ap+")"],
   ["MAC",i.mac],["Akku-Leitung",i.line=="idle"?"Ruhe (J2 low)":"aktiv"],["Laufzeit",hms(i.uptime_s)],["Freier Speicher",nf(i.heap_kb)+" KB · PSRAM "+nf(i.psram_kb)+" KB"],
   ["Uhrzeit",i.time||"nicht synchronisiert"]].map(r=>`<div class="row"><span>${r[0]}</span><span class="mono">${esc(r[1])}</span></div>`).join("");}
async function cmd(text){return (await fetch("/cmd",{method:"POST",headers:{"Content-Type":"text/plain"},body:text})).text();}
$("#testbtn").onclick=async()=>{$("#testres").innerHTML='<p class="hint">…</p>';const r=await cmd("RESET\nIDLE");
  $("#testres").innerHTML=r.startsWith("OK ACK")?'<div class="box ok">✓ Akku antwortet (Sync 0xAA)</div>':`<div class="box err">✗ Keine Antwort – Verkabelung prüfen<br><span class="mono">${esc(r)}</span></div>`;};
$("#cmdbtn").onclick=async()=>{const p=$("#cmdres");p.hidden=false;p.textContent="…";p.textContent=await cmd($("#cmd").value);};

(async()=>{await info();try{const d=await j("/api/last");if(d&&d.eserial!==undefined)render(d,"zuletzt");}catch(e){}
  if(INFO.wifi!="sta")tab("wlan");setInterval(info,10000);})();
</script></body></html>)HTML";
