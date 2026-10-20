#!/usr/bin/env python3
"""Small web UI for Server/server_control.py: the same commands the CLI has. Python standard library only."""
import base64, hmac, json, os, re, shlex, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SC = os.environ.get("FFXI_SC", "/ffxi/Server/server_control.py")
DATA = os.environ.get("FFXI_DATA_DIR") or os.path.join(os.path.dirname(SC), "data")
PORT = int(os.environ.get("WEBUI_PORT", "8080"))
USER = os.environ.get("WEBUI_USER", "admin")
PASS = os.environ.get("WEBUI_PASSWORD", "")
if not PASS:
    raise SystemExit("Set WEBUI_PASSWORD: this UI controls the server.")

sys.path.insert(0, os.path.dirname(SC))
try:  # the modules the Tk app uses, so the rules are checked exactly the same way
    import server_control as sc
    import server_settings as ss
    SS_ERROR = ""
except Exception as e:  # noqa
    sc = ss = None
    SS_ERROR = str(e)
WORLD_LOCK = threading.Lock()


class WorldError(Exception):
    pass


try:  # who is online, world chat, kick/ban/mute/tp/give... exactly what the Tk app's chat box runs
    import server_admin as sa
    SA_ERROR = ""
except Exception as e:  # noqa
    sa = None
    SA_ERROR = str(e)
CHAT_LOCK, CHAT_BUF, CHAT_ID, CHAT_READER = threading.Lock(), [], [0], [None]
try:  # roles for the in-game ! commands, and the chat rules: the files the Tk app edits
    import server_roles as sr
    import server_chat as scr
    SR_ERROR = ""
except Exception as e:  # noqa
    sr = scr = None
    SR_ERROR = str(e)
ADMIN_LOCK = threading.Lock()
CHAR_RE = re.compile(r"^@?[A-Za-z0-9_]{1,20}$")

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,14}$")      # same rule as server_settings.NAME_RE
PROFILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")                # GUESS; the CLI checks again
PASS_RE = re.compile(r"^[A-Za-z0-9]{6,15}$")                      # "6 to 15 letters or numbers" (server_control)
BACKUP_RE = re.compile(r"^ffxi-\d{8}-\d{6}(-[A-Za-z0-9_-]+)?\.sql\.gz$")
ADDR_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
READ_ONLY = {("status",), ("check",), ("backups",), ("name",), ("welcome",), ("address",)}
TIMEOUT = {"start": 900, "stop": 300, "backup": 600, "restore": 900}
JOBS, JOBS_LOCK, MUTATE = {}, threading.Lock(), threading.Lock()
FAILS, FAIL_LOCK, MAX_FAILS, FAIL_WINDOW = [], threading.Lock(), 10, 600


def locked_out():
    """True after 10 wrong passwords in 10 minutes (from anyone): the page then refuses every sign-in until they age out."""
    now = time.time()
    with FAIL_LOCK:
        FAILS[:] = [t for t in FAILS if now - t < FAIL_WINDOW]
        return len(FAILS) >= MAX_FAILS


def worker(job, args, extra_env, timeout, mutating):
    if mutating:
        job["out"] += "(waiting for the previous command to finish)\n"
        MUTATE.acquire()
    timer = None
    try:
        env = {k: v for k, v in os.environ.items() if k != "WEBUI_PASSWORD"}
        env.update(extra_env, PYTHONUNBUFFERED="1")
        p = subprocess.Popen(["python3", SC] + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, cwd=os.path.dirname(SC), env=env)
        timer = threading.Timer(timeout, p.kill)
        timer.start()
        for line in p.stdout:
            job["out"] += ANSI.sub("", line)
            if len(job["out"]) > 200000:
                job["out"] = job["out"][-100000:]
        job["rc"] = p.wait()
    except Exception as e:  # noqa
        job["out"] += "\n[ui error] %s\n" % e
        job["rc"] = -1
    finally:
        if timer:
            timer.cancel()
        job["done"] = True
        if mutating:
            MUTATE.release()


def start_job(args, extra_env, timeout):
    job = {"cmd": "server_control.py " + " ".join(shlex.quote(a) for a in args),   # never contains the password
           "out": "", "done": False, "rc": None, "t": time.time()}
    jid = uuid.uuid4().hex[:8]
    with JOBS_LOCK:
        JOBS[jid] = job
        for old in sorted(JOBS, key=lambda k: JOBS[k]["t"])[:-40]:
            JOBS.pop(old, None)
    threading.Thread(target=worker, args=(job, args, extra_env, timeout, tuple(args) not in READ_ONLY), daemon=True).start()
    return jid


def start_in_progress():
    """True while any 'server_control.py start' is running, the container's own first start included. On a first start it loads
    the world database for minutes, and a second start that sees the database already up goes on without waiting for that."""
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            args = open("/proc/%s/cmdline" % pid, "rb").read().split(b"\0")
        except OSError:
            continue
        if len(args) > 2 and args[0].endswith(b"python3") and args[1].endswith(b"server_control.py") and args[2] == b"start":
            return True
    return False


def build(body):
    """UI request -> (argv for server_control.py, extra environment, timeout). Raises ValueError with a plain message."""
    a, v = body.get("action"), str(body.get("value") or "").strip()
    if a == "start" and start_in_progress():
        raise ValueError("The server is still starting (on a first start it loads the world database for several minutes). Wait for it to finish.")
    if a in ("status", "start", "stop", "check", "backup", "backups"):
        return [a], {}, TIMEOUT.get(a, 120)
    if a in ("name-show", "welcome-show", "address-show"):
        return [a.split("-")[0]], {}, 120
    if a in ("welcome-clear", "address-clear"):
        return [a.split("-")[0], "-"], {}, 120
    if a == "name":
        if not NAME_RE.match(v):
            raise ValueError("Name: 1 to 15 letters, numbers, spaces, _ or -, starting with a letter or number")
        return ["name", v], {}, 120
    if a == "welcome":
        if not v or len(v) > 200 or "\n" in v or v == "-":
            raise ValueError("Welcome message: 1 to 200 characters on one line")
        return ["welcome", v], {}, 120
    if a == "address":
        if not ADDR_RE.match(v) or v == "-":
            raise ValueError("Address: a hostname or IP address")
        return ["address", v], {}, 120
    if a == "restore":
        if not BACKUP_RE.match(v) or not os.path.isfile(os.path.join(DATA, "backups", v)):
            raise ValueError("Unknown backup file")
        return ["restore", os.path.join(DATA, "backups", v)], {}, TIMEOUT["restore"]
    if a == "create-profile":
        pw = str(body.get("password") or "")
        if not PROFILE_RE.match(v):
            raise ValueError("Profile name: letters, digits, _ . - (max 32)")
        if not PASS_RE.match(pw):
            raise ValueError("Password: 6 to 15 letters or numbers")
        return ["create-profile", v], {"FFXI_PW": pw}, 120      # the CLI reads the password from FFXI_PW
    raise ValueError("Unknown action")


_DB = [0.0, False]


def db_up():
    """sc.db_ready() remembered for 10 seconds. Every real check opens a connection that MariaDB logs as 'Aborted connection'."""
    now = time.time()
    if now - _DB[0] > 10:
        _DB[:] = [now, bool(sc.db_ready())]
    return _DB[1]


def roles_state():
    if sr is None:
        raise WorldError("Roles module could not be loaded: " + SR_ERROR)
    groups = [{"group": g, "items": [{"name": n, "perm": pm, "desc": d} for n, pm, d in items]} for g, items in sr.grouped()]
    return {"data": sr.load(), "groups": groups, "db": db_up()}


def roles_action(path, body):
    if sr is None:
        raise WorldError("Roles module could not be loaded: " + SR_ERROR)
    name = str(body.get("name") or body.get("role") or "")
    char = str(body.get("char") or "")
    try:
        with ADMIN_LOCK:
            d = sr.load()
            if path in ("/api/roles/save", "/api/roles/delete"):
                target = str(body.get("old_name") or name)
                if any(r in (target, name) for r in d["members"].values()) and not sc.db_ready():
                    raise WorldError("Start the server first: members of this role need their GM level updated.")
            if path == "/api/roles/save":
                cmds = body.get("commands")
                valid = {n for _, items in sr.grouped() for n, _, _ in items}
                if not isinstance(cmds, list) or any(c not in valid for c in cmds):
                    raise WorldError("Unknown command in the list")
                sr.save_role(name, cmds, body.get("old_name") or None)
            elif path == "/api/roles/delete":
                sr.delete_role(name)
            elif path in ("/api/roles/promote", "/api/roles/demote"):
                if not CHAR_RE.match(char):
                    raise WorldError("Bad character name")
                if not sc.db_ready():
                    raise WorldError("Start the server first.")
                if path == "/api/roles/promote":
                    sr.promote(char, name)
                else:
                    sr.demote(char)
            else:
                raise WorldError("Unknown request")
            return roles_state()
    except sc.ServerError as e:
        raise WorldError(str(e))


def chatrules_state():
    if scr is None:
        raise WorldError("Chat module could not be loaded: " + SR_ERROR)
    hits = [{"time": time.strftime("%m-%d %H:%M", time.localtime(h[1])), "player": h[2], "channel": h[3], "rule": h[4],
             "text": h[5], "actions": h[6]} for h in scr.hits(limit=40)]
    return {"rules": scr.load_rules(), "actions": scr.ACTIONS, "spam_actions": scr.SPAM_ACTIONS, "hits": hits}


def _int(v, lo, hi, what):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        raise WorldError("%s must be a number" % what)
    if not lo <= n <= hi:
        raise WorldError("%s must be between %d and %d" % (what, lo, hi))
    return n


def chatrules_save(body):
    if scr is None:
        raise WorldError("Chat module could not be loaded: " + SR_ERROR)
    inc = body.get("rules")
    if not isinstance(inc, dict) or not isinstance(inc.get("lists"), list) or not isinstance(inc.get("spam"), dict):
        raise WorldError("Bad request")
    act_ok, spam_ok = {k for k, _ in scr.ACTIONS}, {k for k, _ in scr.SPAM_ACTIONS}
    with ADMIN_LOCK:
        d = scr.load_rules()
        old = {l.get("name"): l for l in d.get("lists", [])}
        lists = []
        for l in inc["lists"][:20]:
            name = str(l.get("name") or "").strip()[:40]
            if not name:
                raise WorldError("Every word list needs a name")
            words = list(dict.fromkeys(str(w).strip().lower()[:40] for w in l.get("words", []) if str(w).strip()))[:2000]
            item = dict(old.get(name, {}))
            item.update(name=name, on=bool(l.get("on")), actions=[a for a in l.get("actions", []) if a in act_ok],
                        warn_text=str(l.get("warn_text") or "")[:200], strikes=_int(l.get("strikes", 3), 1, 20, "Strikes"), words=words)
            lists.append(item)
        sp = inc["spam"]
        spam = dict(d.get("spam", {}))
        spam.update(on=bool(sp.get("on")), max_messages=_int(sp.get("max_messages", 5), 1, 50, "Messages"),
                    per_seconds=_int(sp.get("per_seconds", 10), 1, 300, "Seconds"), max_repeats=_int(sp.get("max_repeats", 3), 1, 20, "Repeats"),
                    actions=[a for a in sp.get("actions", []) if a in spam_ok], mute_minutes=_int(sp.get("mute_minutes", 5), 1, 1440, "Mute minutes"),
                    warn_text=str(sp.get("warn_text") or "")[:200])
        d.update(enabled=bool(inc.get("enabled")), archive_days=_int(inc.get("archive_days", 90), 1, 3650, "Archive days"), lists=lists, spam=spam)
        scr.save_rules(d)
    return chatrules_state()


def players_state():
    if sa is None:
        raise WorldError("Admin module could not be loaded: " + SA_ERROR)
    if not db_up():
        return {"db": False, "online": [], "profiles": []}
    return {"db": True, "bridge": sa.bridge_ok(), "online": sa.online_players(), "profiles": sa.profiles()}


def chat_lines(since):
    if sa is None or not db_up():
        return {"lines": [], "last": since}
    with CHAT_LOCK:
        if CHAT_READER[0] is None:
            CHAT_READER[0] = sa.ChatReader()
        for typ, spk, msg, extra in CHAT_READER[0].new_lines():
            CHAT_ID[0] += 1
            CHAT_BUF.append((CHAT_ID[0], typ, spk, msg, extra))
        del CHAT_BUF[:-300]
        if since > CHAT_ID[0]:      # this page outlived a restart of the web UI
            since = 0
        return {"lines": [l for l in CHAT_BUF if l[0] > since], "last": CHAT_ID[0]}


def admin_command(body):
    if sa is None:
        raise WorldError("Admin module could not be loaded: " + SA_ERROR)
    line = str(body.get("line") or "").strip()
    if not line or len(line) > 300 or "\n" in line:
        raise WorldError("Type one message or command (max 300 characters)")
    if not sc.db_ready():
        raise WorldError("Start the server first.")
    ok, text = sa.command(line, "Server")      # can take several seconds: the map server checks for orders every few seconds
    return {"ok": ok, "text": text}


def world_state():
    if ss is None:
        raise WorldError("Settings module could not be loaded: " + SS_ERROR)
    groups = {}
    for g in ("rates", "monsters", "rules"):
        vals = ss.get_values(g)
        groups[g] = [{"key": st.key, "label": st.label, "kind": st.kind, "lo": st.lo, "hi": st.hi, "value": vals[st.key]}
                     for st in ss.GROUPS[g]]
    return {"groups": groups, "era": ss.detect_era(),
            "eras": [{"key": k, "label": label, "desc": desc} for k, label, desc, _ in ss.ERAS]}


def world_save(path, body):
    if ss is None:
        raise WorldError("Settings module could not be loaded: " + SS_ERROR)
    try:
        with WORLD_LOCK:
            if path == "/api/era":
                vals = ss.era_values(str(body.get("era")))
                if vals is None:
                    raise WorldError("Unknown era")
                changed = ss.save_values("rules", vals)
            else:
                group, vals = body.get("group"), body.get("values")
                if group not in ("rates", "monsters", "rules") or not isinstance(vals, dict):
                    raise WorldError("Bad request")
                changed = ss.save_values(group, vals)
            return {"changed": bool(changed), "state": world_state()}
    except sc.ServerError as e:
        raise WorldError(str(e))


class H(BaseHTTPRequestHandler):
    server_version = "ffxi-webui"

    def log_message(self, *a):
        pass

    def authed(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            u, _, p = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:
            return False
        ok_u = hmac.compare_digest(u.encode(), USER.encode())
        ok_p = hmac.compare_digest(p.encode(), PASS.encode())
        return ok_u and ok_p

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def guard(self):
        if locked_out():
            self.send(429, "Too many wrong passwords. Try again in a few minutes.", "text/plain")
            return False
        if self.authed():
            return True
        if self.headers.get("Authorization"):      # a wrong password, not just the browser's first request without one
            with FAIL_LOCK:
                FAILS.append(time.time())
        time.sleep(1)  # slows password guessing
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="FFXI server"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self.guard():
            return
        path, _, query = self.path.partition("?")
        if path in ("/api/players", "/api/chat", "/api/roles", "/api/chatrules"):
            try:
                if path == "/api/players":
                    return self.send(200, json.dumps(players_state()))
                if path == "/api/roles":
                    return self.send(200, json.dumps(roles_state()))
                if path == "/api/chatrules":
                    return self.send(200, json.dumps(chatrules_state()))
                m = re.search(r"since=(\d+)", query)
                return self.send(200, json.dumps(chat_lines(int(m.group(1)) if m else 0)))
            except WorldError as e:
                return self.send(503, str(e), "text/plain")
            except Exception as e:  # noqa
                return self.send(500, str(e), "text/plain")
        if self.path == "/":
            return self.send(200, PAGE, "text/html")
        if self.path == "/api/world":
            try:
                with WORLD_LOCK:
                    return self.send(200, json.dumps(world_state()))
            except WorldError as e:
                return self.send(503, str(e), "text/plain")
            except Exception as e:  # noqa
                return self.send(500, str(e), "text/plain")
        if self.path.startswith("/api/job/"):
            job = JOBS.get(self.path.rsplit("/", 1)[1])
            return self.send(200, json.dumps(job)) if job else self.send(404, '{"error":"unknown job"}')
        self.send(404, '{"error":"not found"}')

    def do_POST(self):
        if not self.guard():
            return
        # a custom header forces a CORS preflight, which we never grant: blocks cross-site requests
        if self.path not in ("/api/run", "/api/world", "/api/era", "/api/cmd", "/api/roles/save", "/api/roles/delete", "/api/roles/promote", "/api/roles/demote", "/api/chatrules/save") or self.headers.get("X-FFXI-UI") != "1":
            return self.send(403, '{"error":"forbidden"}')
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 10000:
                raise ValueError("Request too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/run":
                args, env, timeout = build(body)
                return self.send(200, json.dumps({"id": start_job(args, env, timeout)}))
            if self.path.startswith("/api/roles/"):
                return self.send(200, json.dumps(roles_action(self.path, body)))
            if self.path == "/api/chatrules/save":
                return self.send(200, json.dumps(chatrules_save(body)))
            if self.path == "/api/cmd":
                return self.send(200, json.dumps(admin_command(body)))
            return self.send(200, json.dumps(world_save(self.path, body)))
        except (ValueError, WorldError) as e:
            return self.send(400, json.dumps({"error": str(e)}))
        except Exception as e:  # noqa
            return self.send(500, json.dumps({"error": str(e)}))

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>FFXI Server</title>
<style>
:root{--bg:#0d0c24;--raise:#262370;--line:#4a44a6;--ink:#0a091d;--fg:#ece9ff;--mute:#a09bdc;--gold:#e9b44c;--goldfg:#2a1a02;--ok:#6fd49a;--bad:#f0787a}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 "Trebuchet MS","Segoe UI",system-ui,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:0 16px}
header.top{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
h1{font-size:19px;font-weight:600;margin:0;padding:12px 0 4px;display:flex;align-items:center;gap:10px}
nav#tabs{display:flex;gap:4px;overflow-x:auto}
nav#tabs button{background:none;border:0;border-bottom:3px solid transparent;border-radius:0;color:var(--mute);padding:8px 14px;white-space:nowrap}
nav#tabs button:hover{color:var(--fg);background:none}
nav#tabs button.on{color:var(--fg);border-bottom-color:var(--gold)}
main{max-width:980px;margin:16px auto 84px;padding:6px 18px 16px;background:linear-gradient(#1c1a54,#15133f);border:1px solid var(--line);border-radius:6px}
@media(max-width:1012px){main{margin-left:8px;margin-right:8px}}
.card{padding:12px 0 8px}
.card:not([hidden])~.card:not([hidden]){border-top:1px solid var(--line);margin-top:8px}
h2{font-size:15px;font-weight:600;color:var(--gold);margin:0 0 10px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
input,select,textarea{background:var(--ink);color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:8px;font:inherit}
input,select{flex:1;min-width:150px}
textarea{width:100%;font:13px ui-monospace,monospace}
button{background:var(--raise);color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:7px 14px;font:inherit;cursor:pointer}
button:hover{background:#322f8a}
button.go{background:var(--gold);border-color:var(--gold);color:var(--goldfg);font-weight:600}
button.go:hover{background:#f3c566}
button.stop{background:#5a1f3a;border-color:#a14068}
button.stop:hover{background:#74284b}
:focus-visible{outline:2px solid var(--gold);outline-offset:1px}
pre{background:var(--ink);border:1px solid #2c2873;border-radius:4px;padding:10px;margin:0;white-space:pre-wrap;word-break:break-word;max-height:340px;overflow:auto;font:13px ui-monospace,monospace}
small{color:var(--mute)}
.badge{padding:2px 10px;border-radius:99px;font-size:12px;font-weight:600;background:#333}
.badge.on{background:var(--ok);color:#06210f}.badge.off{background:var(--bad);color:#2b0707}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px;margin:8px 0}
label{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:14px}
.grid input{flex:none;min-width:0;width:90px}.grid input[type=checkbox]{width:auto}
.sub{background:#12103a;border:1px solid #2c2873;border-radius:6px;padding:10px;margin:10px 0;display:grid;gap:8px}
.sub input[type=checkbox]{flex:none;min-width:0;width:auto}.sub input[type=number]{flex:none;min-width:0;width:80px}
details{margin:6px 0}summary{cursor:pointer;padding:4px 0}
#console{position:fixed;left:0;right:0;bottom:0;z-index:6;background:var(--ink);border-top:1px solid var(--line)}
#console .bar{display:flex;align-items:center;gap:10px;max-width:980px;margin:0 auto;padding:7px 16px;cursor:pointer;font-size:13px;color:var(--mute)}
#console .bar b{color:var(--fg);font-weight:600}
#console pre{display:none;max-width:980px;margin:0 auto;border:0;border-radius:0;max-height:34vh}
#console.open pre{display:block}
</style></head><body><header class="top"><div class="wrap"><h1>FFXI 2016 server <span id="badge" class="badge">...</span></h1>
<nav id="tabs" role="tablist"><button data-t="server">Server</button><button data-t="players">Players</button><button data-t="world">World</button><button data-t="roles">Roles</button><button data-t="chat">Chat rules</button><button data-t="backups">Backups</button></nav></div></header><main>
<div class="card" data-tab="server"><h2>Status <small id="stamp"></small></h2><pre id="status">...</pre>
<div class="row" style="margin-top:10px"><button class="go" onclick="run('start')">Start</button>
<button class="stop" onclick="if(confirm('Stop the server? Players will be disconnected.'))run('stop')">Stop</button>
<button onclick="refresh()">Refresh</button><button onclick="run('check')">Check setup</button></div></div>
<div class="card" data-tab="server"><h2>Name and address</h2>
<div class="row"><input id="name" placeholder="World name (max 15)"><button onclick="run('name','name')">Set name</button></div>
<div class="row"><input id="welcome" placeholder="Welcome message"><button onclick="run('welcome','welcome')">Set</button>
<button onclick="run('welcome-show')">Show</button><button onclick="run('welcome-clear')">Clear</button></div>
<div class="row"><input id="address" placeholder="Public hostname or IP (for players outside your house)"><button onclick="run('address','address')">Set</button>
<button onclick="run('address-show')">Show</button><button onclick="run('address-clear')">Auto</button></div>
<small>Name changes need a restart. Players on your LAN do not need the public address.</small></div>
<div class="card" data-tab="players"><h2>Online now <small id="pstamp"></small></h2><div id="players"></div>
<div class="row" style="margin:8px 0"><input id="cmd" placeholder="Message to everyone, or /command (try /help)" onkeydown="if(event.key==='Enter')sendCmd()">
<button onclick="sendCmd()">Send</button><button onclick="sendLine('/help')">/help</button></div>
<pre id="chat" style="max-height:260px"></pre>
<small>Commands can take a few seconds: the map server checks for orders every few seconds. Click a name to start a command with it.</small></div>
<div class="card" data-tab="world"><h2>Rates, levels and expansions</h2>
<div class="row" id="eras"></div><div id="wgroups"></div><small id="wmsg"></small></div>
<div class="card" data-tab="players"><h2>New profile</h2>
<div class="row"><input id="profile" placeholder="Profile name (POL-ID)"><input id="pw" type="password" autocomplete="new-password" placeholder="Password (6-15 letters/numbers)">
<button onclick="run('create-profile','profile',{password:$('pw').value})">Create</button></div>
<small>The server stores profile passwords in plain text in Server/data/players.txt (that is how the app works). This page has no HTTPS: LAN only, or behind a TLS proxy.</small></div>
<div class="card" data-tab="players"><h2>All profiles</h2><div id="profs"></div></div>
<div class="card" data-tab="roles"><h2>Roles</h2><div class="row" id="rolebar"></div><div id="roleedit"></div>
<h2 style="margin-top:14px">Members</h2><div id="members"></div>
<div class="row"><input id="pchar" placeholder="Character name"><select id="prole"></select><button onclick="promote()">Promote</button></div>
<small id="rmsg">Players without a role cannot use any ! GM command. Role changes apply within seconds, no restart.</small></div>
<div class="card" data-tab="chat"><h2>Chat rules</h2><div id="crbox"></div>
<div class="row" style="margin-top:8px"><button class="go" onclick="saveRules()">Save chat rules</button></div><small id="crmsg"></small>
<h2 style="margin-top:14px">Recent hits</h2><pre id="hits" style="max-height:200px"></pre></div>
<div class="card" data-tab="backups"><h2>Backups</h2>
<div class="row"><button class="go" onclick="run('backup')">Back up now</button><button onclick="run('backups')">List</button></div>
<div class="row"><select id="bk"></select><button class="stop" onclick="restore()">Restore</button></div>
<small>Restore needs the game servers stopped. A safety backup is made first.</small></div>
</main>
<div id="console"><div class="bar" onclick="toggleConsole()"><b>Output</b><small id="lastcmd">nothing run yet</small><span id="ctoggle" style="margin-left:auto">show</span></div>
<pre id="out">Nothing run yet.</pre></div>
<script>
const $=id=>document.getElementById(id),sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function launch(b){const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json','X-FFXI-UI':'1'},body:JSON.stringify(b)});
  const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j.id}
async function follow(id,el,quiet){for(;;){const j=await(await fetch('/api/job/'+id)).json();
  if(!quiet||j.done){const t=(quiet?'':'$ '+j.cmd+'\n')+j.out+(j.done&&!quiet?'\n[exit '+j.rc+']':'');if(el.textContent!==t)el.textContent=t;
    if(el.id==='out'){$('console').classList.add('open');$('ctoggle').textContent='hide';$('lastcmd').textContent=j.cmd}}
  if(j.done)return j;await sleep(1000)}}
async function run(action,field,extra){
  try{const j=await follow(await launch(Object.assign({action,value:field?$(field).value:''},extra||{})),$('out'));
    if(action==='backups')fill(j.out);if(action==='create-profile')$('pw').value='';refresh()}
  catch(e){$('out').textContent='Error: '+e.message}}
function fill(t){const names=(t.match(/ffxi-\S+\.sql\.gz/g)||[]);$('bk').innerHTML=names.map(n=>'<option>'+n+'</option>').join('')}
function restore(){const n=$('bk').value;if(n&&confirm('Replace player data with '+n+'?'))run('restore',null,{value:n})}
let busy=false;
async function refresh(){if(busy)return;busy=true;
  try{const j=await follow(await launch({action:'status'}),$('status'),true);const on=/Server is ON/.test(j.out);
    $('badge').textContent=on?'ON':'OFF';$('badge').className='badge '+(on?'on':'off');
    const m=j.out.match(/Server name\s+(.+)/);if(m&&!$('name').value)$('name').value=m[1].trim();
    $('stamp').textContent=new Date().toLocaleTimeString()}
  catch(e){$('status').textContent='Error: '+e.message}busy=false}
const GL={rates:'Rates',monsters:'Monsters',rules:'Levels and expansions'};
let W=null;
async function loadWorld(){const r=await fetch('/api/world');
  if(!r.ok){$('wmsg').textContent='World settings unavailable: '+await r.text();return}W=await r.json();drawWorld()}
function drawWorld(){
  const er=$('eras');er.innerHTML='';
  W.eras.forEach(e=>{const b=document.createElement('button');b.textContent=e.label+(W.era===e.key?' (current)':'');b.title=e.desc;
    b.onclick=()=>{if(confirm(e.label+': '+e.desc+'\nThis changes the level cap and the open expansions.'))postWorld('/api/era',{era:e.key})};er.appendChild(b)});
  if(!W.era){const s=document.createElement('small');s.textContent='Current setup is a custom mix';er.appendChild(s)}
  const box=$('wgroups');box.innerHTML='';
  for(const g of ['rates','monsters','rules']){
    const h=document.createElement('h2');h.textContent=GL[g];h.style.marginTop='14px';box.appendChild(h);
    const grid=document.createElement('div');grid.className='grid';
    W.groups[g].forEach(s=>{const l=document.createElement('label');l.textContent=s.label;const i=document.createElement('input');
      i.dataset.g=g;i.dataset.k=s.key;
      if(s.kind==='flag'){i.type='checkbox';i.checked=!!s.value}
      else{i.type='number';i.value=s.value;i.min=s.lo;i.max=s.hi;i.step=s.kind==='mult'?'0.1':'1'}
      l.appendChild(i);grid.appendChild(l)});
    box.appendChild(grid);
    const b=document.createElement('button');b.textContent='Save '+GL[g].toLowerCase();b.onclick=()=>saveGroup(g);box.appendChild(b)}}
function saveGroup(g){const values={};
  document.querySelectorAll('#wgroups input[data-g="'+g+'"]').forEach(i=>{values[i.dataset.k]=i.type==='checkbox'?i.checked:i.value});
  postWorld('/api/world',{group:g,values})}
async function postWorld(url,body){
  try{const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-FFXI-UI':'1'},body:JSON.stringify(body)});
    const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);W=j.state;drawWorld();
    $('wmsg').textContent=j.changed?'Saved. Rates apply within seconds; level cap and expansions most likely need a server restart.':'Nothing changed.'}
  catch(e){$('wmsg').textContent='Error: '+e.message}}
function el(t,txt,cls){const e=document.createElement(t);e.textContent=txt;if(cls)e.className=cls;return e}
async function getJSON(u){const r=await fetch(u);if(!r.ok)throw new Error(await r.text());return r.json()}
function addChat(t){const c=$('chat');c.textContent+=(c.textContent?'\n':'')+t;if(c.textContent.length>40000)c.textContent=c.textContent.slice(-30000);c.scrollTop=c.scrollHeight}
async function sendLine(line){
  try{const r=await fetch('/api/cmd',{method:'POST',headers:{'Content-Type':'application/json','X-FFXI-UI':'1'},body:JSON.stringify({line})});
    const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);addChat('> '+(j.ok?'':'! ')+(j.text||'(done)'));loadPlayers()}
  catch(e){addChat('> ! '+e.message)}}
function sendCmd(){const v=$('cmd').value.trim();if(v){$('cmd').value='';sendLine(v)}}
function act(label,line,ask){const b=el('button',label);b.onclick=()=>{if(!ask||confirm(ask))sendLine(line)};return b}
let psig='';
async function loadPlayers(){
  try{const j=await getJSON('/api/players'),sig=JSON.stringify(j);
    $('pstamp').textContent=new Date().toLocaleTimeString();if(sig===psig)return;psig=sig;
    const box=el('div',''),pr=el('div','');
    if(!j.db){box.appendChild(el('small','The database is off. Start the server to see players.'))}
    else{
      if(!j.online.length)box.appendChild(el('small','Nobody is online.'));
      j.online.forEach(p=>{const row=el('div','','row'),n=el('button',p.name);n.onclick=()=>{$('cmd').value='/tp @'+p.name+' ';$('cmd').focus()};
        row.append(n,el('small',p.zone+' - '+p.job+' - '+p.state+(p.gm?' - GM '+p.gm:'')),
          act('Kick','/kick @'+p.name,'Log out '+p.name+'?'),act('Mute 10m','/mute @'+p.name+' 10'),act('Ban','/ban @'+p.name,'Ban the profile of '+p.name+'?'));box.appendChild(row)});
      j.profiles.forEach(a=>{const who=a.chars.length?a.chars[0].name:a.login,row=el('div','','row');
        row.append(el('b',a.login+(a.online?' (online)':'')+(a.banned?' (banned)':'')),el('small',a.chars.map(c=>c.name+' '+c.job).join(', ')||'no characters'),
          act(a.banned?'Unban':'Ban',(a.banned?'/unban @':'/ban @')+who,a.banned?null:'Ban '+a.login+'?'));pr.appendChild(row)})}
    $('players').replaceChildren(...box.childNodes);$('profs').replaceChildren(...pr.childNodes)}
  catch(e){$('players').textContent='Error: '+e.message}}
let since=0;
async function pollChat(){try{const j=await getJSON('/api/chat?since='+since);
  j.lines.forEach(l=>addChat('['+l[1]+'] '+l[2]+(l[4]?' ('+l[4]+')':'')+': '+l[3]));since=j.last}catch(e){}}
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-FFXI-UI':'1'},body:JSON.stringify(body)});
  const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}
function mk(tag,props){return Object.assign(document.createElement(tag),props||{})}
function lab(text,...kids){const l=mk('label');l.append(text,...kids);return l}
let R=null,cur=null;
async function loadRoles(){try{R=await getJSON('/api/roles');drawRoles()}catch(e){$('rmsg').textContent='Roles unavailable: '+e.message}}
async function roleCall(url,body,gone){
  try{R=await post(url,body);if(gone)cur=null;else if(url.endsWith('save'))cur=body.name.trim();
    drawRoles();if(cur===null)$('roleedit').innerHTML='';$('rmsg').textContent='Done.'}
  catch(e){$('rmsg').textContent='Error: '+e.message}}
function drawRoles(){
  const bar=$('rolebar');bar.innerHTML='';
  Object.keys(R.data.roles).sort().forEach(n=>{const b=el('button',n+' ('+R.data.roles[n].length+')');b.onclick=()=>editRole(n);bar.appendChild(b)});
  const nb=el('button','+ New role','go');nb.onclick=()=>editRole('');bar.appendChild(nb);
  const sel=$('prole');sel.innerHTML='';Object.keys(R.data.roles).forEach(n=>sel.appendChild(el('option',n)));
  const m=$('members');m.innerHTML='';const ms=Object.entries(R.data.members);
  if(!ms.length)m.appendChild(el('small','No one has a role yet.'));
  ms.forEach(([c,r])=>{const row=el('div','','row'),b=el('button','Demote');
    b.onclick=()=>{if(confirm('Remove the role from '+c+'?'))roleCall('/api/roles/demote',{char:c})};row.append(el('b',c),el('small',r),b);m.appendChild(row)});
  if(cur!==null)editRole(cur)}
function editRole(n){cur=n;const box=$('roleedit');box.innerHTML='';const have=new Set(R.data.roles[n]||[]);
  box.appendChild(mk('input',{id:'rname',value:n,placeholder:'Role name'}));
  R.groups.forEach(g=>{const dang=/dangerous/.test(g.group),d=mk('details'),
      sm=el('summary',g.group+(dang?' (dangerous)':'')+' - '+g.items.filter(i=>have.has(i.name)).length+'/'+g.items.length);
    if(dang)sm.style.color='var(--bad)';d.appendChild(sm);const grid=el('div','','grid');
    g.items.forEach(i=>{const c=mk('input',{type:'checkbox',checked:have.has(i.name)});c.dataset.cmd=i.name;c.dataset.dang=dang?'1':'';
      const l=lab(i.name+' ('+i.perm+')',c);l.title=i.desc||'';grid.appendChild(l)});
    d.appendChild(grid);box.appendChild(d)});
  const row=el('div','','row');row.style.marginTop='8px';const sv=el('button','Save role','go');sv.onclick=saveRole;row.appendChild(sv);
  if(n){const dl=el('button','Delete role','stop');dl.onclick=()=>{if(confirm('Delete '+n+' and remove it from its members?'))roleCall('/api/roles/delete',{name:n},true)};row.appendChild(dl)}
  box.appendChild(row)}
function saveRole(){const cs=[...document.querySelectorAll('#roleedit input[data-cmd]:checked')],dn=cs.filter(c=>c.dataset.dang).map(c=>c.dataset.cmd);
  if(dn.length&&!confirm('This role can use '+dn.length+' dangerous server commands ('+dn.slice(0,6).join(', ')+(dn.length>6?', ...':'')+'). Save anyway?'))return;
  roleCall('/api/roles/save',{name:$('rname').value,old_name:cur||null,commands:cs.map(c=>c.dataset.cmd)})}
function promote(){roleCall('/api/roles/promote',{char:$('pchar').value.trim(),name:$('prole').value})}
let C=null;
async function loadRules(){try{C=await getJSON('/api/chatrules');drawRules()}catch(e){$('crmsg').textContent='Chat rules unavailable: '+e.message}}
function actBoxes(list,sel){const g=el('div','','grid');list.forEach(([k,t])=>g.appendChild(lab(t,mk('input',{type:'checkbox',className:'act',value:k,checked:sel.includes(k)}))));return g}
function listForm(li){const d=mk('div',{className:'sub'});d.dataset.kind='list';
  d.append(lab('Name',mk('input',{className:'ln',value:li.name})),lab('On',mk('input',{type:'checkbox',className:'lon',checked:li.on})),
    actBoxes(C.actions,li.actions),lab('Warning text',mk('input',{className:'lwarn',value:li.warn_text||''})),
    lab('Strikes before kick/ban',mk('input',{type:'number',className:'lstr',min:1,max:20,value:li.strikes})),
    mk('textarea',{className:'lwords',rows:4,value:li.words.join('\n'),placeholder:'One word per line'}));
  const rm=el('button','Remove list','stop');
  rm.onclick=()=>{if(confirm('Remove the list '+li.name+'?')){const r=readRules(),i=[...document.querySelectorAll('#crbox .sub[data-kind="list"]')].indexOf(d);r.lists.splice(i,1);C.rules=r;drawRules()}};
  d.appendChild(rm);return d}
function spamForm(s){const d=mk('div',{className:'sub'});d.dataset.kind='spam';
  d.append(el('b','Spam limit'),lab('On',mk('input',{type:'checkbox',className:'son',checked:s.on})),
    lab('Max messages',mk('input',{type:'number',className:'smax',min:1,max:50,value:s.max_messages})),
    lab('...within seconds',mk('input',{type:'number',className:'sper',min:1,max:300,value:s.per_seconds})),
    lab('Same message max',mk('input',{type:'number',className:'srep',min:1,max:20,value:s.max_repeats})),
    actBoxes(C.spam_actions,s.actions),lab('Mute minutes',mk('input',{type:'number',className:'smute',min:1,max:1440,value:s.mute_minutes})),
    lab('Warning text',mk('input',{className:'swarn',value:s.warn_text||''})));return d}
function drawRules(){const r=C.rules,box=$('crbox');box.innerHTML='';const top=el('div','','grid');
  top.append(lab('Chat rules enabled (master switch)',mk('input',{type:'checkbox',id:'cr_en',checked:r.enabled})),
    lab('Keep chat archive (days)',mk('input',{type:'number',id:'cr_days',min:1,max:3650,value:r.archive_days})));box.appendChild(top);
  if(!r.enabled)box.appendChild(el('small','Rules are OFF: nothing is filtered, whatever the lists below say.'));
  r.lists.forEach(li=>box.appendChild(listForm(li)));
  const add=el('button','+ Add word list');
  add.onclick=()=>{const x=readRules();x.lists.push({name:'New list',on:true,actions:['replace'],warn_text:'',strikes:3,words:[]});C.rules=x;drawRules()};
  box.append(add,spamForm(r.spam));
  $('hits').textContent=C.hits.map(h=>h.time+' '+h.player+' ['+h.channel+'] '+h.rule+': '+h.text+' -> '+h.actions).join('\n')||'No hits recorded.'}
function readRules(){const q=(r,s)=>r.querySelector(s),acts=r=>[...r.querySelectorAll('.act:checked')].map(c=>c.value);
  const lists=[...document.querySelectorAll('#crbox .sub[data-kind="list"]')].map(d=>({name:q(d,'.ln').value,on:q(d,'.lon').checked,actions:acts(d),
    warn_text:q(d,'.lwarn').value,strikes:q(d,'.lstr').value,words:q(d,'.lwords').value.split(/[\n,]+/).map(w=>w.trim()).filter(Boolean)}));
  const s=q(document,'#crbox .sub[data-kind="spam"]');
  return {enabled:$('cr_en').checked,archive_days:$('cr_days').value,lists,spam:{on:q(s,'.son').checked,max_messages:q(s,'.smax').value,
    per_seconds:q(s,'.sper').value,max_repeats:q(s,'.srep').value,actions:acts(s),mute_minutes:q(s,'.smute').value,warn_text:q(s,'.swarn').value}}}
async function saveRules(){try{C=await post('/api/chatrules/save',{rules:readRules()});drawRules();$('crmsg').textContent='Saved.'}
  catch(e){$('crmsg').textContent='Error: '+e.message}}
const TABS=['server','players','world','roles','chat','backups'];let TAB='server';
function showTab(t){if(!TABS.includes(t))t='server';TAB=t;
  document.querySelectorAll('.card[data-tab]').forEach(c=>{c.hidden=c.dataset.tab!==t});
  document.querySelectorAll('#tabs button').forEach(b=>{const on=b.dataset.t===t;b.classList.toggle('on',on);b.setAttribute('aria-selected',on)});
  if(location.hash!=='#'+t)history.replaceState(null,'','#'+t);
  if(t==='players'){loadPlayers();pollChat()}}
document.querySelectorAll('#tabs button').forEach(b=>{b.setAttribute('role','tab');b.onclick=()=>showTab(b.dataset.t)});
function toggleConsole(){const c=$('console');c.classList.toggle('open');$('ctoggle').textContent=c.classList.contains('open')?'hide':'show'}
showTab(location.hash.slice(1));refresh();loadWorld();loadRoles();loadRules();
setInterval(()=>{if(!document.hidden)refresh()},30000);
setInterval(()=>{if(!document.hidden&&TAB==='players')loadPlayers()},10000);
setInterval(()=>{if(!document.hidden&&TAB==='players')pollChat()},4000);
</script></body></html>"""

if __name__ == "__main__":
    print("Web UI on port %d (user: %s)" % (PORT, USER), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
