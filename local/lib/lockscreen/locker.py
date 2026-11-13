#!/usr/bin/env python3
# The lock screen: one window per screen through the Wayland session-lock protocol (gtk4-layer-shell's
# Gtk4SessionLock: the compositor keeps the screen locked even if this dies), showing what ui.py draws (the
# login screen, greeter.py, shows the same): the wallpaper blurred and dimmed under an animation in shaded
# characters (Settings → Idle & lock picks one, in settings.json, and Ctrl + ←/→ steps through them while
# locked), and a frosted-glass card with the clock, the date and the password field. Started by
# local/bin/lockscreen (hypridle's lock_cmd), which falls back to hyprlock if this fails or dies; the password
# goes through PAM, hyprlock's service (/etc/pam.d/hyprlock).
#
#   locker.py                    lock
#   locker.py --demo N [ANIM]    lock, and unlock by itself after N seconds (to look at it; ANIM: one of
#                                ANIMATIONS instead of the chosen one)
import ctypes
import ctypes.util
import getpass
import json
import os
import random
import re
import subprocess
import sys
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui import ANIMATIONS, Field, Prompt, Scene, background  # noqa: E402  (sets GSK_RENDERER first)

import gi  # noqa: E402
gi.require_version("Gtk4SessionLock", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4SessionLock  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
import hyprsock  # noqa: E402

SETTINGS = os.path.expanduser("~/.config/hypr/settings.json")   # ["lockscreen"]["animation"]
CACHE = os.path.expanduser("~/.cache/lockscreen")
WALL = os.path.expanduser("~/.local/state/wallpaper-current")
FAILLOCK = "/etc/security/faillock.conf"   # deny = wrong passwords before the account locks, for unlock_time s


def crash(*exc):
    """Any error ends the process: local/bin/lockscreen then has hyprlock take the lock over (never a lock
    screen that's up but can't unlock)."""
    traceback.print_exception(*exc)
    os._exit(1)


sys.excepthook = crash
threading.excepthook = lambda a: crash(a.exc_type, a.exc_value, a.exc_traceback)


# ---- PAM (ctypes: libpam's conversation answers every prompt with the password) ----

class PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class PamResponse(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


CONV = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(PamMessage)),
                        ctypes.POINTER(ctypes.POINTER(PamResponse)), ctypes.c_void_p)


class PamConv(ctypes.Structure):
    _fields_ = [("conv", CONV), ("appdata_ptr", ctypes.c_void_p)]


def authenticate(password):
    """(ok, message): message is what PAM said besides asking (e.g. faillock's "account locked")."""
    pam = ctypes.CDLL(ctypes.util.find_library("pam"))
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    libc.calloc.restype = ctypes.c_void_p
    libc.calloc.argtypes = (ctypes.c_size_t, ctypes.c_size_t)
    libc.strdup.restype = ctypes.c_void_p
    libc.strdup.argtypes = (ctypes.c_char_p,)
    said = []

    def conv(n, messages, responses, _):
        # PAM frees what it gets: the answers must come from malloc
        resp = libc.calloc(n, ctypes.sizeof(PamResponse))
        if not resp:
            return 5   # PAM_BUF_ERR
        arr = ctypes.cast(resp, ctypes.POINTER(PamResponse))
        for i in range(n):
            m = messages[i].contents
            if m.msg_style in (1, 2):   # PAM_PROMPT_ECHO_OFF / _ON
                arr[i].resp = libc.strdup(password.encode())
            elif m.msg and m.msg_style in (3, 4):   # PAM_ERROR_MSG / PAM_TEXT_INFO
                said.append(m.msg.decode(errors="replace"))
        responses[0] = arr
        return 0

    handle = ctypes.c_void_p()
    callback = CONV(conv)
    conv_struct = PamConv(callback, None)
    if pam.pam_start(b"hyprlock", getpass.getuser().encode(), ctypes.byref(conv_struct),
                     ctypes.byref(handle)) != 0:
        return False, "PAM didn't start"
    ok = pam.pam_authenticate(handle, 0) == 0
    pam.pam_end(handle, 0)
    return ok, " ".join(said)


def tries_left():
    """(wrong passwords faillock still allows, minutes it then locks the account for), or None if it doesn't
    lock or can't be read. Its tally is the user's own file (/run/faillock/<user>), no root needed."""
    conf = {"deny": 3, "unlock_time": 600}      # pam_faillock's defaults
    try:
        with open(FAILLOCK) as f:
            for line in f:
                m = re.match(r"\s*(deny|unlock_time)\s*=\s*(\d+)", line)
                if m:
                    conf[m.group(1)] = int(m.group(2))
        out = subprocess.run(["faillock", "--user", getpass.getuser()], capture_output=True, text=True,
                             timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if not conf["deny"]:
        return None
    failed = sum(1 for line in out.splitlines() if line.rstrip().endswith(" V"))   # V: still counting
    return max(conf["deny"] - failed, 0), max(conf["unlock_time"] // 60, 1)


def keyboard_layout():
    """The active layout's code ("US", "DE"…) when there's more than one to switch between, else ""."""
    devices = hyprsock.query("devices") or {}
    for k in devices.get("keyboards", []):
        if k.get("main"):
            codes = k.get("layout", "").split(",")
            i = k.get("active_layout_index", 0)
            return codes[i].upper() if len(codes) > 1 and 0 <= i < len(codes) else ""
    return ""



# ---- the animation ----

def setting():
    """The animation Settings picked: one of ANIMATIONS, "random", or "ocean" if none."""
    try:
        with open(SETTINGS) as f:
            return json.load(f).get("lockscreen", {}).get("animation", "ocean")
    except (OSError, ValueError):
        return "ocean"


def chosen():
    """The animation to show (the ocean for a name this version doesn't know)."""
    name = setting()
    if name == "random":
        return random.choice(list(ANIMATIONS))
    return name if name in ANIMATIONS else "ocean"


def keep(name):
    """Ctrl + ←/→ picked name: it becomes the setting (unless that's "a different one each time"). Through
    settingslib, so settings.json stays written the one way; a failure here never touches the lock."""
    if setting() == "random":
        return
    try:
        sys.path.insert(0, os.path.expanduser("~/.local/lib/panels"))
        import settingslib
        settingslib.lock_animation_set(name)
    except Exception:   # noqa: BLE001 — losing the choice is fine, losing the lock screen isn't
        traceback.print_exc()


HINTS = (("Enter", "unlock"), ("Esc", "clear"), ("Ctrl \u2190\u2192", "scene"))


class Locker:
    def __init__(self, demo, anim):
        self.loop = GLib.MainLoop()
        h24 = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "clock-format"],
                             capture_output=True, text=True).stdout.strip() != "'12h'"
        display = Gdk.Display.get_default()
        mons = display.get_monitors()
        big = max((mons.get_item(i).get_geometry() for i in range(mons.get_n_items())),
                  key=lambda g: g.width * g.height)
        self.field = Field(big.width, big.height, anim)
        self.prompt = Prompt(getpass.getuser(), self.field, HINTS, self.submit, layout=keyboard_layout,
                             extra_key=self.on_key, h24=h24)
        self.lock = Gtk4SessionLock.Instance.new()
        self.lock.connect("monitor", self.on_monitor)
        self.lock.connect("failed", lambda *_: self.quit(2))
        self.lock.connect("unlocked", lambda *_: self.quit(0))
        self.status = 1
        if demo:
            GLib.timeout_add(int(demo * 1000), lambda: self.lock.unlock() or False)

    def quit(self, status):
        self.status = status
        self.loop.quit()

    def on_monitor(self, lock, monitor):
        g = monitor.get_geometry()
        win = Gtk.Window()
        win.set_cursor(Gdk.Cursor.new_from_name("none"))
        overlay = Gtk.Overlay()
        overlay.set_child(Scene(self.field, background(g.width, g.height, WALL, CACHE)))
        overlay.add_overlay(self.prompt.card(win))
        win.set_child(overlay)
        lock.assign_window_to_monitor(win, monitor)
        self.prompt.focus()

    def on_key(self, keyval, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            keep(self.field.step(1 if keyval == Gdk.KEY_Right else -1))
            return True
        return False

    def submit(self, password):
        self.prompt.busy()

        def work():
            ok, said = authenticate(password)
            GLib.idle_add(self.done, ok, said, None if ok else tries_left())
        threading.Thread(target=work, daemon=True).start()

    def done(self, ok, said, left):
        if ok:
            self.prompt.passed("Welcome back")
            self.lock.unlock()
        else:
            self.prompt.failed(said, left)
        return False

    def run(self):
        if not self.lock.lock():
            return 2
        self.loop.run()
        return self.status


def main():
    demo = float(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--demo" else 0
    anim = sys.argv[3] if demo and len(sys.argv) > 3 and sys.argv[3] in ANIMATIONS else chosen()
    Gtk.init()
    if not Gtk4SessionLock.is_supported():
        sys.exit(2)
    sys.exit(Locker(demo, anim).run())


if __name__ == "__main__":
    main()
