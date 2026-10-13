#!/usr/bin/env python3
# commitgui.py — Commit, a popup like every other panel (the screen blurred behind it; Esc or a click
# outside closes it). The Projects panel opens it (c, or Commit…; a click on a file in its Changes tab opens it
# on that file):  commitgui.py <project, under ~/code> [file]
#   left   the message (subject, then a body if it needs one; the subject's length beside it; Write it for
#          me: a free model through opencode, picked in the dropdown beside it and kept for next time),
#          the changed files (all ticked: untick what stays out; a filter, All / None for what it shows,
#          each file's +/- lines); at the bottom Sync (GitHub's side down: pj.sync, as the Projects panel's) and
#          Push (the commits not on GitHub yet), then Commit (stays open on what's left) and Commit & push
#   right  the selected file's diff (a click on a file, or ↑↓ in the list): each line numbered, added / removed
#          ones tinted, a header per hunk; a long line wraps under its text, not under the numbers (a very
#          long one is cut); Discard… above it (out of the way of Commit) asks first, then throws away
#          that file's changes or every ticked file's — into a git stash, so git stash pop undoes it
# A normal git commit, so the project's hooks run; the Projects panel sees the result by itself (its live
# look at git).
import os, re, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import (Gdk, GLib, Gtk, Pango, View, backdrop_setting, box, button, clear, dropdown, label, recolor, run,
                    scrolled, window_blur)
import projectsgui as pg

pj = pg.pj
SUBJECT_MAX = 72        # past it the subject's count turns amber (git log's one-line view cuts it)
DIFF_MAX = 4000         # lines of a diff shown at most
LINE_MAX = 1000         # characters of a line shown at most: wrapping a bundle's one-line source map (MBs) froze it
CSS = """
.commit-files { background: #16161e; border: 1px solid alpha(#3b4261, .7); }
.commit-files > row { padding: 2px 8px; }
.commit-files > row:selected { background: #292e42; }
textview.commit-message, textview.commit-message text { background: #16161e; color: #c0caf5; }
textview.commit-message { padding: 6px 8px; }
/* the message's box: a fixed height, a long message scrolls inside it */
.commit-message-box { border: 1px solid alpha(#3b4261, .7); background: #16161e; }
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
/* Discard's question: a card over the dimmed panel, as the Projects panel's popups */
.dialog { background: #1a1b26; border: 1px solid alpha(#3b4261, .7); padding: 20px 22px; }
.dialog-title { font-weight: 700; font-size: 12pt; }
button.danger-fill { background: alpha(#f7768e, .16); color: #f7768e; }
button.danger-fill:hover { background: alpha(#f7768e, .26); }
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
    hints = [("Ctrl+Enter", "commit & push"), ("+Shift", "commit only"), ("↑↓", "files"), ("Space", "tick"), ("Esc", "cancel")]

    def __init__(self, rel, pick=None):
        super().__init__()
        self.rel = rel.strip("/")
        self.pick = pick   # the file whose diff shows first (else the first one)
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
        keys = Gtk.EventControllerKey()   # Ctrl+Enter in the message: commit & push; Ctrl+Shift+Enter: commit
        keys.connect("key-pressed", lambda _c, k, _code, st: (k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
                                                               and bool(st & Gdk.ModifierType.CONTROL_MASK)
                                                               and (self.commit(not st & Gdk.ModifierType.SHIFT_MASK)
                                                                    or True)))
        self.message.add_controller(keys)
        self.write = button("Write it for me", self.ai, "flat",
                            tooltip="the model picked beside it writes it from the ticked files' changes "
                                    "(through opencode: the diff is sent to it)")
        # the model: the one kept until opencode's list is in (asking it takes a moment)
        self.model = pj.ai_model()
        self.models = box(False, 0)
        self.paint_models([self.model])
        threading.Thread(target=lambda: GLib.idle_add(self.paint_models, pj.ai_models()), daemon=True).start()
        top = box(False, 8, label("Message", "dim"), label(""), self.write, self.models)
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

        self.sync = button("Sync", self.sync_now, "flat",
                           tooltip="bring GitHub's side down: fetch, fast-forward the branches only behind "
                                   "(never merges or pushes)")
        self.push = button("Push", self.push_now, "flat")
        self.ahead = None   # commits not on GitHub yet: None while unknown, -1 when the branch has no upstream
        self.just_commit = button("Commit", lambda: self.commit(False), tooltip="Ctrl+Shift+Enter")
        self.go = button("Commit & push", lambda: self.commit(True), "primary", tooltip="Ctrl+Enter")
        self.go.set_sensitive(False)
        self.just_commit.set_sensitive(False)
        gap = label("")
        gap.set_hexpand(True)
        bottom = box(False, 8, self.sync, self.push, gap, self.just_commit, self.go)
        self.count_ahead()
        message = scrolled(self.message, vexpand=False)
        message.add_css_class("commit-message-box")
        message.set_size_request(-1, 110)
        left = box(True, 10, top, message, self.counter, files_head, scrolled(self.files), bottom)
        left.set_size_request(560, -1)
        left.set_hexpand(False)

        # the diff
        # long lines wrap: on a small screen the diff's column is ~400 px
        self.diff = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, hexpand=True, vexpand=True,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.diff.add_css_class("commit-diff")
        dbuf = self.diff.get_buffer()

        def tint(colour, alpha):
            rgba = Gdk.RGBA()
            rgba.parse(recolor(colour))
            rgba.alpha = alpha
            return rgba
        dbuf.create_tag("add", foreground=recolor("#9ece6a"), paragraph_background_rgba=tint("#9ece6a", .08))
        dbuf.create_tag("del", foreground=recolor("#f7768e"), paragraph_background_rgba=tint("#f7768e", .08))
        dbuf.create_tag("hunk", foreground=recolor("#7aa2f7"), paragraph_background_rgba=tint("#292e42", .9),
                        pixels_above_lines=10, pixels_below_lines=4)
        dbuf.create_tag("meta", foreground=recolor("#565f89"))
        dbuf.create_tag("num", foreground=recolor("#444b6a"))   # made after add / del, so it wins over them
        self.gutter = dbuf.create_tag("line")   # the hanging indent: its width is set when a diff is painted
        self.diff_title = label("", "dim", ellipsize=True, xalign=0.0)
        self.diff_title.set_ellipsize(Pango.EllipsizeMode.START)
        self.diff_title.set_hexpand(True)
        self.discard = button("Discard…", self.ask_discard, "flat", "small", "danger",
                              tooltip="throw away changes (asks first: this file, or every ticked one)")
        self.discard.set_sensitive(bool(self.rows))
        right = box(True, 10, box(False, 8, self.diff_title, self.discard),
                    box(True, 0, scrolled(self.diff), classes=("diff-box",)))
        right.set_hexpand(True)
        page = box(False, 18, left, right)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        self.update()
        if self.rows:
            self.files.select_row(next((r for r in self.rows if r.path == self.pick), self.rows[0]))
        GLib.idle_add(lambda: self.message.grab_focus() and False)

        # Discard's question: a card over the dimmed, blurred page (a window of its own would be tiled)
        self.card = box(True, 10, classes=("dialog",))
        self.card.set_halign(Gtk.Align.CENTER)
        self.card.set_valign(Gtk.Align.CENTER)
        self.card.set_vexpand(True)
        self.card.set_size_request(520, -1)
        self.dim = box(True, 0, self.card, classes=("dialog-dim",))
        self.dim.set_visible(False)
        esc = Gtk.EventControllerKey()   # Esc closes the question, not the panel
        esc.connect("key-pressed", lambda _c, k, _code, _st: k == Gdk.KEY_Escape and (self.close_dialog() or True))
        self.dim.add_controller(esc)
        self.page = page
        overlay = Gtk.Overlay(child=page)
        overlay.add_overlay(self.dim)
        on, dark = backdrop_setting()
        blur = window_blur()
        css = Gtk.CssProvider()
        css.load_from_string(recolor(f".blurred {{ filter: blur({max(blur // 2, 1) if on and blur else 0}px); }}\n"
                                     f".dialog-dim {{ background: alpha(#16161e, {min(dark + 0.3, 0.9) if on else 0.72}); }}"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 2)
        return overlay

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
        """The diff, readable: no git header (the title above names the file), a line per hunk saying where
        it starts, then each line with its number (the new file's; the old one's for a removed line)."""
        lines = text.splitlines()
        more = len(lines) - DIFF_MAX
        buf = self.diff.get_buffer()
        buf.set_text("")
        it = buf.get_end_iter()
        width = self.diff.create_pango_layout("0" * 6).get_pixel_size()[0]   # "1234 +": the gutter
        self.gutter.set_property("indent", -width)   # Pango's negative indent: the lines after the first
        old = new = 1
        for l in lines[:DIFF_MAX]:
            if l.startswith(("diff --git", "index ", "--- ", "+++ ")):
                continue
            hunk = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)", l)
            if hunk:
                old, new, where = int(hunk[1]), int(hunk[2]), hunk[3].strip()
                buf.insert_with_tags_by_name(it, f"line {new}" + (f" · {where}" if where else "") + "\n",
                                             "hunk", "line")
                continue
            sign = l[:1]
            if sign not in ("+", "-", " "):   # new file mode, rename …, \ No newline at end of file
                buf.insert_with_tags_by_name(it, l + "\n", "meta", "line")
                continue
            tag = {"+": "add", "-": "del"}.get(sign)
            num = old if sign == "-" else new
            old, new = old + (sign != "+"), new + (sign != "-")
            buf.insert_with_tags_by_name(it, f"{num:>4} ", "num", "line", *([tag] if tag else []))
            cut = len(l) - LINE_MAX
            buf.insert_with_tags_by_name(it, l[:LINE_MAX] + ("" if cut > 0 else "\n"), "line", *([tag] if tag else []))
            if cut > 0:
                buf.insert_with_tags_by_name(it, f" … {cut:,} more characters\n", "meta", "line")
        if more > 0:
            buf.insert_with_tags_by_name(it, f"… {more} more lines", "meta")
        if not lines:
            buf.insert_with_tags_by_name(it, "(no changes in the text: a mode change, or empty)", "meta")
        self.diff.scroll_to_iter(buf.get_start_iter(), 0, False, 0, 0)

    # ---- discard ----------------------------------------------------------------------------------------
    def ask_discard(self):
        """Which changes go: the file on the right, or every ticked one. Cancel has the focus, so an Enter
        doesn't throw anything away."""
        row = self.files.get_selected_row()
        one = row if row is not None and hasattr(row, "path") else None
        ticked = [r for r in self.rows if r.tick.get_active()]
        if not one and not ticked:
            return
        clear(self.card)
        self.card.append(label("Discard changes?", "dialog-title"))
        self.card.append(pg.para("The file goes back to how the last commit has it (a new file is removed). "
                              "Kept in a git stash: git stash pop in the project brings it back.", "dim"))
        cancel = button("Cancel", self.close_dialog, "flat", tooltip="Esc")
        acts = box(False, 8, label(""), cancel)
        acts.get_first_child().set_hexpand(True)
        acts.set_margin_top(10)
        if one:
            acts.append(button(f"Discard {os.path.basename(one.path)}", lambda: self.discard_rows([one]),
                               "danger-fill", tooltip=one.shown))
        if ticked and ticked != [one]:
            n = len(ticked)
            acts.append(button(f"Discard {n} ticked file{'s' * (n != 1)}", lambda: self.discard_rows(ticked),
                               "danger-fill"))
        self.card.append(acts)
        self.page.add_css_class("blurred")
        self.dim.set_visible(True)
        GLib.idle_add(lambda: cancel.grab_focus() and False)

    def close_dialog(self):
        self.dim.set_visible(False)
        self.page.remove_css_class("blurred")
        self.files.grab_focus()

    def discard_rows(self, rows):
        self.close_dialog()
        self.discard.set_sensitive(False)
        self.say("discarding…", seconds=60)

        def go():
            ok, msg = pj.discard_changes(self.rel, [r.shown for r in rows])
            GLib.idle_add(lambda: self.discarded(rows, ok, msg) and False)
        threading.Thread(target=go, daemon=True).start()

    def discarded(self, rows, ok, msg):
        self.say(msg, "ok" if ok else "bad", seconds=8)
        if ok:
            self.drop_rows(rows)
        self.update()

    def drop_rows(self, rows):
        """Take files out of the list (discarded, or committed), the next one selected."""
        if rows:
            at = self.rows.index(rows[0])
            for r in rows:
                self.files.remove(r)
                self.rows.remove(r)
            self.shown = None
            if self.rows:
                self.files.select_row(self.rows[min(at, len(self.rows) - 1)])
            else:
                self.files.append(Gtk.ListBoxRow(child=label("Nothing to commit: the working tree is clean.", "dim"),
                                                 selectable=False))
                self.diff.get_buffer().set_text("")
                self.diff_title.set_text("")
        self.discard.set_sensitive(bool(self.rows))

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
        files = f" {n} file{'s' * (n != 1)}" if n else ""
        self.go.set_label("Commit & push" + files)
        self.go.set_tooltip_text("Ctrl+Enter" + ("" if self.ahead != -1 else " · publishes the branch on GitHub"))
        self.just_commit.set_label("Commit" + files)
        ready = bool(self.text()) and n > 0
        self.go.set_sensitive(ready)
        self.just_commit.set_sensitive(ready)
        a = self.ahead
        self.push.set_label("Publish" if a == -1 else f"Push {a}" if a else "Push")
        self.push.set_tooltip_text("put the branch on GitHub (it isn't there yet)" if a == -1 else
                                   f"push the {a} commit{'s' * (a != 1)} not on GitHub yet" if a else
                                   "nothing to push: GitHub has every commit")
        self.push.set_sensitive(bool(a))

    # ---- GitHub: what's not pushed yet, Push, Sync ----------------------------------------------------
    def count_ahead(self):
        def go():
            ok, n = pj.git(self.path, "rev-list", "--count", "@{u}..HEAD")
            a = int(n) if ok and n.strip().isdigit() else -1
            GLib.idle_add(lambda: (setattr(self, "ahead", a), self.update()) and False)
        threading.Thread(target=go, daemon=True).start()

    def push_now(self):
        self.push.set_sensitive(False)
        self.say(f"pushing {self.branch}…", seconds=300)

        def go():
            ok, msg = pj.push(self.path)
            GLib.idle_add(lambda: (self.say(msg, "ok" if ok else "bad", seconds=8), self.count_ahead()) and False)
        threading.Thread(target=go, daemon=True).start()

    def sync_now(self):
        self.sync.set_sensitive(False)
        self.say("syncing…", seconds=300)

        def go():
            rep = pj.sync(self.rel)
            GLib.idle_add(lambda: (self.say(pg.sync_summary([rep]), "bad" if rep["error"] else "ok", seconds=12),
                                   self.sync.set_sensitive(True), self.count_ahead()) and False)
        threading.Thread(target=go, daemon=True).start()

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

    def commit(self, push):
        if not self.go.get_sensitive():
            return
        files, message = self.chosen(), self.text()
        rows = [row for row in self.rows if row.tick.get_active()]
        self.go.set_sensitive(False)
        self.just_commit.set_sensitive(False)
        self.say("committing…", seconds=300)

        def go():
            ok, msg = pj.commit_changes(self.rel, message, files, push)
            GLib.idle_add(lambda: self.committed(ok, msg, push, rows) and False)
        threading.Thread(target=go, daemon=True).start()

    def committed(self, ok, msg, push, rows):
        self.say(msg, "ok" if ok else "bad", seconds=8)
        if ok and push:
            GLib.timeout_add(900, lambda: self.close() or False)   # long enough to read "committed …"
            return
        if ok or msg.startswith("committed"):   # committed (the push may have failed): the rest stays to commit
            self.buf.set_text("")
            self.drop_rows(rows)
            self.count_ahead()
        self.update()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("commitgui.py <project, under ~/code> [file]")
    run(Commit(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None), "panels.commit", (1500, 900))
