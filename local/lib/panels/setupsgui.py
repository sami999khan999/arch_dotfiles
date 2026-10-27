#!/usr/bin/env python3
# setupsgui.py — Setups (Super + Ctrl + R, the bar's briefcase): work environments saved by name — which
# windows, on which workspace, each Chrome window's tabs, each VS Code window's folder, each terminal's
# folder, agentmux's project — and opened again in one go. `setupsgui.py --login` is the same panel after a
# login (sessionlib.py ask): the last session selected, Enter opens it again, Esc leaves everything closed.
#
#   left   Last session (what the last Log out / Restart / Shutdown left; this PC's), then your setups
#   right  the selected one's workspaces, folded: a click shows its windows (× takes one out, + Add a window
#          puts an open window or an app in that workspace)
#
# Keys: Enter (or a double-click) open · r record the desktop as a new setup · n new empty setup · a add (an open window, a whole
# workspace, an app) · u update from the desktop · F2 rename · Delete delete (u and Delete ask once).
# Opening is sessionlib's: one window at a time, the next once the last one is up and the CPU has calmed
# down, each on its workspace while you stay on yours (the backdrop goes dark until it's done; closing the panel
# stops it); what's open already is left alone. Setups live in
# ~/code/.projects/setups.json (the private project-list repo): pulled when the panel opens, committed and
# pushed after each change, so both PCs have the same ones.
import os, sys, threading, time, uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, rule_heading, run, scrolled
import sessionlib
from sessionendgui import STATES

ICON = "\U000f0814"   # nf-md-briefcase_outline, as in waybar
LAST = "last"         # the Last session's id in the list
ACTIONS = {"poweroff": "Shutdown", "reboot": "Restart", "logout": "Log out"}
CSS = """
.end-row { padding: 5px 2px; border-bottom: 1px solid alpha(#c0caf5, .04); }
.end-glyph { min-width: 18px; }
.end-row button { padding: 0 8px; min-height: 0; }
.setup-list > row { padding: 8px 14px; }
.setup-head { min-height: 36px; margin-bottom: 10px; }
.pick-row { padding: 3px 2px; }
button.ws-head { padding: 7px 4px; border-bottom: 1px solid alpha(#c0caf5, .04); }
.ws-body { margin-left: 28px; }
"""
CHEVRON = {False: "\U000f0142", True: "\U000f0140"}   # nf-md-chevron_right / chevron_down


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
        self.worker = None   # the thread opening a setup
        self.picking = False
        self.renaming = None    # the Gtk.Entry while a name is typed
        self.armed = None       # (action, setup id) asked once: the same again within 3 s does it
        self.head_buttons = {}  # action -> its header button (turns red when armed)
        self.expanded = {}      # setup id -> the workspaces shown open (all start collapsed)
        self.pick_ws = None     # the workspace the Add picker adds to (None: each window's own)
        self.groups = sessionlib.wsgroups.groups()
        self.status_rows = {}   # item index -> (glyph, status) while its workspace is shown open
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
        self.host.win.connect("close-request", self.closing)
        if self.login:
            sessionlib.answered()   # offered once: opened or not, it isn't asked about again
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["setup-list"])
        self.list.connect("row-selected", self.row_selected)
        # a click only selects (it opened the setup at once); Enter, a double-click or Open opens it
        self.list.set_activate_on_single_click(False)
        self.list.connect("row-activated", lambda *_: self.open_selected())
        self.list.set_margin_top(8)
        record = button("Record current", lambda: self.record(), "flat", tooltip="The desktop now, as a new setup (r)")
        new = button("New", lambda: self.new(), "flat", tooltip="An empty setup, to add windows to (n)")
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
        self.status_rows = {}
        self.head_buttons = {}
        if self.picking:
            return self.paint_picker()
        if self.selected is None:
            self.detail.append(label("No setups yet. Record current (r) saves the desktop as it is now; New (n) "
                                     "starts an empty one to add windows to.", "dim", wrap=True))
            return
        name, items, saved = self.current()
        head = box(False, 8, classes=("setup-head",))
        if self.renaming:
            if self.renaming.get_parent():   # still in the header painted before: one parent at a time
                self.renaming.get_parent().remove(self.renaming)
            head.append(self.renaming)
        else:
            head.append(label(name, "bold", ellipsize=True))
            head.get_last_child().set_hexpand(True)
        open_b = button("Restore" if self.selected == LAST else "Open", lambda: self.open_selected(), "primary")
        open_b.set_sensitive(bool(items) and not self.opening)
        if saved and not self.opening:
            for text, act, tip in (("Add", lambda: self.start_picking(), "An open window, a whole workspace, an app (a)"),
                                   ("Update", self.update, "Replace its windows with the desktop now (u)"),
                                   ("Rename", self.rename, "F2"), ("Delete", self.delete, "Delete")):
                b = button(text, lambda f=act: self.from_header(f), "flat", tooltip=tip)
                if self.armed and self.armed[0] == getattr(act, "__name__", ""):
                    b.add_css_class("armed")
                self.head_buttons[getattr(act, "__name__", text)] = b
                head.append(b)
        elif self.selected == LAST and not self.opening and items:
            head.append(button("Save as setup", lambda: self.record(items=items), "flat"))
        head.append(open_b)
        self.detail.append(head)
        for ws in self.workspaces(items, saved):
            self.paint_workspace(ws, items, saved)
        if self.selected == LAST and (self.session or {}).get("threads"):
            self.detail.append(rule_heading("Agent threads", str(len(self.session["threads"])), spaced=True))
            self.detail.append(box(False, 10, label("\U000f167a", "end-glyph", "dim"), label("agentmux", "sub"),
                                   label(", ".join(self.session["threads"]), "dim", ellipsize=True),
                                   label("back with agentmux", "dim", xalign=1.0), classes=("end-row",)))

    def workspaces(self, items, saved):
        """The workspaces to list: a setup's all ten (so an empty one can be filled) and any other it uses;
        the last session's, and any while opening, only those with windows."""
        names = [str(n) for n in range(1, 11)] if saved and not self.opening else []
        for it in items:
            if it["workspace"] not in names:
                names.append(it["workspace"])
        return sorted(names, key=lambda w: sessionlib.ws_order({"workspace": w}))

    def paint_workspace(self, ws, items, saved):
        """One workspace: a header (click: open / close) with its apps' icons and how many windows; open,
        its windows (× takes one out) and + Add a window. All open while the setup is opening."""
        mine = [i for i, it in enumerate(items) if it["workspace"] == ws]
        shown = self.opening or ws in self.expanded.get(self.selected, set())
        count = f"{len(mine)} window{'s' * (len(mine) != 1)}" if mine else "empty"
        line = box(False, 10, label(CHEVRON[shown], "end-glyph", "dim"),
                   label(ws_label(ws, self.groups), "accent" if mine else "dim"),
                   label("  ".join(item_glyph(items[i]) for i in mine), "dim", ellipsize=True), label(count, "dim"))
        line.get_last_child().set_hexpand(True)
        line.get_last_child().set_xalign(1.0)
        head = Gtk.Button(child=line, css_classes=["flat", "ws-head"])
        head.connect("clicked", lambda *_: self.toggle_ws(ws))
        self.detail.append(head)
        if not shown:
            return
        body = box(True, 0, classes=("ws-body",))
        for i in mine:
            body.append(self.item_row(i, items[i], saved))
        if saved and not self.opening:
            add = button("+ Add a window", lambda: self.start_picking(ws), "flat")
            add.set_halign(Gtk.Align.START)
            body.append(add)
        self.detail.append(body)

    def toggle_ws(self, ws):
        if self.opening:
            return
        shown = self.expanded.setdefault(self.selected, set())
        shown.symmetric_difference_update({ws})
        self.paint_detail()

    def item_row(self, i, item, saved):
        glyph = label(item_glyph(item), "end-glyph", "dim")
        status = label("", "dim", xalign=1.0)
        detail = label(item.get("detail", ""), "dim", ellipsize=True)
        detail.set_hexpand(True)
        row = box(False, 10, glyph, label(item["name"], "sub"), detail, status, classes=("end-row",))
        self.status_rows[i] = (glyph, status)
        if saved and not self.opening:
            row.append(button("\u00d7", lambda i=i: self.remove(i), "flat", tooltip="Remove from this setup"))
        return row

    def set_state(self, i, state, text):
        if i in self.status_rows:
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
        where = f" · {ws_label(self.pick_ws, self.groups)}" if self.pick_ws else ""
        head = box(False, 8, label(f"Add to {s['name']}{where}", "bold"), classes=("setup-head",))
        head.get_last_child().set_hexpand(True)
        head.append(button("Done", lambda: self.stop_picking(), "primary"))
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
                line.append(button(f"Add the workspace ({len(same)})", lambda w=same: self.add(w), "flat"))
                self.detail.append(line)
            line = box(False, 10, label(item_glyph(item), "end-glyph", "dim"), label(item["name"], "sub"),
                       label(item.get("detail", ""), "dim", ellipsize=True), classes=("end-row",))
            line.get_last_child().set_hexpand(True)
            line.append(button("Add", lambda it=item: self.add([it]), "flat"))
            self.detail.append(line)
        self.detail.append(rule_heading("Apps", spaced=True))
        for cls, name, argv in self.known_apps():
            line = box(False, 10, label(name, "sub"), label(" ".join(argv), "dim", ellipsize=True),
                       classes=("end-row",))
            line.get_last_child().set_hexpand(True)
            line.append(button("Add", lambda c=cls, n=name, a=argv: self.add([sessionlib.app_item(c, n, a)]), "flat"))
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
        self.host.set_backdrop_opaque(True)   # the windows open behind it unseen, the screen stays still

        def progress(i, state, text):
            GLib.idle_add(self.set_state, i, state, text)
            if state == "busy":
                GLib.idle_add(self.set_subtitle, f"opening {i + 1} of {len(items)}")

        def done():
            self.set_subtitle(f"{name} is open")
            self.host.set_backdrop_opaque(False)
            GLib.timeout_add(1500, lambda: self.close() or False)

        def work():
            sessionlib.open_items(items, lambda: self.stopped, progress)
            if not self.stopped:
                GLib.idle_add(done)
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def closing(self, *_):
        """The panel closes (Esc, a click on the backdrop, done): a restore stops, nothing more opens.
        The window being opened is waited for a moment (it checks every 0.25 s), so the workspace rules it
        changed for its launch are put back before this process ends."""
        self.stopped = True
        if self.worker:
            self.worker.join(2)
        return False

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

    def from_header(self, action):
        """A header button pressed while the name is being typed: the name is kept first (the header
        stays as it is: no jump), then the button does its thing."""
        if self.renaming:
            s = self.setup(self.selected)
            text = self.renaming.get_text().strip()
            if s and text and text != s["name"]:
                old, s["name"] = s["name"], text
                self.changed(f"rename {old} to {text}")
                self.paint_list()
            self.renaming = None
            self.paint_detail()
            if action == self.rename:   # Rename again: done renaming
                return
        action()

    def ask_once(self, action):
        """True the second time within 3 s (the button turns red the first time; nothing else redrawn)."""
        if self.armed == (action, self.selected):
            self.armed = None
            return True
        self.armed = (action, self.selected)
        b = self.head_buttons.get(action)
        if b:
            b.add_css_class("armed")

        def disarm(armed=self.armed):
            if self.armed == armed:
                self.armed = None
                b = self.head_buttons.get(action)
                if b:
                    b.remove_css_class("armed")
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

    def start_picking(self, ws=None):
        """The Add picker: into workspace ws (a workspace's + Add a window), else each window to its own."""
        if self.setup(self.selected):
            self.picking, self.pick_ws = True, ws
            self.paint_detail()

    def stop_picking(self):
        if self.pick_ws:   # back to that workspace, open, with what was just added in it
            self.expanded.setdefault(self.selected, set()).add(self.pick_ws)
        self.picking, self.pick_ws = False, None
        self.paint()

    def add(self, items):
        s = self.setup(self.selected)
        if s:
            items = [dict(it, workspace=self.pick_ws) if self.pick_ws else dict(it) for it in items]
            s["items"] = sorted(s["items"] + items, key=sessionlib.ws_order)
            s["updated"] = time.time()
            self.changed(f"add {', '.join(it['name'] for it in items)} to {s['name']}")
            self.say(f"added {len(items)} to {s['name']}", "ok", 3)
            self.paint_list()

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
        actions = {Gdk.KEY_r: self.record, Gdk.KEY_n: self.new, Gdk.KEY_a: lambda: self.start_picking(),
                   Gdk.KEY_u: self.update, Gdk.KEY_F2: self.rename, Gdk.KEY_Delete: self.delete}
        if keyval in actions:
            actions[keyval]()
            return True
        if keyval == Gdk.KEY_Escape and self.picking:
            self.stop_picking()
            return True
        return False



def make(login=False):
    return Setups(login)


def main():
    login = "--login" in sys.argv
    if login and not sessionlib.last_session()[1]:
        return
    run(make(login), "panels.setups", (900, 560))


if __name__ == "__main__":
    main()
