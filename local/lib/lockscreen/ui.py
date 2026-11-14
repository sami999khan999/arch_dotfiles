#!/usr/bin/env python3
# What the lock screen (locker.py) and the login screen (greeter.py) both show: the wallpaper blurred and
# dimmed under an animation drawn in shaded characters (ANIMATIONS: the night ocean, ocean.py, or the aurora,
# aurora.py), and a frosted-glass card with the clock, the date, who's logging in and the password field.
# Prompt is the card's state, shared by every screen (one password, typed once, shown on each); the caller
# only checks the password (PAM, greetd) and says how it went: busy(), failed(), passed().
#
# Light on purpose: NumPy draws the animation FPS times a second (a few ms: the characters as a mask, a colour
# pixel per cell). Everything else is the GPU's: the colours scaled up under the characters, the blur
# under the card. Frames stop while the screens are off (no frame callbacks then).
import hashlib
import math
import os
import pwd
import subprocess
import sys
import time

# GL, not GTK's default Vulkan: Vulkan flickers on this Haswell iGPU (see panels/gtkkit.py)
os.environ.setdefault("GSK_RENDERER", "gl")

import gi  # noqa: E402
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, Gsk, Gtk  # noqa: E402

# beside this file, both in ~/.local/lib and in the login screen's copy (system/greeter/install.sh)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "theme"))
sys.path.insert(0, HERE)
import themelib  # noqa: E402
from aurora import Aurora  # noqa: E402
from ocean import Ocean  # noqa: E402
from pwfield import FIELD_CSS, Dots, Sweep, keycaps, rect, rgba  # noqa: E402  (the password field)

ANIMATIONS = {"ocean": Ocean, "aurora": Aurora}     # Settings' choices, and "random": another each time
FPS = 30            # animation frames a second
FONT = "JetBrainsMono Nerd Font 9"    # the animation's characters (their size is its grid)
SHAKE = 0.45        # s the password field shakes after a wrong password
CARD = (440, 372)   # the card in the middle (px)
FIELD = 356         # the password field's width (px)
BLUR = 16           # the card's frosted glass
FACES = "/var/lib/AccountsService/icons"   # the user's picture (system/greeter/install.sh puts it there)

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


def background(w, h, wall, cache):
    """The wallpaper covering a w x h screen, blurred and dimmed: made once per wallpaper and size in cache (the
    GPU could blur it every frame, but a file costs nothing while locked)."""
    wall = os.path.realpath(wall)
    try:
        key = hashlib.md5(f"{wall} {os.stat(wall).st_mtime} {w}x{h}".encode()).hexdigest()[:12]
    except OSError:
        return None
    out = os.path.join(cache, f"bg-{w}x{h}-{key}.jpg")
    if not os.path.exists(out):
        try:
            os.makedirs(cache, exist_ok=True)
            for old in os.listdir(cache):
                if old.startswith(f"bg-{w}x{h}-"):
                    os.remove(os.path.join(cache, old))
        except OSError:
            return None
        # shrink → blur → enlarge: a big blur in a fraction of the time
        subprocess.run(["magick", wall, "-resize", f"{w}x{h}^", "-gravity", "center", "-extent", f"{w}x{h}",
                        "-resize", "10%", "-blur", "0x2", "-resize", f"{w}x{h}!", "-modulate", "38",
                        "-quality", "92", out], check=False)
    try:
        return Gdk.Texture.new_from_filename(out)
    except GLib.Error:
        return None


class Field:
    """The animation shared by every screen: its latest frame as textures (mask, colour), made at most FPS
    times a second, when a screen is about to draw (so never while they're off)."""

    def __init__(self, w, h, anim):
        self.size = w, h
        self.start(anim)

    def start(self, anim):
        self.name = anim
        self.anim = ANIMATIONS[anim](*self.size, FONT, themelib.rgbf)
        o = self.anim
        self.grid = rect(0, 0, o.px, o.py)
        self.t0 = time.monotonic()
        self.made = 0.0
        self.cur = self.textures()

    def textures(self):
        o = self.anim
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

    def step(self, by):
        """The next (by 1) or previous (-1) animation, from its start; returns its name."""
        names = list(ANIMATIONS)
        self.start(names[(names.index(self.name) + by) % len(names)])
        return self.name

    def ripple(self, kind):
        """Ripples on the water: "key" (typing) or "fail" (a wrong password, red)."""
        self.anim.ripple(kind, time.monotonic() - self.t0)


class Scene(Gtk.Widget):
    """The wallpaper blurred, the animation over it, the card's frosted glass: drawn on the GPU every frame."""

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
.lock-card .who { font-size: 13px; color: #a9b1d6; }
.lock-card .who .face { border: 1px solid alpha(#3b4261, 0.9); }
.lock-card .who .initial {
  min-width: 26px; min-height: 26px; font-size: 13px; font-weight: 800; color: #6b8fe0; background: #292e42;
}
""" + FIELD_CSS


def face(user):
    """The user's picture, square, or None (then their initial)."""
    try:
        return Gdk.Texture.new_from_filename(os.path.join(FACES, user))
    except GLib.Error:
        return None


def full_name(user):
    try:
        gecos = pwd.getpwnam(user).pw_gecos.split(",")[0].strip()
    except KeyError:
        gecos = ""
    return gecos or user


class Prompt:
    """The card's clock, date, user and password field on every screen, sharing one password. submit(password)
    is the caller's: it calls busy(), then failed(said, left) or passed(text). extra_key(keyval, state) -> True
    takes a key first (Ctrl + ←/→); layout() is the keyboard layout's code, or "" (one layout)."""

    def __init__(self, user, field, hints, submit, *, verb="Unlock", layout=lambda: "", extra_key=None, h24=True):
        self.user, self.field, self.hints, self.submit_cb = user, field, hints, submit
        self.verb, self.layout_of, self.extra_key, self.h24 = verb, layout, extra_key, h24
        self.buffer = Gtk.EntryBuffer()
        self.buffer.connect("notify::text", self.on_typed)
        self.cards = []         # per screen: a dict of its widgets
        self.checking = ""      # what the line says while the password is checked ("" = not checking)
        self.passed_text = ""   # the password was right: what the line says now
        self.reveal = False     # the password shown as text (the eye)
        self.left = None        # (tries faillock still allows, its lockout in minutes), after a wrong one
        self.failed_text = ""   # what went wrong with the last password, until something is typed
        self.shaken = 0.0       # when the fields started shaking
        self.face = face(user)
        self.name = full_name(user)
        display = Gdk.Display.get_default()
        css = Gtk.CssProvider()
        css.load_from_string(themelib.recolor(CSS))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self.keyboard = display.get_default_seat().get_keyboard()
        if self.keyboard:
            self.keyboard.connect("notify::caps-lock-state", lambda *_: self.show())
        GLib.timeout_add(1000, self.tick_clock)

    # ---- one card ----

    def card(self, win):
        """The card for one screen, and win's keys."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER, spacing=6)
        box.set_size_request(*CARD)
        box.add_css_class("lock-card")
        clock, date = Gtk.Label(), Gtk.Label()
        clock.add_css_class("clock")
        date.add_css_class("date")
        clock.set_margin_top(30)

        who = Gtk.Box(halign=Gtk.Align.CENTER, spacing=10)
        who.add_css_class("who")
        who.set_margin_top(22)
        if self.face:
            pic = Gtk.Image.new_from_paintable(self.face)
            pic.set_pixel_size(26)
            pic.add_css_class("face")
        else:
            pic = Gtk.Label(label=self.name[:1].upper())
            pic.add_css_class("initial")
        who.append(pic)
        who.append(Gtk.Label(label=self.name))

        row = Gtk.Box()
        icon = Gtk.Label(label="")                          # a padlock
        icon.add_css_class("icon")
        entry = Gtk.Entry(buffer=self.buffer, visibility=False, xalign=0, hexpand=True,
                          placeholder_text="Password")
        entry.connect("activate", self.submit)
        dots = Dots()
        text = Gtk.Overlay(hexpand=True)                          # the entry, its dots drawn over it
        text.set_child(entry)
        text.add_overlay(dots)
        layout = Gtk.Label(valign=Gtk.Align.CENTER, visible=False, tooltip_text="Keyboard layout")
        layout.add_css_class("layout")
        eye = Gtk.Button(label="", focusable=False, tooltip_text="Show the password (Ctrl+H)")
        eye.add_css_class("eye")
        eye.connect("clicked", lambda *_: self.toggle_reveal())
        go = Gtk.Button(label="", focusable=False, tooltip_text=self.verb)   # an arrow
        go.add_css_class("go")
        go.connect("clicked", self.submit)
        for w in (icon, text, layout, eye, go):
            row.append(w)
        field = Gtk.Overlay(halign=Gtk.Align.CENTER, margin_start=12, margin_end=12)
        field.add_css_class("field")
        field.set_size_request(FIELD, -1)
        field.set_margin_top(8)
        field.set_child(row)
        sweep = Sweep()
        field.add_overlay(sweep)

        msg = Gtk.Label(use_markup=True)
        msg.add_css_class("msg")
        msg.set_margin_top(6)
        for w in (clock, date, who, field, msg):
            box.append(w)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)    # before the entry's own Ctrl + ←/→
        keys.connect("key-pressed", self.on_key)
        win.add_controller(keys)
        self.cards.append(dict(clock=clock, date=date, icon=icon, entry=entry, dots=dots, layout=layout, eye=eye,
                               field=field, sweep=sweep, msg=msg))
        self.tick_clock()
        self.show()
        self.show_layout()
        return box

    def focus(self):
        self.cards[-1]["entry"].grab_focus()

    # ---- keys, typing ----

    def on_key(self, _ctl, keyval, _code, state):
        if self.extra_key and self.extra_key(keyval, state):
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_h, Gdk.KEY_H):
            self.toggle_reveal()
            return True
        # the layout switch key reaches here too: look once the compositor has switched
        GLib.timeout_add(120, lambda: self.show_layout() and False)
        if keyval == Gdk.KEY_Escape and not self.checking:
            self.failed_text = ""
            self.buffer.set_text("", -1)
            self.show()
            return True
        return False

    def toggle_reveal(self):
        self.reveal = not self.reveal
        for c in self.cards:
            c["entry"].set_visibility(self.reveal)
            b = c["eye"]
            b.set_label("" if self.reveal else "")   # eye slashed / eye
            b.set_tooltip_text(("Hide" if self.reveal else "Show") + " the password (Ctrl+H)")
            (b.add_css_class if self.reveal else b.remove_css_class)("on")
        self.show()

    def show_layout(self):
        code = self.layout_of()
        for c in self.cards:
            c["layout"].set_text(code)
            c["layout"].set_visible(bool(code))
        return True

    def on_typed(self, *_):
        if self.buffer.get_length():
            self.failed_text = ""
            self.field.ripple("key")
        self.show()

    def tick_clock(self):
        t = time.localtime()
        hm = time.strftime("%H:%M" if self.h24 else "%-I:%M", t)
        date = f"{DAYS[t.tm_wday]}, {t.tm_mday} {MONTHS[t.tm_mon - 1]}"   # English, whatever the locale
        for c in self.cards:
            c["clock"].set_text(hm)
            c["date"].set_text(date)
        return True

    # ---- the state, shown ----

    def show(self):
        """The field and the line under it, from the state: checking, right, wrong, Caps Lock, or the hint."""
        caps = bool(self.keyboard and self.keyboard.get_caps_lock_state())
        esc = GLib.markup_escape_text
        if self.passed_text:
            text, kind = esc(f"  {self.passed_text}"), "ok"
        elif self.checking:
            text, kind = esc(self.checking), ""
        elif self.failed_text:
            text, kind = esc(f"  {self.failed_text}"), "fail"
            if self.left and self.left[0] == 1:
                text = esc(f"  Last try before a {self.left[1]} min lockout")
            elif self.left and self.left[0] > 1:
                text += esc(f" · {self.left[0]} tries left")
        elif caps:
            text, kind = esc("  Caps Lock is on"), "caps"
        else:
            text, kind = keycaps(self.hints), ""
        n = self.buffer.get_length()
        states = {"fail": bool(self.failed_text), "caps": caps, "ok": bool(self.passed_text),
                  "busy": bool(self.checking), "ready": n > 0 and not self.checking and not self.passed_text}
        for c in self.cards:
            c["msg"].set_markup(text)
            for k in ("fail", "caps", "ok"):
                (c["msg"].add_css_class if k == kind else c["msg"].remove_css_class)(k)
            for k, on in states.items():
                (c["field"].add_css_class if on else c["field"].remove_css_class)(k)
            c["icon"].set_label("" if self.passed_text else "")   # open padlock / padlock
            c["sweep"].run(bool(self.checking))
            c["entry"].set_editable(not self.checking and not self.passed_text)
            masked = n > 0 and not self.reveal
            (c["entry"].add_css_class if masked else c["entry"].remove_css_class)("masked")
            c["dots"].sync(n if masked else 0)

    def submit(self, *_):
        password = self.buffer.get_text()
        if self.checking or self.passed_text or not password:
            return
        self.submit_cb(password)

    def busy(self, text="Checking…"):
        self.checking = text
        self.show()

    def passed(self, text):
        self.checking = ""
        self.passed_text = text
        self.show()

    def failed(self, said, left=None):
        """A wrong password (said: what went wrong, if more than that): the field clears, turns red, shakes."""
        self.checking = ""
        self.buffer.set_text("", -1)
        self.failed_text = said or "Wrong password"
        self.left = None if said else left      # PAM's own words (e.g. "account locked") say it already
        self.show()
        self.field.ripple("fail")
        self.shaken = time.monotonic()
        for c in self.cards:
            c["field"].add_tick_callback(self.shake)

    def shake(self, field, _clock):
        """A wrong password: the field shakes side to side, settling within SHAKE seconds."""
        t = time.monotonic() - self.shaken
        off = 0 if t >= SHAKE else round(9 * math.sin(t * 42) * (1 - t / SHAKE))
        field.set_margin_start(12 + off)
        field.set_margin_end(12 - off)
        return GLib.SOURCE_CONTINUE if t < SHAKE else GLib.SOURCE_REMOVE
