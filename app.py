"""
Minimal Scoutmonkeys dashboard: session login + trigger helper for `pipeline.run`.
"""
import os
import secrets
import subprocess
import sys
from pathlib import Path

from flask import Flask, redirect, render_template_string, request, session, url_for

app = Flask(__name__)
_secret_key = os.environ.get("SECRET_KEY", "")
if not _secret_key:
    # Generate a random key so sessions work, but warn — sessions will be
    # invalidated on every restart until SECRET_KEY is set in the environment.
    _secret_key = secrets.token_hex(32)
    print(
        "[WARN] SECRET_KEY is not set — using a random ephemeral key. "
        "Set SECRET_KEY in the environment to persist sessions across restarts."
    )
app.secret_key = _secret_key
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")


PAGE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>Scoutmonkeys</title>
    <style>
      body { font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }
      label { display: block; margin-top: 1rem; font-weight: 600; }
      input[type=text], input[type=password] { width: 100%; padding: 0.5rem; margin-top: 0.25rem; }
      button { margin-top: 1.25rem; padding: 0.5rem 1rem; }
      .muted { color: #555; font-size: 0.9rem; margin-top: 2rem; }
      pre { background: #f6f8fa; padding: 1rem; overflow: auto; }
    </style>
  </head>
  <body>
    {% if not authed %}
      <h1>Scoutmonkeys</h1>
      <form method="post" action="{{ url_for('login') }}">
        <label>Password
          <input type="password" name="password" required />
        </label>
        <button type="submit">Sign in</button>
      </form>
    {% else %}
      <h1>Publish</h1>
      <p>Runs <code>python pipeline.py …</code> in a subprocess (same working directory).</p>
      <form method="post" action="{{ url_for('publish') }}">
        <label>Google Doc URL
          <input type="text" name="gdoc" placeholder="https://docs.google.com/document/d/…" required />
        </label>
        <label>Site
          <input type="text" name="site" value="cd" />
        </label>
        <button type="submit">Create draft</button>
      </form>
      {% if output %}
        <h2>Output</h2>
        <pre>{{ output }}</pre>
      {% endif %}
      <p class="muted"><a href="{{ url_for('logout') }}">Sign out</a></p>
    {% endif %}
  </body>
</html>
"""


def _authed() -> bool:
    return bool(session.get("ok"))


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == PASSWORD and PASSWORD:
            session["ok"] = True
            return redirect(url_for("home"))
        return (
            render_template_string(
                PAGE,
                authed=False,
                url_for=url_for,
            )
            + "<p style='color:#b00'>Invalid password.</p>"
        )
    if _authed():
        return redirect(url_for("home"))
    return render_template_string(PAGE, authed=False, url_for=url_for)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def home():
    if not PASSWORD:
        return (
            "Set DASHBOARD_PASSWORD and SECRET_KEY in the environment.",
            503,
        )
    if not _authed():
        return redirect(url_for("login"))
    return render_template_string(PAGE, authed=True, output=None, url_for=url_for)


@app.route("/publish", methods=["POST"])
def publish():
    if not _authed():
        return redirect(url_for("login"))
    gdoc = (request.form.get("gdoc") or "").strip()
    site = (request.form.get("site") or "cd").strip()
    root = Path(__file__).resolve().parent
    cmd = [sys.executable, str(root / "pipeline.py"), gdoc, site]
    proc = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=900,
        env={**os.environ},
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        output = f"(exit {proc.returncode})\n" + output
    return render_template_string(PAGE, authed=True, output=output, url_for=url_for)
