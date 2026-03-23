#!/usr/bin/env python3
"""
OTP Sender Service v2.0
========================
1. Open in PyCharm
2. Press Shift+F10
3. Open browser: http://localhost:9090

What's new in v2.0:
  - Auto token refresh when expired
  - Pause & Resume (never loses position)
  - Cost tracker in BDT (20 Poisha/OTP)
  - Auto repeat with countdown timer
  - Obfuscated API endpoints
"""

import http.server
import json
import urllib.request
import urllib.error
import urllib.parse
import base64
import zlib
import threading
import time
import os
import hashlib
import hmac

# ── Config loaded from environment variables (never hardcoded) ──────
APP_ID         = os.environ.get("APP_ID",       "")
API_TOKEN      = os.environ.get("API_TOKEN",    "")
PORT           = int(os.environ.get("PORT",     7777))
LOGIN_PASSWORD = os.environ.get("APP_PASSWORD", "changeme")

# ── Session store (in-memory) ───────────────────────────────────────
import secrets as _secrets
_sessions     = {}   # sid -> expiry timestamp
SESSION_TTL   = 8 * 3600  # 8 hours

def _new_session():
    tok = _secrets.token_hex(32)
    _sessions[tok] = time.time() + SESSION_TTL
    return tok

def _valid_session(cookie_header):
    if not cookie_header: return False
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("sid="):
            sid = part[4:]
            expiry = _sessions.get(sid)
            if expiry and time.time() < expiry:
                _sessions[sid] = time.time() + SESSION_TTL  # refresh
                return True
            elif expiry:
                del _sessions[sid]  # expired — remove
    return False

def _purge_sessions():
    """Remove expired sessions periodically."""
    now = time.time()
    expired = [k for k, v in _sessions.items() if v < now]
    for k in expired:
        del _sessions[k]

# ── Brute force protection ──────────────────────────────────────────
_login_attempts = {}   # ip -> [timestamp, count]
MAX_ATTEMPTS    = 5    # max failed attempts
LOCKOUT_TIME    = 300  # 5 minute lockout

def _check_rate_limit(ip):
    """Returns (allowed, seconds_remaining)"""
    now = time.time()
    if ip not in _login_attempts:
        return True, 0
    attempts = _login_attempts[ip]
    # Reset if lockout period passed
    if now - attempts["last"] > LOCKOUT_TIME:
        del _login_attempts[ip]
        return True, 0
    if attempts["count"] >= MAX_ATTEMPTS:
        remaining = int(LOCKOUT_TIME - (now - attempts["last"]))
        return False, remaining
    return True, 0

def _record_failed_attempt(ip):
    now = time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = {"count": 1, "last": now}
    else:
        _login_attempts[ip]["count"] += 1
        _login_attempts[ip]["last"] = now

def _clear_attempts(ip):
    if ip in _login_attempts:
        del _login_attempts[ip]

def _safe_compare(a, b):
    """Constant-time string comparison to prevent timing attacks."""
    return hmac.compare_digest(a.encode(), b.encode())
# ───────────────────────────────────────────────────────────────────

def _d(b): return zlib.decompress(base64.b64decode(b)).decode()

_EP  = _d("eJzLKCkpKLbS109MTs4vzSvJzizRK85ITUrUq6is0k8syNQvzsjPL9HNLykAAFdpEBA=")
_TEP = _d("eJzLKCkpKLbS108syNRNTE7OL80rKdYrzkhNStSrqKwCCeuXGepDZbIzS/TTU/NSixJLUvVL8rNT8wDu7BfK")
_ORG = _d("eJzLKCkpKLbS1y8vL9crzkhNStSrqKwCAFcSB/s=")
_PNM = _d("eJxzLi0uyc9NLVIIT00CAB6FBJE=")
_UA  = _d("eJzzza/KzMlJ1DfVM1DQ8MnMK62wVnDMSynKz0xRMDTWVHAsKMhJDU9N8s4s0Tc1NtczNlNwzijKz03VNzQx0jMAQQXf/KTMnFSF4MS0xKJMqCoAuL4aKg==")

# ── Token refresh (server-side) ─────────────────────────────────────
_current_token = [API_TOKEN]
_token_refreshed_at = [time.time()]   # track when token was last fetched
TOKEN_LIFETIME = 55 * 60              # refresh every 55 min (expires at 60)

def refresh_token(app_id):
    url = _TEP + "?app_id=" + app_id
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
            token = (data.get("token") or data.get("api_token") or
                     data.get("access_token") or data.get("data", {}).get("token") or
                     data.get("data", {}).get("api_token") or "")
            if token:
                _current_token[0] = token
                _token_refreshed_at[0] = time.time()
                print(f"  [SYS] Session synced at {time.strftime('%H:%M:%S')} — next sync in 55 min")
                return {"success": True, "token": token, "raw": data}
            else:
                print(f"  [SYS] Sync failed - response: {data}")
                return {"success": False, "error": "Token not found in response", "raw": data}
    except Exception as e:
        print(f"  [SYS] Sync error: {e}")
        return {"success": False, "error": str(e)}

def _token_refresh_loop():
    """Background thread: refresh token every 55 minutes proactively."""
    while True:
        time.sleep(TOKEN_LIFETIME)
        print(f"  [SYS] 55 min reached — auto syncing...")
        refresh_token(APP_ID)

# Start background token refresh thread
_refresh_thread = threading.Thread(target=_token_refresh_loop, daemon=True)
_refresh_thread.start()
print(f"  [SYS] Auto-sync scheduled every 55 min")


def build_login(error=False, msg="Incorrect password. Try again."):
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>OTP Sender — Login</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#060810;color:#e2e4f0;font-family:'Inter',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;
background-image:radial-gradient(ellipse 60% 50% at 50% 0%,rgba(99,102,241,.1),transparent);}
.box{background:#0d0f18;border:1px solid #1a1d2e;border-radius:20px;padding:40px 36px;width:100%;max-width:380px;box-shadow:0 24px 60px rgba(0,0,0,.5);}
.logo{width:48px;height:48px;background:linear-gradient(135deg,#6366f1,#4f46e5);border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:22px;margin:0 auto 20px;box-shadow:0 6px 20px rgba(99,102,241,.4);}
h1{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;text-align:center;margin-bottom:6px;}
.sub{font-size:12px;color:#4b5070;text-align:center;margin-bottom:28px;}
label{display:block;font-size:12px;color:#6b7290;margin-bottom:6px;font-weight:500;}
input{width:100%;background:#111420;border:1px solid #1a1d2e;border-radius:10px;color:#e2e4f0;font-family:'Inter',sans-serif;font-size:15px;padding:12px 14px;outline:none;transition:border-color .2s;margin-bottom:16px;}
input:focus{border-color:#6366f1;box-shadow:0 0 0 3px rgba(99,102,241,.12);}
button{width:100%;background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff;border:none;border-radius:10px;padding:13px;font-family:'Inter',sans-serif;font-size:15px;font-weight:600;cursor:pointer;transition:all .18s;box-shadow:0 4px 14px rgba(99,102,241,.3);}
button:hover{filter:brightness(1.1);transform:translateY(-1px);}
.err{background:rgba(244,63,94,.08);border:1px solid rgba(244,63,94,.2);border-radius:8px;padding:10px 14px;font-size:13px;color:#f43f5e;margin-bottom:16px;text-align:center;""" + ("display:block" if error else "display:none") + """;}
</style>
</head>
<body>
<div class="box">
  <div class="logo">&#9993;</div>
  <h1>OTP Sender v2.0</h1>
  <p class="sub">Enter your access credentials to continue</p>
  <div class="err">""" + """ + msg + """ + """</div>
  <form method="POST" action="/login">
    <label>Password</label>
    <input type="password" name="password" placeholder="Enter password" autofocus>
    <button type="submit">Access Dashboard &#8594;</button>
  </form>
</div>
</body>
</html>"""


def build_html():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>OTP Sender v2.0</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#060810;--sf:#0d0f18;--sf2:#111420;--bd:#1a1d2e;--bd2:#222640;
  --ac:#6366f1;--ac2:#818cf8;--ac3:#4f46e5;
  --tx:#e2e4f0;--mu:#4b5070;--mu2:#6b7290;
  --ok:#10b981;--er:#f43f5e;--wn:#f59e0b;--cy:#06b6d4;--pu:#a78bfa;
  --go:#10b981;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:var(--bg);color:var(--tx);
  font-family:'Inter',sans-serif;
  min-height:100vh;
  padding:0 0 80px;
  background-image:
    radial-gradient(ellipse 60% 50% at 20% 0%,rgba(99,102,241,.07),transparent),
    radial-gradient(ellipse 40% 40% at 80% 100%,rgba(167,139,250,.05),transparent);
}

/* ── TOP BAR ── */
.topbar{
  display:flex;align-items:center;gap:14px;
  padding:16px 28px;
  border-bottom:1px solid var(--bd);
  background:rgba(13,15,24,.8);
  backdrop-filter:blur(12px);
  position:sticky;top:0;z-index:100;
}
.logo{
  width:36px;height:36px;
  background:linear-gradient(135deg,var(--ac),var(--ac3));
  border-radius:10px;
  display:flex;align-items:center;justify-content:center;
  font-family:'JetBrains Mono',monospace;font-weight:700;font-size:15px;color:#fff;
  flex-shrink:0;box-shadow:0 4px 14px rgba(99,102,241,.4);
}
.brand{font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:700;letter-spacing:-.3px}
.brand span{color:var(--ac2);font-size:11px;margin-left:6px;font-weight:400}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.status-pill{
  display:flex;align-items:center;gap:5px;
  background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);
  border-radius:999px;padding:4px 11px;font-size:11px;color:var(--ok);
  font-family:'JetBrains Mono',monospace;
}
.sdot2{width:6px;height:6px;border-radius:50%;background:var(--ok);box-shadow:0 0 4px var(--ok);animation:bk 1.4s infinite}
.tok-pill{
  display:flex;align-items:center;gap:5px;
  background:rgba(6,182,212,.07);border:1px solid rgba(6,182,212,.18);
  border-radius:999px;padding:4px 11px;font-size:11px;color:var(--cy);
  font-family:'JetBrains Mono',monospace;cursor:pointer;
  transition:background .2s;
}
.tok-pill:hover{background:rgba(6,182,212,.14)}
@keyframes bk{0%,100%{opacity:1}50%{opacity:.2}}

/* ── MAIN LAYOUT ── */
.main{max-width:940px;margin:0 auto;padding:28px 20px;display:grid;grid-template-columns:1fr 340px;gap:20px}
@media(max-width:760px){.main{grid-template-columns:1fr}}

/* ── PANELS ── */
.panel{background:var(--sf);border:1px solid var(--bd);border-radius:16px;padding:22px;margin-bottom:18px}
.panel-title{
  font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;
  color:var(--mu2);letter-spacing:1.8px;text-transform:uppercase;
  margin-bottom:16px;display:flex;align-items:center;gap:8px;
}
.panel-title .ti{width:3px;height:14px;border-radius:999px;background:var(--ac);flex-shrink:0}

/* ── NUMBERS PANEL ── */
.nums-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.num-count{
  background:rgba(99,102,241,.12);color:var(--ac2);
  border:1px solid rgba(99,102,241,.25);
  padding:3px 10px;border-radius:999px;
  font-size:11px;font-weight:600;font-family:'JetBrains Mono',monospace;
}
textarea{
  width:100%;background:var(--sf2);border:1px solid var(--bd);border-radius:10px;
  color:var(--tx);font-family:'JetBrains Mono',monospace;font-size:13px;
  padding:12px;outline:none;resize:vertical;min-height:160px;line-height:1.8;
  transition:border-color .2s;
}
textarea:focus{border-color:var(--ac);box-shadow:0 0 0 3px rgba(99,102,241,.1)}
textarea::placeholder{color:var(--mu)}

/* delay row */
.delay-row{display:flex;align-items:center;gap:10px;margin-top:12px;padding-top:12px;border-top:1px solid var(--bd)}
.delay-row label{font-size:12px;color:var(--mu2);white-space:nowrap}
.delay-row input{
  width:80px;background:var(--sf2);border:1px solid var(--bd);border-radius:8px;
  color:var(--tx);font-family:'JetBrains Mono',monospace;font-size:13px;
  padding:7px 10px;outline:none;-webkit-appearance:none;
  transition:border-color .2s;
}
.delay-row input:focus{border-color:var(--ac)}
.delay-row span{font-size:11px;color:var(--mu)}

/* position bar */
.pos-bar{background:var(--sf2);border:1px solid var(--bd);border-radius:10px;padding:10px 13px;margin-top:12px;display:none}
.pos-top{display:flex;justify-content:space-between;margin-bottom:6px}
.pos-lbl{font-size:11px;color:var(--mu2)}
.pos-val{font-size:11px;color:var(--cy);font-family:'JetBrains Mono',monospace;font-weight:600}
.pos-track{background:var(--bd2);border-radius:999px;height:3px;overflow:hidden}
.pos-fill{height:100%;background:linear-gradient(90deg,var(--ac),var(--ac2));border-radius:999px;transition:width .3s;width:0}
.pos-hint{font-size:11px;color:var(--cy);margin-top:5px}

/* buttons */
.btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:10px 18px;border-radius:9px;border:none;cursor:pointer;font-family:'Inter',sans-serif;font-size:13px;font-weight:600;transition:all .15s;min-height:40px;white-space:nowrap}
.b-send{background:linear-gradient(135deg,var(--ac),var(--ac3));color:#fff;flex:1;box-shadow:0 4px 14px rgba(99,102,241,.3)}
.b-send:hover:not(:disabled){filter:brightness(1.1);transform:translateY(-1px);box-shadow:0 6px 18px rgba(99,102,241,.4)}
.b-send:disabled{opacity:.35;cursor:not-allowed;transform:none}
.b-pause{background:rgba(245,158,11,.12);color:var(--wn);border:1px solid rgba(245,158,11,.25)}
.b-pause:hover{background:rgba(245,158,11,.2)}
.b-resume{background:rgba(6,182,212,.12);color:var(--cy);border:1px solid rgba(6,182,212,.25)}
.b-resume:hover{background:rgba(6,182,212,.2)}
.b-stop{background:rgba(244,63,94,.1);color:var(--er);border:1px solid rgba(244,63,94,.2)}
.b-stop:hover{background:rgba(244,63,94,.18)}
.b-ghost{background:transparent;color:var(--mu2);border:1px solid var(--bd)}
.b-ghost:hover{border-color:var(--ac2);color:var(--ac2)}
.spinner{width:13px;height:13px;border:2px solid rgba(255,255,255,.2);border-top-color:#fff;border-radius:50%;animation:sp .6s linear infinite;display:none}
@keyframes sp{to{transform:rotate(360deg)}}

/* ── RIGHT COLUMN ── */
.right{}

/* cost panel */
.cost-panel{background:var(--sf);border:1px solid var(--bd);border-radius:16px;padding:22px;margin-bottom:18px}
.cost-panel .panel-title .ti{background:var(--go)}
.cost-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:14px}
.cstat{background:var(--sf2);border:1px solid var(--bd);border-radius:10px;padding:13px 10px;text-align:center}
.cnum{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700}
.clbl{font-size:10px;color:var(--mu2);margin-top:3px;text-transform:uppercase;letter-spacing:.5px}
.cost-total{
  background:linear-gradient(135deg,rgba(16,185,129,.08),rgba(16,185,129,.03));
  border:1px solid rgba(16,185,129,.2);border-radius:12px;
  padding:16px;text-align:center;
}
.cost-bdt{font-family:'JetBrains Mono',monospace;font-size:34px;font-weight:700;color:var(--go);letter-spacing:-1px}
.cost-lbl{font-size:11px;color:var(--mu2);margin-top:3px}
.cost-formula{font-size:11px;color:var(--mu2);margin-top:4px;font-family:'JetBrains Mono',monospace}

/* auto panel */
.auto-panel{background:var(--sf);border:1px solid var(--bd);border-radius:16px;padding:22px;margin-bottom:18px}
.auto-panel .panel-title .ti{background:var(--pu)}
.auto-row{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.auto-row label{font-size:12px;color:var(--mu2);white-space:nowrap;min-width:90px}
.auto-row input{
  width:75px;background:var(--sf2);border:1px solid var(--bd);border-radius:8px;
  color:var(--tx);font-family:'JetBrains Mono',monospace;font-size:13px;
  padding:7px 10px;outline:none;-webkit-appearance:none;
}
.auto-row input:focus{border-color:var(--pu)}
.timer-box{background:var(--sf2);border:1px solid var(--bd);border-radius:12px;padding:16px;text-align:center;margin:12px 0}
.countdown{font-size:40px;font-weight:700;font-family:'JetBrains Mono',monospace;color:var(--pu);letter-spacing:2px;line-height:1}
.countdown.tk{animation:pp 1s ease-in-out infinite}
@keyframes pp{0%,100%{opacity:1}50%{opacity:.4}}
.cd-lbl{font-size:10px;color:var(--mu2);margin-top:5px;text-transform:uppercase;letter-spacing:1px}
.round-pill{
  background:rgba(167,139,250,.08);border:1px solid rgba(167,139,250,.2);
  color:var(--pu);padding:2px 12px;border-radius:999px;
  font-size:11px;font-weight:600;font-family:'JetBrains Mono',monospace;
  display:inline-block;margin-top:7px;
}
.next-txt{font-size:11px;color:var(--mu2);margin-top:5px}
.next-txt span{color:var(--pu);font-weight:600}
.auto-status{font-size:11px;color:var(--mu2);margin-top:10px;display:flex;align-items:center;gap:5px}
.sd{width:7px;height:7px;border-radius:50%;background:var(--mu);flex-shrink:0}
.sd.on{background:var(--ok);box-shadow:0 0 5px var(--ok);animation:bk 1.2s infinite}
.b-auto-start{background:linear-gradient(135deg,rgba(167,139,250,.2),rgba(167,139,250,.1));color:var(--pu);border:1px solid rgba(167,139,250,.3);flex:1}
.b-auto-start:hover{background:rgba(167,139,250,.25)}
.b-auto-stop{background:rgba(244,63,94,.1);color:var(--er);border:1px solid rgba(244,63,94,.2);flex:1}
.b-auto-stop:hover{background:rgba(244,63,94,.18)}

/* results panel */
.res-panel{background:var(--sf);border:1px solid var(--bd);border-radius:16px;padding:22px;display:none}
.res-panel .panel-title .ti{background:var(--wn)}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}
.stat{background:var(--sf2);border:1px solid var(--bd);border-radius:10px;padding:12px 6px;text-align:center}
.sn{font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:700}
.bl{color:#60a5fa}.gn{color:var(--ok)}.rd{color:var(--er)}
.sl{font-size:10px;color:var(--mu2);margin-top:2px;text-transform:uppercase;letter-spacing:.4px}
.pbw{background:var(--bd2);border-radius:999px;height:3px;overflow:hidden;margin:8px 0 3px}
.pb{height:100%;background:linear-gradient(90deg,var(--ac),var(--ac2));border-radius:999px;transition:width .3s;width:0}
.pt{font-size:11px;color:var(--mu2);font-family:'JetBrains Mono',monospace}
.lw{
  max-height:220px;overflow-y:auto;
  background:var(--sf2);border-radius:9px;border:1px solid var(--bd);
  padding:9px 11px;margin-top:11px;
  font-family:'JetBrains Mono',monospace;font-size:11px;line-height:1.9;
}
.lw::-webkit-scrollbar{width:3px}
.lw::-webkit-scrollbar-thumb{background:var(--bd2);border-radius:999px}
.le{display:flex;gap:8px;border-bottom:1px solid rgba(255,255,255,.02);word-break:break-all}
.lts{color:var(--mu);flex-shrink:0}
.ln{color:var(--pu);flex-shrink:0}
.lm{flex:1}
.ok{color:var(--ok)}.er{color:var(--er)}.wn{color:var(--wn)}.pn{color:var(--mu)}.sc{color:var(--ac2)}.cy{color:var(--cy)}
</style>
</head>
<body>

<!-- TOP BAR -->
<div class="topbar">
  <div class="logo">&#9993;</div>
  <div class="brand">OTP Sender <span>v2.0</span></div>
  <div class="topbar-right">
    <div class="tok-pill" onclick="manualRefresh()" title="Session sync">
      &#8635; Session: <span id="tokTimer">55:00</span>
    </div>
    <div class="status-pill"><div class="sdot2"></div>Online</div>
  </div>
</div>

<!-- MAIN -->
<div class="main">

  <!-- LEFT -->
  <div class="left">

    <!-- Numbers -->
    <div class="panel">
      <div class="panel-title"><div class="ti"></div>Phone Numbers <span class="num-count" id="nBadge">0</span></div>
      <textarea id="nums" placeholder="+8801554313118&#10;+8801712345678&#10;+8801987654321" oninput="upC()"></textarea>

      <div class="pos-bar" id="posBar">
        <div class="pos-top">
          <span class="pos-lbl">Progress</span>
          <span class="pos-val" id="posNum">0 / 0</span>
        </div>
        <div class="pos-track"><div class="pos-fill" id="posFill"></div></div>
        <div class="pos-hint" id="posResume"></div>
      </div>

      <div class="delay-row">
        <label>Delay</label>
        <input id="dly" type="number" value="800" min="0" max="30000">
        <span>ms between each number</span>
      </div>

      <div class="btn-row">
        <button class="btn b-send" id="sBtn" onclick="sendNow()">
          <div class="spinner" id="sp"></div>
          <span id="sBt">&#9654; Send Now</span>
        </button>
        <button class="btn b-pause" id="pauseBtn" onclick="doPause()" style="display:none">&#9646;&#9646; Pause</button>
        <button class="btn b-resume" id="resumeBtn" onclick="doResume()" style="display:none">&#9654; Resume #<span id="resumeIdx">0</span></button>
        <button class="btn b-stop" id="stopBtn" onclick="doStop()" style="display:none">&#9646; Stop</button>
        <button class="btn b-ghost" onclick="doClear()">Clear</button>
      </div>
    </div>

    <!-- Results -->
    <div class="res-panel panel" id="resCard">
      <div class="panel-title"><div class="ti" style="background:var(--wn)"></div>Live Results</div>
      <div class="stats">
        <div class="stat"><div class="sn bl" id="sTot">0</div><div class="sl">Total</div></div>
        <div class="stat"><div class="sn gn" id="sOk">0</div><div class="sl">Success</div></div>
        <div class="stat"><div class="sn rd" id="sFl">0</div><div class="sl">Failed</div></div>
      </div>
      <div class="pbw"><div class="pb" id="pBar"></div></div>
      <div class="pt" id="pTxt">0 / 0</div>
      <div class="lw" id="lWrap"></div>
    </div>

  </div>

  <!-- RIGHT -->
  <div class="right">

    <!-- Cost Tracker -->
    <div class="cost-panel">
      <div class="panel-title"><div class="ti"></div>Cost Tracker &mdash; 20 Poisha / OTP</div>
      <div class="cost-grid">
        <div class="cstat"><div class="cnum" style="color:#60a5fa" id="ctT">0</div><div class="clbl">Total</div></div>
        <div class="cstat"><div class="cnum" style="color:var(--ok)" id="ctO">0</div><div class="clbl">Success</div></div>
        <div class="cstat"><div class="cnum" style="color:var(--er)" id="ctF">0</div><div class="clbl">Failed</div></div>
        <div class="cstat"><div class="cnum" style="color:var(--wn)" id="ctR">0</div><div class="clbl">Rounds</div></div>
      </div>
      <div class="cost-total">
        <div class="cost-bdt" id="tcA">&#2547;0.00</div>
        <div class="cost-lbl">Total Cost in BDT</div>
        <div class="cost-formula" id="tcB">0 SMS &times; &#2547;0.20 = &#2547;0.00</div>
      </div>
      <div style="margin-top:10px;text-align:right">
        <button class="btn b-ghost" style="font-size:11px;padding:5px 12px;min-height:0" onclick="resetCost()">&#128257; Reset</button>
      </div>
    </div>

    <!-- Auto Repeat -->
    <div class="auto-panel">
      <div class="panel-title"><div class="ti"></div>Auto Repeat</div>
      <div class="auto-row">
        <label>Every</label>
        <input id="iMin" type="number" value="4" min="1" max="9999">
        <span style="font-size:12px;color:var(--mu2)">minutes</span>
      </div>
      <div class="timer-box" id="tBox" style="display:none">
        <div class="countdown" id="cd">04:00</div>
        <div class="cd-lbl">next batch in</div>
        <div class="round-pill" id="rPill">Round 0</div>
        <div class="next-txt" id="nTxt"></div>
      </div>
      <div class="btn-row">
        <button class="btn b-auto-start" id="aStBtn" onclick="startAuto()">&#9654; Start Auto</button>
        <button class="btn b-auto-stop" id="aSpBtn" onclick="stopAuto()" style="display:none">&#9646; Stop</button>
      </div>
      <div class="auto-status"><div class="sd" id="sdot"></div><span id="aSt">Auto mode OFF</span></div>
    </div>

  </div>
</div>

<script>
var _running=false,_paused=false,_stopped=false;
var _ok=0,_fl=0,_tot=0;
var _am=false,_ar=0,_ct=null,_at=null;
var _cOk=0,_cFl=0,_cRnd=0;
var _pauseAt=0,_resumeFrom=0;
var POISHA=20;
var _tokenRefreshing=false;

function $(i){return document.getElementById(i)}
function nums(){return[...new Set($('nums').value.split('\\n').map(function(l){return l.trim()}).filter(Boolean))]}
function upC(){$('nBadge').textContent=nums().length}
function ts(){return new Date().toLocaleTimeString('en-GB',{hour12:false})}
function sleep(ms){return new Promise(function(r){setTimeout(r,ms)})}

function refreshToken(){
  if(_tokenRefreshing)return Promise.resolve(false);
  _tokenRefreshing=true;
  return fetch('/refresh-token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})})
  .then(function(r){return r.json()}).then(function(res){
    _tokenRefreshing=false;
    if(res.success){addLog('SYS','cy','Session synced');return true;}
    else{addLog('SYS','er','Sync failed: '+(res.error||'?'));return false;}
  }).catch(function(e){_tokenRefreshing=false;return false;});
}
function manualRefresh(){refreshToken();}
function isTokenError(res){
  var m=(res.message||res.error||'').toLowerCase();
  return m.indexOf('invalid')!==-1||m.indexOf('unauthorized')!==-1||m.indexOf('expired')!==-1||res.code===401||res.status===401;
}

function updCost(){
  var bdt=(_cOk*POISHA/100).toFixed(2);
  $('ctT').textContent=_cOk+_cFl;$('ctO').textContent=_cOk;$('ctF').textContent=_cFl;$('ctR').textContent=_cRnd;
  $('tcA').innerHTML='&#2547;'+bdt;
  $('tcB').textContent=_cOk+' SMS x BDT 0.20 = BDT '+bdt;
}
function resetCost(){_cOk=0;_cFl=0;_cRnd=0;updCost();}

function addLog(num,cls,msg){
  var w=$('lWrap'),d=document.createElement('div');d.className='le';
  d.innerHTML='<span class="lts">'+ts()+'</span><span class="ln"> '+num+' </span><span class="lm '+cls+'">'+msg+'</span>';
  w.appendChild(d);w.scrollTop=w.scrollHeight;return d;
}
function addSep(lbl){
  var w=$('lWrap'),d=document.createElement('div');d.className='le';
  d.innerHTML='<span class="lm sc" style="width:100%;text-align:center;padding:2px 0">-- '+lbl+' --</span>';
  w.appendChild(d);w.scrollTop=w.scrollHeight;
}
function updStats(){
  $('sTot').textContent=_tot;$('sOk').textContent=_ok;$('sFl').textContent=_fl;
  var done=_ok+_fl,pct=_tot>0?Math.round(done/_tot*100):0;
  $('pBar').style.width=pct+'%';$('pTxt').textContent=done+' / '+_tot+' ('+pct+'%)';
}
function updPos(idx,total){
  $('posBar').style.display='block';
  $('posNum').textContent=idx+' / '+total;
  $('posFill').style.width=total>0?Math.round(idx/total*100)+'%':'0%';
}
function setBtns(state){
  if(state==='idle'){
    $('sBtn').disabled=false;$('sp').style.display='none';$('sBt').textContent='Send Now';
    $('pauseBtn').style.display='none';$('resumeBtn').style.display='none';$('stopBtn').style.display='none';
  }else if(state==='running'){
    $('sBtn').disabled=true;$('sp').style.display='block';$('sBt').textContent='Sending...';
    $('pauseBtn').style.display='inline-flex';$('resumeBtn').style.display='none';$('stopBtn').style.display='inline-flex';
  }else if(state==='paused'){
    $('sBtn').disabled=true;$('sp').style.display='none';
    $('pauseBtn').style.display='none';
    $('resumeIdx').textContent=_pauseAt+1;
    $('resumeBtn').style.display='inline-flex';$('stopBtn').style.display='inline-flex';
    $('posResume').textContent='Paused at #'+(_pauseAt+1)+' - Resume to continue';
  }
}
function sendOne(mobile){
  return fetch('/proxy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mobile:mobile})}).then(function(r){return r.json()});
}
function runBatch(lbl,startFrom){
  var ns=nums();if(!ns.length)return Promise.resolve();
  startFrom=startFrom||0;
  _running=true;_paused=false;_stopped=false;_tot=ns.length;_ok=0;_fl=0;
  $('resCard').style.display='block';setBtns('running');updStats();updPos(startFrom,ns.length);
  addSep(lbl+(startFrom>0?' (from #'+(startFrom+1)+')':'')+' '+ts());
  var delay=parseInt($('dly').value)||0,i=startFrom;
  function next(){
    if(_paused){_pauseAt=i;_running=false;setBtns('paused');addLog('SYS','wn','Paused at #'+(i+1));updPos(i,ns.length);return Promise.resolve();}
    if(_stopped){_running=false;_resumeFrom=0;setBtns('idle');$('posResume').textContent='';addLog('SYS','er','Stopped.');return Promise.resolve();}
    if(i>=ns.length){_running=false;_resumeFrom=0;setBtns('idle');$('posResume').textContent='All done!';updPos(ns.length,ns.length);addLog('SYS','wn','Done: '+_ok+' ok, '+_fl+' fail | BDT '+(_ok*POISHA/100).toFixed(2));return Promise.resolve();}
    var num=ns[i],entry=addLog(num,'pn','sending...');updPos(i+1,ns.length);
    return sendOne(num).then(function(res){
      entry.remove();
      if(isTokenError(res)){
        addLog('SYS','cy','Session expired - syncing...');
        return refreshToken().then(function(ok){
          if(ok){return sendOne(num).then(function(r2){
            if(r2.success){_ok++;_cOk++;addLog(num,'ok','OK '+(r2.message||'sent'));}
            else{_fl++;_cFl++;addLog(num,'er','FAIL '+(r2.message||'?'));}
            updStats();updCost();i++;
            if(delay>0&&i<ns.length&&!_paused&&!_stopped)return sleep(delay).then(next);return next();
          });}
          _fl++;_cFl++;addLog(num,'er','FAIL - sync failed, skipping');updStats();updCost();i++;return next();
        });
      }
      if(res.success){_ok++;_cOk++;addLog(num,'ok','OK '+(res.message||res.msg||'OTP sent'));}
      else{_fl++;_cFl++;addLog(num,'er','FAIL '+(res.message||res.error||JSON.stringify(res)));}
      updStats();updCost();i++;
      if(delay>0&&i<ns.length&&!_paused&&!_stopped)return sleep(delay).then(next);return next();
    }).catch(function(e){entry.remove();_fl++;_cFl++;addLog(num,'er','ERR '+e.message);updStats();updCost();i++;return next();});
  }
  return next();
}
function sendNow(){if(_running)return;if(!nums().length){alert('Enter at least one number.');return}_cRnd++;runBatch('Manual #'+_cRnd,0);}
function doPause(){_paused=true;}
function doStop(){_stopped=true;_paused=false;}
function doResume(){if(!nums().length){alert('No numbers.');return}setBtns('running');$('posResume').textContent='';runBatch('Resumed',_pauseAt);}
function doClear(){if(_running){alert('Stop first.');return}$('nums').value='';$('lWrap').innerHTML='';$('resCard').style.display='none';$('posBar').style.display='none';_resumeFrom=0;_pauseAt=0;upC();}

function fmt(s){return String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')}
function startCd(sec){
  clearInterval(_ct);var r=sec;
  var el=$('cd');el.classList.add('tk');el.textContent=fmt(r);
  function u(){var t=new Date(Date.now()+r*1000);$('nTxt').innerHTML='Next at <span>'+t.toLocaleTimeString('en-GB',{hour12:false})+'</span>';}
  u();_ct=setInterval(function(){r--;el.textContent=fmt(Math.max(0,r));u();if(r<=0)clearInterval(_ct);},1000);
}
function startAuto(){
  if(!nums().length){alert('Enter numbers first.');return}
  var mins=parseInt($('iMin').value)||4,ms=mins*60*1000;
  _am=true;_ar=0;
  $('aStBtn').style.display='none';$('aSpBtn').style.display='inline-flex';
  $('tBox').style.display='block';$('sdot').className='sd on';
  $('aSt').textContent='Auto ON - every '+mins+' min';
  function doR(){if(!_am)return;_ar++;_cRnd++;$('rPill').textContent='Round '+_ar;runBatch('Round '+_ar,0).then(function(){if(_am)startCd(mins*60);});}
  doR();_at=setInterval(doR,ms);
}
function stopAuto(){
  _am=false;clearInterval(_at);clearInterval(_ct);_stopped=true;
  $('aStBtn').style.display='inline-flex';$('aSpBtn').style.display='none';
  $('tBox').style.display='none';$('sdot').className='sd';
  $('aSt').textContent='Auto OFF - '+_ar+' round(s) done';
  $('cd').classList.remove('tk');
}
upC();
var _tokSec=55*60;
function startTokTimer(){
  fetch('/token-status').then(function(r){return r.json()}).then(function(d){_tokSec=d.next_refresh_in||(55*60);$('tokTimer').textContent=fmt(_tokSec);}).catch(function(){});
  setInterval(function(){
    _tokSec--;if(_tokSec<=0){_tokSec=55*60;}
    $('tokTimer').textContent=fmt(Math.max(0,_tokSec));
    $('tokTimer').style.color=_tokSec<=120?'var(--wn)':'var(--cy)';
  },1000);
}
startTokTimer();
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print("  %s - %s" % (self.address_string(), fmt % args))

    def _ip(self):
        # Support Railway proxy headers
        return (self.headers.get("X-Forwarded-For", "") or
                self.headers.get("X-Real-IP", "") or
                self.address_string()).split(",")[0].strip()

    def _is_auth(self):
        return _valid_session(self.headers.get("Cookie", ""))

    def _redirect_login(self):
        self.send_response(302)
        self.send_header("Location", "/login")
        self.end_headers()

    def _add_security_headers(self):
        self.send_header("X-Content-Type-Options",  "nosniff")
        self.send_header("X-Frame-Options",          "DENY")
        self.send_header("X-XSS-Protection",         "1; mode=block")
        self.send_header("Referrer-Policy",           "no-referrer")
        self.send_header("Cache-Control",             "no-store, no-cache, must-revalidate")
        self.send_header("Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "connect-src 'self'; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'")

    def _send_html(self, html, status=200):
        page = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self._add_security_headers()
        self.end_headers()
        self.wfile.write(page)

    def do_GET(self):
        if self.path == "/login":
            self._send_html(build_login())
            return
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if not self._is_auth():
            self._redirect_login()
            return
        if self.path == "/token-status":
            elapsed   = int(time.time() - _token_refreshed_at[0])
            remaining = max(0, TOKEN_LIFETIME - elapsed)
            out = json.dumps({"next_refresh_in": remaining}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self._add_security_headers()
            self.end_headers()
            self.wfile.write(out)
            return
        # Purge expired sessions occasionally
        _purge_sessions()
        self._send_html(build_html())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if self.path == "/login":
            self._handle_login()
            return
        if not self._is_auth():
            self._send_json({"success": False, "error": "Unauthorized"})
            return
        if self.path == "/proxy":
            self._handle_proxy()
        elif self.path == "/refresh-token":
            self._handle_refresh()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_login(self):
        ip = self._ip()
        allowed, wait = _check_rate_limit(ip)
        if not allowed:
            self._send_html(build_login(
                error=True,
                msg="Too many attempts. Try again in %d seconds." % wait
            ))
            return

        length = int(self.headers.get("Content-Length", 0))
        # Reject oversized bodies
        if length > 512:
            self._send_html(build_login(error=True, msg="Invalid request."))
            return

        body   = self.rfile.read(length).decode("utf-8", errors="ignore")
        params = {}
        for part in body.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = urllib.parse.unquote_plus(v)

        pwd = params.get("password", "")

        if _safe_compare(pwd, LOGIN_PASSWORD):
            _clear_attempts(ip)
            sid = _new_session()
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header(
                "Set-Cookie",
                "sid=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (sid, SESSION_TTL)
            )
            self.end_headers()
        else:
            _record_failed_attempt(ip)
            _, remaining_attempts = _check_rate_limit(ip)
            left = MAX_ATTEMPTS - _login_attempts.get(ip, {}).get("count", 0)
            msg = "Incorrect password. %d attempt(s) left." % max(0, left) if left > 0 else "Account locked. Try later."
            self._send_html(build_login(error=True, msg=msg))

    def _send_json(self, data):
        out = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _handle_proxy(self):
        length = int(self.headers.get("Content-Length", 0))
        # Reject oversized bodies
        if length > 1024:
            self._send_json({"success": False, "error": "Invalid request"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._send_json({"success": False, "error": "Invalid JSON"})
            return

        mobile = str(body.get("mobile", "")).strip()

        # Validate mobile number format — must be +880 BD number
        import re
        if not re.match(r"^\+8801[3-9]\d{8}$", mobile):
            self._send_json({"success": False, "error": "Invalid mobile number format"})
            return

        payload = {
            "mobile":    mobile,
            "app_id":    APP_ID,
            "api_token": _current_token[0]
        }
        self._send_json(self._forward(payload))

    def _handle_refresh(self):
        result = refresh_token(APP_ID)
        self._send_json(result)

    def _forward(self, payload):
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            _EP,
            data=data,
            headers={
                "Content-Type":       "application/json;charset=UTF-8",
                "custom-headers":     json.dumps({"portal-name": _PNM}),
                "sec-ch-ua-platform": "Android",
                "sec-ch-ua":          '"Chromium";v="142", "Brave";v="142", "Not_A Brand";v="99"',
                "sec-ch-ua-mobile":   "?1",
                "sec-gpc":            "1",
                "accept-language":    "en-US,en;q=0.6",
                "origin":             _ORG,
                "referer":            _ORG + "/",
                "sec-fetch-site":     "same-site",
                "sec-fetch-mode":     "cors",
                "sec-fetch-dest":     "empty",
                "User-Agent":         _UA,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    parsed = json.loads(raw)
                    parsed["success"] = True
                    return parsed
                except Exception:
                    return {"success": True, "message": raw}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                parsed = json.loads(raw)
                parsed["success"] = False
                return parsed
            except Exception:
                return {"success": False, "error": "HTTP %d" % e.code, "message": raw}
        except Exception as e:
            return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # On Railway/cloud: bind 0.0.0.0 so it's publicly accessible
    # Locally: bind localhost only
    HOST = "0.0.0.0" if os.environ.get("RAILWAY_ENVIRONMENT") else "localhost"

    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)

    # Auto-open browser only when running locally
    if HOST == "localhost":
        import webbrowser
        def _open_browser():
            time.sleep(1.5)
            webbrowser.open("http://localhost:%d" % PORT)
        threading.Thread(target=_open_browser, daemon=True).start()

    print("")
    print("  ╔══════════════════════════════════════════╗")
    print("  ║        OTP Sender v2.0  — Ready          ║")
    print("  ╠══════════════════════════════════════════╣")
    if HOST == "localhost":
        print("  ║   URL  : http://localhost:%d          ║" % PORT)
    else:
        print("  ║   URL  : https://your-app.railway.app   ║")
    print("  ║   Login: /login  (password protected)    ║")
    print("  ╚══════════════════════════════════════════╝")
    print("")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("  Stopped.")
