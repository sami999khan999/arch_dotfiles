#!/usr/bin/env python3
# setupsgui.py — Setups (Super + Ctrl + R, the bar's briefcase): work environments saved by name — which
# windows, on which workspace, each Chrome window's tabs, each VS Code window's folder, each terminal's
# folder, agentmux's project — and opened again in one go. `setupsgui.py --login` is the same panel after a
# login (sessionlib.py ask): the last session selected, Enter opens it again, Esc leaves everything closed.
#
#   left   Last session (what the last Log out / Restart / Shutdown left; this PC's), then your setups
#   right  the selected one's windows by workspace: a setup's can be moved to another workspace or removed
#
# Keys: Enter open · r record the desktop as a new setup · n new empty setup · a add (an open window, a whole
# workspace, an app) · u update from the desktop · F2 rename · Delete delete (u and Delete ask once).
# Opening is sessionlib's: one window at a time, the next once the last one is up and the CPU has calmed
# down, each on its workspace while you stay on yours; what's open already is left alone. Setups live in
# ~/code/.projects/setups.json (the private project-list repo): pulled when the panel opens, committed and
# pushed after each change, so both PCs have the same ones.
import os, sys, threading, time, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, dropdown, label, rule_heading, run, scrolled
import sessionlib
from sessionendgui import STATES

ICON = "\U000f0814"   # nf-md-briefcase_outline, as in waybar
LAST = "last"         # the Last session's id in the list
ACTIONS = {"poweroff": "Shutdown", "reboot": "Restart", "logout": "Log out"}
CSS = """
.end-row { padding: 5px 2px; border-bottom: 1px solid alpha(#c0caf5, .04); }
.end-glyph { min-width: 18px; }
.end-row button { padding: 0 8px; min-height: 0; }
.end-row dropdown button { padding: 0 6px; }
.setup-list > row { padding: 8px 14px; }
.pick-row { padding: 3px 2px; }
"""


def item_glyph(item):
    return {"chrome": "\U000f02af", "code": "\U000f0a1e", "kitty": "",        # google-chrome, vscode, terminal
            "agentmux": "\U000f167a"}.get(item["kind"], "\U000f03d6")               # robot, package


def ws_label(ws, groups):
    if ws.lstrip("-").isdigit():
        g = groups.get(int(ws))
        return f"Workspace {ws}" + (f" · {g['name']}" if g else "")
    return ws.removeprefix("special:").capitalize()


class Setups(View):
    icon = ICON
    title = "Setups"
    interval = 0
    css = CSS

    def __init__(self, login=False):
        super().__init__()
        self.login = login
        self.setups = sessionlib.load_setups()
        self.session, asking = sessionlib.last_session()
        self.selected = LAST if self.session and (login or not self.setups) else \
            (self.setups[0]["id"] if self.setups else None)
        self.opening = False
        self.stopped = False
        self.picking = False
        self.renaming = None    # the Gtk.Entry while a name is typed
        self.armed = None       # (action, setup id) asked once: the same again within 3 s does it
        self.groups = sessionlib.wsgroups.groups()
        self.status_rows = []
        self.hints = [("Enter", "restore"), ("Esc", "not now")] if login else \
            [("Enter", "open"), ("r", "record"), ("n", "new"), ("a", "add"), ("u", "update"), ("F2", "rename"),
             ("Del", "delete"), ("Esc", "close")]
        self.subtitle = "open again what was open before?" if login else "work environments, opened in one go"

    # ---- data --------------------------------------------------------------------------------------------
    def session_items(self):
        s = self.session or {}
        # a list saved before items existed: one entry per app
        return s.get("items") or [{"kind": "app", "detail": "", **a} for a in s.get("apps", [])]

    def current(self):
        """(name, items, is it a saved setup) of the selected one."""
        if self.selected == LAST and self.session:
            when = time.strftime("%a %H:%M", time.localtime(self.session.get("at", 0)))
            return f"Last session · {ACTIONS.get(self.session.get('action'), 'ended')} {when}", self.session_items(), False
        s = self.setup(self.selected)
        return (s["name"], s["items"], True) if s else ("", [], False)

    def setup(self, sid):
        return next((s for s in self.setups if s["id"] == sid), None)

    def changed(self, message):
        """Save setups.json, then commit + push it in the background (the other PC pulls it)."""
        sessionlib.save_setups(self.setups)

        def push():
            problem = sessionlib.push_setups(message)
            if problem:
                GLib.idle_add(self.say, f"saved here, not synced: {problem}", "bad", 8)
        threading.Thread(target=push, daemon=True).start()

    def pull(self):
        """In a thread: what the other PC changed; the list redrawn if anything did."""
        sessionlib.pull_setups()
        fresh = sessionlib.load_setups()
        if fresh != self.setups:
            def apply():
                if not self.opening and not self.renaming:
                    self.setups = fresh
                    if self.selected not in (LAST, None) and not self.setup(self.selected):
                        self.selected = self.setups[0]["id"] if self.setups else (LAST if self.session else None)
                    self.paint()
            GLib.idle_add(apply)

    # ---- layout ------------------------------------------------------------------------------------------
    def build(self):
        if self.login:
            sessionlib.answered()   # offered once: opened or not, it isn't asked about again
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["setup-list"])
        self.list.connect("row-selected", self.row_selected)
        self.list.connect("row-activated", lambda *_: self.open_selected())
        self.list.set_margin_top(8)
        record = button("Record current", lambda _b: self.record(), "flat", tooltip="The desktop now, as a new setup (r)")
        new = button("New", lambda _b: self.new(), "flat", tooltip="An empty setup, to add windows to (n)")
        side = box(True, 0, scrolled(self.list), box(False, 4, record, new), classes=("side",))
        side.get_last_child().set_margin_start(10)
        side.get_last_child().set_margin_bottom(10)
        side.set_size_request(260, -1)
        self.detail = box(True, 0)
        for edge in ("start", "end", "top", "bottom"):
            getattr(self.detail, f"set_margin_{edge}")(18)
        self.paint()
        threading.Thread(target=self.pull, daemon=True).start()
        page = scrolled(self.detail)
        page.set_hexpand(True)   # the detail takes the rest of the width (a wrapped line otherwise wraps per word)
        return box(False, 0, side, page)

    def paint(self):
        self.paint_list()
        self.paint_detail()

    def paint_list(self):
        self.painting = True
        clear(self.list)
        entries = []
        if self.session:
            when = time.strftime("%a %H:%M", time.localtime(self.session.get("at", 0)))
            entries.append((LAST, "Last session", f"{ACTIONS.get(self.session.get('action'), 'ended')} · {when}"))
        for s in self.setups:
            n = len(s["items"])
            entries.append((s["id"], s["name"], f"{n} window{'s' * (n != 1)}"))
        for sid, name, sub in entries:
            row = Gtk.ListBoxRow(child=box(True, 1, label(name, "dim" if sid == LAST else "sub", ellipsize=True),
                                           label(sub, "dim", ellipsize=True)))
            row.sid = sid
            self.list.append(row)
            if sid == self.selected:
                self.list.select_row(row)
                GLib.idle_add(lambda r=row: r.grab_focus() and False)
        self.painting = False

    def row_selected(self, _l, row):
        if row and not self.painting and row.sid != self.selected and not self.opening:
            self.selected, self.picking, self.armed = row.sid, False, None
            self.cancel_rename()
            self.paint_detail()

    def paint_detail(self):
        clear(self.detail)
        self.status_rows = []
        if self.picking:
            return self.paint_picker()
        if self.selected is None:
            self.detail.append(label("No setups yet. Record current (r) saves the desktop as it is now; New (n) "
                                     "starts an empty one to add windows to.", "dim", wrap=True))
            return
        name, items, saved = self.current()
        head = box(False, 8)
        if self.renaming:
            head.append(self.renaming)
        else:
            head.append(label(name, "bold", ellipsize=True))
            head.get_last_child().set_hexpand(True)
        open_b = button("Restore" if self.selected == LAST else "Open", lambda _b: self.open_selected(), "primary")
        open_b.set_sensitive(bool(items) and not self.opening)
        if saved and not self.opening:
            for text, act, tip in (("Add", self.start_picking, "An open window, a whole workspace, an app (a)"),
                                   ("Update", self.update, "Replace its windows with the desktop now (u)"),
                                   ("Rename", self.rename, "F2"), ("Delete", self.delete, "Delete")):
                b = button(text, lambda _b, f=act: f(), "flat", tooltip=tip)
                if self.armed and self.armed[0] == act.__name__:
                    b.add_css_class("armed")
                head.append(b)
        elif self.selected == LAST and not self.opening and items:
            head.append(button("Save as setup", lambda _b: self.record(items=items), "flat"))
        head.append(open_b)
        self.detail.append(head)
        if not items:
            self.detail.append(label("Empty: Add (a) puts open windows, a whole workspace or an app in it.", "dim",
                                     wrap=True))
            self.detail.get_last_child().set_margin_top(16)
        last_ws = None
        for i, item in enumerate(items):
            if item["workspace"] != last_ws:
                last_ws = item["workspace"]
                self.detail.append(rule_heading(ws_label(last_ws, self.groups), spaced=True))
            self.detail.append(self.item_row(i, item, saved))
        if self.selected == LAST and (self.session or {}).get("threads"):
            self.detail.append(rule_heading("Agent threads", str(len(self.session["threads"])), spaced=True))
            self.detail.append(box(False, 10, label("\U000f167a", "end-glyph", "dim"), label("agentmux", "sub"),
                                   label(", ".join(self.session["threads"]), "dim", ellipsize=True),
                                   label("back with agentmux", "dim", xalign=1.0), classes=("end-row",)))

    def item_row(self, i, item, saved):
        glyph = label(item_glyph(item), "end-glyph", "dim")
        status = label("", "dim", xalign=1.0)
        detail = label(item.get("detail", ""), "dim", ellipsize=True)
        detail.set_hexpand(True)
        row = box(False, 10, glyph, label(item["name"], "sub"), detail, status, classes=("end-row",))
        self.status_rows.append((glyph, status))
        if saved and not self.opening:
            # short: the heading above already says it in full
            options = [(str(n), ws_label(str(n), self.groups).removeprefix("Workspace ")) for n in range(1, 11)] + \
                [("special:scratchpad", "Scratchpad")]
            if item["workspace"] not in [v for v, _ in options]:
                options.append((item["workspace"], ws_label(item["workspace"], self.groups)))
            row.append(dropdown(options, item["workspace"], lambda v, i=i: self.move(i, v)))
            row.append(button("×", lambda _b, i=i: self.remove(i), "flat", tooltip="Remove from this setup"))
        return row

    def set_state(self, i, state, text):
        if i < len(self.status_rows):
            glyph, status = self.status_rows[i]
            g, colour, said = STATES[state]
            glyph.set_text(g)
            for w in (glyph, status):
                for c in ("dim", "amber", "green", "red"):
                    w.remove_css_class(c)
                w.add_css_class(colour)
            status.set_text(text or said)
        return False

    # ---- the Add picker ----------------------------------------------------------------------------------
    def paint_picker(self):
        s = self.setup(self.selected)
        head = box(False, 8, label(f"Add to {s['name']}", "bold"))
        head.get_last_child().set_hexpand(True)
        head.append(button("Done", lambda _b: self.stop_picking(), "primary"))
        self.detail.append(head)
        items = sessionlib.capture()
        self.detail.append(rule_heading("Open windows", f"{len(items)}", spaced=True))
        last_ws = None
        for item in items:
            if item["workspace"] != last_ws:
                last_ws = item["workspace"]
                same = [it for it in items if it["workspace"] == last_ws]
                line = box(False, 10, label(ws_label(last_ws, self.groups), "accent"), classes=("pick-row",))
                line.get_last_child().set_hexpand(True)
                line.append(button(f"Add the workspace ({len(same)})", lambda _b, w=same: self.add(w), "flat"))
                self.detail.append(line)
            line = box(False, 10, label(item_glyph(item), "end-glyph", "dim"), label(item["name"], "sub"),
                       label(item.get("detail", ""), "dim", ellipsize=True), classes=("end-row",))
            line.get_last_child().set_hexpand(True)
            line.append(button("Add", lambda _b, it=item: self.add([it]), "flat"))
            self.detail.append(line)
        self.detail.append(rule_heading("Apps", spaced=True))
        for cls, name, argv in self.known_apps():
            line = box(False, 10, label(name, "sub"), label(" ".join(argv), "dim", ellipsize=True),
                       classes=("end-row",))
            line.get_last_child().set_hexpand(True)
            line.append(button("Add", lambda _b, c=cls, n=name, a=argv: self.add([sessionlib.app_item(c, n, a)]), "flat"))
            self.detail.append(line)

    def known_apps(self):
        """The apps this desktop knows how to start: the workspace groups' and the app hotkeys' (apps.conf)."""
        apps, seen = [], set()
        for n, g in sorted(self.groups.items()):
            for cls, cmd in zip(g["cls"].split("|"), g["launch"]):
                if cls not in seen and not cls.startswith(sessionlib.SKIP):
                    seen.add(cls)
                    apps.append((cls, sessionlib.name_of({"class": cls, "title": ""}) if not cls.startswith("sami.")
                                 else g["name"], [os.path.expanduser(a) for a in cmd.split()]))
        try:
            for line in open(os.path.expanduser("~/.config/hypr/apps.conf")):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) == 3 and not line.lstrip().startswith("#") and parts[1] not in seen:
                    seen.add(parts[1])
                    apps.append((parts[1], sessionlib.name_of({"class": parts[1], "title": ""}),
                                 [os.path.expanduser(a) for a in parts[2].split()]))
        except OSError:
            pass
        return apps

    # ---- actions -----------------------------------------------------------------------------------------
    def open_selected(self):
        name, items, _saved = self.current()
        if self.opening or self.picking or self.renaming or not items:
            return
        self.opening = True
        self.paint_detail()
        self.hints = [("Esc", "stop")]
        self.host.show_hints()

        def progress(i, state, text):
            GLib.idle_add(self.set_state, i, state, text)
            if state == "busy":
                GLib.idle_add(self.set_subtitle, f"opening {i + 1} of {len(items)}")

        def work():
            sessionlib.open_items(items, lambda: self.stopped, progress)
            GLib.idle_add(self.set_subtitle, f"{name} is open")
            GLib.timeout_add(1500, lambda: self.close() or False)
        threading.Thread(target=work, daemon=True).start()

    def record(self, items=None):
        """The desktop now (or the Last session's list) as a new setup; its name typed next."""
        items = items if items is not None else sessionlib.capture()
        s = {"id": uuid.uuid4().hex[:12], "name": self.free_name(), "created": time.time(), "updated": time.time(),
             "items": items}
        self.setups.append(s)
        self.selected = s["id"]
        self.changed(f"record {s['name']}")
        self.paint_list()
        self.rename()

    def new(self):
        self.record(items=[])

    def free_name(self):
        names = {s["name"] for s in self.setups}
        n = 1
        while f"Setup {n}" in names:
            n += 1
        return f"Setup {n}"

    def ask_once(self, action):
        """True the second time within 3 s (the button turns red the first time)."""
        if self.armed == (action, self.selected):
            self.armed = None
            return True
        self.armed = (action, self.selected)
        self.paint_detail()

        def disarm(armed=self.armed):
            if self.armed == armed:
                self.armed = None
                self.paint_detail()
        GLib.timeout_add(3000, lambda: disarm() or False)
        self.say("again to confirm", "ok", 3)
        return False

    def update(self):
        s = self.setup(self.selected)
        if s and self.ask_once("update"):
            s["items"], s["updated"] = sessionlib.capture(), time.time()
            self.changed(f"update {s['name']}")
            self.paint()

    def delete(self):
        s = self.setup(self.selected)
        if s and self.ask_once("delete"):
            self.setups.remove(s)
            self.selected = self.setups[0]["id"] if self.setups else (LAST if self.session else None)
            self.changed(f"delete {s['name']}")
            self.paint()

    def rename(self):
        s = self.setup(self.selected)
        if not s:
            return
        entry = Gtk.Entry(text=s["name"], hexpand=True)
        entry.connect("activate", lambda e: self.finish_rename(e.get_text()))
        self.renaming = entry
        self.paint_detail()
        GLib.idle_add(lambda: entry.grab_focus() and entry.select_region(0, -1) or False)

    def finish_rename(self, text):
        s = self.setup(self.selected)
        self.renaming = None
        if s and text.strip() and text.strip() != s["name"]:
            old, s["name"] = s["name"], text.strip()
            self.changed(f"rename {old} to {s['name']}")
        self.paint()

    def cancel_rename(self):
        if self.renaming:
            self.renaming = None
            self.paint_detail()

    def start_picking(self):
        if self.setup(self.selected):
            self.picking = True
            self.paint_detail()

    def stop_picking(self):
        self.picking = False
        self.paint()

    def add(self, items):
        s = self.setup(self.selected)
        if s:
            s["items"] = sorted(s["items"] + [dict(it) for it in items], key=sessionlib.ws_order)
            s["updated"] = time.time()
            self.changed(f"add {', '.join(it['name'] for it in items)} to {s['name']}")
            self.say(f"added {len(items)} to {s['name']}", "ok", 3)
            self.paint_list()

    def move(self, i, ws):
        s = self.setup(self.selected)
        if s and s["items"][i]["workspace"] != ws:
            s["items"][i]["workspace"] = ws
            s["items"].sort(key=sessionlib.ws_order)
            self.changed(f"{s['name']}: {s['items'][i]['name']} to workspace {ws}")
            GLib.idle_add(lambda: self.paint_detail() or False)   # not from inside the dropdown's own signal

    def remove(self, i):
        s = self.setup(self.selected)
        if s:
            gone = s["items"].pop(i)
            self.changed(f"{s['name']}: remove {gone['name']}")
            self.paint()

    def key(self, keyval, state):
        if self.renaming:
            if keyval == Gdk.KEY_Escape:
                self.cancel_rename()
                return True
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.open_selected()
            return True
        if self.login or self.opening or self.typing():
            return False
        actions = {Gdk.KEY_r: self.record, Gdk.KEY_n: self.new, Gdk.KEY_a: self.start_picking,
                   Gdk.KEY_u: self.update, Gdk.KEY_F2: self.rename, Gdk.KEY_Delete: self.delete}
        if keyval in actions:
            actions[keyval]()
            return True
        if keyval == Gdk.KEY_Escape and self.picking:
            self.stop_picking()
            return True
        return False

    def close(self):
        self.stopped = True
        super().close()


def make(login=False):
    return Setups(login)


def main():
    login = "--login" in sys.argv
    if login and not sessionlib.last_session()[1]:
        return
    run(make(login), "panels.setups", (900, 560))


if __name__ == "__main__":
    main()
