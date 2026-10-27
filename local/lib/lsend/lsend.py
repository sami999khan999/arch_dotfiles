# lsend.py — LocalSend, our own: send files and text to a phone (or any LocalSend device) on the same
# network, and receive from it. Speaks LocalSend's protocol v2 (https://github.com/localsend/protocol): the
# official app on the phone sees this PC like any other LocalSend device. No LocalSend package needed.
#   lsend daemon            the service (lsend.service): announces this PC, receives, sends for the window
#   lsend enable|disable    the user service (setup/install.sh enables it)
#   lsend status            this PC's name, the devices seen
#   lsend send <file>…      opens the window with these files picked (Files' "Send to phone")
# A phone without the app: the window's code opens web.html (beside this file) in its browser, from this same
# HTTPS server; while that page is open the phone is a device like the others ("browser"). Sending to it
# offers the files on the page (Save / Decline), each fetched with a one-time key (so its download list keeps
# no address that works again); what it sends is taken without asking (only who scanned this PC's code has
# the page), then the same upload as the app's.
# Safe by default: HTTPS with this PC's own certificate (~/.local/share/lsend, never in the repo); every
# transfer to this PC is asked first in the window; only private-network addresses are answered; a
# device's certificate must match the fingerprint it announced before anything is sent to it; uploads
# need the session's per-file token from the address that asked; names can't climb out of ~/Downloads.
# The window (local/lib/panels/lsendgui.py) talks to the service over a Unix socket, JSON lines:
# it sends {"op": …} requests and gets every event; nothing polls (blocking sockets, no timers).
import hashlib, http.client, http.server, ipaddress, json, mimetypes, os, secrets, socket, socketserver
import queue, ssl, struct, subprocess, sys, threading, time, urllib.parse
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
DATA = os.path.join(os.environ.get("XDG_DATA_HOME") or f"{HOME}/.local/share", "lsend")
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "lsend.sock")
DOWNLOADS = f"{HOME}/Downloads"
UNITS = f"{HOME}/.config/systemd/user"
GUI = f"{HOME}/.local/lib/panels/lsendgui.py"
GROUP, PORT = "224.0.0.167", 53317
API = "/api/localsend/v2"
WEB_PAGE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "web.html")
KEEPALIVE = 20               # seconds between a browser page's pings: a closed page is noticed by them
ASK_TIMEOUT = 90             # seconds an incoming request waits for Accept / Decline
SEND_ASK_TIMEOUT = 120       # seconds we wait for the phone's user to accept ours
PROGRESS_EVERY = 0.25        # seconds between progress events
SERVICE = """[Unit]
Description=lsend: LocalSend — send and receive files with the phone (see lsend)
After=network-online.target

[Service]
ExecStart=%h/.local/bin/lsend daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


# ---- this PC -------------------------------------------------------------------------------------------
def certificate():
    """This PC's key and self-signed certificate (made once), and the certificate's fingerprint."""
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    key, cert = os.path.join(DATA, "key.pem"), os.path.join(DATA, "cert.pem")
    if not (os.path.exists(key) and os.path.exists(cert)):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", key, "-out", cert, "-days", "3650", "-subj", "/CN=LocalSend User"],
                       check=True, capture_output=True)
        os.chmod(key, 0o600)
    with open(cert) as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    return key, cert, hashlib.sha256(der).hexdigest().upper()


def alias():
    """What the phone shows for this PC: the hostname, unless lsend's settings name it."""
    try:
        with open(os.path.join(DATA, "settings.json")) as f:
            return json.load(f).get("alias") or socket.gethostname()
    except (OSError, ValueError):
        return socket.gethostname()


def private(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private and not a.is_loopback or a.is_link_local
    except ValueError:
        return False


def fingerprints_of(der):
    """What a device may have announced for this certificate: LocalSend hashes the certificate; which
    encoding it hashes isn't pinned down by the protocol, so both."""
    pem = ssl.DER_cert_to_PEM_cert(der)
    return {hashlib.sha256(der).hexdigest().upper(), hashlib.sha256(pem.encode()).hexdigest().upper()}


def safe_name(name):
    """A received file's path under the download folder: its own folders kept (a folder sent whole), no way
    out of it (no absolute path, no ..), nothing hidden by a leading dot."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    parts = [p.lstrip(".") or "file" for p in parts] or ["file"]
    return os.path.join(*parts)


def unique(path):
    """path, or "name (2).ext" … if that's taken."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


def lan_ip():
    """This PC's address on the network the phone is on (the default route's), if it's a private one."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))   # no packet is sent: it only picks the route
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return ip if private(ip) else None


def web_token():
    """The browser page's code: kept in $XDG_RUNTIME_DIR (in memory, gone at logout), so a service restart
    doesn't shut out a page that's open."""
    path = os.path.join(os.path.dirname(SOCK), "lsend-web.token")
    try:
        with open(path) as f:
            if token := f.read().strip():
                return token
    except OSError:
        pass
    token = secrets.token_urlsafe(18)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


def notify(title, body=""):
    subprocess.Popen(["notify-send", "-a", "LocalSend", "-i", "folder-download", title, body],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---- the service ---------------------------------------------------------------------------------------
class Service:
    def __init__(self):
        self.key, self.cert, self.fingerprint = certificate()
        self.lock = threading.Lock()
        self.devices = {}        # fingerprint -> device (its announcement + ip, seen)
        self.incoming = {}       # session id -> {from, files, state, tokens, event, answer…}
        self.outgoing = {}       # job id -> {device, files, state…}
        self.watchers = []       # the window's connections
        self.web_token = web_token()   # in the code the window shows: opens the browser page
        self.pages = {}          # browser device fingerprint -> {queue (its events), offers: fid -> offer}
        self.client_ctx = ssl.create_default_context()
        # a device's certificate is self-signed: no CA can vouch for it; it's checked against the
        # fingerprint the device announced instead (pinned), before anything is sent
        self.client_ctx.check_hostname = False
        self.client_ctx.verify_mode = ssl.CERT_NONE
        # LocalSend asks the sender for its certificate too (mutual TLS) and checks it against the fingerprint
        # we announced: without one the phone ends the handshake (TLSV13_ALERT_CERTIFICATE_REQUIRED)
        self.client_ctx.load_cert_chain(self.cert, self.key)

    def info(self):
        return {"alias": alias(), "version": "2.1", "deviceModel": "Linux", "deviceType": "desktop",
                "fingerprint": self.fingerprint, "port": PORT, "protocol": "https", "download": False}

    # -- events to the window --
    def emit(self, ev):
        line = (json.dumps(ev) + "\n").encode()
        with self.lock:
            watchers = list(self.watchers)
        for conn in watchers:
            try:
                conn.sendall(line)
            except OSError:
                self.drop_watcher(conn)

    def drop_watcher(self, conn):
        with self.lock:
            if conn in self.watchers:
                self.watchers.remove(conn)

    def snapshot(self):
        with self.lock:
            ip = lan_ip()
            return {"ev": "state", "me": {"alias": alias(), "fingerprint": self.fingerprint,
                                          "web": f"https://{ip}:{PORT}/#{self.web_token}" if ip else None},
                    "devices": list(self.devices.values()),
                    "incoming": [self.public_session(s) for s in self.incoming.values()],
                    "outgoing": [dict(j) for j in self.outgoing.values()]}

    @staticmethod
    def public_session(s):
        return {k: s[k] for k in ("id", "from", "files", "state", "done_bytes", "total", "saved", "text", "error")
                if k in s}

    def devices_changed(self):
        with self.lock:
            devices = list(self.devices.values())
        self.emit({"ev": "devices", "devices": devices})

    def seen(self, info, ip):
        fp = str(info.get("fingerprint", "")).upper()
        if not fp or fp == self.fingerprint or not private(ip):
            return
        dev = {"fingerprint": fp, "alias": str(info.get("alias") or "Unknown")[:64],
               "model": str(info.get("deviceModel") or "")[:64], "type": str(info.get("deviceType") or "mobile"),
               "ip": ip, "port": int(info.get("port") or PORT), "protocol": info.get("protocol") or "https",
               "seen": time.time()}
        with self.lock:
            old = self.devices.get(fp)
            self.devices[fp] = dev
        if not old or any(old[k] != dev[k] for k in ("alias", "ip", "port", "model")):
            self.devices_changed()

    # -- discovery: multicast --
    def multicast_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", PORT))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton("0.0.0.0")))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)   # this network only
        return s

    def announce(self, reply=False):
        msg = dict(self.info(), announce=not reply, announcement=not reply)
        try:
            self.udp.sendto(json.dumps(msg).encode(), (GROUP, PORT))
        except OSError:
            pass

    def listen_multicast(self):
        while True:
            try:
                data, (ip, _port) = self.udp.recvfrom(65535)
                msg = json.loads(data)
            except (OSError, ValueError):
                continue
            if not isinstance(msg, dict) or str(msg.get("fingerprint", "")).upper() == self.fingerprint:
                continue
            self.seen(msg, ip)
            if msg.get("announce") or msg.get("announcement"):
                # answer it: over HTTP to the device (as LocalSend does), else on the group
                threading.Thread(target=self.register_with, args=(msg, ip), daemon=True).start()

    def register_with(self, msg, ip):
        dev = {"ip": ip, "port": int(msg.get("port") or PORT), "protocol": msg.get("protocol") or "https",
               "fingerprint": str(msg.get("fingerprint", "")).upper()}
        try:
            reply = self.request(dev, "POST", f"{API}/register", self.info(), timeout=3)
            if isinstance(reply, dict):
                self.seen(reply, ip)
        except (OSError, ValueError, http.client.HTTPException, PermissionError):
            self.announce(reply=True)

    def scan(self):
        """Look for devices now: an announcement on the group, and (some routers drop multicast) a knock on
        every address of this PC's networks."""
        self.announce()
        threading.Thread(target=self.sweep, daemon=True).start()

    def sweep(self):
        nets = set()
        try:
            out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"], capture_output=True,
                                 text=True).stdout
            for line in out.splitlines():
                net = ipaddress.ip_interface(line.split()[3]).network
                if net.is_private and net.prefixlen >= 22 and not line.split()[1].startswith(("docker", "br-", "veth")):
                    nets.add(net)
        except (OSError, ValueError, IndexError):
            return
        mine = {line.split()[3].split("/")[0] for line in out.splitlines() if len(line.split()) > 3}
        hosts = [str(h) for n in nets for h in n.hosts() if str(h) not in mine]
        self.emit({"ev": "scanning", "on": True})

        def knock(ip):
            try:
                with socket.create_connection((ip, PORT), timeout=0.4):
                    pass
            except OSError:
                return
            try:   # something listens on LocalSend's port: ask who it is
                reply = self.request({"ip": ip, "port": PORT, "protocol": "https"}, "POST", f"{API}/register",
                                     self.info(), timeout=3, pin=False)
                if isinstance(reply, dict):
                    self.seen(reply, ip)
            except (OSError, ValueError, http.client.HTTPException):
                pass
        threads = []
        for ip in hosts:
            t = threading.Thread(target=knock, args=(ip,), daemon=True)
            t.start()
            threads.append(t)
            while sum(t.is_alive() for t in threads) >= 64:
                time.sleep(0.02)
        for t in threads:
            t.join()
        self.emit({"ev": "scanning", "on": False})

    # -- talking to a device --
    def connect(self, dev, timeout, pin=True):
        if dev.get("protocol") == "http":
            return http.client.HTTPConnection(dev["ip"], dev["port"], timeout=timeout)
        conn = http.client.HTTPSConnection(dev["ip"], dev["port"], timeout=timeout, context=self.client_ctx)
        conn.connect()
        if pin:
            der = conn.sock.getpeercert(binary_form=True)
            if not der or dev.get("fingerprint", "").upper() not in fingerprints_of(der):
                conn.close()
                raise PermissionError("its certificate doesn't match the device that announced itself")
        return conn

    def request(self, dev, method, path, body=None, timeout=10, pin=True):
        conn = self.connect(dev, timeout, pin)
        try:
            data = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
            r = conn.getresponse()
            text = r.read()
            if r.status >= 400:
                raise http.client.HTTPException(f"HTTP {r.status}")
            return json.loads(text) if text else None
        finally:
            conn.close()

    # -- sending (for the window) --
    def send(self, fingerprint, paths=(), text=None):
        with self.lock:
            dev = self.devices.get(str(fingerprint).upper())
        if not dev:
            self.emit({"ev": "error", "msg": "that device isn't around any more: scan again"})
            return
        files, sources = {}, {}
        if text is not None:
            fid = secrets.token_hex(8)
            data = text.encode()
            files[fid] = {"id": fid, "fileName": "message.txt", "size": len(data), "fileType": "text/plain",
                          "preview": text}
            sources[fid] = data
        for p in paths:
            for path, rel in walk(p):
                fid = secrets.token_hex(8)
                st = os.stat(path)
                files[fid] = {"id": fid, "fileName": rel, "size": st.st_size,
                              "fileType": mimetypes.guess_type(path)[0] or "application/octet-stream",
                              "metadata": {"modified": iso(st.st_mtime), "accessed": iso(st.st_atime)}}
                sources[fid] = path
        if not files:
            return
        web = dev.get("protocol") == "web"
        job = {"id": secrets.token_hex(6), "to": dev["alias"], "fingerprint": dev["fingerprint"],
               "files": [{"name": f["fileName"], "size": f["size"]} for f in files.values()],
               "total": sum(f["size"] for f in files.values()), "done_bytes": 0, "state": "asking",
               "text": text}
        with self.lock:
            self.outgoing[job["id"]] = job
        self.emit({"ev": "outgoing", "job": dict(job)})
        if web:
            self.offer(job, dev, files, sources)
        else:
            threading.Thread(target=self.send_job, args=(job, dev, files, sources), daemon=True).start()

    def send_job(self, job, dev, files, sources):
        def update(**kw):
            job.update(kw)
            self.emit({"ev": "outgoing", "job": dict(job)})
        try:
            conn = self.connect(dev, SEND_ASK_TIMEOUT)
            try:
                body = json.dumps({"info": self.info(), "files": files}).encode()
                conn.request("POST", f"{API}/prepare-upload", body=body, headers={"Content-Type": "application/json"})
                r = conn.getresponse()
                reply = r.read()
            finally:
                conn.close()
            if r.status == 204:   # a text the phone just showed: nothing to upload
                update(state="done", done_bytes=job["total"])
                return
            if r.status != 200:
                why = {401: "it wants a PIN", 403: "declined on the phone", 409: "the phone is busy with another transfer",
                       429: "too many requests: wait a moment"}.get(r.status, f"HTTP {r.status}")
                update(state="failed", error=why)
                return
            session = json.loads(reply)
            sid, tokens = session["sessionId"], session.get("files", {})
            update(state="sending", session=sid)
            last = [0.0]

            def progress(n):
                job["done_bytes"] += n
                if time.time() - last[0] > PROGRESS_EVERY:
                    last[0] = time.time()
                    self.emit({"ev": "outgoing", "job": dict(job)})
            for fid, token in tokens.items():
                if job.get("cancel"):
                    break
                q = urllib.parse.urlencode({"sessionId": sid, "fileId": fid, "token": token})
                src = sources[fid]
                conn = self.connect(dev, 60)
                try:
                    if isinstance(src, bytes):
                        conn.request("POST", f"{API}/upload?{q}", body=src)
                        progress(len(src))
                    else:
                        with open(src, "rb") as f:
                            conn.request("POST", f"{API}/upload?{q}", body=Counting(f, progress, job),
                                         headers={"Content-Length": str(files[fid]["size"]),
                                                  "Content-Type": "application/octet-stream"})
                    r = conn.getresponse()
                    r.read()
                finally:
                    conn.close()
                if r.status != 200:
                    raise http.client.HTTPException(f"the phone refused {files[fid]['fileName']} (HTTP {r.status})")
            if job.get("cancel"):
                try:
                    self.request(dev, "POST", f"{API}/cancel?sessionId={urllib.parse.quote(sid)}", timeout=5)
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                update(state="cancelled")
            else:
                update(state="done", done_bytes=job["total"])
        except PermissionError as e:
            update(state="failed", error=str(e))
        except (OSError, ValueError, KeyError, http.client.HTTPException) as e:
            update(state="failed", error=str(e) or type(e).__name__)

    # -- a phone's browser (web.html) --
    def web_ok(self, token):
        return secrets.compare_digest(str(token or "").encode(), self.web_token.encode())

    def page_open(self, page, name, ip, wfile):
        """A page's event stream (Server-Sent Events): it's a device while this runs. Pings every
        KEEPALIVE seconds are how a closed page is noticed (the write fails)."""
        fp = f"WEB-{page}"
        q = queue.Queue()
        with self.lock:
            self.pages[fp] = {"queue": q, "offers": {}}
            self.devices[fp] = {"fingerprint": fp, "alias": str(name or "Phone")[:64], "model": "browser",
                                "type": "browser", "ip": ip, "port": PORT, "protocol": "web", "seen": time.time()}
        self.devices_changed()
        try:
            wfile.write(f"data: {json.dumps({'type': 'hello', 'pc': alias()})}\n\n".encode())
            wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=KEEPALIVE)
                except queue.Empty:
                    ev = None
                if ev == "bye":
                    break
                wfile.write(f"data: {json.dumps(ev)}\n\n".encode() if ev else b": ping\n\n")
                wfile.flush()
        except OSError:
            pass
        finally:
            with self.lock:
                page_ = self.pages.pop(fp, None)
                self.devices.pop(fp, None)
            for o in (page_ or {}).get("offers", {}).values():   # what it never saved: failed, not left asking
                if o["job"]["state"] in ("asking", "sending"):
                    o["job"].update(state="failed", error="the page was closed")
                    self.emit({"ev": "outgoing", "job": dict(o["job"])})
            self.devices_changed()

    def page_close(self, page):
        with self.lock:
            pg = self.pages.get(f"WEB-{page}")
        if pg:
            pg["queue"].put("bye")

    def offer(self, job, dev, files, sources):
        """Files for a browser page: shown there to Save or Decline; each fetched once, with its own key."""
        with self.lock:
            pg = self.pages.get(dev["fingerprint"])
        if not pg:
            job.update(state="failed", error="the page was closed")
            self.emit({"ev": "outgoing", "job": dict(job)})
            return
        shown = []
        for fid, f in files.items():
            o = {"job": job, "name": os.path.basename(f["fileName"]), "size": f["size"], "src": sources[fid],
                 "key": secrets.token_urlsafe(18), "done": False}
            pg["offers"][fid] = o
            shown.append({"id": fid, "name": o["name"], "size": o["size"], "key": o["key"]})
        pg["queue"].put({"type": "offer", "job": job["id"], "text": job.get("text"), "files": [] if job.get("text")
                         is not None else shown})

    def offer_answer(self, page, jid, accept):
        with self.lock:
            pg = self.pages.get(f"WEB-{page}")
            job = self.outgoing.get(jid)
        if not pg or not job or job["state"] != "asking" or job["fingerprint"] != f"WEB-{page}":
            return
        if not accept:
            job["state"] = "declined"
        elif job.get("text") is not None:
            job.update(state="done", done_bytes=job["total"])
        else:
            job["state"] = "sending"
        self.emit({"ev": "outgoing", "job": dict(job)})

    def offered(self, fid, key):
        """The offer a download asks for, if its key is right; the key works until the file has gone whole."""
        with self.lock:
            for pg in self.pages.values():
                o = pg["offers"].get(fid)
                if o and not o["done"] and o["job"]["state"] == "sending" and \
                        secrets.compare_digest(o["key"].encode(), str(key or "").encode()):
                    return o
        return None

    def served(self, o, n, whole):
        job = o["job"]
        job["done_bytes"] += n
        if whole:
            o["done"] = True
            with self.lock:
                left = [x for pg in self.pages.values() for x in pg["offers"].values() if x["job"] is job and not x["done"]]
            if not left:
                job.update(state="done", done_bytes=job["total"])
        self.emit({"ev": "outgoing", "job": dict(job)})

    # -- receiving --
    def ask(self, info, files, ip, trusted=False):
        """A device wants to send: the window asks (opened if it isn't); waits for the answer. trusted: the
        browser page, opened with the code shown on this PC, so it's this PC's user: taken without asking."""
        sid = secrets.token_hex(8)
        s = {"id": sid, "from": str(info.get("alias") or ip)[:64], "ip": ip, "state": "asking",
             "files": [{"id": f.get("id"), "name": str(f.get("fileName")), "size": int(f.get("size") or 0),
                        "type": f.get("fileType")} for f in files.values()],
             "total": sum(int(f.get("size") or 0) for f in files.values()), "done_bytes": 0,
             "answered": threading.Event(), "accept": trusted}
        if trusted:
            s["answered"].set()
        texts = [f for f in files.values() if f.get("fileType") == "text/plain" and f.get("preview")]
        if texts and len(files) == 1:
            s["text"] = str(texts[0]["preview"])
        with self.lock:   # several at once: each session has its own tokens, files and progress
            self.incoming[sid] = s
            opened = bool(self.watchers)
        if not trusted:   # an "asking" card would bring the window up for nothing
            self.emit({"ev": "incoming", "session": self.public_session(s)})
        if trusted and "text" in s and not opened:   # a message: nowhere else to read it
            notify(f"Message from {s['from']}", s["text"][:300])
        elif not opened and not trusted:
            subprocess.Popen(["uwsm", "app", "--", "python3", GUI], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            notify(f"{s['from']} wants to send you something", "Accept or decline in LocalSend (Super + Ctrl + D)")
        s["answered"].wait(ASK_TIMEOUT)
        if not s["accept"]:
            s["state"] = "declined"
            self.emit({"ev": "incoming", "session": self.public_session(s)})
            return 403, None
        if "text" in s:   # a message: the window shows it; nothing is uploaded
            s["state"] = "done"
            self.emit({"ev": "incoming", "session": self.public_session(s)})
            return 204, None
        s["tokens"] = {f["id"]: secrets.token_hex(16) for f in s["files"]}
        s["state"], s["saved"] = "receiving", []
        self.emit({"ev": "incoming", "session": self.public_session(s)})
        return 200, {"sessionId": sid, "files": s["tokens"]}

    def answer(self, sid, accept):
        s = self.incoming.get(sid)
        if s and s["state"] == "asking":
            s["accept"] = bool(accept)
            s["answered"].set()

    def receive(self, sid, fid, token, ip, stream, length):
        s = self.incoming.get(sid)
        if not s or s["state"] != "receiving" or s["ip"] != ip or \
                not secrets.compare_digest(s["tokens"].get(fid, ""), token):
            return 403
        f = next(f for f in s["files"] if f["id"] == fid)
        if f.get("received"):
            return 409
        target = unique(os.path.join(DOWNLOADS, safe_name(f["name"])))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # length None: a chunked body (the phone's app streams some uploads), read to its end
        part, last, left, got = target + ".part", 0.0, length, 0
        try:
            with open(part, "wb") as out:
                while left is None or left > 0:
                    chunk = stream.read(1 << 20 if left is None else min(left, 1 << 20))
                    if not chunk:
                        if left is None:
                            break
                        raise OSError("the transfer broke off")
                    out.write(chunk)
                    got += len(chunk)
                    if left is not None:
                        left -= len(chunk)
                    s["done_bytes"] += len(chunk)
                    if time.time() - last > PROGRESS_EVERY:
                        last = time.time()
                        self.emit({"ev": "incoming", "session": self.public_session(s)})
                    if s["state"] == "cancelled":
                        raise OSError("cancelled")
            if f["size"] and got != f["size"]:   # never keep a cut-off (or empty) file as if it came whole
                raise OSError(f"got {got} of {f['size']} bytes")
            os.replace(part, target)
        except OSError as e:
            try:
                os.unlink(part)
            except OSError:
                pass
            if s["state"] != "cancelled":
                s["state"], s["error"] = "failed", str(e)
                self.emit({"ev": "incoming", "session": self.public_session(s)})
            return 500
        f["received"] = True
        s["saved"].append(target)
        if all(x.get("received") for x in s["files"]):
            s["state"] = "done"
            n = len(s["files"])
            notify(f"{n} file{'s' if n > 1 else ''} from {s['from']}",
                   os.path.basename(target) if n == 1 else f"in {DOWNLOADS.replace(HOME, '~')}")
        self.emit({"ev": "incoming", "session": self.public_session(s)})
        return 200

    def cancel(self, sid, ip=None):
        s = self.incoming.get(sid)
        if s and (ip is None or s["ip"] == ip) and s["state"] in ("asking", "receiving"):
            s["state"] = "cancelled"
            s["answered"].set()
            self.emit({"ev": "incoming", "session": self.public_session(s)})
        job = self.outgoing.get(sid)
        if job and job["state"] in ("asking", "sending"):
            job["cancel"] = True
            if str(job["fingerprint"]).startswith("WEB-"):   # a browser's: its offer withdrawn
                job["state"] = "cancelled"
                with self.lock:
                    pg = self.pages.get(job["fingerprint"])
                if pg:
                    pg["queue"].put({"type": "withdrawn", "job": sid})
                self.emit({"ev": "outgoing", "job": dict(job)})

    def clear(self):
        """Forget finished transfers (the window's Clear)."""
        with self.lock:
            for d in (self.incoming, self.outgoing):
                for k in [k for k, v in d.items() if v["state"] not in ("asking", "receiving", "sending")]:
                    del d[k]
        self.emit(self.snapshot())

    # -- the window's socket --
    def serve_window(self):
        try:
            os.unlink(SOCK)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCK)
        os.chmod(SOCK, 0o600)
        srv.listen(4)
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self.window_conn, args=(conn,), daemon=True).start()

    def window_conn(self, conn):
        with self.lock:
            self.watchers.append(conn)
        try:
            conn.sendall((json.dumps(self.snapshot()) + "\n").encode())
            buf = b""
            while chunk := conn.recv(65536):
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    try:
                        self.window_op(json.loads(line))
                    except (ValueError, KeyError, TypeError):
                        pass
        except OSError:
            pass
        finally:
            self.drop_watcher(conn)
            conn.close()

    def window_op(self, m):
        op = m["op"]
        if op == "scan":
            self.scan()
        elif op == "send":
            self.send(m["device"], m.get("paths", []), m.get("text"))
        elif op == "answer":
            self.answer(m["id"], m["accept"])
        elif op == "cancel":
            self.cancel(m["id"])
        elif op == "clear":
            self.clear()

    def run(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert, self.key)
        server = Server(("", PORT), Handler)
        server.service = self
        # the handshake happens in each request's thread (Handler.setup): a stalled phone can't hold up the rest
        server.socket = ctx.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
        self.udp = self.multicast_socket()
        threading.Thread(target=self.listen_multicast, daemon=True).start()
        threading.Thread(target=self.serve_window, daemon=True).start()
        self.announce()
        print(f"lsend: {alias()} on port {PORT} (fingerprint {self.fingerprint[:16]}…)", flush=True)
        server.serve_forever()


class Counting:
    """A file read for an upload, counting what's gone (progress) and stopping on cancel."""

    def __init__(self, f, progress, job):
        self.f, self.progress, self.job = f, progress, job

    def read(self, n=-1):
        if self.job.get("cancel"):
            return b""
        data = self.f.read(1 << 16 if n is None or n < 0 else n)
        self.progress(len(data))
        return data


class Chunked:
    """A chunked request body (Transfer-Encoding: chunked, no Content-Length) read as a plain stream:
    b"" at its end. http.server leaves the decoding to the handler."""

    def __init__(self, f):
        self.f, self.left, self.done = f, 0, False

    def read(self, n):
        while not self.left and not self.done:
            try:
                size = int(self.f.readline(1024).split(b";", 1)[0].strip() or b"0", 16)
            except ValueError:
                raise OSError("a malformed chunk") from None
            if size == 0:
                while self.f.readline(1024).strip():   # the trailer, up to its empty line
                    pass
                self.done = True
            self.left = size
        if self.done:
            return b""
        data = self.f.read(min(n, self.left))
        if not data:
            raise OSError("the transfer broke off")
        self.left -= len(data)
        if not self.left:
            self.f.readline(1024)   # the CRLF after each chunk
        return data


def walk(path):
    """(file, name to send) for a file, or every file under a folder (named with the folder's path)."""
    path = os.path.abspath(path)
    if os.path.isfile(path):
        yield path, os.path.basename(path)
        return
    top = os.path.dirname(path)
    for root, dirs, names in os.walk(path):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.isfile(p):
                yield p, os.path.relpath(p, top)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass   # a dropped connection, a failed handshake (a port scan): nothing to log


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "lsend"
    timeout = 60

    def setup(self):
        self.request.settimeout(self.timeout)
        self.request.do_handshake()
        super().setup()

    def log_message(self, *_):
        pass

    def reply(self, status, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self, limit=1 << 20):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("too big")
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):
        svc, ip = self.server.service, self.client_address[0]
        if not private(ip):
            return self.reply(403)
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.path in (f"{API}/info", "/api/localsend/v1/info"):
            return self.reply(200, svc.info())
        if url.path == "/":   # the browser page; what it does needs the code in its address
            with open(WEB_PAGE, "rb") as f:
                return self.raw(200, f.read(), "text/html; charset=utf-8")
        if url.path == "/web/events" and svc.web_ok(q.get("t")) and q.get("page"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.request.settimeout(None)   # quiet for long stretches: the pings find a closed page
            return svc.page_open(q["page"], q.get("name"), ip, self.wfile)
        if url.path == "/web/file":
            o = svc.offered(q.get("f"), q.get("k"))
            if not o:
                return self.reply(404)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(o["size"]))
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(o['name'])}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            sent = 0
            with open(o["src"], "rb") as f:
                while data := f.read(1 << 20):
                    self.wfile.write(data)
                    sent += len(data)
                    svc.served(o, len(data), sent == o["size"])
            if not o["size"]:
                svc.served(o, 0, True)
            return
        self.reply(404)

    def raw(self, status, data, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        svc, ip = self.server.service, self.client_address[0]
        if not private(ip):
            return self.reply(403)
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        try:
            if url.path == f"{API}/register":
                svc.seen(self.body(), ip)
                return self.reply(200, svc.info())
            if url.path == f"{API}/prepare-upload":
                m = self.body(8 << 20)   # a long text's preview fits
                if not isinstance(m.get("files"), dict) or not m["files"]:
                    return self.reply(400)
                svc.seen(m.get("info") or {}, ip)
                status, body = svc.ask(m.get("info") or {}, m["files"], ip)
                return self.reply(status, body)
            if url.path == f"{API}/upload":
                stream, n = self.rfile, self.headers.get("Content-Length")
                if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                    stream, n = Chunked(self.rfile), None
                status = svc.receive(q.get("sessionId"), q.get("fileId"), q.get("token", ""), ip, stream,
                                     None if n is None else int(n))
                if status != 200:
                    self.close_connection = True   # the body may be left unread
                return self.reply(status)
            if url.path == f"{API}/cancel":
                svc.cancel(q.get("sessionId"), ip)
                return self.reply(200)
            if url.path.startswith("/web/"):   # the browser page's: all need its code
                if not svc.web_ok(q.get("t")):
                    return self.reply(403)
                if url.path == "/web/prepare":   # what it wants to send: asked like the app's
                    m = self.body(8 << 20)
                    if not isinstance(m.get("files"), dict) or not m["files"]:
                        return self.reply(400)
                    status, body = svc.ask(m.get("info") or {}, m["files"], ip, trusted=True)
                    return self.reply(status, body)
                if url.path == "/web/answer":
                    svc.offer_answer(q.get("page"), q.get("job"), q.get("accept") == "1")
                    return self.reply(200)
                if url.path == "/web/bye":
                    svc.page_close(q.get("page"))
                    return self.reply(200)
        except (ValueError, KeyError, TypeError, StopIteration):
            return self.reply(400)
        self.reply(404)


# ---- the command ---------------------------------------------------------------------------------------
def enable():
    os.makedirs(UNITS, exist_ok=True)
    path = os.path.join(UNITS, "lsend.service")
    try:
        same = open(path).read() == SERVICE
    except OSError:
        same = False
    if not same:   # install.sh runs this on every dotsync: systemd is reloaded only for a changed unit
        with open(path, "w") as f:
            f.write(SERVICE)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if subprocess.run(["systemctl", "--user", "is-enabled", "-q", "lsend.service"]).returncode:
        subprocess.run(["systemctl", "--user", "enable", "--now", "lsend.service"], check=True)
    elif not same:
        subprocess.run(["systemctl", "--user", "restart", "lsend.service"])
    print(f"lsend: receiving as {alias()} (LocalSend, port {PORT})")


def status():
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCK)
        state = json.loads(s.makefile().readline())
        s.close()
    except (OSError, ValueError):
        print("lsend: the service isn't running (systemctl --user start lsend)")
        return 1
    print(f"this PC   {state['me']['alias']}   ({state['me']['fingerprint'][:16]}…)")
    for d in state["devices"]:
        print(f"device    {d['alias']}   {d['model']}  {d['ip']}")
    if not state["devices"]:
        print("devices   none seen yet: open LocalSend on the phone, on the same Wi-Fi")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "daemon":
        Service().run()
    elif cmd == "enable":
        enable()
    elif cmd == "disable":
        subprocess.run(["systemctl", "--user", "disable", "--now", "lsend.service"])
    elif cmd == "status":
        return status()
    elif cmd == "send":
        os.execvp("python3", ["python3", GUI, *[os.path.abspath(p) for p in argv[2:]]])
    else:
        print("usage: lsend daemon | enable | disable | status | send <file>…", file=sys.stderr)
        return 2
    return 0
