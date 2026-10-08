#!/usr/bin/env python3
# commitgui.py — Commit, a popup like every other panel (the screen blurred behind it; Esc or a click
# outside closes it). The Projects panel opens it (c, or Commit…):  commitgui.py <project, under ~/code>
#   left   the message (subject, then a body if it needs one; the subject's length beside it; Write it for
#          me: a free model through opencode, picked in the dropdown beside it and kept for next time),
#          the changed files (all ticked: untick what stays out; a filter, All / None for what it shows,
#          each file's +/- lines), Push it after
#   right  the selected file's diff (a click on a file, or ↑↓ in the list)
# A normal git commit, so the project's hooks run; the Projects panel sees the result by itself (its live
# look at git).
import os, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, Pango, View, box, button, dropdown, label, recolor, run, scrolled
import projectsgui as pg

pj = pg.pj
SUBJECT_MAX = 72        # past it the subject's count turns amber (git log's one-line view cuts it)
DIFF_MAX = 4000         # lines of a diff shown at most
CSS = """
.commit-files { background: #16161e; border: 1px solid alpha(#3b4261, .7); }
.commit-files > row { padding: 2px 8px; }
.commit-files > row:selected { background: #292e42; }
textview.commit-message, textview.commit-message text { background: #16161e; color: #c0caf5; }
textview.commit-message { border: 1px solid alpha(#3b4261, .7); padding: 6px 8px; min-height: 96px; }
textview.commit-diff, textview.commit-diff text { background: #16161e; color: #a9b1d6; }
textview.commit-diff { font-family: "JetBrainsMono Nerd Font"; font-size: 9.5pt; padding: 8px 10px; }
.diff-box { border: 1px solid alpha(#3b4261, .7); }
.st { min-width: 18px; font-weight: 700; }
.st-mod { color: #e0af68; }
.st-new { color: #9ece6a; }
.st-del { color: #f7768e; }
.st-ren { color: #7aa2f7; }
.adds { color: #9ece6a; font-size: 9pt; }
.dels { color: #f7768e; font-size: 9pt; }
.count-over { color: #e0af68; }
button.small { padding: 2px 10px; min-height: 0; }
dropdown.model > button { min-width: 0; padding: 3px 10px; }
"""
# the letter shown for a status, and its colour class
KINDS = {"M": ("M", "st-mod"), "A": ("A", "st-new"), "?": ("+", "st-new"), "D": ("D", "st-del"),
         "R": ("R", "st-ren"), "C": ("C", "st-ren"), "U": ("!", "st-del"), "T": ("T", "st-mod")}


def kind(xy):
    """(letter, class) for a porcelain XY: a conflict wins, then what the work tree did, then the index."""
    if "U" in xy or xy in ("AA", "DD"):
        return KINDS["U"]
    for c in (xy[1], xy[0]):
        if c in KINDS:
            return KINDS[c]
    return xy.strip() or "?", "st-mod"


class Commit(View):
    icon = ""   # as the Projects panel
    interval = 0
    css = CSS
    hints = [("Ctrl+Enter", "commit"), ("↑↓", "files"), ("Space", "tick"), ("Esc", "cancel")]

    def __init__(self, rel):
        super().__init__()
        self.rel = rel.strip("/")
        self.path = f"{pj.ROOT}/{self.rel}"
        self.branch = pj.current_branch(self.path) or "HEAD"
        self.title, self.subtitle = f"Commit to {self.branch}", os.path.basename(self.rel)
        self.shown = None   # the file whose diff is on the right

    def build(self):
        changed = pg.status_lines(self.path)
        self.message = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True)
        self.message.add_css_class("commit-message")
        self.buf = self.message.get_buffer()
        self.buf.connect("changed", lambda *_: self.update())
        keys = Gtk.EventControllerKey()   # Ctrl+Enter in the message: commit
        keys.connect("key-pressed", lambda _c, k, _code, st: (k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
                                                               and bool(st & Gdk.ModifierType.CONTROL_MASK)
                                                               and (self.commit() or True)))
        self.message.add_controller(keys)
        self.write = button("Write it for me", self.ai, "flat",
                            tooltip="the model picked beside it writes it from the ticked files' changes "
                                    "(through opencode: the diff is sent to it)")
        # the model: the one kept until opencode's list is in (asking it takes a moment)
        self.model = pj.ai_model()
        self.models = box(False, 0)
        self.paint_models([self.model])
        threading.Thread(target=lambda: GLib.idle_add(self.paint_models, pj.ai_models()), daemon=True).start()
        top = box(False, 8, label("Message", "dim"), label(""), self.models, self.write)
        top.get_first_child().get_next_sibling().set_hexpand(True)
        self.counter = label("", "dim", xalign=1.0)
        self.counter.set_tooltip_text(f"the subject line's length: keep it under {SUBJECT_MAX}")

        # the files: ticked by default; the selected one's diff is on the right
        self.files = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.files.add_css_class("commit-files")
        self.files.connect("row-selected", lambda _l, row: row and self.show_diff(row))
        self.files.set_filter_func(self.visible)
        self.rows = []
        for l in changed:
            xy, shown = l[:2], l[3:]
            path = shown.split(" -> ")[-1]
            tick = Gtk.CheckButton(active=True)
            tick.connect("toggled", lambda *_: self.update())
            tick.set_focusable(False)   # Space on the row ticks it (key), ↑↓ stay in the list
            letter, cls = kind(xy)
            folder, name = os.path.split(path)
            text = label("", ellipsize=True)
            text.set_markup(GLib.markup_escape_text(name) + (recolor(
                f"  <span foreground='#565f89'>{GLib.markup_escape_text(folder)}</span>") if folder else ""))
            text.set_ellipsize(Pango.EllipsizeMode.MIDDLE)   # a long path keeps the file's name in view
            text.set_tooltip_text(shown)
            adds, dels = label("", "adds"), label("", "dels")
            row = Gtk.ListBoxRow(child=box(False, 8, tick, label(letter, "st", cls), text, adds, dels))
            row.tick, row.path, row.shown, row.xy, row.adds, row.dels = tick, path, shown, xy, adds, dels
            self.files.append(row)
            self.rows.append(row)
        if not changed:
            self.files.append(Gtk.ListBoxRow(child=label("Nothing to commit: the working tree is clean.", "dim"),
                                             selectable=False))
        space = Gtk.EventControllerKey()   # Space in the list: tick / untick the selected file
        space.connect("key-pressed", self.list_key)
        self.files.add_controller(space)
        threading.Thread(target=self.count_lines, daemon=True).start()

        self.filter = Gtk.SearchEntry(placeholder_text="filter files", hexpand=True)
        self.filter.connect("search-changed", lambda *_: (self.files.invalidate_filter(), self.update()))
        self.ticked = label("", "dim")
        files_head = box(False, 8, self.ticked, self.filter,
                         button("All", lambda: self.tick_shown(True), "flat", "small", tooltip="tick every file shown"),
                         button("None", lambda: self.tick_shown(False), "flat", "small",
                                tooltip="untick every file shown"))

        has_up = pj.git(self.path, "rev-parse", "--abbrev-ref", "@{u}")[0]
        self.push = Gtk.CheckButton(label="Push it to GitHub after" + ("" if has_up else " (publishes the branch)"),
                                    active=bool(has_up))
        self.push.connect("toggled", lambda *_: self.update())
        self.go = button("Commit", self.commit, "primary", tooltip="Ctrl+Enter")
        self.go.set_sensitive(False)
        bottom = box(False, 8, self.push, label(""), self.go)
        bottom.get_first_child().get_next_sibling().set_hexpand(True)
        left = box(True, 10, top, self.message, self.counter, files_head, scrolled(self.files), bottom)
        left.set_size_request(560, -1)
        left.set_hexpand(False)

        # the diff
        # long lines wrap: on a small screen the diff's column is ~400 px
        self.diff = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, hexpand=True, vexpand=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.diff.add_css_class("commit-diff")
        dbuf = self.diff.get_buffer()
        for tag, colour in (("add", "#9ece6a"), ("del", "#f7768e"), ("hunk", "#7aa2f7"), ("meta", "#565f89")):
            dbuf.create_tag(tag, foreground=recolor(colour))
        self.diff_title = label("", "dim", ellipsize=True)
        self.diff_title.set_ellipsize(Pango.EllipsizeMode.START)
        right = box(True, 10, self.diff_title, box(True, 0, scrolled(self.diff), classes=("diff-box",)))
        right.set_hexpand(True)
        page = box(False, 18, left, right)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        self.update()
        if self.rows:
            self.files.select_row(self.rows[0])
        GLib.idle_add(lambda: self.message.grab_focus() and False)
        return page

    def paint_models(self, models):
        if self.model not in models:
            models = [self.model] + models
        dd = dropdown([(m, m.split("/")[-1].removesuffix("-free")) for m in models], self.model, self.pick_model)
        dd.add_css_class("model")
        dd.set_tooltip_text("the model that writes the message (free, through opencode); kept for next time")
        while self.models.get_first_child():
            self.models.remove(self.models.get_first_child())
        self.models.append(dd)
        return False

    def pick_model(self, model):
        self.model = model

        def save():
            problem = pj.set_ai_model(model)
            if problem:
                GLib.idle_add(lambda: self.say(f"model not kept: {problem}", "bad") and False)
        threading.Thread(target=save, daemon=True).start()

    # ---- the files ---------------------------------------------------------------------------------
    def visible(self, row):
        q = self.filter.get_text().strip().lower() if hasattr(self, "filter") else ""
        return not q or not hasattr(row, "path") or q in row.shown.lower()

    def tick_shown(self, on):
        for row in self.rows:
            if self.visible(row):
                row.tick.set_active(on)

    def list_key(self, _ctrl, keyval, _code, _state):
        row = self.files.get_selected_row()
        if keyval == Gdk.KEY_space and row is not None and hasattr(row, "tick"):
            row.tick.set_active(not row.tick.get_active())
            return True
        return False

    def count_lines(self):
        """Each file's added / removed lines (git diff --numstat; a new file's lines), shown as they come."""
        ok, out = pj.git(self.path, "diff", "HEAD", "--numstat", "-M", "--")
        stats = {}
        for l in (out.splitlines() if ok else []):
            parts = l.split("\t")
            if len(parts) >= 3:
                path = parts[-1]
                if " => " in path:   # a rename: a/{old => new}/b or old => new
                    path = path.replace("{", "").replace("}", "")
                    pre, _, post = path.partition(" => ")
                    path = post if "/" not in pre else pre.rsplit("/", 1)[0] + "/" + post.split("/")[-1]
                stats[path] = (parts[0], parts[1])
        for row in self.rows:
            if row.path not in stats and row.xy == "??":
                try:
                    with open(os.path.join(self.path, row.path), "rb") as f:
                        data = f.read(2_000_000)
                    stats[row.path] = ("-", "-") if b"\0" in data[:8000] else (str(data.count(b"\n")), "0")
                except OSError:
                    pass

        def show():
            for row in self.rows:
                a, d = stats.get(row.path, ("", ""))
                binary = a == "-"
                row.adds.set_text("bin" if binary else f"+{a}" if a not in ("", "0") else "")
                row.dels.set_text("" if binary else f"−{d}" if d not in ("", "0") else "")
            return False
        GLib.idle_add(show)

    def show_diff(self, row):
        if not hasattr(row, "path") or self.shown == row.path:
            return
        self.shown = row.path
        self.diff_title.set_text(row.shown)

        def go():
            if row.xy == "??":   # not in git yet: the whole file is new
                try:
                    with open(os.path.join(self.path, row.path), "rb") as f:
                        data = f.read(400_000)
                    text = ("(a binary file)" if b"\0" in data[:8000] else
                            "\n".join("+" + l for l in data.decode(errors="replace").splitlines()))
                except OSError as e:
                    text = f"can't read it: {e.strerror}"
            else:
                old = row.shown.split(" -> ")[0] if " -> " in row.shown else None
                ok, text = pj.git(self.path, "diff", "HEAD", "-M", "--no-color", "--no-ext-diff", "--",
                                  *([old] if old else []), row.path)
                text = text if ok else f"git diff failed: {text}"
            if row.path == self.shown:
                GLib.idle_add(lambda: self.paint_diff(text) and False)
        threading.Thread(target=go, daemon=True).start()

    def paint_diff(self, text):
        lines = text.splitlines()
        more = len(lines) - DIFF_MAX
        buf = self.diff.get_buffer()
        buf.set_text("")
        it = buf.get_end_iter()
        for l in lines[:DIFF_MAX]:
            tag = ("meta" if l.startswith(("diff --git", "index ", "--- ", "+++ ", "new file", "deleted file",
                                           "similarity", "rename ", "old mode", "new mode", "Binary"))
                   else "hunk" if l.startswith("@@") else "add" if l.startswith("+")
                   else "del" if l.startswith("-") else None)
            buf.insert_with_tags_by_name(it, l + "\n", tag) if tag else buf.insert(it, l + "\n")
        if more > 0:
            buf.insert_with_tags_by_name(it, f"… {more} more lines", "meta")
        if not lines:
            buf.insert_with_tags_by_name(it, "(no changes in the text: a mode change, or empty)", "meta")
        self.diff.scroll_to_iter(buf.get_start_iter(), 0, False, 0, 0)

    # ---- the message, the commit ----------------------------------------------------------------------
    def text(self):
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False).strip()

    def chosen(self):
        """The ticked files, or None for all of them (git commit -a's way, new files too)."""
        picked = [row.path for row in self.rows if row.tick.get_active()]
        return None if len(picked) == len(self.rows) else picked

    def update(self):
        n = sum(row.tick.get_active() for row in self.rows)
        shown = sum(self.visible(row) for row in self.rows)
        self.ticked.set_text(f"{n} of {len(self.rows)} ticked" + (f" · {shown} shown" if shown != len(self.rows)
                                                                  else ""))
        subject = self.text().split("\n", 1)[0]
        self.counter.set_text(f"subject {len(subject)}/{SUBJECT_MAX}" if subject else "")
        (self.counter.add_css_class if len(subject) > SUBJECT_MAX else self.counter.remove_css_class)("count-over")
        self.go.set_label(("Commit & push" if self.push.get_active() else "Commit")
                          + (f" {n} file{'s' * (n != 1)}" if n else ""))
        self.go.set_sensitive(bool(self.text()) and n > 0)

    def ai(self):
        files = self.chosen()
        self.write.set_sensitive(False)
        self.write.set_label("Writing…")

        def go():
            ok, msg = pj.ai_message(self.rel, files, self.model)
            GLib.idle_add(lambda: self.written(ok, msg) and False)
        threading.Thread(target=go, daemon=True).start()

    def written(self, ok, msg):
        self.write.set_sensitive(True)
        self.write.set_label("Write it again" if ok else "Write it for me")
        if ok:
            self.buf.set_text(msg)
        else:
            self.say(f"no message: {msg}", "bad", seconds=8)

    def commit(self):
        if not self.go.get_sensitive():
            return
        files, message, push = self.chosen(), self.text(), self.push.get_active()
        self.go.set_sensitive(False)
        self.say("committing…", seconds=300)

        def go():
            ok, msg = pj.commit_changes(self.rel, message, files, push)
            GLib.idle_add(lambda: self.committed(ok, msg) and False)
        threading.Thread(target=go, daemon=True).start()

    def committed(self, ok, msg):
        self.say(msg, "ok" if ok else "bad", seconds=8)
        if ok:
            GLib.timeout_add(900, lambda: self.close() or False)   # long enough to read "committed …"
        else:
            self.update()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("commitgui.py <project, under ~/code>")
    run(Commit(sys.argv[1]), "panels.commit", (1500, 900))
