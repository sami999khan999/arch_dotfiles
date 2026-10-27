# mirror.py — screen sharing with the phone, both ways, controlled from the other side, leaving nothing behind.
#   mirror share [--output NAME] [--no-sound]   this PC's screen on the phone (in the browser, no app):
#                                               watch it, touch it, type, hear it
#   mirror share --extend [--no-sound]          the phone as a second screen instead: a screen of its own
#                                               (a headless output, at the phone's size) beside this one
#   mirror share … --usb                        over the USB cable, not Wi-Fi: the page opens on the phone
#                                               by itself (USB debugging; adb reverse makes it localhost)
#   mirror phone usb | qr NAME PASSWORD | code HOST:PORT CODE
#                                               the phone's screen in a window here (scrcpy), mouse and keyboard
#                                               drive it; Wi-Fi pairs with Android's Wireless debugging
#   mirror start share|phone …   the same, as a transient user unit (mirror-share / mirror-phone): the panel
#                                (local/lib/panels/mirrorgui.py, Super + Ctrl + C) starts them this way
#   mirror stop [share|phone]    ends one, or both
#   mirror output NAME           which screen the phone sees, while sharing
#   mirror status | bar          what's on; the bar's JSON
# Nothing kept, either side. Everything of a session lives in $XDG_RUNTIME_DIR/mirror (tmpfs, 0700) and is
# deleted when it ends: the TLS certificate made for it, the one-time token, adb's key and its server.
#   PC → phone: wayvnc on a Unix socket (input on: the phone drives it), bridged by this file's HTTPS server
#     (port 53321, private networks only, one phone at a time) to a page using noVNC; the page has no
#     cookies or storage, is never cached, and takes the token out of its address once read. The sound
#     is the default output's monitor, raw PCM over a WebSocket. Stops by itself IDLE seconds without a phone.
#     Extended: the page tells its size first; the screen is made at that size, then wayvnc started on it.
#     Order matters: Hyprland 0.56.2 crashes (SEGV in CScreenshareFrame::transform) when a capture client asks
#     for a frame of a screen that's gone, so the screen is never removed, nor resized, while wayvnc runs on
#     it: wayvnc is stopped and waited for first. (Fixed upstream after 0.56.2.)
#   phone → PC: scrcpy (sound too, Android 11+) with the clipboard sync off (the phone's clipboard would land
#     in the clipboard history); a fresh adb key per session, so a phone that kept "always allow" holds a key
#     that's gone; Wireless debugging is switched off on the phone at the end.
import asyncio, base64, hashlib, ipaddress, json, mimetypes, os, secrets, shutil, signal, socket, ssl, struct
import subprocess, sys, threading, time, urllib.parse

RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "mirror")
BAR = os.path.join(os.path.dirname(RUN), "mirror-bar.json")
BAR_SIGNAL = 13
PORT = 53321
NOVNC = "/usr/share/webapps/novnc"   # the AUR package's place
HERE = os.path.dirname(os.path.realpath(__file__))
IDLE = 600                  # seconds sharing waits with no phone watching before it stops
ADB_PORT = "5038"           # our own adb server, apart from any other (Android Studio's is 5037)
SHARE_ADB_PORT = "5039"     # sharing over USB's own one, apart from the phone's (they'd fight over the phone)
PHONE_WAIT = 180            # seconds to wait for the phone to pair / connect / allow USB debugging
MDNS_GRACE = 6              # seconds mDNS gets to announce the phone's connect port before its ports are tried
QR_HINT = 20                # seconds after which a QR code no phone has answered suggests the pairing code
RATE, CHANNELS = 48000, 2   # the sound: s16le
PHONE_SCREEN = "PHONE"     # the extended screen's name (a headless output)
SCALES = (1, 1.25, 1.5, 1.6, 2, 2.4, 2.5, 3)   # what Hyprland accepts when the size divides evenly
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ---- shared bits ---------------------------------------------------------------------------------------
def notify(text):
    subprocess.Popen(["notify-send", "-a", "Mirror", "-i", "video-display", "Mirror", text],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def read_state(name):
    try:
        with open(os.path.join(RUN, name + ".json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_state(name, state):
    os.makedirs(RUN, mode=0o700, exist_ok=True)
    path = os.path.join(RUN, name + ".json")
    if state is None:
        try:
            os.remove(path)
        except OSError:
            pass
    else:
        with open(path + ".new", "w") as f:   # in tmpfs, not a dotfiles link: a rename is fine here
            json.dump(state, f)
        os.replace(path + ".new", path)
    update_bar()


def update_bar():
    """The bar's icon: shown while either way is on, amber while the phone is watching or shown here."""
    share, phone = read_state("share"), read_state("phone")
    lines, live = [], False
    if share:
        lines.append(f"This PC on the phone: {share['viewer']} is watching" if share.get("viewer")
                     else "This PC is shared: waiting for the phone")
        live = live or bool(share.get("viewer"))
    if phone:
        lines.append(f"The phone on this PC: {phone['step']}")
        live = live or phone.get("live", False)
    bar = {"text": "\U000f0989" if lines else "", "class": "live" if live else "waiting",   # nf-md-monitor_cellphone
           "tooltip": "\n".join(lines)}
    with open(BAR, "w") as f:
        json.dump(bar, f)
    try:
        sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
        import waybarsig
        waybarsig.send(BAR_SIGNAL)
    except Exception:
        pass


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
    return ip if ipaddress.ip_address(ip).is_private else None


def outputs():
    try:
        sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
        import hyprsock
        return [m["name"] for m in hyprsock.query("monitors")]
    except Exception:
        return []


# ---- this PC on the phone ------------------------------------------------------------------------------
class Share:
    def __init__(self, output, sound, extend=False, usb=False):
        self.dir = os.path.join(RUN, "share")
        self.output, self.sound, self.extend, self.usb = output, sound, extend, usb
        self.usb_step = "Starting" if usb else None
        self.adb_home = os.path.join(RUN, "share-adb")   # adb's HOME over USB: a fresh key, gone at the end
        self.adb_env = adb_env(self.adb_home, SHARE_ADB_PORT)
        self.serial = None
        self.vnc = os.path.join(self.dir, "vnc.sock")
        self.ctl = os.path.join(self.dir, "ctl.sock")
        self.size = None            # the extended screen's size, once the phone told it
        self.wayvnc = None
        self.token = secrets.token_urlsafe(18)
        self.viewer = None          # the phone's address while it's connected
        self.links = 0              # its open VNC connections (a page reload overlaps two)
        self.last_seen = time.monotonic()

    def setup(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir, mode=0o700)
        os.chmod(RUN, 0o700)
        key, cert = os.path.join(self.dir, "key.pem"), os.path.join(self.dir, "cert.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=mirror"],
                       check=True, capture_output=True)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        if self.extend:
            self.output = PHONE_SCREEN   # made once the phone says its size (hello)
        else:
            self.start_wayvnc()

    def start_wayvnc(self):
        conf = os.path.join(self.dir, "wayvnc.conf")
        open(conf, "w").close()     # not ~/.config/wayvnc: nothing of the user's applies
        cmd = ["wayvnc", "-C", conf, "-S", self.ctl, "-u", "-r", "-f", "30"]
        if self.output:
            cmd += ["-o", self.output]
        self.wayvnc = subprocess.Popen(cmd + [self.vnc])
        for _ in range(50):
            if os.path.exists(self.vnc):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("wayvnc didn't start")

    def stop_wayvnc(self):
        """wayvnc gone for good, its capture with it: only then may its screen go (see the top)."""
        if self.wayvnc:
            self.wayvnc.terminate()
            try:
                self.wayvnc.wait(3)
            except subprocess.TimeoutExpired:
                self.wayvnc.kill()
                self.wayvnc.wait()
            self.wayvnc = None
        for path in (self.vnc, self.ctl):
            try:
                os.remove(path)
            except OSError:
                pass

    def extended_screen(self, w, h):
        """The phone's screen, at its size (landscape), scaled so it reads like a small laptop's: about 720
        rows of logical pixels. Made again, wayvnc first stopped, only when the size changed."""
        w, h = max(w, h), min(w, h)
        if self.size == (w, h) and self.wayvnc and self.wayvnc.poll() is None:
            return
        self.stop_wayvnc()
        if self.size is None:
            hyprctl("output", "create", "headless", PHONE_SCREEN)
        fits = [s for s in SCALES if (w / s).is_integer() and (h / s).is_integer()] or [1]
        scale = min(fits, key=lambda s: abs(h / s - 720))
        hyprctl("eval", f'hl.monitor({{ output = "{PHONE_SCREEN}", mode = "{w}x{h}@60", '
                        f'position = "auto-right", scale = {scale} }})')
        self.size = (w, h)
        self.start_wayvnc()

    def state(self):
        ip = lan_ip()
        return {"url": f"https://{ip}:{PORT}/#{self.token}" if ip else None, "output": self.output,
                "sound": self.sound, "extend": self.extend, "usb": self.usb_step,
                "viewer": self.viewer and who(self.viewer),
                "pid": os.getpid()}

    def publish(self):
        self.output = (read_state("share") or {}).get("output", self.output)   # `mirror output` may have changed it
        write_state("share", self.state())

    # -- over USB --
    def connect_usb(self):
        """Each time the phone is plugged in (and allows USB debugging): its localhost:PORT reaches this
        server (adb reverse), and the page opens in its browser. Waits on adb, never polls."""
        shutil.rmtree(self.adb_home, ignore_errors=True)
        os.makedirs(self.adb_home, mode=0o700)
        run_adb(self.adb_env, "start-server")
        while True:
            self.set_usb_step("Plug the phone in and allow USB debugging (leave \"Always allow\" off)")
            if run_adb(self.adb_env, "wait-for-usb-device", timeout=None).returncode:
                return   # the server went: sharing is ending
            self.serial = next((l.split("\t")[0] for l in run_adb(self.adb_env, "devices").stdout.splitlines()[1:]
                                if l.endswith("\tdevice")), None)
            if not self.serial:
                time.sleep(1)
                continue
            run_adb(self.adb_env, "-s", self.serial, "reverse", f"tcp:{PORT}", f"tcp:{PORT}")
            # quoted for the phone's shell: the token follows a #
            run_adb(self.adb_env, "-s", self.serial, "shell", "am", "start", "-a", "android.intent.action.VIEW",
                    "-d", f"'https://localhost:{PORT}/#{self.token}'")
            self.set_usb_step("Opened in the phone's browser: Advanced → Proceed if it warns, then Connect")
            run_adb(self.adb_env, "-s", self.serial, "wait-for-disconnect", timeout=None)
            self.serial = None

    def set_usb_step(self, text):
        self.usb_step = text
        self.publish()

    def cleanup(self):
        if self.usb:
            if self.serial:
                run_adb(self.adb_env, "-s", self.serial, "reverse", "--remove-all", timeout=5)
            run_adb(self.adb_env, "kill-server", timeout=5)
            shutil.rmtree(self.adb_home, ignore_errors=True)
        self.stop_wayvnc()
        if self.extend and self.size:   # after wayvnc is gone, never before (see the top)
            time.sleep(0.5)   # and after Hyprland has dealt with its closed connection
            hyprctl("output", "remove", PHONE_SCREEN)
        shutil.rmtree(self.dir, ignore_errors=True)
        write_state("share", None)

    # -- the server --
    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")[0]
        try:
            if not ipaddress.ip_address(peer).is_private:
                return
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}
            path, _, query = target.partition("?")
            if method != "GET":
                return await self.reply(writer, 405, b"")
            if path in ("/vnc", "/sound", "/screen", "/hello"):
                if not secrets.compare_digest(query.partition("&")[0].encode(), f"t={self.token}".encode()):
                    return await self.reply(writer, 403, b"")
                if self.viewer and peer != self.viewer:   # one phone at a time
                    return await self.reply(writer, 409, b"")
                if path == "/hello":   # the page, before it connects: its size, for the extended screen
                    if self.extend and not self.links:
                        args = urllib.parse.parse_qs(query)
                        w, h = (min(int(args[k][0]), 8192) for k in ("w", "h"))
                        await asyncio.get_running_loop().run_in_executor(None, self.extended_screen, w, h)
                    return await self.reply(writer, 200, json.dumps({"extend": self.extend}).encode(),
                                            "application/json")
                if path == "/screen":
                    return await self.reply(writer, 200, self.next_output().encode(), "text/plain")
                if headers.get("upgrade", "").lower() != "websocket":
                    return await self.reply(writer, 400, b"")
                await self.accept_ws(writer, headers)
                if path == "/vnc":
                    await self.bridge_vnc(peer, reader, writer)
                elif self.sound:
                    await self.stream_sound(reader, writer)
                return
            await self.static(writer, path)
        except (OSError, ValueError, KeyError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError,
                ssl.SSLError):
            pass
        finally:
            writer.close()

    async def reply(self, writer, code, body, ctype="text/plain"):
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
                  409: "Conflict"}[code]
        writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\n"
                     "Connection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def static(self, writer, path):
        """The page (viewer.html beside this file) and noVNC's modules; nothing else."""
        if path == "/":
            file = os.path.join(HERE, "viewer.html")
        elif path.startswith("/novnc/"):
            file = os.path.realpath(os.path.join(NOVNC, path[len("/novnc/"):]))
            if not (file.startswith(os.path.join(NOVNC, "core") + "/") or file.startswith(os.path.join(NOVNC, "vendor") + "/")):
                return await self.reply(writer, 404, b"")
        else:
            return await self.reply(writer, 404, b"")
        try:
            with open(file, "rb") as f:
                body = f.read()
        except OSError:
            return await self.reply(writer, 404, b"")
        ctype = "text/javascript" if file.endswith(".js") else mimetypes.guess_type(file)[0] or "application/octet-stream"
        await self.reply(writer, 200, body, ctype)

    async def accept_ws(self, writer, headers):
        accept = base64.b64encode(hashlib.sha1(headers["sec-websocket-key"].encode() + WS_GUID).digest()).decode()
        extra = ""
        if "sec-websocket-protocol" in headers:   # noVNC may ask for "binary": the first one asked is taken
            extra = f"Sec-WebSocket-Protocol: {headers['sec-websocket-protocol'].split(',')[0].strip()}\r\n"
        writer.write(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {accept}\r\n{extra}\r\n".encode())
        await writer.drain()

    async def frames(self, reader, writer):
        """The phone's WebSocket messages, as bytes; pings answered, ends at a close."""
        while True:
            b0, b1 = await reader.readexactly(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", await reader.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", await reader.readexactly(8))[0]
            if n > 1 << 20:
                return
            mask = await reader.readexactly(4) if b1 & 0x80 else b"\0\0\0\0"
            data = bytes(c ^ mask[i % 4] for i, c in enumerate(await reader.readexactly(n))) if n else b""
            if op == 8:
                return
            if op == 9:
                send(writer, data, 10)
            elif op in (0, 1, 2):
                yield data

    async def bridge_vnc(self, peer, reader, writer):
        up_r, up_w = await asyncio.open_unix_connection(self.vnc)
        if not self.links:
            self.viewer = peer
            self.publish()
            notify(f"The phone ({who(peer)}) is watching and controlling this screen")
        self.links += 1

        async def down():
            while data := await up_r.read(1 << 16):
                send(writer, data)
                await writer.drain()
        task = asyncio.create_task(down())
        try:
            async for data in self.frames(reader, writer):
                up_w.write(data)
                await up_w.drain()
        finally:
            task.cancel()
            up_w.close()
            self.links -= 1
            if not self.links:
                self.viewer = None
                self.last_seen = time.monotonic()
                self.publish()
                notify("The phone stopped watching this screen")

    async def stream_sound(self, reader, writer):
        rec = await asyncio.create_subprocess_exec(
            "parec", "-d", "@DEFAULT_MONITOR@", "--raw", "--format=s16le", f"--rate={RATE}",
            f"--channels={CHANNELS}", "--latency-msec=20", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        async def down():
            chunk = RATE * CHANNELS * 2 // 50   # 20 ms
            while data := await rec.stdout.readexactly(chunk):
                send(writer, data)
                await writer.drain()
        task = asyncio.create_task(down())
        try:
            async for _ in self.frames(reader, writer):
                pass
        finally:
            task.cancel()
            rec.kill()
            await rec.wait()

    def next_output(self):
        """The page's Screen button: the next screen, in Hyprland's order."""
        names = outputs()
        if self.extend:   # its own screen: nothing to cycle
            return self.output
        self.output = (read_state("share") or {}).get("output", self.output)
        if len(names) > 1:
            self.output = names[(names.index(self.output) + 1) % len(names)] if self.output in names else names[1]
            set_output(self.output)
            self.publish()
        return self.output or ""

    async def watch(self, server):
        while True:
            await asyncio.sleep(5)
            if not self.links and time.monotonic() - self.last_seen > IDLE:
                notify("Screen sharing stopped: no phone for 10 minutes")
                server.close()
                return
            if self.wayvnc and self.wayvnc.poll() is not None:
                notify("Screen sharing stopped: wayvnc ended")
                server.close()
                return

    async def serve(self):
        server = await asyncio.start_server(self.handle, "0.0.0.0", PORT, ssl=self.ctx,
                                            ssl_handshake_timeout=15)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, server.close)
        watcher = asyncio.create_task(self.watch(server))
        self.publish()
        if self.usb:
            threading.Thread(target=self.connect_usb, daemon=True).start()
        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            pass
        watcher.cancel()

    def run(self):
        try:
            self.setup()
            asyncio.run(self.serve())
        finally:
            self.cleanup()


def send(writer, data, op=2):
    """One unmasked WebSocket frame (binary by default) to the phone."""
    n = len(data)
    head = bytes([0x80 | op, n]) if n < 126 else (bytes([0x80 | op, 126]) + struct.pack(">H", n) if n < 1 << 16
                                                   else bytes([0x80 | op, 127]) + struct.pack(">Q", n))
    writer.write(head + data)


def who(peer):
    """The phone, as the PC's notifications say it: over USB (adb reverse) it comes from localhost."""
    return "USB" if ipaddress.ip_address(peer).is_loopback else peer


def adb_env(home, port):
    """adb with its own HOME (its key) and server port: nothing of it outside this session."""
    return dict(os.environ, HOME=home, ANDROID_USER_HOME=os.path.join(home, ".android"), ANDROID_ADB_SERVER_PORT=port)


def run_adb(env, *args, timeout=20):
    try:
        return subprocess.run(["adb", *args], env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "timed out")


def hyprctl(*args):
    subprocess.run(["hyprctl", *args], capture_output=True)


def set_output(name):
    subprocess.run(["wayvncctl", "-S", os.path.join(RUN, "share", "ctl.sock"), "output-set", name],
                   capture_output=True)


# ---- the phone on this PC ------------------------------------------------------------------------------
class Phone:
    def __init__(self, how, args):
        self.how, self.args = how, args
        self.home = os.path.join(RUN, "phone")   # adb's HOME: its key, made fresh, gone at the end
        self.env = adb_env(self.home, ADB_PORT)
        self.serial = None
        self.scrcpy = None

    def step(self, text, live=False):
        print(f"mirror: {text}", flush=True)
        # the Wi-Fi pairing code stays here (tmpfs, this session only): the panel shows it again when reopened
        write_state("phone", {"how": self.how, "step": text, "live": live, "pid": os.getpid(),
                              "qr": self.args if self.how == "qr" else None})

    def adb(self, *args, timeout=20):
        return run_adb(self.env, *args, timeout=timeout)

    def services(self):
        """adb's mDNS view: [(instance, type, host:port)]."""
        out = self.adb("mdns", "services").stdout
        return [tuple(l.split("\t")[:3]) for l in out.splitlines() if l.count("\t") >= 2]

    def wait(self, find, what):
        end = time.monotonic() + PHONE_WAIT
        while time.monotonic() < end:
            if found := find():
                return found
            time.sleep(1)
        raise RuntimeError(f"no {what} after {PHONE_WAIT // 60} minutes")

    def pair(self, addr, code):
        out = self.adb("pair", addr, code, timeout=30)
        if "Successfully paired" not in out.stdout:
            raise RuntimeError((out.stdout + out.stderr).strip().splitlines()[-1] if (out.stdout + out.stderr).strip()
                               else "pairing failed")
        host, port = addr.rsplit(":", 1)
        return host, int(port)

    def connect(self, host, pair_port):
        """Wireless debugging's connect port is another than the pairing one, a random one: announced over
        mDNS, but a router that keeps multicast from Wi-Fi to the wired side (this PC's network) hides it.
        So mDNS for a few seconds, then the phone's own ports are tried."""
        self.step("Paired: connecting")
        end = time.monotonic() + MDNS_GRACE
        while time.monotonic() < end:
            for _name, kind, addr in self.services():
                if kind.startswith("_adb-tls-connect") and addr.rsplit(":", 1)[0] == host:
                    if "connected to" in self.adb("connect", addr).stdout:
                        return addr
            time.sleep(1)
        self.step("Paired: looking for the phone's debugging port")
        for port in asyncio.run(open_ports(host, exclude={pair_port})):
            addr = f"{host}:{port}"
            if "connected to" in self.adb("connect", addr, timeout=8).stdout:
                return addr
            self.adb("disconnect", addr, timeout=5)
        raise RuntimeError("the phone's debugging port wasn't found: is Wireless debugging still on?")

    def run(self):
        shutil.rmtree(self.home, ignore_errors=True)
        os.makedirs(self.home, mode=0o700)
        os.chmod(RUN, 0o700)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        try:
            self.adb("start-server")
            if self.how == "usb":
                self.step("Plug the phone in and allow USB debugging (leave \"Always allow\" off)")
                self.serial = self.wait(lambda: next((l.split("\t")[0] for l in self.adb("devices").stdout.splitlines()[1:]
                                                      if l.endswith("\tdevice")), None), "phone allowed USB debugging")
            elif self.how == "qr":
                name, password = self.args
                self.step("Scan the code: Wireless debugging → Pair device with QR code")
                started = time.monotonic()

                def scanned():
                    if time.monotonic() - started > QR_HINT and not read_state("phone")["step"].startswith("Nothing"):
                        self.step("Nothing heard from the phone yet: if it scanned, this network hides it (mDNS). "
                                  "Use Pair device with pairing code below instead")
                    return next((a for n, k, a in self.services() if n == name and k.startswith("_adb-tls-pairing")),
                                None)
                addr = self.wait(scanned, "phone scanning")
                self.step("Pairing")
                self.serial = self.connect(*self.pair(addr, password))
            else:
                addr, code = self.args
                self.step("Pairing")
                self.serial = self.connect(*self.pair(addr, code))
            self.step("Starting")
            self.scrcpy = subprocess.Popen(["scrcpy", "-s", self.serial, "--no-clipboard-autosync",
                                            "--window-title", "Phone"], env=self.env)
            time.sleep(2)
            if self.scrcpy.poll() is None:
                self.step("On", live=True)
                notify("The phone is on this screen")
            self.scrcpy.wait()
        except RuntimeError as e:
            self.step(f"Stopped: {e}")
            notify(f"The phone wasn't shown: {e}")
            time.sleep(4)   # the panel shows why before the state goes
        finally:
            self.cleanup()

    def cleanup(self):
        if self.scrcpy and self.scrcpy.poll() is None:
            self.scrcpy.terminate()
            try:
                self.scrcpy.wait(3)
            except subprocess.TimeoutExpired:
                self.scrcpy.kill()
        if self.serial and self.how != "usb":
            # Wireless debugging off: the phone stops listening for adb on the network
            self.adb("-s", self.serial, "shell", "settings", "put", "global", "adb_wifi_enabled", "0", timeout=5)
            self.adb("disconnect", self.serial, timeout=5)
        self.adb("kill-server", timeout=5)
        shutil.rmtree(self.home, ignore_errors=True)
        write_state("phone", None)


async def open_ports(host, exclude=(), lo=30000, hi=65535, at_once=800):
    """The TCP ports open on host (the phone: adbd picks a random high port), at most at_once tried together."""
    found, gate = [], asyncio.Semaphore(at_once)

    async def probe(port):
        async with gate:
            try:
                _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), 1.5)
            except (OSError, asyncio.TimeoutError):
                return
            w.close()
            found.append(port)
    await asyncio.gather(*(probe(p) for p in range(lo, hi + 1) if p not in exclude))
    return sorted(found)


# ---- the commands --------------------------------------------------------------------------------------
UNITS = {"share": "mirror-share", "phone": "mirror-phone"}


def start(what, args):
    """As a transient user unit: it outlives the panel, and stop is systemctl's."""
    unit = UNITS[what]
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
    subprocess.run(["systemctl", "--user", "reset-failed", unit], capture_output=True)
    return subprocess.run(["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
                           "-p", "KillMode=mixed", "-p", "TimeoutStopSec=10",
                           os.path.expanduser("~/.local/bin/mirror"), what, *args]).returncode


def stop(what):
    for w in ([what] if what else UNITS):
        subprocess.run(["systemctl", "--user", "stop", UNITS[w]], capture_output=True)
        if left := read_state(w):   # it died without cleaning up; its wayvnc went with the unit
            if left.get("extend"):
                hyprctl("output", "remove", PHONE_SCREEN)
            write_state(w, None)


def status():
    share, phone = read_state("share"), read_state("phone")
    print(f"share   {('watched by ' + share['viewer']) if share.get('viewer') else 'waiting'}   {share['url']}"
          if share else "share   off")
    print(f"phone   {phone['step']}" if phone else "phone   off")
    return 0


def main(argv):
    cmd, args = (argv[1] if len(argv) > 1 else "status"), argv[2:]
    if cmd == "share":
        output = args[args.index("--output") + 1] if "--output" in args else None
        Share(output, "--no-sound" not in args, "--extend" in args, "--usb" in args).run()
    elif cmd == "phone" and args and args[0] in ("usb", "qr", "code"):
        Phone(args[0], args[1:]).run()
    elif cmd == "start" and args and args[0] in UNITS:
        return start(args[0], args[1:])
    elif cmd == "stop":
        stop(args[0] if args and args[0] in UNITS else None)
    elif cmd == "output" and args:
        if (read_state("share") or {}).get("extend"):
            return 0   # the phone's own screen: nothing to pick
        set_output(args[0])
        if s := read_state("share"):
            s["output"] = args[0]
            write_state("share", s)
    elif cmd == "status":
        return status()
    elif cmd == "bar":
        try:
            print(open(BAR).read())
        except OSError:
            print('{"text": ""}')
    else:
        print("usage: mirror share [--output NAME | --extend] [--usb] [--no-sound] | phone usb | phone qr NAME PASSWORD | "
              "phone code HOST:PORT CODE | start share|phone … | stop [share|phone] | output NAME | status",
              file=sys.stderr)
        return 2
    return 0
