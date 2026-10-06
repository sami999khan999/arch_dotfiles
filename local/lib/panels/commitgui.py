#!/usr/bin/env python3
# commitgui.py — Commit, a popup like every other panel (the screen blurred behind it; Esc or a click
# outside closes it). The Projects panel opens it (c, or Commit…):  commitgui.py <project, under ~/code>
# The message (subject, then a body if it needs one; Write it for me: a free model through opencode),
# the changed files (all ticked: untick what stays out), Push it after. A normal git commit, so the
# project's hooks run; the Projects panel sees the result by itself (its live look at git).
import os, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, Pango, View, box, button, label, run, scrolled
import projectsgui as pg

pj = pg.pj
CSS = """
.commit-files { background: #16161e; border: 1px solid alpha(#3b4261, .7); }
.commit-files > row { padding: 2px 8px; }
textview.commit-message, textview.commit-message text { background: #16161e; color: #c0caf5; }
textview.commit-message { border: 1px solid alpha(#3b4261, .7); padding: 6px 8px; min-height: 72px; }
.st { min-width: 22px; font-weight: 700; }
"""


class Commit(View):
    icon = "\uea62"   # as the Projects panel
    interval = 0
    css = CSS
    hints = [("Ctrl+Enter", "commit"), ("Esc", "cancel")]

    def __init__(self, rel):
        super().__init__()
        self.rel = rel.strip("/")
        self.path = f"{pj.ROOT}/{self.rel}"
        self.branch = pj.current_branch(self.path) or "HEAD"
        self.title, self.subtitle = f"Commit to {self.branch}", os.path.basename(self.rel)

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
                            tooltip=f"a free model writes it from the ticked files' changes "
                                    f"({pj.AI_MODEL.split('/')[-1]}, through opencode: the diff is sent to it)")
        top = box(False, 8, label("Message", "dim"), label(""), self.write)
        top.get_first_child().get_next_sibling().set_hexpand(True)

        files = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        files.add_css_class("commit-files")
        self.ticks = []
        for l in changed:
            tick = Gtk.CheckButton(active=True)
            tick.connect("toggled", lambda *_: self.update())
            name = label(l[3:].replace(" -> ", "  →  "), ellipsize=True)
            name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)   # a long path keeps its file name in view
            name.set_tooltip_text(l[3:])
            code = (l[:2].strip() or "?").replace("??", "+")
            files.append(Gtk.ListBoxRow(child=box(False, 8, tick, label(code, "st", "amber"), name), activatable=False))
            self.ticks.append((tick, l[3:].split(" -> ")[-1]))
        if not changed:
            files.append(Gtk.ListBoxRow(child=label("Nothing to commit: the working tree is clean.", "dim")))

        has_up = pj.git(self.path, "rev-parse", "--abbrev-ref", "@{u}")[0]
        self.push = Gtk.CheckButton(label="Push it to GitHub after" + ("" if has_up else " (publishes the branch)"),
                                    active=bool(has_up))
        self.go = button("Commit", self.commit, "primary", tooltip="Ctrl+Enter")
        self.go.set_sensitive(False)
        bottom = box(False, 8, self.push, label(""), self.go)
        bottom.get_first_child().get_next_sibling().set_hexpand(True)
        n = len(changed)
        page = box(True, 10, top, self.message,
                   label(f"{n} changed file{'s' * (n != 1)} (untick what stays out)", "dim"),
                   scrolled(files), bottom)   # the list takes the room the popup has, and scrolls
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        GLib.idle_add(lambda: self.message.grab_focus() and False)
        return page

    def text(self):
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False).strip()

    def chosen(self):
        """The ticked files, or None for all of them (git commit -a's way, new files too)."""
        picked = [path for t, path in self.ticks if t.get_active()]
        return None if len(picked) == len(self.ticks) else picked

    def update(self):
        self.go.set_sensitive(bool(self.text()) and any(t.get_active() for t, _ in self.ticks))

    def ai(self):
        files = self.chosen()
        self.write.set_sensitive(False)
        self.write.set_label("Writing…")

        def go():
            ok, msg = pj.ai_message(self.rel, files)
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
    run(Commit(sys.argv[1]), "panels.commit", (900, 640))
