#!/usr/bin/env python3
# The lock screen: one window per screen through the Wayland session-lock protocol (gtk4-layer-shell's
# Gtk4SessionLock: the compositor keeps the screen locked even if this dies), showing the wallpaper
# dimmed and blurred, Conway's Game of Life drifting over it (life.py), and a frosted-glass card with the
# clock, the date and the password. Started by local/bin/lockscreen (hypridle's lock_cmd), which falls back
# to hyprlock if this fails or dies; the password goes through PAM, hyprlock's service (/etc/pam.d/hyprlock).
#
# Light on purpose: Python only steps the Life grid every STEP seconds (~2 ms) and makes it a tiny texture,
# one pixel per cell. Everything else is the GPU's: the texture scaled up, cut into dots by a mask, faded in
# over the last few generations fading out (so the dots glide, with a short trail), blurred under the card.
# Frames stop while the screens are off (no frame callbacks then).
#
#   locker.py            lock
#   locker.py --demo N   lock, and unlock by itself after N seconds (to look at it)
import ctypes
import ctypes.util
import getpass
import hashlib
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
from life import Life  # noqa: E402

STEP = 0.2          # seconds per generation, all of it spent fading into the next (at a steady pace:
                    # an eased fade made every step start and stop, a pulse instead of a flow)
TRAIL = 4           # generations a cell takes to fade out once dead: moving shapes leave a short trail
FPS = 40            # frames a second at most
CELL = 10           # px per cell
DOT = 5             # px: a dot's size in its cell
CARD = (440, 300)   # the card in the middle (px)
BLUR = 16           # the card's frosted glass
CACHE = os.path.expanduser("~/.cache/lockscreen")
WALL = os.path.expanduser("~/.local/state/wallpaper-current")

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
    """The Life grid shared by every screen: the previous and current generation as textures."""

    def __init__(self, w, h):
        self.life = Life(w // CELL + 1, h // CELL + 1)
        c = rgba("#737aa2")
        self.pixel = bytes(round(v * 255) for v in (c.red, c.green, c.blue)) + b"\xd8"
        self.cur = self.texture()
        self.past = []             # the generations before, newest first (at most TRAIL)
        self.dots = self.dot_grid()
        self.born = time.monotonic()
        GLib.timeout_add(int(STEP * 1000), self.step)

    def texture(self):
        life = self.life
        return Gdk.MemoryTexture.new(life.w, life.h, Gdk.MemoryFormat.R8G8B8A8,
                                     GLib.Bytes.new(life.rgba(self.pixel)), life.w * 4)

    def dot_grid(self):
        """The mask that cuts the cells into dots: one texture, drawn once (a repeated tile was ~20000
        draws a frame for the renderer, which cost a whole CPU core)."""
        import cairo
        w, h = self.life.w * CELL, self.life.h * CELL
        tile = cairo.ImageSurface(cairo.FORMAT_ARGB32, CELL, CELL)
        t = cairo.Context(tile)
        t.arc(CELL / 2, CELL / 2, DOT / 2, 0, 6.2832)
        t.set_source_rgba(1, 1, 1, 1)
        t.fill()
        pattern = cairo.SurfacePattern(tile)
        pattern.set_extend(cairo.EXTEND_REPEAT)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        cr.set_source(pattern)
        cr.paint()
        surf.flush()
        return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                     GLib.Bytes.new(bytes(surf.get_data())), surf.get_stride())

    def step(self):
        self.life.step()
        self.past = [self.cur] + self.past[:TRAIL - 1]
        self.cur = self.texture()
        self.born = time.monotonic()
        return True

    def layers(self):
        """(texture, opacity) from oldest to newest: the new generation fading in, the older ones fading
        out a little more every frame, so a glider's dots slide along with a fading trail."""
        t = min(1.0, (time.monotonic() - self.born) / STEP)
        out = []
        for age, tex in reversed(list(enumerate(self.past))):
            e = age + t                                  # how many generations ago, continuously
            out.append((tex, max(0.0, 1 - e / TRAIL) ** 2))
        out.append((self.cur, t))
        return out


class Scene(Gtk.Widget):
    """Wallpaper, the Life dots, the card's frosted glass: drawn on the GPU every frame."""

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

    def picture(self, snap, w, h):
        if self.bg:
            snap.append_texture(self.bg, rect(0, 0, w, h))
        else:
            snap.append_color(self.base, rect(0, 0, w, h))
        life = self.field.life
        grid = rect(0, 0, life.w * CELL, life.h * CELL)
        snap.push_mask(Gsk.MaskMode.ALPHA)          # the mask: a dot in every cell
        snap.append_texture(self.field.dots, grid)
        snap.pop()
        for tex, opacity in self.field.layers():    # the source: this generation over its trail
            if opacity > 0.01:
                snap.push_opacity(opacity)
                snap.append_scaled_texture(tex, Gsk.ScalingFilter.NEAREST, grid)
                snap.pop()
        snap.pop()

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        self.picture(snap, w, h)
        cw, ch = CARD
        card = rect((w - cw) // 2, (h - ch) // 2, cw, ch)
        snap.push_clip(card)                         # frosted glass: the same picture, blurred
        snap.push_blur(BLUR)
        self.picture(snap, w, h)
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
.lock-card entry {
  min-height: 42px; min-width: 340px; padding: 0 12px; font-size: 14px; color: #c0caf5;
  background: #292e42; border: 1px solid #3b4261; border-radius: 0; box-shadow: none; outline: none;
  caret-color: #6b8fe0;
}
.lock-card entry:focus-within { border-color: #6b8fe0; }
.lock-card entry.fail { border-color: #f7768e; }
.lock-card entry > text > placeholder { color: #565f89; }
.lock-card .msg { font-size: 12px; color: #565f89; min-height: 16px; }
.lock-card .msg.fail { color: #f7768e; }
"""


class Locker:
    def __init__(self, demo):
        self.loop = GLib.MainLoop()
        self.buffer = Gtk.EntryBuffer()
        self.windows, self.entries, self.msgs, self.clocks, self.dates = [], [], [], [], []
        self.busy = False
        self.h24 = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "clock-format"],
                                  capture_output=True, text=True).stdout.strip() != "'12h'"
        css = Gtk.CssProvider()
        css.load_from_string(themelib.recolor(CSS))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
        mons = Gdk.Display.get_default().get_monitors()
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

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER,
                       valign=Gtk.Align.CENTER, spacing=6)
        card.set_size_request(*CARD)
        card.add_css_class("lock-card")
        clock, date = Gtk.Label(), Gtk.Label()
        clock.add_css_class("clock")
        date.add_css_class("date")
        clock.set_margin_top(30)
        entry = Gtk.Entry(buffer=self.buffer, visibility=False, xalign=0.5, halign=Gtk.Align.CENTER,
                          placeholder_text=f"  {getpass.getuser()}")
        entry.set_invisible_char("\u2022")
        entry.set_margin_top(24)
        entry.connect("activate", self.submit)
        msg = Gtk.Label()
        msg.add_css_class("msg")
        for w in (clock, date, entry, msg):
            card.append(w)
        overlay.add_overlay(card)
        win.set_child(overlay)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        win.add_controller(keys)
        self.windows.append(win)
        self.entries.append(entry)
        self.msgs.append(msg)
        self.clocks.append(clock)
        self.dates.append(date)
        self.tick_clock()
        lock.assign_window_to_monitor(win, monitor)
        entry.grab_focus()

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape and not self.busy:
            self.buffer.set_text("", -1)
            self.say("")
            return True
        return False

    def tick_clock(self):
        t = time.localtime()
        hm = time.strftime("%H:%M" if self.h24 else "%-I:%M", t)
        date = f"{DAYS[t.tm_wday]}, {t.tm_mday} {MONTHS[t.tm_mon - 1]}"   # English, whatever the locale
        for c in self.clocks:
            c.set_text(hm)
        for d in self.dates:
            d.set_text(date)
        return True

    def say(self, text, fail=False):
        for m in self.msgs:
            m.set_text(text)
            (m.add_css_class if fail else m.remove_css_class)("fail")
        for e in self.entries:
            (e.add_css_class if fail else e.remove_css_class)("fail")

    def submit(self, _entry):
        password = self.buffer.get_text()
        if self.busy or not password:
            return
        self.busy = True
        self.say("checking…")

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
        self.say(said or "Wrong password", fail=True)
        return False

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
