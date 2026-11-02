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
.prog{height:6px;background:var(--raise);border-radius:3px;overflow:hidden;margin-top:10px}.prog i{display:block;height:100%;width:0;background:var(--accent);transition:width .3s}
.warn{color:var(--watch)}input[type=range]{padding:0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:640px){.two{grid-template-columns:1fr}}
</style></head><body>
<header><span class="logo">M18</span><h1>Akku-Brücke<small id="hostline">…</small></h1><span class="pill" id="pill">…</span></header>
<nav><button data-tab="akku" class="on">Akku</button><button data-tab="verlauf">Verlauf <span id="newcount" class="pill ok" style="padding:0 7px;margin:0" hidden></span></button><button data-tab="wlan">WLAN</button><button data-tab="geraet">Gerät</button></nav>
<main>
<section id="tab-akku">
  <div class="card">
    <div class="two" style="gap:10px;margin-bottom:12px"><div><label style="margin-top:0">Standort / Kunde (optional)</label><input id="loc" maxlength="60" placeholder="z. B. Kunde Meier, Halle 2"></div>
      <div><label style="margin-top:0">Kommentar (optional)</label><input id="comment" maxlength="200" placeholder="z. B. Akku 3 aus Koffer"></div></div>
    <button class="btn primary big" id="readbtn">Akku auslesen</button>
    <div class="prog" id="prog" hidden><i></i></div>
    <p class="hint" id="readhint">Akku am Adapter anschließen und auslesen (dauert ca. 7 Sekunden).</p></div>
  <div id="result"></div>
</section>
<section id="tab-verlauf" hidden>
  <div class="card"><h2>Gespeicherte Messungen</h2><div id="hstats">…</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
      <button class="btn" id="hexport">Alle als JSON exportieren</button><button class="btn" id="hclear">Übernommene löschen</button></div>
    <p class="hint">Jede erfolgreiche Messung wird auf der Brücke gespeichert. Die PC-Software übernimmt sie in die Datenbank
      (Auslesen → Brücke verbinden → „Übernehmen“) – oder die JSON-Datei dort unter Einstellungen importieren.</p></div>
  <div class="card"><div id="hlist"></div><button class="btn" id="hmore" hidden style="margin-top:10px">Ältere laden</button></div>
  <div id="hview"></div>
</section>
<section id="tab-wlan" hidden>
  <div class="card"><h2>Verbindung</h2><div id="wifistate">…</div></div>
  <div class="card"><h2>Mit WLAN verbinden</h2><div id="lockbox"></div>
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
  <div class="two"><div class="card"><h2>Status-LED</h2>
    <label>LED-Pin</label><select id="ledpin"><option value="48">GPIO 48 (DevKitC-1 v1.0, die meisten N16R8)</option>
      <option value="38">GPIO 38 (DevKitC-1 v1.1)</option><option value="-1">aus</option><option value="custom">anderer Pin …</option></select>
    <input id="ledcustom" type="number" min="1" max="48" placeholder="GPIO-Nummer" hidden style="margin-top:6px">
    <label>Helligkeit</label><input id="ledbright" type="range" min="0" max="255">
    <div style="display:flex;gap:8px;margin-top:10px"><button class="btn" id="ledtest">Testen (rot, grün, blau, weiß)</button><button class="btn primary" id="ledsave">Speichern</button></div>
    <p class="hint">Grün = im WLAN · Blau blinkt = Einrichtungsmodus · Gelb = liest Akku · Grün kurz = gelesen · Rot blinkt = Fehler ·
      Weiß blinkt = PC liest über die Brücke · Türkis = Einstellungen entsperrt · Lila = Firmware-Update</p></div>
  <div class="card"><h2>Firmware-Update</h2><div id="otalock"></div>
    <input type="file" id="fwfile" accept=".bin"><button class="btn primary" id="fwbtn" style="margin-top:10px">Firmware hochladen</button>
    <div class="prog" id="fwprog" hidden><i></i></div><div id="fwres"></div>
    <p class="hint">.bin aus der Arduino IDE: <i>Sketch → Kompilierte Binärdatei exportieren</i> (die Datei ohne „bootloader“/„merged“ im Namen).</p></div></div>
  <div class="card"><h2>Zugriffsschutz</h2><div id="passstate"></div>
    <label>Neues Passwort (leer lassen = Schutz aus)</label><input id="newpass" type="password" autocomplete="new-password" maxlength="63">
    <label>Wiederholen</label><input id="newpass2" type="password" autocomplete="new-password" maxlength="63">
    <button class="btn primary" id="passbtn" style="margin-top:10px">Speichern</button>
    <p class="hint">Schützt Weboberfläche und PC-Zugriff (Benutzer „m18“). Ab 8 Zeichen gilt es auch für das Einrichtungs-WLAN.
      Vergessen? BOOT-Taste 5 s halten setzt WLAN und Passwort zurück.</p></div>
  <div class="card"><h2>Befehle</h2><textarea id="cmd" rows="4">PING
RESET
READ 0x400A 10
IDLE</textarea><button class="btn" id="cmdbtn" style="margin-top:8px">Senden</button><pre id="cmdres" hidden></pre>
    <p class="hint">PING · RESET · IDLE · READ &lt;addr&gt; &lt;len&gt; · NOTE &lt;addr&gt; &lt;byte&gt; · SCAN · INFO · HSTAT · HLIST · HGET &lt;id&gt;</p></div>
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
  if(name=="wlan")wifiState();if(name=="geraet")devInfo();if(name=="verlauf"){histStats();histLoad(true);}}
$$("nav button").forEach(b=>b.onclick=()=>tab(b.dataset.tab));

async function info(){try{INFO=await j("/api/info");}catch(e){return;}
  const w=INFO.wifi,p=$("#pill");
  p.className="pill "+(w=="sta"?"ok":"ap");
  p.textContent=w=="sta"?"WLAN: "+INFO.ssid:"Einrichtungsmodus";
  $("#hostline").textContent=(w=="sta"?"http://"+INFO.host+" · "+INFO.ip:"WLAN „"+INFO.ap+"“ · "+INFO.ap_ip);
  if(!$("#host").value){$("#host").value=INFO.host.replace(".local","");hostPreview();}
  try{const hs=await j("/api/history/stats");$("#newcount").hidden=!hs.unsynced;$("#newcount").textContent=hs.unsynced||"";}catch(e){}
  const allowed=INFO.setup_client||INFO.unlocked;
  const lock=allowed?(INFO.unlocked?`<div class="box ok">Einstellungen entsperrt (noch ${INFO.unlock_s} s).</div>`:""):
    `<div class="box info">🔒 Änderungen sind gesperrt. Kurz die <b>BOOT-Taste</b> am Gerät drücken – dann 2 Minuten entsperrt (LED türkis).</div>`;
  $("#lockbox").innerHTML=lock;$("#otalock").innerHTML=lock;
  $$("#wifiform input,#wifiform button,#forget,#fwbtn,#fwfile,#newpass,#newpass2,#passbtn").forEach(e=>e.disabled=!allowed);
  $("#passstate").innerHTML=(INFO.auth?'<div class="box ok" style="margin-top:0">🔒 Passwortschutz ist aktiv.</div>':'<div class="box info" style="margin-top:0">Kein Passwort gesetzt – jeder im WLAN kann die Brücke nutzen.</div>')+lock;
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
  const warns=(d.warnings||[]).length?`<div class="card"><div class="box err" style="margin:0">${d.warnings.map(w=>"⚠ "+esc(w)).join("<br>")}</div></div>`:"";
  $("#result").innerHTML=warns+`
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
  <p class="hint">${when?"Ausgelesen "+when+" · ":""}${d.blocks_ok}/${d.blocks_total} Blöcke in ${nf(d.duration_ms/1000,1)} s${d.retries?` · ${d.retries} Wiederholungen`:""} ·
    <a href="/api/last" download="m18-${esc(d.eserial)}.json" style="color:inherit">JSON</a></p>`;}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
$("#readbtn").onclick=async()=>{const b=$("#readbtn"),pg=$("#prog");b.disabled=true;b.innerHTML='<span class="spin"></span> Lese Akku …';
  pg.hidden=false;pg.firstChild.style.width="0%";
  try{try{localStorage.setItem("m18.loc",$("#loc").value);localStorage.setItem("m18.comment",$("#comment").value);}catch(e){}
    const body=new URLSearchParams({t:Math.floor(Date.now()/1000),loc:$("#loc").value,comment:$("#comment").value});
    const r=await fetch("/api/read",{method:"POST",body});
    if(r.status==409){$("#readhint").textContent="Gerät ist beschäftigt (PC liest gerade über die Brücke) – gleich nochmal versuchen.";return;}
    for(let i=0;i<120;i++){await sleep(500);let st;try{st=await j("/api/status");}catch(e){continue;}
      pg.firstChild.style.width=st.progress+"%";if(!st.reading)break;}
    const d=await j("/api/last");render(d,new Date().toLocaleTimeString("de-DE"));
    if(d.history_id)$("#readhint").textContent="Gespeichert als Messung #"+d.history_id+" – wird bei der nächsten Übernahme in die PC-Datenbank übertragen.";
    $("#comment").value="";info();}
  catch(e){$("#result").innerHTML=`<div class="card"><div class="box err">Keine Antwort vom Gerät.</div></div>`;}
  finally{b.disabled=false;b.textContent="Akku erneut auslesen";pg.hidden=true;}};

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
  try{let r={scanning:true};for(let i=0;i<30&&r.scanning;i++){r=await j("/api/networks");if(r.scanning)await sleep(700);}
    const l=r.networks||[];$("#nets").innerHTML=l.length?l.map(n=>`<div class="net" data-ssid="${esc(n.ssid)}"><span>${n.secure?"🔒 ":""}${esc(n.ssid)}</span>
    <span class="bars">${n.rssi>-60?"▂▄▆█":n.rssi>-70?"▂▄▆":n.rssi>-80?"▂▄":"▂"} ${n.rssi} dBm</span></div>`).join(""):'<p class="hint">Keine Netzwerke gefunden.</p>';
    $$(".net").forEach(n=>n.onclick=()=>{$("#ssid").value=n.dataset.ssid;$("#pass").focus();});}
  finally{b.disabled=false;b.textContent="Erneut suchen";}};
$("#wifiform").onsubmit=async e=>{e.preventDefault();const body=new URLSearchParams(new FormData(e.target));
  $("#wifiprogress").innerHTML=`<div class="box info"><span class="spin" style="display:inline-block;vertical-align:middle"></span> Verbinde mit ${esc($("#ssid").value)} … (bis zu 20 s)</div>`;
  const resp=await fetch("/api/wifi",{method:"POST",body});
  if(!resp.ok){const e=await resp.json().catch(()=>({}));$("#wifiprogress").innerHTML=`<div class="box err">${esc(e.error||"Fehler")}</div>`;return;}
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
$("#forget").onclick=async()=>{if(!confirm("WLAN-Daten löschen und neu starten?"))return;const r=await fetch("/api/wifi/forget",{method:"POST"});
  if(!r.ok){const e=await r.json().catch(()=>({}));$("#wifistate").innerHTML=`<div class="box err">${esc(e.error||"Fehler")}</div>`;return;}
  $("#wifistate").innerHTML='<div class="box info">Gelöscht – das Gerät startet im Einrichtungsmodus neu.</div>';};

/* ---------- Gerät ---------- */
async function devInfo(){const i=await info();if(!i)return;ledInit();
  $("#devinfo").innerHTML=[["Firmware",i.version],["Gerätename",i.host],["IP",i.ip||"–"],["WLAN",i.wifi=="sta"?i.ssid+" ("+i.rssi+" dBm)":"Einrichtungsmodus ("+i.ap+")"],
   ["MAC",i.mac],["Akku-Leitung",i.line=="idle"?"Ruhe (J2 low)":"aktiv"],["Laufzeit",hms(i.uptime_s)],["Letzter Neustart",i.reset_reason],
   ["Freier Speicher",nf(i.heap_kb)+" KB (min. "+nf(i.min_heap_kb)+") · PSRAM "+nf(i.psram_kb)+" KB"],
   ["Uhrzeit",i.time||"nicht synchronisiert"],["Build",i.build]].map(r=>`<div class="row"><span>${r[0]}</span><span class="mono">${esc(r[1])}</span></div>`).join("");}
async function cmd(text){return (await fetch("/cmd",{method:"POST",headers:{"Content-Type":"text/plain"},body:text})).text();}
$("#testbtn").onclick=async()=>{$("#testres").innerHTML='<p class="hint">…</p>';const r=await cmd("RESET\nIDLE");
  $("#testres").innerHTML=r.startsWith("OK ACK")?'<div class="box ok">✓ Akku antwortet (Sync 0xAA)</div>':`<div class="box err">✗ Keine Antwort – Verkabelung prüfen<br><span class="mono">${esc(r)}</span></div>`;};
/* ---------- Verlauf ---------- */
let hNext=0,hItems=[];
function fdt(ts){return ts?new Date(ts*1000).toLocaleString("de-DE",{dateStyle:"short",timeStyle:"short"}):"Zeit unbekannt";}
async function histStats(){const s=await j("/api/history/stats");
  if(s.ok===false||!s.ready){$("#hstats").innerHTML='<div class="box err" style="margin-top:0">Kein Speicher verfügbar – Partition Scheme „16M Flash (3MB APP/9.9MB FATFS)“ wählen.</div>';return s;}
  $("#hstats").innerHTML=`<div class="row"><span>Messungen</span><span>${nf(s.count)}</span></div>
    <div class="row"><span>Noch nicht in der PC-Datenbank</span><span class="${s.unsynced?"warn":""}">${nf(s.unsynced)}</span></div>
    <div class="row"><span>Speicher frei</span><span>${nf(s.free_kb/1024,1)} von ${nf(s.total_kb/1024,1)} MB</span></div>`;return s;}
function histRow(m){return `<div class="net" data-id="${m.id}"><span><b>${esc(m.note||m.eserial||"?")}</b> <span class="mono">${esc(m.model||"")}</span><br>
  <span class="hint" style="margin:0">${fdt(m.ts)}${m.loc?" · 📍 "+esc(m.loc):""}${m.comment?" · "+esc(m.comment):""}</span></span>
  <span style="text-align:right"><b class="${m.health>=80?"good":m.health>=60?"watch":"weak"}">${m.health??"–"}</b><br>
  <span class="bars">${m.synced?"✓ übernommen":"neu"}</span></span></div>`;}
async function histLoad(reset){
  if(reset){hNext=0;hItems=[];}
  // newest first: page through all ids once (ascending), then show reversed
  let after=hNext,guard=0;const page=[];
  do{const r=await j("/api/history?after="+after+"&max=100&filter=all");if(r.ok===false)break;page.push(...r.items);after=r.next;guard++;}while(after&&guard<40);
  hItems=page.reverse();
  $("#hlist").innerHTML=hItems.length?hItems.map(histRow).join(""):'<p class="hint">Noch keine Messungen gespeichert.</p>';
  $$("#hlist .net").forEach(n=>n.onclick=()=>histView(n.dataset.id));}
async function histView(id){const r=await j("/api/history/item?id="+id);if(r.ok===false)return;
  const m=r.meta;render(r.scan,fdt(m.ts));const res=$("#result").innerHTML;$("#result").innerHTML="";
  $("#hview").innerHTML=`<div class="card"><h2>Messung #${m.id} · ${fdt(m.ts)}${m.loc?" · "+esc(m.loc):""}</h2>${m.comment?`<p>${esc(m.comment)}</p>`:""}
    <button class="btn" id="hdel">Diese Messung löschen</button></div>`+res;
  $("#hdel").onclick=async()=>{if(!confirm("Messung #"+m.id+" löschen?"))return;await fetch("/api/history/delete",{method:"POST",body:new URLSearchParams({id:m.id})});
    $("#hview").innerHTML="";histStats();histLoad(true);};
  $("#hview").scrollIntoView({behavior:"smooth"});}
$("#hexport").onclick=async()=>{const b=$("#hexport");b.disabled=true;const recs=[];
  try{for(let i=0;i<hItems.length;i++){b.textContent="Exportiere "+(i+1)+"/"+hItems.length+" …";const r=await j("/api/history/item?id="+hItems[i].id);if(r.meta)recs.push(r);}
    const blob=new Blob([JSON.stringify({format:"m18-bridge-export",version:1,device:INFO.host,exported:new Date().toISOString(),records:recs})],{type:"application/json"});
    const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="m18-bruecke-"+new Date().toISOString().slice(0,10)+".json";a.click();}
  finally{b.disabled=false;b.textContent="Alle als JSON exportieren";}};
$("#hclear").onclick=async()=>{if(!confirm("Alle bereits in die PC-Datenbank übernommenen Messungen von der Brücke löschen?"))return;
  const r=await j("/api/history/clear",{method:"POST"});alert(r.deleted+" Messungen gelöscht");histStats();histLoad(true);};
/* ---------- Passwort ---------- */
$("#passbtn").onclick=async()=>{const a=$("#newpass").value,b=$("#newpass2").value;
  if(a!==b){alert("Die Passwörter stimmen nicht überein");return;}
  const r=await fetch("/api/password",{method:"POST",body:new URLSearchParams({pass:a})});const e=await r.json().catch(()=>({}));
  if(!r.ok){alert("Fehler: "+(e.error||r.status));return;}
  alert(a?"Passwort gesetzt. Der Browser fragt gleich danach (Benutzer „m18“).":"Passwortschutz ausgeschaltet.");$("#newpass").value=$("#newpass2").value="";location.reload();};
/* LED */
function ledPinValue(){const v=$("#ledpin").value;return v=="custom"?$("#ledcustom").value:v;}
function ledInit(){if(INFO.led_pin==null)return;const p=String(INFO.led_pin);
  if([...$("#ledpin").options].some(o=>o.value==p))$("#ledpin").value=p;else{$("#ledpin").value="custom";$("#ledcustom").hidden=false;$("#ledcustom").value=p;}
  $("#ledbright").value=INFO.led_brightness;}
$("#ledpin").onchange=()=>{$("#ledcustom").hidden=$("#ledpin").value!="custom";};
$("#ledtest").onclick=()=>fetch("/api/led/test",{method:"POST",body:new URLSearchParams({pin:ledPinValue()})});
$("#ledsave").onclick=async()=>{const r=await fetch("/api/led",{method:"POST",body:new URLSearchParams({pin:ledPinValue(),brightness:$("#ledbright").value})});
  const e=await r.json().catch(()=>({}));alert(r.ok?"LED-Einstellung gespeichert":"Fehler: "+(e.error||r.status));};
$("#ledbright").onchange=()=>fetch("/api/led",{method:"POST",body:new URLSearchParams({brightness:$("#ledbright").value})});
/* Firmware update */
$("#fwbtn").onclick=()=>{const f=$("#fwfile").files[0];if(!f){alert("Bitte zuerst die .bin-Datei wählen");return;}
  if(!confirm("Firmware "+f.name+" ("+Math.round(f.size/1024)+" KB) aufspielen? Das Gerät startet danach neu."))return;
  const x=new XMLHttpRequest(),fd=new FormData(),pg=$("#fwprog");fd.append("firmware",f,f.name);pg.hidden=false;
  x.upload.onprogress=e=>{if(e.lengthComputable)pg.firstChild.style.width=(e.loaded/e.total*100)+"%";};
  x.onload=()=>{let r={};try{r=JSON.parse(x.responseText);}catch(e){}
    $("#fwres").innerHTML=r.ok?'<div class="box ok">✓ Update erfolgreich – Gerät startet neu. Seite in ca. 15 s neu laden.</div>'
      :`<div class="box err">Update fehlgeschlagen: ${esc(r.error||x.status)}</div>`;};
  x.onerror=()=>{$("#fwres").innerHTML='<div class="box err">Verbindung abgebrochen.</div>';};
  x.open("POST","/api/update");x.send(fd);};
$("#cmdbtn").onclick=async()=>{const p=$("#cmdres");p.hidden=false;p.textContent="…";p.textContent=await cmd($("#cmd").value);};

try{$("#loc").value=localStorage.getItem("m18.loc")||"";}catch(e){}
(async()=>{await info();try{const d=await j("/api/last");if(d&&d.eserial!==undefined)render(d,"zuletzt");}catch(e){}
  if(INFO.wifi!="sta")tab("wlan");
  setInterval(()=>{const t=$("nav button.on").dataset.tab;info();if(t=="wlan")wifiState();},3000);})();
</script></body></html>)HTML";
