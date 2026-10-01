#!/usr/bin/env python3
# syncgui.py — the Code Sync panel as a GTK window (Tokyo Night): everything about the ~/code -> HDD
# backup. Opened by the sync icon in waybar; running it again closes it. Reads codesync's state;
# edits its settings.json and, through codesync, this PC's two folders (machine.json).
#
#   left   status in words, the last sync, what the backup holds, today, each top-level folder
#   right  the two folders (Change… opens a folder dialog), the timing settings, and recent
#          changes — only that list scrolls
#
#   s  sync now     p  pause / resume     r  restore the backup into an empty code folder
#   i  ignore list     l  log     t  old versions     ↑↓  select     ←→ / - +  change a setting
#   Enter  change the selected folder     y / n  apply / cancel a folder change
# The data helpers (tree_size, the settings table…) are syncpanel.py's, the terminal version.
import datetime, json, os, signal, subprocess, sys, threading, time
from fnmatch import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, Pango, View, box, button, clear, label, rule_heading, run, scrolled
import syncpanel
from syncpanel import CODESYNC, FOLDERPICK, FOLDERS, SETTINGS, load_codesync, secs, tilde, tree_size

cs = syncpanel.cs
STATUS = {  # words, and whether it's a problem (shown in red)
    "ok": ("Up to date", False),
    "pending": ("Changes waiting", False),
    "syncing": ("Syncing…", False),
    "paused": ("Paused", False),
    "error": ("Problem", True),
    "restore": ("Restore needed", True),
    "restoring": ("Restoring…", False),
    "off": ("Service not running", True),
}


def path_label(text, *classes):
    """A path that shortens from the left, so the folder's own name stays visible."""
    w = label(text, *classes, ellipsize=True)
    w.set_ellipsize(Pango.EllipsizeMode.START)
    return w


def kv(grid, row, key, value, *classes):
    k = label(key, "dim")
    k.set_size_request(120, -1)
    k.set_valign(Gtk.Align.START)
    v = label(value, *classes, wrap=True)   # a long value wraps instead of losing its end
    v.set_hexpand(True)
    grid.attach(k, 0, row, 1, 1)
    grid.attach(v, 1, row, 1, 1)


def table(rows):
    """Labelled rows (name, value) in two aligned columns."""
    grid = Gtk.Grid(column_spacing=16, row_spacing=5)
    for i, (k, v) in enumerate(rows):
        kv(grid, i, k, v)
    return grid


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


REASON = {"change": "after your edits", "periodic": "full check", "manual": "sync now", "start": "at startup"}


class CodeSync(View):
    title = "Code Sync"
    icon = "\U000f04e6"   # as the waybar sync icon
    interval = 1.0
    css = """
    .sync-col { margin: 18px 20px; }
    .sync-opts > row { padding: 5px 10px; }
    .sync-step { padding: 0 9px; min-width: 0; }
    .sync-num { margin-left: 18px; }
    .red { color: #f7768e; }
    separator { background: alpha(#3b4261, .7); min-width: 1px; }
    """

    def __init__(self):
        super().__init__()
        self.folders, self.trash, self.measured = {}, (0, 0), 0
        self.busy = None        # what a background step is doing ("waiting for the folder dialog…")
        self.proposal = None    # a picked folder waiting for apply / cancel: dict(src, dst, which, warning, preview, restore)
        self.remeasure = threading.Event()
        self.load()
        threading.Thread(target=self.measure, daemon=True).start()

    @property
    def subtitle(self):
        return f"{tilde(cs.SRC)} → {tilde(cs.DST)}"

    @property
    def hints(self):
        if self.proposal:
            return [("y", "apply"), ("n", "cancel")]
        pairs = [("s", "sync now"), ("p", "resume" if self.paused else "pause"), ("↑↓", "select"),
                 ("Enter", "folder"), ("←→", "setting"), ("i", "ignore list"), ("l", "log"), ("t", "old versions")]
        return ([("r", "restore")] if self.status_key() == "restore" else []) + pairs

    # ---- data ------------------------------------------------------------------------------------
    def measure(self):
        """The slow numbers (walks the disks), in the background, every 2 minutes or on demand."""
        while True:
            dirs, files = cs.ignore_patterns()
            out = {}
            try:
                for name in sorted(os.listdir(cs.SRC)):
                    p = os.path.join(cs.SRC, name)
                    if os.path.isdir(p) and not any(fnmatch(name, x) for x in dirs):
                        out[name] = tree_size(p, dirs, files)
            except OSError:
                pass
            self.folders = out
            size = days = 0
            try:
                for day in os.listdir(cs.TRASH):
                    days += 1
                    size += tree_size(os.path.join(cs.TRASH, day), [], [])[2]
            except OSError:
                pass
            self.trash, self.measured = (size, days), time.time()
            self.remeasure.wait(120)
            self.remeasure.clear()

    def load(self):
        self.state = cs.load_state()
        self.cfg = cs.settings()
        self.paused = os.path.exists(cs.PAUSED)
        self.pid = cs.service_pid()

    def status_key(self):
        st = self.state.get("status", "off")
        if st == "restoring" and cs.restoring():
            return st  # runs outside the service, so it shows even when the service is off
        return st if self.pid else "off"

    # ---- layout ----------------------------------------------------------------------------------
    def header_extra(self):
        self.pause_btn = button("Pause", lambda: self.act("p"), "flat", tooltip="Pause / resume syncing (p)")
        return [button("Sync now", lambda: self.act("s"), "flat", tooltip="s"), self.pause_btn]

    def build(self):
        self.left = box(True, 0, classes=("sync-col",))
        self.left.set_hexpand(True)

        # right: the folders and settings are one selectable list (↑↓, Enter, ←→), kept across refreshes
        self.opts = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.opts.add_css_class("sync-opts")
        self.opts.connect("row-activated", lambda _l, row: row.index < len(FOLDERS) and self.choose(FOLDERS[row.index][0]))
        self.opt_values = []
        for i, (key, title, role) in enumerate(FOLDERS):
            value = path_label("")
            value.set_hexpand(True)
            change = button("Change…", lambda k=key: self.choose(k), tooltip="Pick another folder (Enter)")
            self.add_opt(i, box(False, 12, label(title), label(role, "dim"), value, change), value)
        for j, (key, title, _, fmt) in enumerate(SETTINGS):
            i = len(FOLDERS) + j
            value = label("", "bold", xalign=0.5)
            value.set_size_request(80, -1)
            name = label(title, ellipsize=True)
            minus = button("−", lambda i=i: self.change(-1, i), "sync-step", tooltip="← or -")
            plus = button("+", lambda i=i: self.change(+1, i), "sync-step", tooltip="→ or +")
            self.add_opt(i, box(False, 8, name, minus, value, plus), value)
        self.opts.select_row(self.opts.get_row_at_index(0))

        self.confirm = box(True, 0)        # the folder-change question, when there is one
        self.busy_label = label("", "accent")
        self.next_full = label("", "dim")
        self.recent = box(True, 2)
        top = rule_heading("Folders and settings")   # first heading: no gap above, level with the left side
        top.set_margin_bottom(6)
        fixed = box(True, 0, self.confirm, top, self.opts, self.busy_label, self.next_full,
                    rule_heading("Recent changes", spaced=True), classes=("sync-col",))
        fixed.set_margin_bottom(0)
        recent = scrolled(self.recent)          # only this part scrolls; folders and settings stay put
        self.recent.set_margin_start(20)
        self.recent.set_margin_end(20)
        self.recent.set_margin_bottom(12)
        right = box(True, 0, fixed, recent)
        right.set_size_request(520, -1)
        self.paint()
        GLib.idle_add(lambda: self.opts.get_row_at_index(0).grab_focus() and False)
        return box(False, 0, scrolled(self.left), Gtk.Separator(), right)

    def add_opt(self, i, child, value):
        row = Gtk.ListBoxRow(child=child)
        row.index = i
        self.opts.append(row)
        self.opt_values.append(value)

    def paint(self):
        self.paint_left()
        self.paint_right()
        self.pause_btn.set_label("Resume" if self.paused else "Pause")
        if self.host and not getattr(self.host, "status_timer", 0):
            self.host.show_hints()   # the hints follow pause / resume, restore, a pending folder change

    def explain(self, st):
        """One plain sentence under the status: what's going on and what happens next."""
        c = self.cfg
        return {
            "ok": "Everything in the code folder is in the backup.",
            "pending": f"Files changed. They're copied once you stop editing for {secs(c['quiet'])} "
                       f"(at most {secs(c['max_wait'])} after the first change).",
            "syncing": "Copying the changes to the backup now.",
            "paused": "Syncing is paused. Changes are still noticed and copied when you resume.",
            "restoring": "Copying the backup into the code folder.",
            "off": "The codesync service isn't running, so nothing is being backed up "
                   "(start it with: codesync enable).",
        }.get(st, "")

    def paint_left(self):
        clear(self.left)
        s, st = self.state, self.status_key()
        words, bad = STATUS.get(st, STATUS["ok"])
        self.left.append(label(words, "heading", *(["red"] if bad else [])))
        if st in ("error", "restore") and s.get("detail"):
            self.left.append(label(s["detail"], "red", wrap=True))
        sentence = self.explain(st)
        if sentence:
            self.left.append(label(sentence, "dim", wrap=True))
        if st == "restore":
            self.left.append(label("The code folder is empty but the backup isn't. Restore copies the backup "
                                   "into it; nothing is deleted, and syncing resumes when it's done.", "dim", wrap=True))
            restore = button("Restore", self.restore, "primary", tooltip="r")
            restore.set_halign(Gtk.Align.START)
            restore.set_margin_top(8)
            self.left.append(restore)

        # -- the last sync --
        last = s.get("last")
        if last and st not in ("restore", "restoring"):
            self.left.append(rule_heading("Last sync", spaced=True))
            what = []
            for key, word in (("added", "new"), ("updated", "changed"), ("deleted", "removed")):
                if last.get(key):
                    what.append(f"{last[key]:,} {word}")
            self.left.append(table([
                ("When", f"{cs.ago(last['at'])} · {REASON.get(last['reason'], last['reason'])}"),
                ("Copied", f"{plural(last['changes'], 'file')} · {cs.human(last.get('bytes', 0))} · "
                           f"took {last['took']:.1f} s"),
                ("Files", " · ".join(what) or "nothing changed")]))

        # -- what the backup holds --
        tot = s.get("totals") or {}
        skipped = sum(v[3] for v in self.folders.values())
        try:
            ssd_free = cs.human(os.statvfs(cs.SRC).f_bavail * os.statvfs(cs.SRC).f_frsize) + " free"
        except OSError:
            ssd_free = "?"
        try:
            hdd_free = cs.human(os.statvfs(cs.MOUNT).f_bavail * os.statvfs(cs.MOUNT).f_frsize) + " free" \
                if os.path.ismount(cs.MOUNT) else "not mounted"
        except OSError:
            hdd_free = "?"
        self.left.append(rule_heading("Backup", spaced=True))
        self.left.append(table([
            ("Holds", f"{plural(tot.get('files', 0), 'file')} in {plural(tot.get('dirs', 0), 'folder')} · "
                      f"{cs.human(tot.get('size', 0))}"),
            ("Left out", f"{cs.human(skipped)} matched by the ignore list" if self.measured else "measuring…"),
            ("Old versions", f"{cs.human(self.trash[0])} from {plural(self.trash[1], 'day')}, "
                             f"each kept {self.cfg['trash_days']} days" if self.measured else "measuring…"),
            ("Code drive", ssd_free),
            ("Backup drive", hdd_free),
            ("Watching", f"{plural(s.get('watched', 0), 'folder')} for changes")]))

        # -- today --
        t = s.get("today", {})
        if t.get("date") != datetime.date.today().isoformat():
            t = {}
        self.left.append(rule_heading("Today", spaced=True))
        self.left.append(table([
            ("Syncs", f"{t.get('syncs', 0):,}"),
            ("Copied", f"{t.get('added', 0):,} new · {t.get('updated', 0):,} changed · {cs.human(t.get('bytes', 0))}"),
            ("Removed", f"{t.get('deleted', 0):,} (kept as old versions)")]))

        # -- each top-level folder --
        self.left.append(rule_heading("Folders in the code folder", spaced=True))
        if self.folders:
            grid = Gtk.Grid(column_spacing=18, row_spacing=4)
            for c, text in enumerate(["folder", "files", "size", "left out"]):
                grid.attach(label(text, "dim", xalign=0.0 if c == 0 else 1.0), c, 0, 1, 1)
            for r, (name, (f, d, size, skip)) in enumerate(self.folders.items(), 1):
                name_label = label(name, ellipsize=True)
                name_label.set_size_request(150, -1)
                grid.attach(name_label, 0, r, 1, 1)
                grid.attach(label(f"{f:,}", xalign=1.0), 1, r, 1, 1)
                grid.attach(label(cs.human(size), xalign=1.0), 2, r, 1, 1)
                grid.attach(label(cs.human(skip), "dim", xalign=1.0), 3, r, 1, 1)
            self.left.append(grid)
        else:
            self.left.append(label("measuring…" if not self.measured else "the code folder is empty", "dim"))

    def paint_right(self):
        clear(self.confirm)
        if self.proposal:
            self.paint_confirm()
        for i, value in enumerate(self.opt_values):
            if i < len(FOLDERS):
                value.set_text(tilde(cs.SRC if FOLDERS[i][0] == "src" else cs.DST))
            else:
                key, _, _, fmt = SETTINGS[i - len(FOLDERS)]
                value.set_text(fmt(self.cfg[key]))
        self.busy_label.set_text(self.busy or "")
        self.busy_label.set_visible(bool(self.busy))
        full = self.state.get("last_full")
        self.next_full.set_visible(bool(full and not self.paused))
        if full:
            self.next_full.set_text(f"next full check in {secs(max(int(full + self.cfg['full_every'] - time.time()), 0))}")
        self.next_full.set_margin_top(6)

        clear(self.recent)
        recent = self.state.get("recent", [])
        for t, op, path in recent[:40]:
            stamp = time.strftime("%H:%M:%S", time.localtime(t))
            self.recent.append(box(False, 10, label(stamp, "dim"), label(op, "red" if op == "-" else "accent"),
                                   label(path, ellipsize=True)))
        if not recent:
            self.recent.append(label("nothing yet", "dim"))

    def paint_confirm(self):
        p, pv = self.proposal, self.proposal["preview"]
        new = p["src"] if p["which"] == "src" else p["dst"]
        what = "code folder" if p["which"] == "src" else "backup"
        head = label("Change folder", "dim")   # at the top of the column: no gap above
        head.set_margin_bottom(6)
        self.confirm.append(head)
        self.confirm.append(label(f"Use {tilde(new)} as the {what}?", "bold", wrap=True))
        lines = []
        if p["restore"]:
            lines.append(("That code folder is empty and the backup is not. Apply saves, then copies "
                          "the backup into it.", "dim"))
        elif pv is None:
            lines.append(("comparing the folders…", "dim"))
        elif pv.get("error"):
            lines.append((f"can't compare: {pv['error']}", "red"))
        else:
            lines.append((f"backup: {pv['new']:,} new · {pv['updated']:,} changed · "
                          f"{pv['removed']:,} to old versions", "dim"))
            if pv["removed"]:
                lines.append((f"{pv['removed']:,} backed-up files are not in that code folder; they move to "
                              f"old versions, kept {self.cfg['trash_days']} days.", "red"))
        if p["warning"]:
            lines.append((p["warning"], "red"))
        for text, cls in lines:
            self.confirm.append(label(text, cls, wrap=True))
        apply = button("Apply", self.apply, "primary", tooltip="y")
        apply.set_sensitive(self.can_apply())
        acts = box(False, 8, apply, button("Cancel", self.cancel, tooltip="n"))
        acts.set_margin_top(10)
        acts.set_margin_bottom(22)
        self.confirm.append(acts)

    # ---- actions ---------------------------------------------------------------------------------
    def refresh(self):
        self.load()
        self.paint()

    def later(self, fn, *args):
        """Run fn on the GTK thread (from a background thread)."""
        GLib.idle_add(lambda: fn(*args) and False)

    def selected(self):
        row = self.opts.get_selected_row()
        return row.index if row else 0

    def change(self, step, index=None):
        i = self.selected() if index is None else index
        if i < len(FOLDERS):
            return
        self.opts.select_row(self.opts.get_row_at_index(i))
        key, title, choices, fmt = SETTINGS[i - len(FOLDERS)]
        cur = self.cfg[key]
        idx = min(range(len(choices)), key=lambda n: abs(choices[n] - cur))
        new = choices[max(0, min(len(choices) - 1, idx + step))]
        cfg = dict(self.cfg, **{key: new})
        with open(cs.SETTINGS, "w") as f:  # in place: the file is a symlink into ~/dotfiles
            json.dump(cfg, f, indent=2)
            f.write("\n")
        if self.pid:
            os.kill(self.pid, signal.SIGUSR2)
        self.refresh()
        self.say(f"{title.rstrip('…').strip()}: {fmt(new)}")

    def choose(self, which):
        """Folder dialog, checks and a dry-run preview, in the background; ends in self.proposal."""
        if self.busy or self.proposal:
            return
        cur = cs.SRC if which == "src" else cs.DST
        title = "Code folder: where you work" if which == "src" else "Backup folder: where copies go"
        self.busy = "waiting for the folder dialog…"
        self.paint_right()

        def work():
            try:
                r = subprocess.run([FOLDERPICK, cur, title], capture_output=True, text=True)
                picked = r.stdout.strip()
                if r.returncode != 0 or not picked:
                    return  # cancelled
                src, dst = (picked, cs.DST) if which == "src" else (cs.SRC, picked)
                if os.path.realpath(src) == os.path.realpath(cs.SRC) and \
                        os.path.realpath(dst) == os.path.realpath(cs.DST):
                    self.later(self.say, "that's the folder already in use")
                    return
                error, warning = cs.check_folders(src, dst)
                if error:
                    self.later(self.say, error, "bad")
                    return
                restore = cs.needs_restore(src, dst)
                self.proposal = dict(which=which, src=src, dst=dst, warning=warning, preview=None, restore=restore)
                if not restore:
                    self.busy = "comparing the folders…"
                    self.later(self.paint)
                    self.proposal["preview"] = cs.preview(src, dst)
            except Exception as e:  # a background failure must not leave the panel stuck
                self.later(self.say, f"couldn't change the folder: {e}", "bad")
            finally:
                self.busy = None
                self.later(self.paint)

        threading.Thread(target=work, daemon=True).start()

    def can_apply(self):
        p = self.proposal
        return bool(p) and not (p["preview"] or {}).get("error") and (p["restore"] or p["preview"] is not None)

    def apply(self):
        global cs
        if not self.can_apply():
            return
        p, self.proposal = self.proposal, None
        cs.save_machine(p["src"], p["dst"])  # also restarts the service on the new folders
        cs = load_codesync()
        self.folders, self.measured = {}, 0
        self.remeasure.set()
        self.refresh()
        if p["restore"]:
            self.restore()
        elif not cs.service_pid():
            self.say("folders saved — start the backup service with: codesync enable")
        else:
            self.say(f"now backing up {tilde(cs.SRC)} → {tilde(cs.DST)}")

    def cancel(self):
        self.proposal = None
        self.paint()
        self.say("not changed")

    def restore(self):
        if cs.restoring():
            self.say("a restore is already running")
            return
        if not cs.needs_restore():
            self.say("nothing to restore: the code folder isn't empty")
            return
        subprocess.Popen([CODESYNC, "restore"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.say("restoring the backup into the code folder — syncing resumes when it's done")

    def act(self, k):
        if k == "r":
            self.restore()
        elif k == "s":
            if self.pid:
                os.kill(self.pid, signal.SIGUSR1)
                self.say("syncing now…")
            else:
                self.say("the service isn't running — start it with: codesync enable", "bad")
        elif k == "p":
            if self.paused:
                if os.path.exists(cs.PAUSED):
                    os.remove(cs.PAUSED)
            else:
                open(cs.PAUSED, "w").close()
            if self.pid:
                os.kill(self.pid, signal.SIGUSR2)
                if self.paused:
                    os.kill(self.pid, signal.SIGUSR1)  # resuming: catch up now
            self.say("resumed" if self.paused else "paused — changes will sync when resumed")
        elif k == "i":
            subprocess.Popen(["gnome-text-editor", cs.IGNORE], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say("ignore list opened — changes apply on the next sync")
        elif k == "l":
            subprocess.Popen(["uwsm", "app", "--", "kitty", "--class=TUI.float", "--title=codesync log", "-e",
                              "sh", "-c", "journalctl --user -u codesync -o cat -n 500 --no-pager | less -R +G"],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif k == "t":
            if not os.path.ismount(cs.MOUNT):
                self.say("the backup drive isn't mounted", "bad")
                return
            os.makedirs(cs.TRASH, exist_ok=True)
            subprocess.Popen(["dolphin", cs.TRASH], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.refresh()

    def key(self, keyval, state):
        if self.typing():
            return False
        name = Gdk.keyval_name(keyval) or ""
        if self.proposal:
            if name in ("y", "Y"):
                self.apply()
            elif name in ("n", "N"):
                self.cancel()
            else:
                return False
            return True
        if name in ("Left", "minus", "KP_Subtract"):
            self.change(-1)
        elif name in ("Right", "plus", "equal", "KP_Add"):
            self.change(+1)
        elif name in ("r", "s", "p", "i", "l", "t"):
            self.act(name)
        else:
            return False   # ↑↓ and Enter: the list handles them
        return True


def make():
    """The view, for run() here and for the Control Center."""
    return CodeSync()


def main():
    run(make(), "panels.codesync", (1180, 640))


if __name__ == "__main__":
    main()
