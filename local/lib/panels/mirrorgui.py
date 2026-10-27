#!/usr/bin/env python3
# mirrorgui.py — Mirror (Super + Ctrl + C, the bar's phone-screen icon while on): screen sharing with the
# phone, both ways, each side controlling the other, nothing kept on either.
# The window of `mirror` (local/lib/mirror/mirror.py), which runs each way as a transient user unit, so a
# share outlives this popup; this shows what its state files say ($XDG_RUNTIME_DIR/mirror/*.json), every
# second while open.
#   This PC on the phone: a switch; the code to scan opens the screen in the phone's browser (no app).
#   The phone on this PC: over Wi-Fi (scan a code in the phone's Wireless debugging, or type its pairing
#   code) or USB; scrcpy shows it in a window, mouse and keyboard drive it.
#
#   s  share this screen on / off   w  phone over Wi-Fi   u  phone over USB   x  stop the phone   Esc  close
import json, os, secrets, subprocess, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import (Gdk, GLib, Gtk, View, box, button, clear, dropdown, label, recolor, rule_heading, run,
                    scrolled, setting_row, switch)

MIRROR = os.path.expanduser("~/.local/bin/mirror")
RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "mirror")
G_MIRROR = "\U000f0989"   # nf-md-monitor_cellphone, as on the bar


def state(name):
    try:
        with open(os.path.join(RUN, name + ".json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def outputs():
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True).stdout
        return [m["name"] for m in json.loads(out)]
    except (OSError, ValueError):
        return []


def qr(text, px=190):
    """A QR code as a picture: dark on light, which every phone camera reads."""
    png = subprocess.run(["qrencode", "-t", "PNG", "-s", "8", "-m", "2", "-o", "-",
                          "--foreground=" + recolor("#1a1b26").lstrip("#"),
                          "--background=" + recolor("#c0caf5").lstrip("#"), text], capture_output=True).stdout
    pic = Gtk.Picture.new_for_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)))
    pic.set_size_request(px, px)
    pic.set_can_shrink(True)
    pic.set_valign(Gtk.Align.START)
    return pic


class Mirror(View):
    icon = G_MIRROR
    title, subtitle = "Mirror", "screens with the phone, both ways"
    interval = 1.0
    hints = [("s", "share this screen"), ("w", "phone over Wi-Fi"), ("u", "over USB"), ("x", "stop the phone"),
             ("Esc", "close")]
    css = """
    .mirror-page { padding: 6px 26px 20px 22px; }
    .code-card { padding: 14px 0 6px 0; }
    .code-text { margin-top: 2px; }
    .url { font-size: 9pt; }
    .pair-entry { min-width: 150px; }
    """

    def __init__(self):
        super().__init__()
        self.share = self.phone = None
        self.shown = None

    # ---- the window ----
    def build(self):
        page = box(True, 0, classes=("mirror-page",))
        self.share_head = box(True, 0)
        self.share_box = box(True, 0)
        self.phone_head = box(True, 0)
        self.phone_box = box(True, 0)
        for w in (self.share_head, self.share_box, self.phone_head, self.phone_box):
            page.append(w)
        self.refresh()
        return scrolled(page)

    def refresh(self):
        self.share, self.phone = state("share"), state("phone")
        key = (self.share and (self.share.get("url"), self.share.get("viewer"), self.share.get("output")),
               self.phone and (self.phone.get("step"), self.phone.get("live")))
        if key != self.shown:   # redrawn only on a change: a pairing code being typed stays
            self.shown = key
            self.paint_share()
            self.paint_phone()

    def paint_share(self):
        s = self.share
        clear(self.share_head)
        self.share_head.append(rule_heading("This PC on the phone",
                                            "watched" if s and s.get("viewer") else "on" if s else "off"))
        clear(self.share_box)
        self.share_switch = switch(bool(s), self.set_share)
        self.share_box.append(setting_row(
            "Share this screen", "The phone watches it in its browser, with the sound, and drives it by touch "
            "and its keyboard. Stops by itself after 10 minutes without a phone", self.share_switch))
        names = outputs()
        if len(names) > 1:
            current = (s or {}).get("output") or self.picked_output()
            self.share_box.append(setting_row("Screen", "Which one the phone sees (its Screen button cycles them)",
                                              dropdown([(n, n) for n in names], current, self.set_output)))
        if not s:
            return
        if not s.get("url"):
            self.share_box.append(label("This PC has no address on a private network: the phone can't reach it.",
                                        "red", wrap=True))
            return
        if s.get("viewer"):
            status = label(f"{s['viewer']} is watching and controlling this screen", "amber", wrap=True)
        else:
            status = label("Waiting for the phone…", "accent")
        text = box(True, 6,
                   label("Scan with the phone's camera", "bold"),
                   label("Best in a private tab (Chrome: a new Incognito tab, then the camera in its address bar): "
                         "then nothing of it stays on the phone. A normal tab keeps only the bare address.",
                         "sub", wrap=True),
                   label("The browser warns that the connection isn't private: the certificate is made for this "
                         "share only. Advanced → Proceed.", "dim", wrap=True),
                   status, classes=("code-text",))
        text.append(label(s["url"], "dim", "url", wrap=True))   # not selectable: a copy would stay in the clipboard history
        text.set_hexpand(True)
        self.share_box.append(box(False, 20, qr(s["url"]), text, classes=("code-card",)))

    def paint_phone(self):
        p = self.phone
        clear(self.phone_head)
        self.phone_head.append(rule_heading("The phone on this PC", "on" if p and p.get("live") else
                                            "starting" if p else "off", spaced=True))
        clear(self.phone_box)
        if p:
            cls = "green" if p.get("live") else "red" if p["step"].startswith("Stopped") else "amber"
            what = ("Shown in the Phone window: the mouse and keyboard drive it" if p.get("live") else p["step"])
            self.phone_box.append(setting_row("Over " + ("USB" if p["how"] == "usb" else "Wi-Fi"), "",
                                              button("Stop", self.stop_phone, "flat", "danger")))
            self.phone_box.append(label(what, cls, wrap=True))
            if p.get("qr") and not p.get("live") and not p["step"].startswith("Stopped"):
                self.phone_box.append(self.pair_card(*p["qr"]))
            return
        self.phone_box.append(label("Once on the phone: Settings → About phone → tap Build number 7 times; then "
                                    "Settings → System → Developer options → USB debugging on.", "dim", wrap=True))
        self.phone_box.append(setting_row(
            "Over Wi-Fi", "Developer options → Wireless debugging → Pair device with QR code. "
            "Wireless debugging is switched off again at the end", button("Show code", self.wifi, "primary")))
        self.phone_box.append(setting_row(
            "Over USB", "Plug it in and allow USB debugging; leave “Always allow” off, so the phone keeps nothing",
            button("Connect", self.usb, "flat")))
        self.phone_box.append(label("Every session pairs a new key that's thrown away at the end. Android lists "
                                    "a Wi-Fi pairing under Paired devices until you forget it (it can't be used "
                                    "again).", "dim", wrap=True))

    def pair_card(self, name, password):
        self.addr = Gtk.Entry(placeholder_text="IP address : port", css_classes=["pair-entry"])
        self.code = Gtk.Entry(placeholder_text="Wi-Fi pairing code", css_classes=["pair-entry"])
        self.code.connect("activate", lambda *_: self.pair_code())
        text = box(True, 8,
                   label("Scan with the phone", "bold"),
                   label("Wireless debugging → Pair device with QR code. Same Wi-Fi as this PC.", "sub", wrap=True),
                   label("Or Pair device with pairing code, and type what it shows:", "dim", wrap=True),
                   box(False, 8, self.addr, self.code, button("Pair", self.pair_code, "flat")),
                   classes=("code-text",))
        text.set_hexpand(True)
        return box(False, 20, qr(f"WIFI:T:ADB;S:{name};P:{password};;"), text, classes=("code-card",))

    # ---- doing it ----
    def picked_output(self):
        try:
            sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
            import hyprsock
            return next(m["name"] for m in hyprsock.query("monitors") if m.get("focused"))
        except Exception:
            return None

    def mirror(self, *args, then=None):
        def work():
            out = subprocess.run([MIRROR, *args], capture_output=True, text=True)
            GLib.idle_add(self.done, out.returncode, (out.stderr or "").strip(), then)
        threading.Thread(target=work, daemon=True).start()

    def done(self, code, err, then):
        if code:
            self.say(err.splitlines()[-1] if err else "mirror failed", "bad", 6)
        if then:
            then()
        self.refresh()

    def set_share(self, on):
        if on:
            output = self.picked_output()
            self.mirror("start", "share", *(["--output", output] if output else []))
            self.say("Sharing this screen")
        else:
            self.mirror("stop", "share")
            self.say("Sharing stopped")

    def set_output(self, name):
        if self.share:
            self.mirror("output", name)

    def wifi(self):
        self.mirror("start", "phone", "qr", f"mirror-{secrets.token_hex(3)}", secrets.token_urlsafe(9))

    def pair_code(self):
        addr, code = self.addr.get_text().strip(), self.code.get_text().strip()
        if ":" not in addr or not code:
            return self.say("Type the address with its port (192.168.1.20:37000) and the 6-digit code", "bad")
        self.mirror("start", "phone", "code", addr, code)

    def usb(self):
        self.mirror("start", "phone", "usb")

    def stop_phone(self):
        self.mirror("stop", "phone")

    def key(self, keyval, state_):
        if self.typing():
            return False
        k = Gdk.keyval_name(keyval)
        if k == "s":
            self.share_switch.set_active(not self.share_switch.get_active())
        elif k == "w" and not self.phone:
            self.wifi()
        elif k == "u" and not self.phone:
            self.usb()
        elif k == "x" and self.phone:
            self.stop_phone()
        else:
            return False
        return True


def make():
    return Mirror()


def main():
    run(make(), "panels.mirror", (760, 640))


if __name__ == "__main__":
    main()
