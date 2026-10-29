#!/usr/bin/env python3
# powergui.py — the power menu (Super + Esc, the power key, the bar's power button; scripts/power-menu.sh
# opens it): Lock, Suspend, Log out, Restart, Shutdown as tiles in a popup like every other panel.
# A tile's letter (L S O R P), ←→ and Enter, or a click; Log out, Restart and Shutdown ask once
# (the tile turns red: the same key, Enter or a click again within 3 s does it).
# Before Suspend, Log out, Restart or Shutdown, work only this PC has (uncommitted changes, commits not
# pushed) in ~/dotfiles or a project in ~/code turns the popup into a warning: each repo and what it has,
# Sync (dotfiles: dotsync) or Commit… (commitgui.py), and the same key or "… anyway" to go regardless.
import importlib.machinery, importlib.util, os, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gtkkit import Gdk, GLib, Gtk, View, box, button, label, rule_heading, run, scrolled

_loader = importlib.machinery.SourceFileLoader("projects", os.path.expanduser("~/.local/bin/projects"))
pj = importlib.util.module_from_spec(importlib.util.spec_from_loader("projects", _loader))
_loader.exec_module(pj)
DOTFILES = os.path.expanduser("~/dotfiles")

# (key, name, icon, command, asks first); Log out, Restart and Shutdown close every app first, in a panel
# that shows each one closing (sessionendgui.py)
END = os.path.join(HERE, "sessionendgui.py")
ACTIONS = [
    ("l", "Lock", "", ["loginctl", "lock-session"], False),
    ("s", "Suspend", "\U000f0904", ["systemctl", "suspend"], False),
    ("o", "Log out", "\U000f0343", [sys.executable, END, "logout"], True),
    ("r", "Restart", "\U000f0709", [sys.executable, END, "reboot"], True),
    ("p", "Shutdown", "\U000f0425", [sys.executable, END, "poweroff"], True),
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
.unsaved-row { padding: 4px 2px; border-bottom: 1px solid alpha(#c0caf5, .04); }
.unsaved-row button { padding: 0 8px; min-height: 0; }
"""


def unsaved_in(path):
    """What only this copy of the repo has, as phrases ([] if nothing): uncommitted changes, commits on
    no remote (a repo with no remote at all: only its changes)."""
    out = []
    ok, st = pj.git(path, "status", "--porcelain", timeout=10)
    if ok and st:
        n = len(st.splitlines())
        out.append(f"{n} uncommitted change{'s' * (n != 1)}")
    ok, names = pj.git(path, "remote", timeout=5)
    if ok and names:
        ok, n = pj.git(path, "rev-list", "--count", "--branches", "--not", "--remotes", timeout=10)
        if ok and n not in ("", "0"):
            out.append(f"{n} commit{'s' * (n != '1')} not pushed")
    return out


def unsaved():
    """[(name, project path under ~/code or None for the dotfiles, phrases)] of every repo with work only
    this PC has: ~/dotfiles and each project in the list that's here."""
    data = pj.load()
    rels = sorted(e["path"] for e in data["projects"] + data["local_only"] if pj.is_repo(f"{pj.ROOT}/{e['path']}"))
    repos = [("dotfiles", None, DOTFILES)] + [(rel, rel, f"{pj.ROOT}/{rel}") for rel in rels]
    with ThreadPoolExecutor(8) as pool:
        found = list(pool.map(lambda r: unsaved_in(r[2]), repos))
    return [(name, rel, what) for (name, rel, _path), what in zip(repos, found) if what]


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
        self.unsaved = None       # [(name, rel, phrases)] once checked
        self.waiting = None       # the action pressed before the check was done
        self.warned = None        # the action the warning is up for (its key again goes anyway)
        threading.Thread(target=self.check, daemon=True).start()

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
        self.warning = box(True, 0)
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(page, "tiles")
        self.stack.add_named(self.warning, "warning")
        for w in (page, self.warning):
            for edge in ("start", "end", "top", "bottom"):
                getattr(w, f"set_margin_{edge}")(18)
        GLib.idle_add(lambda: self.tiles["l"].grab_focus() and False)
        return self.stack

    # -- work not committed --
    def check(self):
        try:
            found = unsaved()
        except Exception:   # a broken list mustn't stop the power menu
            found = []
        GLib.idle_add(lambda: self.checked(found) and False)

    def checked(self, found):
        self.unsaved = found
        if self.waiting:
            key, self.waiting = self.waiting, None
            self.note.set_text("")
            self.press(key)
        elif self.warned and found:
            self.warn(self.warned)   # after a Sync: what's left
        elif self.warned:            # nothing left: the tiles again, the one pressed focused
            key = self.warned
            self.back()
            self.tiles[key].grab_focus()
            self.note.set_text("everything committed and pushed")
        return False

    def warn(self, key):
        """The popup becomes the warning: each repo, what it has, a way to save it; Back, or go anyway."""
        name = next(a[1] for a in ACTIONS if a[0] == key)
        self.disarm()
        self.warned = key
        child = self.warning.get_first_child()
        while child:
            self.warning.remove(child)
            child = self.warning.get_first_child()
        rows = box(True, 0)
        for repo, rel, what in self.unsaved:
            act = (button("Sync", self.dotsync, "flat", tooltip="dotsync: commit, pull, push") if rel is None else
                   button("Commit…", lambda r=rel: self.commit(r), "flat"))
            detail = label(" · ".join(what), "amber", ellipsize=True)
            detail.set_hexpand(True)
            rows.append(box(False, 10, label(repo, "sub"), detail, act, classes=("unsaved-row",)))
        n = len(self.unsaved)
        self.warning.append(rule_heading("Not committed", f"{n} repo{'s' * (n != 1)}"))
        self.warning.append(scrolled(rows))
        anyway = button(f"{name} anyway", lambda: self.go(key), "danger")
        back = button("Back", self.back, "flat")
        said = label("Only this PC has these. Commit and push first?", "dim", ellipsize=True)
        said.set_hexpand(True)
        bottom = box(False, 8, said, back, anyway)
        bottom.set_margin_top(12)
        self.warning.append(bottom)
        self.stack.set_visible_child_name("warning")
        self.hints = [(key.upper(), f"{name.lower()} anyway"), ("Esc", "close")]
        self.host.show_hints()
        GLib.idle_add(lambda: back.grab_focus() and False)

    def back(self):
        self.warned = None
        self.stack.set_visible_child_name("tiles")
        self.hints = type(self).hints
        self.host.show_hints()
        self.tiles["l"].grab_focus()

    def dotsync(self):
        """Commit, pull and push the dotfiles here, then look again."""
        self.say("dotsync…", "ok", 30)

        def run_it():
            try:
                r = subprocess.run([os.path.expanduser("~/.local/bin/dotsync")], capture_output=True, text=True,
                                   timeout=120)
                ok, said = r.returncode == 0, pj.last_line(r.stdout + r.stderr, "dotsync failed")
            except (OSError, subprocess.TimeoutExpired) as e:
                ok, said = False, f"dotsync: {e}"
            GLib.idle_add(lambda: self.synced(ok, said) and False)
        threading.Thread(target=run_it, daemon=True).start()

    def synced(self, ok, said):
        self.say(said, "ok" if ok else "bad", 8)
        threading.Thread(target=self.check, daemon=True).start()
        return False

    def commit(self, rel):
        """The project's Commit popup, in place of this one (the power menu again when it's done)."""
        self.close()
        subprocess.Popen([sys.executable, os.path.join(HERE, "commitgui.py"), rel], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def press(self, key):
        _key, name, _icon, cmd, ask = next(a for a in ACTIONS if a[0] == key)
        if key != "l" and self.warned != key:   # the system goes down or to sleep: anything not committed?
            if self.unsaved is None:
                self.waiting = key
                self.note.set_text("looking for work not committed…")
                return
            if self.unsaved:
                return self.warn(key)
        if ask and self.armed != key and self.warned != key:   # the first press arms it: the tile turns red
            self.disarm()
            self.armed = key
            self.tiles[key].add_css_class("armed")
            self.tiles[key].name_label.set_text(f"{name}?")
            self.tiles[key].grab_focus()
            self.note.set_text(f"{name}: press {key.upper()}, Enter or click again")
            self.armed_timer = GLib.timeout_add(3000, self.disarm)
            return
        self.go(key)

    def go(self, key):
        cmd = next(a[3] for a in ACTIONS if a[0] == key)
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
        if self.warned:   # the warning: only its own key (go anyway); the buttons have the rest
            if name == self.warned and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
                self.go(name)
                return True
            return False
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
