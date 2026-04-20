"""
Scoutmonkeys publishing dashboard — password-protected Flask UI with
live-streaming pipeline output via Server-Sent Events.
"""
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from flask import Flask, Response, redirect, render_template_string, request, session, stream_with_context, url_for

app = Flask(__name__)
_secret_key = os.environ.get("SECRET_KEY", "")
if not _secret_key:
    _secret_key = secrets.token_hex(32)
    print("[WARN] SECRET_KEY not set — sessions will reset on restart.")
app.secret_key = _secret_key
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

# ── Templates ─────────────────────────────────────────────────────────────

_LOGIN = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Scoutmonkeys</title>
<style>
*,*::before,*::after{box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#f0f2f5;
  display:flex;align-items:center;justify-content:center;
  min-height:100vh;margin:0;padding:1rem}
.card{background:#fff;border-radius:14px;box-shadow:0 4px 24px rgba(0,0,0,.10);
  padding:2.5rem 2rem;width:100%;max-width:360px}
h1{margin:0 0 1.75rem;font-size:1.6rem;text-align:center;color:#111}
label{display:block;font-size:.85rem;font-weight:600;color:#444;margin-bottom:.3rem}
input[type=password]{width:100%;padding:.8rem 1rem;border:1.5px solid #d1d5db;
  border-radius:8px;font-size:1rem;outline:none;transition:border .15s}
input[type=password]:focus{border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.15)}
button{width:100%;margin-top:1.25rem;padding:.85rem;background:#2563eb;
  color:#fff;border:none;border-radius:8px;font-size:1rem;
  font-weight:700;cursor:pointer;transition:background .15s}
button:hover{background:#1d4ed8}
.err{color:#dc2626;font-size:.85rem;margin-top:.75rem;text-align:center}
</style>
</head>
<body>
<div class="card">
  <h1>🐒 Scoutmonkeys</h1>
  <form method="post" action="/login">
    <label for="pw">Password</label>
    <input type="password" id="pw" name="password" autofocus required/>
    <button type="submit">Sign in</button>
  </form>
  {% if error %}<p class="err">Incorrect password — try again.</p>{% endif %}
</div>
</body>
</html>"""

_MAIN = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Scoutmonkeys — Publish</title>
<style>
*,*::before,*::after{box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#f0f2f5;
  margin:0;padding:1rem;min-height:100vh}
.wrap{max-width:660px;margin:2rem auto}
.card{background:#fff;border-radius:14px;box-shadow:0 4px 24px rgba(0,0,0,.10);padding:2rem}
h1{margin:0 0 .2rem;font-size:1.55rem;color:#111}
.sub{color:#6b7280;font-size:.88rem;margin:0 0 1.75rem}
label{display:block;font-size:.85rem;font-weight:600;color:#444;
  margin-top:1.25rem;margin-bottom:.3rem}
input[type=text],select{width:100%;padding:.8rem 1rem;
  border:1.5px solid #d1d5db;border-radius:8px;font-size:1rem;
  outline:none;transition:border .15s;background:#fff}
input[type=text]:focus,select:focus{
  border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.15)}
#pub-btn{width:100%;margin-top:1.75rem;padding:1rem;
  background:#16a34a;color:#fff;border:none;border-radius:8px;
  font-size:1.1rem;font-weight:700;cursor:pointer;
  letter-spacing:.01em;transition:background .15s}
#pub-btn:hover:not(:disabled){background:#15803d}
#pub-btn:disabled{background:#9ca3af;cursor:not-allowed}
#log-section{display:none;margin-top:1.75rem}
#log-section h2{font-size:.95rem;font-weight:600;color:#374151;margin:0 0 .5rem}
#log{background:#0f172a;color:#cbd5e1;border-radius:8px;padding:1rem 1.1rem;
  font-family:'SF Mono','Fira Mono',monospace;font-size:.76rem;
  line-height:1.65;max-height:380px;overflow-y:auto;
  white-space:pre-wrap;word-break:break-all}
#result-section{display:none;margin-top:1.5rem;text-align:center}
#draft-btn{display:inline-block;padding:.85rem 2.5rem;
  background:#2563eb;color:#fff;border-radius:8px;
  font-weight:700;font-size:1rem;text-decoration:none;
  transition:background .15s}
#draft-btn:hover{background:#1d4ed8}
.qa-ok{color:#16a34a;font-size:.85rem;margin-top:.6rem}
.qa-warn{color:#d97706;font-size:.85rem;margin-top:.6rem}
.signout{text-align:right;font-size:.8rem;margin-top:.9rem}
.signout a{color:#6b7280;text-decoration:none}
.signout a:hover{color:#111}
@media(max-width:500px){
  .wrap{margin:.5rem auto}
  .card{padding:1.25rem}
  #pub-btn{font-size:1rem;padding:.85rem}
}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>🐒 Scoutmonkeys</h1>
    <p class="sub">Publish a Google Doc to WordPress as a draft.</p>

    <label for="gdoc">Google Doc URL</label>
    <input type="text" id="gdoc"
      placeholder="https://docs.google.com/document/d/…" />

    <label for="site">Site</label>
    <select id="site">
      <option value="cd">Cultural Daily</option>
      <option value="dcr">DCReport</option>
    </select>

    <button id="pub-btn" onclick="startPublish()">Publish Draft</button>

    <div id="log-section">
      <h2>Progress</h2>
      <div id="log"></div>
    </div>

    <div id="result-section">
      <a id="draft-btn" href="#" target="_blank">Open Draft in WordPress →</a>
      <p id="qa-msg"></p>
    </div>
  </div>
  <p class="signout"><a href="/logout">Sign out</a></p>
</div>

<script>
function startPublish() {
  const gdoc = document.getElementById('gdoc').value.trim();
  const site = document.getElementById('site').value;
  if (!gdoc) { alert('Please enter a Google Doc URL.'); return; }

  const btn      = document.getElementById('pub-btn');
  const logSec   = document.getElementById('log-section');
  const logEl    = document.getElementById('log');
  const resSec   = document.getElementById('result-section');
  const draftBtn = document.getElementById('draft-btn');
  const qaMsg    = document.getElementById('qa-msg');

  btn.disabled = true;
  btn.textContent = 'Publishing…';
  logSec.style.display = 'block';
  resSec.style.display = 'none';
  logEl.textContent = '';
  qaMsg.textContent = '';
  qaMsg.className = '';

  const qs = new URLSearchParams({ gdoc, site });
  const es = new EventSource('/stream?' + qs.toString());

  es.addEventListener('log', ev => {
    logEl.textContent += ev.data + '\\n';
    logEl.scrollTop = logEl.scrollHeight;
  });

  es.addEventListener('done', ev => {
    es.close();
    btn.disabled = false;
    btn.textContent = 'Publish Draft';
    try {
      const d = JSON.parse(ev.data);
      if (d.edit_url) {
        draftBtn.href = d.edit_url;
        resSec.style.display = 'block';
      }
      if (d.qa_ok === false) {
        qaMsg.textContent = '⚠️ Some QA checks failed — review the draft before publishing.';
        qaMsg.className = 'qa-warn';
      } else if (d.qa_ok === true) {
        qaMsg.textContent = '✅ All QA checks passed.';
        qaMsg.className = 'qa-ok';
      }
    } catch(_) {}
  });

  es.addEventListener('error', () => {
    es.close();
    btn.disabled = false;
    btn.textContent = 'Publish Draft';
    logEl.textContent += '\\n[connection lost — check log above for errors]';
  });
}
</script>
</body>
</html>"""


# ── Helpers ───────────────────────────────────────────────────────────────

def _authed() -> bool:
    return bool(session.get("ok"))


def _sse(event: str, data: str) -> str:
    safe = data.replace("\n", " ")
    return f"event: {event}\ndata: {safe}\n\n"


def _extract_result(output: str) -> dict:
    """Pull the final JSON result block from pipeline stdout."""
    try:
        start = output.rfind("\n{")
        if start == -1:
            start = output.find("{")
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


# ── Routes ────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == PASSWORD and PASSWORD:
            session["ok"] = True
            return redirect(url_for("home"))
        return render_template_string(_LOGIN, error=True)
    if _authed():
        return redirect(url_for("home"))
    return render_template_string(_LOGIN, error=False)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def home():
    if not PASSWORD:
        return "Set DASHBOARD_PASSWORD and SECRET_KEY in the environment.", 503
    if not _authed():
        return redirect(url_for("login"))
    return render_template_string(_MAIN)


@app.route("/stream")
def stream():
    if not _authed():
        return Response(_sse("done", "{}"), mimetype="text/event-stream")

    gdoc = (request.args.get("gdoc") or "").strip()
    site = (request.args.get("site") or "cd").strip()
    if not gdoc:
        return Response(
            _sse("log", "[error] No Google Doc URL provided.") + _sse("done", "{}"),
            mimetype="text/event-stream",
        )

    root = Path(__file__).resolve().parent
    env = {**os.environ, "CD_RELAX_SOCIAL_WP_PIXEL_ASSERT": "1"}
    cmd = [sys.executable, str(root / "pipeline.py"), gdoc, site]

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
