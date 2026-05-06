#!/usr/bin/env python3
"""
dashboard.py
------------
Run: python3 dashboard.py
View: http://localhost:5050
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from flask import Flask, Response, jsonify, render_template_string, request, stream_with_context

app = Flask(__name__, static_folder="dashboard_static")
LEARNED_RULES_FILE = Path("data/learned_rules.json")
DASHBOARD_HTML = Path("dashboard_static/index.html")
PUBLISHER_URL = (os.getenv("PUBLISHER_URL") or "https://scoutmonkeys-production.up.railway.app/login").strip()

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ScoutMonkeys — Learned Rules</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#fff;--bg2:#f5f5f3;--text:#1a1a18;--text2:#6b6b67;--text3:#9b9b96;--border:rgba(0,0,0,0.12);--border2:rgba(0,0,0,0.22);--red:#A32D2D;--red-bg:#FCEBEB;--red-b:#E24B4A;--amb:#854F0B;--amb-bg:#FAEEDA;--amb-b:#EF9F27;--blu:#185FA5;--blu-bg:#E6F1FB;--blu-b:#378ADD;--r:8px;--rl:12px;--mono:'SF Mono','Fira Code',monospace;--sans:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
@media(prefers-color-scheme:dark){:root{--bg:#1c1c1a;--bg2:#252523;--text:#e8e8e4;--text2:#9b9b96;--text3:#6b6b67;--border:rgba(255,255,255,0.1);--border2:rgba(255,255,255,0.2);--red:#F7C1C1;--red-bg:#501313;--amb:#FAC775;--amb-bg:#412402;--blu:#B5D4F4;--blu-bg:#042C53}}
body{font-family:var(--sans);background:var(--bg);color:var(--text);padding:2rem 1.5rem;max-width:900px;margin:0 auto}
.top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:1.5rem;padding-bottom:1rem;border-bottom:0.5px solid var(--border)}
.top-title{font-size:18px;font-weight:500}.top-sub{font-size:13px;color:var(--text2);margin-top:3px}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.pub-link{display:inline-block;font-size:13px;font-family:var(--sans);padding:6px 10px;border:0.5px solid var(--border2);border-radius:var(--r);background:var(--bg);color:var(--text);text-decoration:none}
.pub-link:hover{background:var(--bg2)}
select,button{font-size:13px;font-family:var(--sans);padding:6px 10px;border:0.5px solid var(--border2);border-radius:var(--r);background:var(--bg);color:var(--text);cursor:pointer}
select:hover,button:hover{background:var(--bg2)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:1.5rem}
.stat{background:var(--bg2);border-radius:var(--r);padding:12px 14px}
.stat-label{font-size:12px;color:var(--text2);margin-bottom:4px}.stat-val{font-size:24px;font-weight:500}
.stat-val.red{color:var(--red-b)}.stat-val.amb{color:var(--amb-b)}.stat-val.blu{color:var(--blu-b)}
.list{display:flex;flex-direction:column;gap:8px}
.card{background:var(--bg);border:0.5px solid var(--border);border-left:3px solid transparent;border-radius:var(--rl);padding:12px 14px}
.card.critical{border-left-color:var(--red-b)}.card.warning{border-left-color:var(--amb-b)}.card.info{border-left-color:var(--blu-b)}
.card-top{display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap}
.badge{font-size:11px;font-weight:500;padding:2px 8px;border-radius:var(--r)}
.badge.critical{background:var(--red-bg);color:var(--red)}.badge.warning{background:var(--amb-bg);color:var(--amb)}.badge.info{background:var(--blu-bg);color:var(--blu)}
.site-badge{font-size:11px;font-weight:500;padding:2px 8px;border-radius:var(--r);background:var(--bg2);color:var(--text2)}
.field{font-size:12px;font-family:var(--mono);color:var(--text2);margin-left:auto}
.rule-text{font-size:14px;line-height:1.5;margin-bottom:8px}
.diff{display:flex;gap:8px;flex-wrap:wrap}
.diff-box{flex:1;min-width:0;background:var(--bg2);border-radius:var(--r);padding:6px 10px}
.diff-label{font-size:11px;color:var(--text3);margin-bottom:2px}
.diff-val{font-size:12px;font-family:var(--mono);word-break:break-all}
.meta{font-size:11px;color:var(--text3);margin-top:8px}
.empty{text-align:center;padding:3rem;color:var(--text2);font-size:14px}
</style>
</head>
<body>
<div class="top">
  <div>
    <div class="top-title">Learned rules</div>
    <div class="top-sub">Paste article Google Doc URL in Publisher.</div>
    <div class="top-sub" id="sub">Loading...</div>
  </div>
  <div class="filters">
    <a id="publish-link" class="pub-link" href="#" target="_blank" rel="noopener">Open Publisher</a>
    <select id="sf" onchange="render()"><option value="">All sites</option><option value="dcr">DCR</option><option value="cd">CD</option></select>
    <select id="vf" onchange="render()"><option value="">All severities</option><option value="critical">Critical</option><option value="warning">Warning</option><option value="info">Info</option></select>
    <select id="ff" onchange="render()"><option value="">All fields</option></select>
    <button onclick="clear_()">Clear</button>
    <button onclick="load()">↻ Reload</button>
  </div>
</div>
<div class="stats">
  <div class="stat"><div class="stat-label">Total</div><div class="stat-val" id="st">—</div></div>
  <div class="stat"><div class="stat-label">Critical</div><div class="stat-val red" id="sc">—</div></div>
  <div class="stat"><div class="stat-label">Warning</div><div class="stat-val amb" id="sw">—</div></div>
  <div class="stat"><div class="stat-label">Info</div><div class="stat-val blu" id="si">—</div></div>
  <div class="stat"><div class="stat-label">Posts</div><div class="stat-val" id="sp">—</div></div>
</div>
<div class="list" id="list"></div>
<script>
let rules=[];
const fmt=v=>typeof v==='object'&&v?JSON.stringify(v):String(v);
const ts=iso=>new Date(iso).toLocaleDateString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
function render(){
  const s=document.getElementById('sf').value,v=document.getElementById('vf').value,f=document.getElementById('ff').value;
  const filt=rules.filter(r=>(!s||r.site===s)&&(!v||r.severity===v)&&(!f||r.field===f));
  document.getElementById('st').textContent=rules.length;
  document.getElementById('sc').textContent=rules.filter(r=>r.severity==='critical').length;
  document.getElementById('sw').textContent=rules.filter(r=>r.severity==='warning').length;
  document.getElementById('si').textContent=rules.filter(r=>r.severity==='info').length;
  document.getElementById('sp').textContent=new Set(rules.map(r=>r.post_id)).size;
  const list=document.getElementById('list');
  if(!filt.length){list.innerHTML='<div class="empty">No deltas match current filters.</div>';return;}
  list.innerHTML=filt.map(r=>`<div class="card ${r.severity}"><div class="card-top"><span class="badge ${r.severity}">${r.severity}</span><span class="site-badge">${r.site.toUpperCase()}</span><span class="field">${r.field}</span></div><div class="rule-text">${r.inferred_rule}</div><div class="diff"><div class="diff-box"><div class="diff-label">before</div><div class="diff-val">${fmt(r.before)}</div></div><div class="diff-box"><div class="diff-label">after</div><div class="diff-val">${fmt(r.after)}</div></div></div><div class="meta">Post #${r.post_id} · ${ts(r.timestamp)}</div></div>`).join('');
}
function populateFields(){
  const sel=document.getElementById('ff');
  const fields=[...new Set(rules.map(r=>r.field))].sort();
  sel.innerHTML='<option value="">All fields</option>'+fields.map(f=>`<option value="${f}">${f}</option>`).join('');
}
function clear_(){document.getElementById('sf').value='';document.getElementById('vf').value='';document.getElementById('ff').value='';render();}
async function load(){
  try{const res=await fetch('/api/learned-rules');rules=await res.json();}catch{rules=[];}
  document.getElementById('sub').textContent='Last loaded: '+ts(new Date().toISOString());
  populateFields();render();
}
load();
document.getElementById('publish-link').href = {{ publisher_url|tojson }};
</script>
</body>
</html>"""

PUBLISH_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Scoutmonkeys Publisher</title>
  <style>
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fb;margin:0;padding:1rem}
    .card{max-width:760px;margin:1rem auto;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:1rem 1.1rem}
    h1{margin:0 0 .2rem 0;font-size:1.2rem}
    .sub{margin:.15rem 0 .9rem 0;color:#6b7280;font-size:.92rem}
    label{display:block;font-weight:600;margin:.7rem 0 .35rem}
    input,select,textarea{width:100%;padding:.72rem .78rem;border:1px solid #d1d5db;border-radius:8px;font-size:.98rem}
    textarea{min-height:88px}
    button{margin-top:.95rem;width:100%;border:0;border-radius:8px;padding:.82rem;background:#2563eb;color:#fff;font-weight:700;cursor:pointer}
    #log-wrap{display:none;margin-top:1rem}
    #log{height:260px;overflow:auto;white-space:pre-wrap;background:#0b1220;color:#e5e7eb;border-radius:8px;padding:.7rem;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.85rem}
    #result{display:none;margin-top:.8rem}
    #open{display:inline-block;padding:.65rem .85rem;border-radius:8px;background:#16a34a;color:#fff;text-decoration:none}
  </style>
</head>
<body>
  <div class="card">
    <h1>Scoutmonkeys Publisher</h1>
    <p class="sub">Paste a Google Doc URL and publish a WordPress draft.</p>
    <label for="gdoc">Google Doc URL</label>
    <input id="gdoc" placeholder="https://docs.google.com/document/d/.../edit" />
    <label for="site">Site</label>
    <select id="site"><option value="cd">Cultural Daily</option><option value="dcr">DCReport</option></select>
    <label for="notes">Notes (optional)</label>
    <textarea id="notes" placeholder="Optional run-specific notes"></textarea>
    <button id="pub" onclick="startPublish()">Publish Draft</button>
    <div id="log-wrap"><div id="log"></div></div>
    <div id="result"><a id="open" href="#" target="_blank">Open Draft</a></div>
  </div>
<script>
function startPublish(){
  const gdoc=document.getElementById('gdoc').value.trim();
  const site=document.getElementById('site').value;
  const notes=document.getElementById('notes').value.trim();
  if(!gdoc){alert('Please enter a Google Doc URL.');return;}
  const btn=document.getElementById('pub');
  const logWrap=document.getElementById('log-wrap');
  const logEl=document.getElementById('log');
  const result=document.getElementById('result');
  const open=document.getElementById('open');
  btn.disabled=true; btn.textContent='Publishing...'; logWrap.style.display='block'; result.style.display='none'; logEl.textContent='';
  const qs=new URLSearchParams({gdoc,site}); if(notes) qs.set('notes',notes);
  const es=new EventSource('/stream?'+qs.toString());
  es.addEventListener('log',ev=>{logEl.textContent+=ev.data+'\\n';logEl.scrollTop=logEl.scrollHeight;});
  es.addEventListener('done',ev=>{
    es.close(); btn.disabled=false; btn.textContent='Publish Draft';
    try{
      const d=JSON.parse(ev.data||'{}');
      if(d.edit_url){open.href=d.edit_url;result.style.display='block';}
    }catch(_e){}
  });
}
</script>
</body>
</html>"""


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


def _extract_result(output: str) -> dict:
    try:
        start = output.rfind("{")
        if start >= 0:
            chunk = output[start:].strip()
            return json.loads(chunk)
    except Exception:
        pass
    try:
        m = re.search(r'"edit_url"\s*:\s*"([^"]+)"', output)
        url = m.group(1) if m else ""
        qa_m = re.search(r'"qa_ok"\s*:\s*(true|false)', output)
        qa = (qa_m.group(1) == "true") if qa_m else None
        return {"edit_url": url, "qa_ok": qa} if url else {}
    except Exception:
        return {}
    return {}

def read_rules():
    if not LEARNED_RULES_FILE.exists():
        return []
    with open(LEARNED_RULES_FILE) as f:
        return json.load(f)

@app.route("/")
def index():
    DASHBOARD_HTML.parent.mkdir(parents=True, exist_ok=True)
    if not DASHBOARD_HTML.exists():
        DASHBOARD_HTML.write_text(HTML)
    return render_template_string(HTML, publisher_url=PUBLISHER_URL)

@app.route("/api/learned-rules")
def learned_rules():
    return jsonify(read_rules())


@app.route("/login")
def login():
    return render_template_string(PUBLISH_HTML)


@app.route("/stream")
def stream():
    gdoc = (request.args.get("gdoc") or "").strip()
    site = (request.args.get("site") or "cd").strip()
    notes = (request.args.get("notes") or "").strip()
    if not gdoc:
        return Response(
            _sse("log", "[error] No Google Doc URL provided.") + _sse("done", "{}"),
            mimetype="text/event-stream",
        )

    root = Path(__file__).resolve().parent
    env = {**os.environ, "CD_RELAX_SOCIAL_WP_PIXEL_ASSERT": "1"}
    cmd = [sys.executable, str(root / "pipeline.py"), gdoc, site]
    if notes:
        cmd += ["--notes", notes]

    def generate():
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        collected = []
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n\r")
            collected.append(line)
            yield _sse("log", line)
        proc.wait()
        result = _extract_result("\n".join(collected))
        if proc.returncode != 0 and not result.get("edit_url"):
            result["qa_ok"] = False
        yield _sse("done", json.dumps(result))

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

if __name__ == "__main__":
    DASHBOARD_HTML.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_HTML.write_text(HTML)
    print("[dashboard] Starting at http://localhost:5050")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False)
