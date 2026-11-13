#!/usr/bin/env python3
# The login screen: what the lock screen shows (ui.py: the wallpaper blurred under the animation in characters,
# the frosted card with the clock and the password field), logging in through greetd (its IPC socket,
# $GREETD_SOCK) to the one session, with power off / restart / suspend in the corner.
#
# It runs as the greeter user inside noctalia-greeter's compositor (a kiosk: one window per screen,
# fullscreen), from a root-owned copy of this folder in /usr/local/lib/dotfiles/greeter (it can't read the
# home folder), with what it needs from there in greeter.json beside it: the user, the session, the clock
# format, the animation, the wallpaper — all made by system/greeter/install.sh. If it can't start or dies,
# its launcher (system/greeter/greeter) runs noctalia-greeter itself instead: never a login screen that
# can't log in.
#
#   greeter.py           log in (needs $GREETD_SOCK)
#   greeter.py --demo    a look at it in a window: any password but "wrong" logs in to nothing
import json
import os
import random
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui import ANIMATIONS, Field, Prompt, Scene, background  # noqa: E402  (sets GSK_RENDERER first)

import themelib  # noqa: E402  (found through ui's path)
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
CONFIG = os.path.join(ROOT, "greeter.json")      # written by system/greeter/install.sh
CACHE = os.path.expanduser("~/.cache/lockscreen")  # the greeter user's (/var/lib/noctalia-greeter): the blurred wallpaper
HINTS = (("Enter", "log in"), ("Esc", "clear"), ("Ctrl ←→", "scene"))
POWER = (("", "Suspend", ["systemctl", "suspend"]),           # a moon
         ("", "Restart", ["systemctl", "reboot"]),
         ("", "Power off", ["systemctl", "poweroff"]))

CSS = """
.power { margin: 24px; }
.power button {
  min-width: 40px; min-height: 40px; padding: 0; font-family: "JetBrainsMono Nerd Font"; font-size: 15px;
  color: #a9b1d6; background: alpha(#1a1b26, 0.6); border: 1px solid alpha(#3b4261, 0.7); border-radius: 0;
  box-shadow: none; outline: none; transition: color 150ms ease, background-color 150ms ease;
}
.power button:hover { color: #c0caf5; background: alpha(#292e42, 0.9); }
.power button.off:hover { color: #1a1b26; background: #f7768e; border-color: #f7768e; }
"""


def crash(*exc):
    """Any error ends the process: the launcher then runs noctalia-greeter instead."""
    traceback.print_exception(*exc)
    os._exit(1)


sys.excepthook = crash
threading.excepthook = lambda a: crash(a.exc_type, a.exc_value, a.exc_traceback)


def config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Greetd:
    """greetd's IPC: a JSON request, its reply; each one a native-endian u32 length and the JSON."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)

    def ask(self, **req):
        data = json.dumps(req).encode()
        self.sock.sendall(struct.pack("=I", len(data)) + data)
        (n,) = struct.unpack("=I", self.read(4))
        return json.loads(self.read(n))

    def read(self, n):
        buf = b""
        while len(buf) < n:
            got = self.sock.recv(n - len(buf))
            if not got:
                raise ConnectionError("greetd hung up")
            buf += got
        return buf

    def login(self, user, password, cmd, env):
        """Log user in and start cmd: (True, "") or (False, what went wrong; "" for just a wrong password)."""
        self.ask(type="cancel_session")             # one left half-done (an error is fine: there's none)
        r = self.ask(type="create_session", username=user)
        said, answered = [], False
        while r["type"] == "auth_message":
            if r["auth_message_type"] in ("secret", "visible"):
                if answered:                        # a second question (a code?): not ours to answer
                    self.ask(type="cancel_session")
                    return False, r.get("auth_message", "").strip() or "Can't log in from here"
                answered = True
                r = self.ask(type="post_auth_message_response", response=password)
            else:                                   # info / error, e.g. faillock's "account locked"
                if r.get("auth_message", "").strip():
                    said.append(r["auth_message"].strip())
                r = self.ask(type="post_auth_message_response")
        if r["type"] == "error":
            self.ask(type="cancel_session")
            if r.get("error_type") == "auth_error":
                return False, " ".join(said)
            return False, r.get("description", "") or "Can't log in"
        r = self.ask(type="start_session", cmd=cmd, env=env)
        if r["type"] != "success":
            self.ask(type="cancel_session")
            return False, r.get("description", "") or "The session didn't start"
        return True, ""


class Greeter:
    def __init__(self, demo):
        self.demo = demo
        conf = config()
        self.user = conf.get("user") or os.environ.get("USER", "")
        self.cmd = conf.get("session") or ["uwsm", "start", "-e", "-D", "Hyprland", "hyprland.desktop"]
        self.desktop = conf.get("desktop", "Hyprland")
        self.wall = conf.get("wallpaper", "")
        anim = conf.get("animation", "ocean")
        anim = random.choice(list(ANIMATIONS)) if anim == "random" else anim
        self.loop = GLib.MainLoop()
        display = Gdk.Display.get_default()
        css = Gtk.CssProvider()
        css.load_from_string(themelib.recolor(CSS))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        mons = display.get_monitors()
        geos = [mons.get_item(i).get_geometry() for i in range(mons.get_n_items())]
        big = max(geos, key=lambda g: g.width * g.height) if geos else Gdk.Rectangle(width=1366, height=768)
        self.field = Field(big.width, big.height, anim if anim in ANIMATIONS else "ocean")
        self.prompt = Prompt(self.user, self.field, HINTS, self.submit, verb="Log in", extra_key=self.on_key,
                             h24=conf.get("h24", True))
        self.greetd = None if demo else Greetd(os.environ["GREETD_SOCK"])
        self.windows = []
        if demo:
            self.window(None, 1366, 768)
        else:
            for i, g in enumerate(geos):
                self.window(mons.get_item(i), g.width, g.height)
        self.prompt.focus()

    def window(self, monitor, w, h):
        win = Gtk.Window(title="Log in")
        win.set_default_size(w, h)
        overlay = Gtk.Overlay()
        overlay.set_child(Scene(self.field, background(w, h, self.wall, CACHE) if self.wall else None))
        overlay.add_overlay(self.prompt.card(win))
        overlay.add_overlay(self.power())
        win.set_child(overlay)
        win.connect("close-request", lambda *_: True)    # only a login (or a power button) ends it
        if monitor:
            win.fullscreen_on_monitor(monitor)
        win.present()
        self.windows.append(win)

    def power(self):
        box = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END, spacing=6)
        box.add_css_class("power")
        for glyph, what, cmd in POWER:
            b = Gtk.Button(label=glyph, focusable=False, tooltip_text=what)
            if what == "Power off":
                b.add_css_class("off")
            b.connect("clicked", lambda _b, c=cmd: self.demo or subprocess.Popen(c))
            box.append(b)
        return box

    def on_key(self, keyval, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            self.field.step(1 if keyval == Gdk.KEY_Right else -1)   # for now: Settings keeps the choice
            return True
        return False

    def submit(self, password):
        self.prompt.busy("Logging in…")
        env = ["XDG_SESSION_TYPE=wayland", f"XDG_CURRENT_DESKTOP={self.desktop}",
               f"XDG_SESSION_DESKTOP={self.desktop}"]

        def work():
            if self.demo:
                time.sleep(0.8)
                ok, said = password != "wrong", ""
            else:
                ok, said = self.greetd.login(self.user, password, self.cmd, env)
            GLib.idle_add(self.done, ok, said)
        threading.Thread(target=work, daemon=True).start()

    def done(self, ok, said):
        if ok:
            # greetd starts the session once this exits: a moment to show it went through
            self.prompt.passed(f"Starting {self.desktop}…")
            GLib.timeout_add(350 if not self.demo else 1500, lambda: self.loop.quit() or False)
        else:
            self.prompt.failed(said)
        return False

    def run(self):
        self.loop.run()
        return 0


def main():
    Gtk.init()
    sys.exit(Greeter("--demo" in sys.argv).run())


if __name__ == "__main__":
    main()
