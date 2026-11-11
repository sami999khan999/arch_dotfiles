#!/usr/bin/env python3
# The lock screen: one window per screen through the Wayland session-lock protocol (gtk4-layer-shell's
# Gtk4SessionLock: the compositor keeps the screen locked even if this dies), showing the wallpaper
# blurred and dimmed under a night ocean drawn in shaded characters (ocean.py), and a frosted-glass card
# with the clock, the date and the password field. Started by local/bin/lockscreen (hypridle's lock_cmd),
# which falls back to hyprlock if this fails or dies; the password goes through PAM, hyprlock's service
# (/etc/pam.d/hyprlock).
#
# Light on purpose: NumPy draws the ocean FPS times a second (a few ms: the characters as a mask, a colour
# pixel per cell). Everything else is the GPU's: the colours scaled up under the characters, the blur
# under the card. Frames stop while the screens are off (no frame callbacks then).
#
#   locker.py            lock
#   locker.py --demo N   lock, and unlock by itself after N seconds (to look at it)
import ctypes
import ctypes.util
import getpass
import hashlib
import math
import os
import subprocess
import sys
import threading
import time
import traceback

# GL, not GTK's default Vulkan: Vulkan flickers on this Haswell iGPU (see panels/gtkkit.py)
os.environ.setdefault("GSK_RENDERER", "gl")

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk4SessionLock", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Gtk4SessionLock  # noqa: E402

sys.path.insert(0, os.path.expanduser("~/.local/lib/theme"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import themelib  # noqa: E402
from ocean import Ocean  # noqa: E402

FPS = 30            # ocean frames a second
CACHE = os.path.expanduser("~/.cache/lockscreen")
WALL = os.path.expanduser("~/.local/state/wallpaper-current")
FONT = "JetBrainsMono Nerd Font 9"    # the ocean's characters (their size is its grid)
SHAKE = 0.45        # s the password field shakes after a wrong password
CARD = (440, 320)   # the card in the middle (px)
BLUR = 16           # the card's frosted glass

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


def crash(*exc):
    """Any error ends the process: local/bin/lockscreen then has hyprlock take the lock over (never a lock
    screen that's up but can't unlock)."""
    traceback.print_exception(*exc)
    os._exit(1)


sys.excepthook = crash
threading.excepthook = lambda a: crash(a.exc_type, a.exc_value, a.exc_traceback)


def rgba(hex_, alpha=1.0):
    c = Gdk.RGBA()
    c.parse(themelib.recolor(hex_))
    c.alpha = alpha
    return c


def rect(x, y, w, h):
    return Graphene.Rect().init(x, y, w, h)


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


# ---- the picture ----

def background(w, h):
    """The wallpaper covering a w x h screen, blurred and dimmed: made once per wallpaper and size (the GPU
    could blur it every frame, but a file costs nothing while locked)."""
    wall = os.path.realpath(WALL)
    try:
        key = hashlib.md5(f"{wall} {os.stat(wall).st_mtime} {w}x{h}".encode()).hexdigest()[:12]
    except OSError:
        return None
    out = os.path.join(CACHE, f"bg-{w}x{h}-{key}.jpg")
    if not os.path.exists(out):
        os.makedirs(CACHE, exist_ok=True)
        for old in os.listdir(CACHE):
            if old.startswith(f"bg-{w}x{h}-"):
                os.remove(os.path.join(CACHE, old))
        # shrink → blur → enlarge: a big blur in a fraction of the time
        subprocess.run(["magick", wall, "-resize", f"{w}x{h}^", "-gravity", "center", "-extent", f"{w}x{h}",
                        "-resize", "10%", "-blur", "0x2", "-resize", f"{w}x{h}!", "-modulate", "38",
                        "-quality", "92", out], check=False)
    try:
        return Gdk.Texture.new_from_filename(out)
    except GLib.Error:
        return None


class Field:
    """The ocean shared by every screen: its latest frame as textures (mask, colour), made at most FPS
    times a second, when a screen is about to draw (so never while they're off)."""

    def __init__(self, w, h):
        rgb = themelib.rgbf
        self.ocean = Ocean(w, h, FONT,
                           [(0, rgb("#24283b")), (.3, rgb("#3b4261")), (.55, rgb("#6b8fe0")),
                            (.8, rgb("#7dcfff")), (1, rgb("#c0caf5"))],
                           [(0, rgb("#3b4261")), (.5, rgb("#a9b1d6")), (1, rgb("#cfc9c2"))],
                           rgb("#f7768e"), 0.9)
        o = self.ocean
        self.grid = rect(0, 0, o.px, o.py)
        self.t0 = time.monotonic()
        self.made = 0.0
        self.cur = self.textures()

    def textures(self):
        o = self.ocean
        mask, colour = o.frame(time.monotonic() - self.t0)
        return (Gdk.MemoryTexture.new(o.px, o.py, Gdk.MemoryFormat.A8, GLib.Bytes.new(mask), o.px),
                Gdk.MemoryTexture.new(o.w, o.h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(colour), o.w * 4))

    def frame(self):
        """The textures for now: a new frame once 1/FPS has gone by, else the last one (every screen)."""
        now = time.monotonic()
        if now - self.made >= 1 / FPS - 0.004:
            self.made = now
            self.cur = self.textures()
        return self.cur

    def ripple(self, kind):
        """Ripples on the water: "key" (typing) or "fail" (a wrong password, red)."""
        self.ocean.ripple(kind, time.monotonic() - self.t0)


class Scene(Gtk.Widget):
    """The wallpaper blurred, the ocean over it, the card's frosted glass: drawn on the GPU every frame."""

    def __init__(self, field, bg):
        super().__init__()
        self.field, self.bg, self.last = field, bg, 0.0
        self.base = rgba("#1a1b26")
        self.tint = rgba("#1a1b26", 0.5)
        self.edge = rgba("#3b4261", 0.7)
        self.add_tick_callback(self.tick)

    def tick(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        if now - self.last >= 1 / FPS - 0.002:
            self.last = now
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def picture(self, snap, w, h, frame):
        if self.bg:
            snap.append_texture(self.bg, rect(0, 0, w, h))
        else:
            snap.append_color(self.base, rect(0, 0, w, h))
        mask, colour = frame
        snap.push_mask(Gsk.MaskMode.ALPHA)          # the mask: the characters
        snap.append_texture(mask, self.field.grid)
        snap.pop()
        snap.append_scaled_texture(colour, Gsk.ScalingFilter.NEAREST, self.field.grid)   # their colours
        snap.pop()

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        frame = self.field.frame()
        self.picture(snap, w, h, frame)
        cw, ch = CARD
        card = rect((w - cw) // 2, (h - ch) // 2, cw, ch)
        snap.push_clip(card)                         # frosted glass: the same picture, blurred
        snap.push_blur(BLUR)
        self.picture(snap, w, h, frame)
        snap.pop()
        snap.pop()
        snap.append_color(self.tint, card)
        border = Gsk.RoundedRect()
        border.init_from_rect(card, 0)
        snap.append_border(border, [1, 1, 1, 1], [self.edge] * 4)


CSS = """
.lock-card { color: #c0caf5; font-family: "JetBrainsMono Nerd Font"; }
.lock-card .clock { font-size: 72px; font-weight: 800; color: #c0caf5; }
.lock-card .date { font-size: 14px; color: #a9b1d6; }
.lock-card .field {
  min-height: 44px; background: alpha(#16161e, 0.65); border: 1px solid alpha(#3b4261, 0.9);
}
.lock-card .field:focus-within { border-color: #6b8fe0; }
.lock-card .field.fail, .lock-card .field.fail:focus-within { border-color: #f7768e; }
.lock-card .field .icon { font-size: 15px; color: #565f89; min-width: 44px; }
.lock-card .field:focus-within .icon { color: #6b8fe0; }
.lock-card .field.fail .icon, .lock-card .field.fail:focus-within .icon { color: #f7768e; }
.lock-card .field entry {
  min-height: 42px; padding: 0; font-size: 14px; color: #c0caf5; caret-color: #6b8fe0;
  background: none; border: none; border-radius: 0; box-shadow: none; outline: none;
}
.lock-card .field entry > text > placeholder { color: #565f89; }
.lock-card .field button {
  min-width: 44px; min-height: 42px; padding: 0; font-size: 15px; color: #3b4261;
  background: none; border: none; border-radius: 0; box-shadow: none; outline: none;
}
.lock-card .field.ready button { color: #6b8fe0; }
.lock-card .field.ready button:hover { color: #c0caf5; background: alpha(#6b8fe0, 0.25); }
.lock-card .msg { font-size: 12px; color: #565f89; min-height: 16px; }
.lock-card .msg.caps { color: #e0af68; }
.lock-card .msg.fail { color: #f7768e; }
"""

HINT = "Enter to unlock \u00b7 Esc to clear"


class Locker:
    def __init__(self, demo):
        self.loop = GLib.MainLoop()
        self.buffer = Gtk.EntryBuffer()
        self.buffer.connect("notify::text", self.on_typed)
        self.windows, self.entries, self.fields, self.msgs, self.clocks, self.dates = [], [], [], [], [], []
        self.busy = False
        self.failed = ""        # what PAM said after the last wrong password, until something is typed
        self.shaken = 0.0       # when the fields started shaking
        self.h24 = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "clock-format"],
                                  capture_output=True, text=True).stdout.strip() != "'12h'"
        display = Gdk.Display.get_default()
        css = Gtk.CssProvider()
        css.load_from_string(themelib.recolor(CSS))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.keyboard = display.get_default_seat().get_keyboard()
        if self.keyboard:
            self.keyboard.connect("notify::caps-lock-state", lambda *_: self.show())
        mons = display.get_monitors()
        big = max((mons.get_item(i).get_geometry() for i in range(mons.get_n_items())),
                  key=lambda g: g.width * g.height)
        self.field = Field(big.width, big.height)
        self.lock = Gtk4SessionLock.Instance.new()
        self.lock.connect("monitor", self.on_monitor)
        self.lock.connect("failed", lambda *_: self.quit(2))
        self.lock.connect("unlocked", lambda *_: self.quit(0))
        self.status = 1
        GLib.timeout_add(1000, self.tick_clock)
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
        overlay.set_child(Scene(self.field, background(g.width, g.height)))
        overlay.add_overlay(self.card())
        win.set_child(overlay)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        win.add_controller(keys)
        self.windows.append(win)
        lock.assign_window_to_monitor(win, monitor)
        self.entries[-1].grab_focus()

    def card(self):
        """The clock, the date and the password field, for one screen."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER,
                       valign=Gtk.Align.CENTER, spacing=6)
        card.set_size_request(*CARD)
        card.add_css_class("lock-card")
        clock, date = Gtk.Label(), Gtk.Label()
        clock.add_css_class("clock")
        date.add_css_class("date")
        clock.set_margin_top(30)

        field = Gtk.Box(halign=Gtk.Align.CENTER, margin_start=12, margin_end=12)
        field.add_css_class("field")
        field.set_size_request(356, -1)
        field.set_margin_top(26)
        icon = Gtk.Label(label="\uf023")                          # a padlock
        icon.add_css_class("icon")
        entry = Gtk.Entry(buffer=self.buffer, visibility=False, xalign=0, hexpand=True,
                          placeholder_text=f"Password for {getpass.getuser()}")
        entry.set_invisible_char("\u25cf")
        entry.connect("activate", self.submit)
        go = Gtk.Button(label="\uf061", focusable=False, tooltip_text="Unlock")   # an arrow
        go.connect("clicked", self.submit)
        for w in (icon, entry, go):
            field.append(w)

        msg = Gtk.Label()
        msg.add_css_class("msg")
        msg.set_margin_top(4)
        for w in (clock, date, field, msg):
            card.append(w)
        self.entries.append(entry)
        self.fields.append(field)
        self.msgs.append(msg)
        self.clocks.append(clock)
        self.dates.append(date)
        self.tick_clock()
        self.show()
        return card

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape and not self.busy:
            self.failed = ""
            self.buffer.set_text("", -1)
            self.show()
            return True
        return False

    def on_typed(self, *_):
        if self.buffer.get_length():
            self.failed = ""
            self.field.ripple("key")
        self.show()

    def tick_clock(self):
        t = time.localtime()
        hm = time.strftime("%H:%M" if self.h24 else "%-I:%M", t)
        date = f"{DAYS[t.tm_wday]}, {t.tm_mday} {MONTHS[t.tm_mon - 1]}"   # English, whatever the locale
        for c in self.clocks:
            c.set_text(hm)
        for d in self.dates:
            d.set_text(date)
        return True

    def show(self):
        """The field and the line under it, from the state: checking, a wrong password, Caps Lock, or the
        hint."""
        caps = bool(self.keyboard and self.keyboard.get_caps_lock_state())
        if self.busy:
            text, kind = "Checking\u2026", ""
        elif self.failed:
            text, kind = f"\uf00d  {self.failed}", "fail"
        elif caps:
            text, kind = "\uf071  Caps Lock is on", "caps"
        else:
            text, kind = HINT, ""
        ready = self.buffer.get_length() > 0 and not self.busy
        for m in self.msgs:
            m.set_text(text)
            for k in ("fail", "caps"):
                (m.add_css_class if k == kind else m.remove_css_class)(k)
        for f in self.fields:
            (f.add_css_class if self.failed else f.remove_css_class)("fail")
            (f.add_css_class if ready else f.remove_css_class)("ready")
        for e in self.entries:
            e.set_editable(not self.busy)

    def submit(self, *_):
        password = self.buffer.get_text()
        if self.busy or not password:
            return
        self.busy = True
        self.show()

        def work():
            ok, said = authenticate(password)
            GLib.idle_add(self.done, ok, said)
        threading.Thread(target=work, daemon=True).start()

    def done(self, ok, said):
        self.busy = False
        if ok:
            self.lock.unlock()
            return False
        self.buffer.set_text("", -1)
        self.failed = said or "Wrong password"
        self.show()
        self.field.ripple("fail")
        self.shaken = time.monotonic()
        for f in self.fields:
            f.add_tick_callback(self.shake)
        return False

    def shake(self, field, _clock):
        """A wrong password: the field shakes side to side, settling within SHAKE seconds."""
        t = time.monotonic() - self.shaken
        off = 0 if t >= SHAKE else round(9 * math.sin(t * 42) * (1 - t / SHAKE))
        field.set_margin_start(12 + off)
        field.set_margin_end(12 - off)
        return GLib.SOURCE_CONTINUE if t < SHAKE else GLib.SOURCE_REMOVE

    def run(self):
        if not self.lock.lock():
            return 2
        self.loop.run()
        return self.status


def main():
    demo = float(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--demo" else 0
    Gtk.init()
    if not Gtk4SessionLock.is_supported():
        sys.exit(2)
    sys.exit(Locker(demo).run())


if __name__ == "__main__":
    main()
