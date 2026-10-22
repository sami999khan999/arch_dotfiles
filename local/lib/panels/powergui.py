#!/usr/bin/env python3
# powergui.py — the power menu (Super + Esc, the power key, the bar's power button; scripts/power-menu.sh
# opens it): Lock, Suspend, Log out, Restart, Shutdown as tiles in a popup like every other panel.
# A tile's letter (L S O R P), ←→ and Enter, or a click; Log out, Restart and Shutdown ask once
# (the tile turns red: the same key, Enter or a click again within 3 s does it).
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, label, run

# (key, name, icon, command, asks first); Log out, Restart and Shutdown close every app first, in a panel
# that shows each one closing (sessionendgui.py)
END = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessionendgui.py")
ACTIONS = [
    ("l", "Lock", "", ["loginctl", "lock-session"], False),
    ("s", "Suspend", "\U000f0904", ["systemctl", "suspend"], False),
    ("o", "Log out", "\U000f0343", [END, "logout"], True),
    ("r", "Restart", "\U000f0709", [END, "reboot"], True),
    ("p", "Shutdown", "\U000f0425", [END, "poweroff"], True),
]
CSS = """
button.tile { background: alpha(#c0caf5, .04); border: 1px solid alpha(#3b4261, .7); padding: 16px 0 12px 0;
              min-width: 104px; }
button.tile:hover { background: alpha(#c0caf5, .08); }
button.tile:focus-visible, button.tile:focus { background: #292e42; border-color: alpha(#6b8fe0, .55); }
button.tile .tile-icon { font-size: 22pt; color: #a9b1d6; }
button.tile:focus .tile-icon { color: #6b8fe0; }
button.tile .tile-key { color: #e0af68; font-size: 9pt; }
button.tile.armed { background: alpha(#f7768e, .16); border-color: #f7768e; }
button.tile.armed .tile-icon, button.tile.armed .tile-name { color: #f7768e; }
"""


def uptime():
    try:
        secs = int(float(open("/proc/uptime").read().split()[0]))
    except (OSError, ValueError):
        return ""
    d, h, m = secs // 86400, secs // 3600 % 24, secs // 60 % 60
    return "up " + (f"{d} d {h} h" if d else f"{h} h {m} min" if h else f"{m} min")


class Power(View):
    icon = "\U000f0425"   # nf-md-power
    title = "System"
    interval = 0
    css = CSS
    hints = [("L S O R P", "do it"), ("←→", "pick"), ("Enter", "do it"), ("Esc", "close")]

    def __init__(self):
        super().__init__()
        self.subtitle = uptime()
        self.armed, self.armed_timer = None, 0

    def build(self):
        self.tiles = {}
        row = box(False, 10)
        row.set_halign(Gtk.Align.CENTER)
        for key, name, icon, _cmd, _ask in ACTIONS:
            tile = Gtk.Button(css_classes=["tile"])
            tile.name_label = label(name, "tile-name", xalign=0.5)
            tile.set_child(box(True, 6, label(icon, "tile-icon", xalign=0.5), tile.name_label,
                                    label(key.upper(), "tile-key", xalign=0.5)))
            tile.connect("clicked", lambda _b, k=key: self.press(k))
            self.tiles[key] = tile
            row.append(tile)
        self.note = label("", "dim", xalign=0.5)
        page = box(True, 14, row, self.note)
        page.set_valign(Gtk.Align.CENTER)
        page.set_vexpand(True)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        GLib.idle_add(lambda: self.tiles["l"].grab_focus() and False)
        return page

    def press(self, key):
        _key, name, _icon, cmd, ask = next(a for a in ACTIONS if a[0] == key)
        if ask and self.armed != key:   # the first press arms it: the tile turns red
            self.disarm()
            self.armed = key
            self.tiles[key].add_css_class("armed")
            self.tiles[key].name_label.set_text(f"{name}?")
            self.tiles[key].grab_focus()
            self.note.set_text(f"{name}: press {key.upper()}, Enter or click again")
            self.armed_timer = GLib.timeout_add(3000, self.disarm)
            return
        self.close()
        # after the popup is gone (the lock screen mustn't show it, a restart needn't wait for it)
        subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def disarm(self):
        if self.armed_timer:
            GLib.source_remove(self.armed_timer)
            self.armed_timer = 0
        if self.armed:
            tile = self.tiles[self.armed]
            tile.remove_css_class("armed")
            tile.name_label.set_text(next(a[1] for a in ACTIONS if a[0] == self.armed))
            self.armed = None
            self.note.set_text("")
        return False

    def key(self, keyval, state):
        name = Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or ""
        if name in self.tiles and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            self.press(name)
        elif keyval == Gdk.KEY_Escape:
            if self.armed:
                self.disarm()
            else:
                self.close()
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and self.armed:
            self.disarm()   # moving away cancels the question
            return False    # and the focus still moves
        else:
            return False
        return True


def make():
    return Power()


def main():
    run(make(), "panels.power", (640, 290))


if __name__ == "__main__":
    main()
