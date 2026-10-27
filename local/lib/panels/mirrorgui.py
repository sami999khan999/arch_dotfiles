#!/usr/bin/env python3
# mirrorgui.py — Mirror (Super + Ctrl + C, the bar's phone-screen icon while on): screen sharing with the
# phone, both ways, each side controlling the other, nothing kept on either.
# The window of `mirror` (local/lib/mirror/mirror.py), which runs each way as a transient user unit, so a
# share outlives this popup; this shows what its state files say ($XDG_RUNTIME_DIR/mirror/*.json), every
# second while open.
# A tab each way:
#   This PC on the phone: a switch; the code to scan opens the screen in the phone's browser (no app). The phone
#     shows this screen, or is a second screen beside it (a screen of its own, at the phone's size); over
#     Wi-Fi, or the USB cable (USB debugging: the page then opens on the phone by itself).
#     Files both ways through the same page: Send files… offers them to the phone (mirror send); the phone's
#     come into ~/Downloads.
#   The phone on this PC: over Wi-Fi (scan a code in the phone's Wireless debugging, or type its pairing
#     code) or USB; scrcpy shows it in a window, mouse and keyboard drive it.
#
#   1 2  tabs   s  share on / off   e  mirror / second screen   c  Wi-Fi / USB   w  phone over Wi-Fi   u  over USB
#   x  stop the phone   Esc  close
import json, os, secrets, subprocess, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import (Gdk, GLib, Gtk, View, box, button, clear, dropdown, label, qr, run,
                    scrolled, setting_row, switch)

MIRROR = os.path.expanduser("~/.local/bin/mirror")
RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "mirror")
G_MIRROR = "\U000f0989"   # nf-md-monitor_cellphone, as on the bar
PHONE_SCREEN = "PHONE"     # mirror.py's extended screen: not one to mirror
TABS = {"share": "This PC on the phone", "phone": "The phone on this PC"}


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


class Mirror(View):
    icon = G_MIRROR
    title, subtitle = "Mirror", "screens with the phone, both ways"
    interval = 1.0
    css = """
    .mirror-page { padding: 12px 26px 20px 22px; }
    .code-card { padding: 14px 0 6px 0; }
    .code-text { margin-top: 2px; }
    .url { font-size: 9pt; }
    .pair-entry { min-width: 150px; }
    """

    def __init__(self):
        super().__init__()
        self.share = self.phone = None
        self.shown = None
        self.extend = False   # what Share this screen starts: a mirror of a screen, or a second screen
        self.usb = False      # and how the phone gets it: Wi-Fi (a code to scan) or the USB cable
        self.tab = None

    @property
    def hints(self):
        if self.tab == "phone":
            keys = [("x", "stop the phone")] if self.phone else [("w", "over Wi-Fi"), ("u", "over USB")]
        else:
            keys = [("s", "share"), ("e", "mirror / second screen"), ("c", "Wi-Fi / USB")]
        return [("1 2", "tabs")] + keys + [("Esc", "close")]

    # ---- the window ----
    def build(self):
        self.share_box = box(True, 0, classes=("mirror-page",))
        self.phone_box = box(True, 0, classes=("mirror-page",))
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, vexpand=True)
        self.pages.add_named(scrolled(self.share_box), "share")
        self.pages.add_named(scrolled(self.phone_box), "phone")
        self.tab_buttons = {}
        bar = box(False, 0, classes=("tabs",))
        for i, t in enumerate(TABS, 1):
            b = button("", lambda t=t: self.switch(t), "tab", tooltip=str(i))
            self.tab_buttons[t] = b
            bar.append(b)
        self.refresh()
        # opens on the way that's on: the phone's while only it is
        self.switch("phone" if self.phone and not self.share else "share")
        return box(True, 0, bar, self.pages)

    def switch(self, tab):
        self.tab = tab
        self.pages.set_visible_child_name(tab)
        for t, b in self.tab_buttons.items():
            (b.add_css_class if t == tab else b.remove_css_class)("on")
        if self.host:
            self.host.show_hints()

    def refresh(self):
        self.share, self.phone = state("share"), state("phone")
        key = (self.share and (self.share.get("url"), self.share.get("viewer"), self.share.get("output"),
                               self.share.get("extend"), self.share.get("usb")),
               self.phone and (self.phone.get("step"), self.phone.get("live")))
        if key != self.shown:   # redrawn only on a change: a pairing code being typed stays
            self.shown = key
            self.paint_tabs()
            self.paint_share()
            self.paint_phone()
            if self.host:
                self.host.show_hints()

    def paint_tabs(self):
        s, p = self.share, self.phone
        state = {"share": "watched" if s and s.get("viewer") else "on" if s else "",
                 "phone": "on" if p and p.get("live") else "starting" if p else ""}
        for t, b in self.tab_buttons.items():
            b.set_label(TABS[t] + (f"  · {state[t]}" if state[t] else ""))

    def paint_share(self):
        s = self.share
        if s:
            self.extend, self.usb = bool(s.get("extend")), s.get("usb") is not None
        clear(self.share_box)
        self.share_switch = switch(bool(s), self.set_share)
        self.share_box.append(setting_row(
            "Share this screen", "The phone watches it in its browser, with the sound, and drives it by touch "
            "and its keyboard. Stops by itself after 10 minutes without a phone", self.share_switch))
        self.share_box.append(setting_row(
            "The phone shows", "A second screen is one of its own, at the phone's size, right of this one: "
            "move windows and workspaces to it as to any screen. Gone when sharing stops; its windows come back",
            dropdown([("mirror", "This screen"), ("extend", "A second screen")],
                     "extend" if self.extend else "mirror", self.set_extend)))
        self.share_box.append(setting_row(
            "Connect by", "USB: plugged in with USB debugging on, the page opens on the phone by itself; "
            "no Wi-Fi needed, and less lag",
            dropdown([("wifi", "Wi-Fi"), ("usb", "USB cable")], "usb" if self.usb else "wifi", self.set_usb)))
        names = [n for n in outputs() if n != PHONE_SCREEN]
        if len(names) > 1 and not self.extend:
            current = (s or {}).get("output") or self.picked_output()
            self.share_box.append(setting_row("Screen", "Which one the phone sees (its Screen button cycles them)",
                                              dropdown([(n, n) for n in names], current, self.set_output)))
        if not s:
            return
        self.share_box.append(setting_row(
            "Files", "Both ways through the page (its Files button): the phone's land in ~/Downloads; these show "
            "on the phone to save", button("Send files…", self.send_files, "flat")))
        if s.get("usb") is not None:
            self.share_box.append(self.usb_card(s))
            return
        if not s.get("url"):
            self.share_box.append(label("This PC has no address on a private network: the phone can't reach it.",
                                        "red", wrap=True))
            return
        text = box(True, 6,
                   label("Scan with the phone's camera", "bold"),
                   label("Best in a private tab (Chrome: a new Incognito tab, then the camera in its address bar): "
                         "then nothing of it stays on the phone. A normal tab keeps only the bare address.",
                         "sub", wrap=True),
                   label("The browser warns that the connection isn't private: the certificate is made for this "
                         "share only. Advanced → Proceed.", "dim", wrap=True),
                   self.share_status(s), classes=("code-text",))
        text.append(label(s["url"], "dim", "url", wrap=True))   # not selectable: a copy would stay in the clipboard history
        text.set_hexpand(True)
        self.share_box.append(box(False, 20, qr(s["url"]), text, classes=("code-card",)))

    def share_status(self, s):
        if s.get("viewer"):
            who = "The phone (USB)" if s["viewer"] == "USB" else s["viewer"]
            return label(f"{who} is this PC's second screen" if s.get("extend") else
                         f"{who} is watching and controlling this screen", "amber", wrap=True)
        return label("Waiting for the phone…", "accent")

    def usb_card(self, s):
        steps = label("Once on the phone: Settings → About phone → tap Build number 7 times; then Settings → "
                      "System → Developer options → USB debugging on. Every session uses a new key, thrown away "
                      "at the end; unplug the phone and plug it in again to open the page again.", "dim", wrap=True)
        return box(True, 8, label("Over the USB cable", "bold"), label(s["usb"], "sub", wrap=True),
                   self.share_status(s), steps, classes=("code-card",))

    def paint_phone(self):
        p = self.phone
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
        elif then:
            then()
        self.refresh()

    def set_share(self, on):
        usb = ["--usb"] if self.usb else []
        if on and self.extend:
            self.mirror("start", "share", "--extend", *usb)
            self.say("The phone is a second screen once it " + ("is plugged in" if self.usb else "scans the code"))
        elif on:
            output = self.picked_output()
            self.mirror("start", "share", *(["--output", output] if output else []), *usb)
            self.say("Sharing this screen")
        else:
            self.mirror("stop", "share")
            self.say("Sharing stopped")

    def set_extend(self, value):
        self.extend = value == "extend"
        if self.share and bool(self.share.get("extend")) != self.extend:
            self.set_share(True)   # started again the other way: the phone scans the new code
        else:
            self.shown = None
            self.refresh()

    def send_files(self):
        def chosen(d, res):
            try:
                paths = [f.get_path() for f in d.open_multiple_finish(res)]
            except GLib.Error:
                return   # cancelled
            self.mirror("send", *[p for p in paths if p],
                        then=lambda: self.say(f"Offered to the phone: {len(paths)} file{'s' * (len(paths) != 1)} "
                                              "(its Files button)"))
        Gtk.FileDialog(title="Send to the phone", modal=True).open_multiple(self.window, None, chosen)

    def set_usb(self, value):
        self.usb = value == "usb"
        if self.share and (self.share.get("usb") is not None) != self.usb:
            self.set_share(True)   # started again the other way
        else:
            self.shown = None
            self.refresh()

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
        if k in ("1", "2"):
            self.switch(list(TABS)[int(k) - 1])
        elif k == "s" and self.tab == "share":
            self.share_switch.set_active(not self.share_switch.get_active())
        elif k == "e" and self.tab == "share":
            self.set_extend("mirror" if self.extend else "extend")
        elif k == "c" and self.tab == "share":
            self.set_usb("wifi" if self.usb else "usb")
        elif k == "w" and self.tab == "phone" and not self.phone:
            self.wifi()
        elif k == "u" and self.tab == "phone" and not self.phone:
            self.usb()
        elif k == "x" and self.tab == "phone" and self.phone:
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
