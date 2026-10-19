# downloads.py — the download manager: aria2 does the downloading (up to 16 connections per file, several files
# at once, resumed after a restart), this service keeps track of it and tells the window and the bar.
#   downloads daemon       the service (downloads.service): runs aria2c, the window's socket, the bar's file, and
#                          serves the Chrome extension to Chrome on localhost (its policy install, system/root-setup.sh)
#   downloads enable       the user service, the Chrome extension packed for this PC, its native-messaging host
#                          (setup/install.sh runs it)
#   downloads add <url>…   download these (the panel's "paste a link" does the same)
#   downloads status       what's downloading
# Chrome hands its downloads over through the extension (chrome/: it asks the native host, native-host.py, which
# passes the link, cookies and referrer to this service, then cancels Chrome's own); if the service isn't there,
# Chrome keeps the download. The window (local/lib/panels/downloadsgui.py) talks over a Unix socket, JSON lines,
# like lsend's. aria2 is asked about progress once a second only while something downloads; idle, nothing runs.
# The bar: $XDG_RUNTIME_DIR/downloads-bar.json, waybar's custom/downloads reads it on signal 12.
import hashlib, http.server, json, os, secrets, shutil, socket, socketserver, subprocess, sys, threading, time
import urllib.error, urllib.parse, urllib.request

HOME = os.path.expanduser("~")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or f"{HOME}/.local/state", "downloads")
DATA = os.path.join(os.environ.get("XDG_DATA_HOME") or f"{HOME}/.local/share", "downloads")
RUN = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
SOCK = os.path.join(RUN, "downloads.sock")
BAR = os.path.join(RUN, "downloads-bar.json")
DIR = f"{HOME}/Downloads"
UNITS = f"{HOME}/.config/systemd/user"
HERE = os.path.dirname(os.path.realpath(__file__))
EXT_SRC = os.path.join(HERE, "chrome")
EXT_DIR = os.path.join(DATA, "chrome")        # this PC's packed extension, its key and id (never in the repo)
EXT_PORT = 53319                               # localhost only: Chrome fetches the extension (and updates) here
HOST_NAME = "sami.downloads"
HOST_MANIFEST = f"{HOME}/.config/google-chrome/NativeMessagingHosts/{HOST_NAME}.json"
BAR_SIGNAL = 12
TICK = 1.0                                     # seconds between progress reads while something downloads
KEEP = 100                                     # finished downloads remembered
G_DOWN, G_DONE, G_FAIL, G_PAUSE = "\U000f01da", "\U000f0b8f", "\U000f0b8e", "\U000f03e4"
# aria2: 8 connections per file, pieces from 2 MB, the fastest mirrors first; a session file, so what's unfinished
# resumes after a restart. 8, not aria2's most (16): on a 100 Mbit line 8 already filled it (kernel.org's CDN:
# 44.8 s with 1 connection, 12.8 s with 8, 12.8 s with 16), and 16 got this PC banned by OVH's test server
ARIA2 = ["--split=8", "--max-connection-per-server=8", "--min-split-size=2M", "--piece-length=1M",
         "--max-concurrent-downloads=5", "--optimize-concurrent-downloads=true", "--uri-selector=adaptive",
         "--stream-piece-selector=geom", "--disk-cache=32M", "--file-allocation=falloc", "--continue=true",
         "--auto-file-renaming=true", "--allow-overwrite=false", "--content-disposition-default-utf8=true",
         "--http-accept-gzip=true", "--max-tries=5", "--retry-wait=3", "--connect-timeout=15", "--timeout=30",
         "--seed-time=0", "--summary-interval=0", "--console-log-level=warn", "--save-session-interval=30",
         "--force-save=false"]
ERRORS = {   # aria2's error codes (man aria2c, EXIT STATUS) that say little as they are
    "1": "it failed (aria2 couldn't say why)",
    "2": "the server stopped answering (timed out)",
    "3": "the server says it isn't there (404)",
    "6": "the network went away",
    "8": "the server can't resume this download",
    "9": "not enough disk space",
    "13": "a file of that name is already there",
    "19": "the name couldn't be looked up (DNS)",
    "22": "the server refused it (an expired link, or not logged in?)",
    "24": "the server wants a login",
}
KEYS = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "connections", "errorCode",
        "errorMessage", "files", "dir", "bittorrent", "followedBy"]
SERVICE = """[Unit]
Description=downloads: the download manager (aria2) — see downloads
After=network-online.target

[Service]
ExecStart=%h/.local/bin/downloads daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def notify(title, body=""):
    subprocess.Popen(["notify-send", "-a", "Downloads", "-i", "folder-download", title, body],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def signal_bar():
    sys.path.insert(0, f"{HOME}/.local/lib/hypr")
    try:
        import waybarsig
        waybarsig.send(BAR_SIGNAL)
    except ImportError:
        pass


def write_private(path, text):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


# ---- the service ---------------------------------------------------------------------------------------
class Service:
    def __init__(self):
        os.makedirs(STATE, mode=0o700, exist_ok=True)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.watchers = []
        self.items = {}          # gid -> a download (what the window shows), in the order added
        self.unseen = None       # "done" / "failed": the bar keeps an icon until the window has been opened
        self.bar_text = None
        self.settle = 0          # reads left after a request: aria2 takes a moment (a cancel of a paused download)
        try:
            with open(os.path.join(STATE, "items.json")) as f:
                for it in json.load(f):
                    self.items[it["gid"]] = it
        except (OSError, ValueError, KeyError):
            pass

    # -- aria2 --
    def start_aria2(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.secret = secrets.token_hex(16)
        session = os.path.join(STATE, "session")
        if not os.path.exists(session):
            write_private(session, "")
        os.makedirs(DIR, exist_ok=True)
        conf = os.path.join(STATE, "aria2.conf")   # the secret in a private file: a command line is public
        write_private(conf, f"rpc-secret={self.secret}\n")
        self.aria2 = subprocess.Popen(
            ["aria2c", f"--conf-path={conf}", "--enable-rpc", "--rpc-listen-all=false", f"--rpc-listen-port={self.port}",
             f"--dir={DIR}", f"--input-file={session}", f"--save-session={session}",
             f"--stop-with-process={os.getpid()}", *ARIA2],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        for _ in range(100):
            try:
                self.rpc("getVersion")
                return
            except OSError:
                if self.aria2.poll() is not None:
                    break
                time.sleep(0.1)
        raise SystemExit("downloads: aria2c didn't start")

    def rpc(self, method, *params):
        body = json.dumps({"jsonrpc": "2.0", "id": "1", "method": f"aria2.{method}",
                           "params": [f"token:{self.secret}", *params]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/jsonrpc", body,
                                     {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)["result"]
        except urllib.error.HTTPError as e:   # aria2's error (an unknown gid…): its message
            try:
                raise RuntimeError(json.load(e)["error"]["message"]) from None
            except (ValueError, KeyError):
                raise RuntimeError(str(e)) from None

    # -- the downloads --
    def add(self, m):
        """A download from a request: the link, and from Chrome its name, referrer, cookies and agent."""
        opts = {}
        headers = [f"Cookie: {m['cookie']}"] if m.get("cookie") else []
        if m.get("agent"):
            headers.append(f"User-Agent: {m['agent']}")
        if headers:
            opts["header"] = headers
        if m.get("referrer"):
            opts["referer"] = m["referrer"]
        if m.get("name"):   # Chrome's name (from the server's Content-Disposition, decoded): aria2's can be raw
            opts["out"] = os.path.basename(m["name"])
        return self.start(m["url"], opts, m.get("source", "link"), m.get("size") or 0)

    def start(self, url, opts, source, size=0):
        url = url.strip()
        if urllib.parse.urlsplit(url).scheme not in ("http", "https", "ftp", "sftp", "magnet"):
            raise RuntimeError(f"not a link aria2 can download: {url[:80]}")
        gid = self.rpc("addUri", [url], opts)
        with self.lock:
            self.items[gid] = {"gid": gid, "url": url, "name": opts.get("out") or self.name_from_url(url),
                               "path": None, "status": "waiting", "total": size or 0, "done": 0, "speed": 0,
                               "conns": 0, "error": None, "added": time.time(), "finished": None,
                               "source": source, "opts": opts}   # opts: to retry it the same way
        self.save()
        self.wake.set()
        self.emit(self.snapshot())
        return gid

    @staticmethod
    def name_from_url(url):
        if url.startswith("magnet:"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            return q.get("dn", ["torrent"])[0]
        return urllib.parse.unquote(os.path.basename(urllib.parse.urlsplit(url).path)) or url

    def op(self, m):
        op, gid = m["op"], m.get("gid")
        it = self.items.get(gid)
        if op == "add":
            return self.add(m)
        if op == "seen":
            if self.unseen:
                self.unseen = None
                self.update_bar()
        elif op == "pause" and it:
            self.rpc("forcePause", gid)
        elif op == "resume" and it:
            self.rpc("unpause", gid)
        elif op == "pause_all":
            self.rpc("forcePauseAll")
        elif op == "resume_all":
            self.rpc("unpauseAll")
        elif op == "cancel" and it:
            it["cancel"] = True   # its partial file goes once aria2 has let go of it
            try:
                self.rpc("forceRemove", gid)
            except RuntimeError:   # aria2 no longer has it (it failed): just the files
                self.forget(gid)
        elif op == "retry" and it and it["status"] in ("error", "removed"):
            self.forget(gid, files=False)
            self.start(it["url"], it["opts"], it["source"], it["total"])
        elif op == "remove" and it and it["status"] in ("complete", "error", "removed"):
            self.forget(gid, files=False)
        elif op == "clear":
            for g in [g for g, i in self.items.items() if i["status"] in ("complete", "error", "removed")]:
                self.forget(g, files=False)
        self.settle = 3
        self.wake.set()
        self.emit(self.snapshot())

    def forget(self, gid, files=True):
        with self.lock:
            it = self.items.pop(gid, None)
        if it and files and it.get("path") and it["status"] != "complete":
            for p in (it["path"], it["path"] + ".aria2"):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        self.save()

    def save(self):
        with self.lock:
            items = list(self.items.values())
        done = [i for i in items if i["status"] in ("complete", "error", "removed")]
        drop = {i["gid"] for i in done[:-KEEP]} if len(done) > KEEP else set()
        write_private(os.path.join(STATE, "items.json"), json.dumps([i for i in items if i["gid"] not in drop]))

    def poll(self):
        """Read aria2's state for every download; True while one is moving."""
        calls = [{"methodName": "aria2.tellActive", "params": [f"token:{self.secret}", KEYS]},
                 {"methodName": "aria2.tellWaiting", "params": [f"token:{self.secret}", 0, 1000, KEYS]},
                 {"methodName": "aria2.tellStopped", "params": [f"token:{self.secret}", 0, 1000, KEYS]}]
        body = json.dumps({"jsonrpc": "2.0", "id": "p", "method": "system.multicall", "params": [calls]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/jsonrpc", body,
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            active, waiting, stopped = (x[0] for x in json.load(r)["result"])
        changed, moving = False, False
        for d in active + waiting + stopped:
            gid = d["gid"]
            with self.lock:
                it = self.items.get(gid)
                if it is None:   # resumed from the session after a restart, without our record: adopt it
                    it = self.items[gid] = {"gid": gid, "url": "", "name": "", "path": None, "status": "waiting",
                                            "total": 0, "done": 0, "speed": 0, "conns": 0, "error": None,
                                            "added": time.time(), "finished": None, "source": "link", "opts": {}}
                before = it["status"]
                f = (d.get("files") or [{}])[0]
                if f.get("path"):
                    it["path"] = f["path"]
                    it["name"] = os.path.basename(f["path"])
                elif not it["url"] and f.get("uris"):
                    it["url"] = f["uris"][0]["uri"]
                    it["name"] = it["name"] or self.name_from_url(it["url"])
                if d.get("bittorrent", {}).get("info", {}).get("name"):
                    it["name"] = d["bittorrent"]["info"]["name"]
                it["total"] = int(d.get("totalLength") or 0) or it["total"]
                it["done"] = int(d.get("completedLength") or 0)
                it["speed"] = int(d.get("downloadSpeed") or 0)
                it["conns"] = int(d.get("connections") or 0)
                it["status"] = d["status"]
                if d["status"] == "error":
                    it["error"] = ERRORS.get(d.get("errorCode"), d.get("errorMessage")
                                             or f"aria2 error {d.get('errorCode')}")
                if d["status"] in ("active", "waiting"):
                    moving = True
                if before != it["status"]:
                    changed = True
                    self.status_changed(it, before, d)
        for d in stopped:   # recorded above: aria2 can let go of it
            try:
                self.rpc("removeDownloadResult", d["gid"])
            except (RuntimeError, OSError):
                pass
        with self.lock:   # cancelled, and aria2 has already let go of it: its files go
            known = {d["gid"] for d in active + waiting + stopped}
            gone = [g for g, it in self.items.items() if it.get("cancel") and g not in known]
        for g in gone:
            self.forget(g)
            changed = True
        if changed:
            self.save()
        return moving, changed

    def status_changed(self, it, before, d):
        """Called with the lock held."""
        if it["status"] == "complete":
            it["finished"], it["speed"], it["conns"] = time.time(), 0, 0
            if d.get("followedBy"):   # a .torrent / magnet's metadata: the real download follows, this goes
                it["status"] = "removed"
                return
            notify(f"Downloaded {it['name']}", it["path"] or DIR)
            self.unseen = self.unseen or "done"
        elif it["status"] == "error":
            it["finished"], it["speed"], it["conns"] = time.time(), 0, 0
            notify(f"Download failed: {it['name']}", it["error"] or "")
            self.unseen = "failed"
        elif it["status"] == "removed":
            it["finished"], it["speed"], it["conns"] = time.time(), 0, 0
            if it.get("cancel"):
                threading.Thread(target=self.forget, args=(it["gid"],), daemon=True).start()

    def follow(self):
        """While something downloads: aria2's state every TICK, to the window and the bar. Idle: waits."""
        while True:
            self.wake.clear()   # before reading: a request that comes in meanwhile still wakes the wait below
            try:
                moving, changed = self.poll()
            except (OSError, ValueError, KeyError):
                if self.aria2.poll() is not None:
                    print("downloads: aria2c stopped", flush=True)
                    os._exit(1)   # systemd starts the service (and aria2) again
                moving, changed = True, False
            if self.watchers:
                self.emit(self.snapshot())
            self.update_bar()
            if moving or self.settle > 0:
                self.settle = max(self.settle - 1, 0)
                self.wake.wait(TICK)
            else:
                self.wake.wait()

    # -- the bar --
    def update_bar(self):
        with self.lock:
            items = list(self.items.values())
        moving = [i for i in items if i["status"] in ("active", "waiting")]
        paused = [i for i in items if i["status"] == "paused"]
        if paused and not moving:   # still there, not going
            total = sum(i["total"] for i in paused)
            pct = sum(i["done"] for i in paused) * 100 // total if total else 0
            bar = {"text": f"{G_PAUSE} {pct}%", "class": "paused",
                   "tooltip": f"{len(paused)} paused\nSuper + Ctrl + J"}
        elif moving:
            total = sum(i["total"] for i in moving)
            pct = sum(i["done"] for i in moving) * 100 // total if total else 0
            speed = sum(i["speed"] for i in moving)
            n = len(moving)
            bar = {"text": f"{G_DOWN} {pct}%", "class": "active",
                   "tooltip": f"{n} download{'s' if n > 1 else ''}  ·  {human(speed)}/s\nSuper + Ctrl + J"}
        elif self.unseen == "failed":
            bar = {"text": G_FAIL, "class": "failed", "tooltip": "A download failed\nSuper + Ctrl + J"}
        elif self.unseen == "done":
            bar = {"text": G_DONE, "class": "done", "tooltip": "Downloads finished\nSuper + Ctrl + J"}
        else:
            bar = {"text": ""}
        text = json.dumps(bar)
        if text != self.bar_text:   # waybar is woken only when what it shows changes
            self.bar_text = text
            with open(BAR, "w") as f:
                f.write(text + "\n")
            signal_bar()

    # -- the window --
    def snapshot(self):
        with self.lock:
            items = [{k: v for k, v in i.items() if k != "opts"} for i in self.items.values()]
        return {"ev": "state", "items": items, "dir": DIR}

    def emit(self, ev):
        line = (json.dumps(ev) + "\n").encode()
        with self.lock:
            watchers = list(self.watchers)
        for conn in watchers:
            try:
                conn.sendall(line)
            except OSError:
                with self.lock:
                    if conn in self.watchers:
                        self.watchers.remove(conn)

    def serve_window(self):
        try:
            os.unlink(SOCK)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCK)
        os.chmod(SOCK, 0o600)
        srv.listen(8)
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self.window_conn, args=(conn,), daemon=True).start()

    def window_conn(self, conn):
        """A connection: the window (stays, gets every state) or a one-off request (the native host, `add`)."""
        watching = False
        try:
            f = conn.makefile("rb")
            for line in f:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("op") == "watch":
                    watching = True
                    with self.lock:
                        self.watchers.append(conn)
                    conn.sendall((json.dumps(self.snapshot()) + "\n").encode())
                    continue
                try:
                    reply = {"ok": True, "gid": self.op(m)}
                except (RuntimeError, OSError, KeyError, TypeError, ValueError) as e:
                    reply = {"ok": False, "error": str(e)}
                    if watching:
                        conn.sendall((json.dumps({"ev": "error", "msg": str(e)}) + "\n").encode())
                if not watching:
                    conn.sendall((json.dumps(reply) + "\n").encode())
        except OSError:
            pass
        finally:
            with self.lock:
                if conn in self.watchers:
                    self.watchers.remove(conn)
            conn.close()

    def run(self):
        self.start_aria2()
        with self.lock:   # what was moving when the service stopped is in aria2's session again
            for it in self.items.values():
                if it["status"] == "active":
                    it["status"] = "waiting"
            over = {g for g, it in self.items.items() if it["status"] in ("complete", "error", "removed")}
        # aria2's session keeps failed downloads too: one already over here would start again (a failed one came
        # back and kept the bar on "0 B/s"), so aria2 lets go of it
        there = self.rpc("tellWaiting", 0, 1000, ["gid"]) + self.rpc("tellActive", ["gid"])
        with self.lock:   # and ours it didn't bring back: lost (a crash before its session was saved)
            for it in self.items.values():
                if it["status"] in ("waiting", "paused") and it["gid"] not in {d["gid"] for d in there}:
                    it["status"], it["error"] = "error", "lost (the service stopped before aria2 saved it)"
        for d in there:
            if d["gid"] in over:
                for call in ("forceRemove", "removeDownloadResult"):
                    try:
                        self.rpc(call, d["gid"])
                    except RuntimeError:
                        pass
        threading.Thread(target=self.serve_window, daemon=True).start()
        threading.Thread(target=serve_extension, daemon=True).start()
        print(f"downloads: aria2 on 127.0.0.1:{self.port}, into {DIR}", flush=True)
        self.follow()


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# ---- the Chrome extension ------------------------------------------------------------------------------
class ExtHandler(http.server.BaseHTTPRequestHandler):
    """Chrome's policy install and its update checks: the update manifest and the packed extension, nothing
    else; bound to 127.0.0.1."""
    FILES = {"/update.xml": ("update.xml", "text/xml"), "/downloads.crx": ("downloads.crx",
                                                                           "application/x-chrome-extension")}

    def do_GET(self):
        name, kind = self.FILES.get(urllib.parse.urlsplit(self.path).path, (None, None))
        try:
            with open(os.path.join(EXT_DIR, name or "-"), "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def serve_extension():
    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    try:
        Server(("127.0.0.1", EXT_PORT), ExtHandler).serve_forever()
    except OSError as e:
        print(f"downloads: can't serve the Chrome extension on {EXT_PORT}: {e}", flush=True)


def extension_id(key):
    """Chrome's id for an extension signed with this key: the public key's SHA-256, first 32 hex digits as a–p."""
    der = subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-outform", "DER"], check=True,
                         capture_output=True).stdout
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32])


def pack_extension():
    """The extension (chrome/) packed with this PC's key, again only when its files changed; the update manifest
    beside it. Its id: what the policy (root-setup.sh) and the native host allow."""
    os.makedirs(EXT_DIR, mode=0o700, exist_ok=True)
    key = os.path.join(EXT_DIR, "key.pem")
    if not os.path.exists(key):
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", key],
                       check=True, capture_output=True)
        os.chmod(key, 0o600)
    ext_id = extension_id(key)
    digest = hashlib.sha256()
    for name in sorted(os.listdir(EXT_SRC)):
        with open(os.path.join(EXT_SRC, name), "rb") as f:
            digest.update(name.encode() + b"\0" + f.read())
    stamp, crx = os.path.join(EXT_DIR, "packed"), os.path.join(EXT_DIR, "downloads.crx")
    try:
        same = open(stamp).read() == digest.hexdigest() and os.path.exists(crx)
    except OSError:
        same = False
    chrome = shutil.which("google-chrome-stable") or shutil.which("google-chrome")
    if not same and chrome:
        work = os.path.join(EXT_DIR, "src")
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(EXT_SRC, work)
        subprocess.run([chrome, f"--pack-extension={work}", f"--pack-extension-key={key}", "--no-message-box"],
                       capture_output=True, timeout=60)
        shutil.move(work + ".crx", crx)
        shutil.rmtree(work, ignore_errors=True)
        with open(stamp, "w") as f:
            f.write(digest.hexdigest())
    with open(os.path.join(EXT_SRC, "manifest.json")) as f:
        version = json.load(f)["version"]
    with open(os.path.join(EXT_DIR, "update.xml"), "w") as f:
        f.write(f"<?xml version='1.0' encoding='UTF-8'?>\n"
                f"<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>\n"
                f"  <app appid='{ext_id}'>\n"
                f"    <updatecheck codebase='http://127.0.0.1:{EXT_PORT}/downloads.crx' version='{version}'/>\n"
                f"  </app>\n</gupdate>\n")
    with open(os.path.join(EXT_DIR, "id"), "w") as f:
        f.write(ext_id + "\n")
    os.makedirs(os.path.dirname(HOST_MANIFEST), exist_ok=True)
    with open(HOST_MANIFEST, "w") as f:
        json.dump({"name": HOST_NAME, "description": "Hands Chrome's downloads to the download manager",
                   "path": os.path.join(HOME, ".local/lib/downloads/native-host.py"), "type": "stdio",
                   "allowed_origins": [f"chrome-extension://{ext_id}/"]}, f, indent=2)
    return ext_id, chrome is not None


# ---- the command ---------------------------------------------------------------------------------------
def request(msg):
    """One request to the service: its reply, or None if it isn't running."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(15)
            s.connect(SOCK)
            s.sendall((json.dumps(msg) + "\n").encode())
            return json.loads(s.makefile().readline())
    except (OSError, ValueError):
        return None


def enable():
    os.makedirs(UNITS, exist_ok=True)
    path = os.path.join(UNITS, "downloads.service")
    try:
        same = open(path).read() == SERVICE
    except OSError:
        same = False
    if not same:   # install.sh runs this on every dotsync: systemd is reloaded only for a changed unit
        with open(path, "w") as f:
            f.write(SERVICE)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if subprocess.run(["systemctl", "--user", "is-enabled", "-q", "downloads.service"]).returncode:
        subprocess.run(["systemctl", "--user", "enable", "--now", "downloads.service"], check=True)
    elif not same:
        subprocess.run(["systemctl", "--user", "restart", "downloads.service"])
    ext_id, packed = pack_extension()
    print(f"downloads: into {DIR}; Chrome extension {ext_id}" + ("" if packed else " (no Chrome here: not packed)"))


def status():
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(SOCK)
            s.sendall(b'{"op": "watch"}\n')
            state = json.loads(s.makefile().readline())
    except (OSError, ValueError):
        print("downloads: the service isn't running (systemctl --user start downloads)")
        return 1
    items = state["items"]
    for i in items:
        pct = f"{i['done'] * 100 // i['total']:3d} %" if i["total"] else "  ? %"
        speed = f"  {human(i['speed'])}/s" if i["status"] == "active" else ""
        print(f"{i['status']:9} {pct}  {i['name']}{speed}")
    if not items:
        print("nothing downloading")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "daemon":
        Service().run()
    elif cmd == "enable":
        enable()
    elif cmd == "disable":
        subprocess.run(["systemctl", "--user", "disable", "--now", "downloads.service"])
    elif cmd == "status":
        return status()
    elif cmd == "add" and len(argv) > 2:
        for url in argv[2:]:
            reply = request({"op": "add", "url": url})
            if not reply or not reply.get("ok"):
                print(f"downloads: {reply['error'] if reply else 'the service is not running'}", file=sys.stderr)
                return 1
    else:
        print("usage: downloads daemon | enable | disable | status | add <url>…", file=sys.stderr)
        return 2
    return 0
