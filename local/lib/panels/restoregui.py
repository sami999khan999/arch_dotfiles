#!/usr/bin/env python3
# restoregui.py — after a login, opens what was open before the last Log out / Restart / Shutdown, if you
# say so. `sessionlib.py ask` (autostart.lua) starts it only when the session-end panel left a list.
# Each app is a row: Restore (Enter) opens them one at a time, gently (sessionlib.open_gently: the next
# one once the last one's window is up and the CPU has calmed down), each on the workspace it was on.
# Chrome and VS Code reopen all their windows and tabs; agentmux brings its threads back by itself.
# Not now (Esc) leaves it all closed. Either way the list isn't offered again; Esc while restoring stops
# there (what's open stays open).
import os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, label, rule_heading, run, scrolled
import sessionlib
from sessionendgui import CSS, STATES

ACTIONS = {"poweroff": "Shutdown", "reboot": "Restart", "logout": "Log out"}


class Row:
    """One app: a glyph, its name, where it goes, its state at the right."""

    def __init__(self, name, detail):
        self.glyph = label("", "end-glyph")
        self.status = label("", "dim", xalign=1.0)
        self.widget = box(False, 10, self.glyph, label(name, "sub"), label(detail, "dim", ellipsize=True),
                          self.status, classes=("end-row",))
        self.set("wait")

    def set(self, state, text=None):
        glyph, colour, said = STATES[state]
        self.glyph.set_text(glyph)
        for w in (self.glyph, self.status):
            for c in ("dim", "amber", "green", "red"):
                w.remove_css_class(c)
            w.add_css_class(colour)
        self.status.set_text(text or said)


class Restore(View):
    icon = "\U000f099b"   # nf-md-restore
    title = "Restore"
    interval = 0
    css = CSS
    backdrop = False   # the apps opening stay in view
    hints = [("Enter", "restore"), ("Esc", "not now")]

    def __init__(self, saved):
        super().__init__()
        self.saved = saved
        when = time.strftime("%a %H:%M", time.localtime(saved.get("at", 0)))
        self.subtitle = f"open before {ACTIONS.get(saved.get('action'), 'the last session ended')} · {when}"
        self.apps = saved.get("apps", [])
        self.started = False
        self.stopped = False

    def build(self):
        sessionlib.answered()   # offered once: Restore or not, it isn't asked again
        page = box(True, 0)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        page.append(rule_heading("Apps", f"{len(self.apps)} to open"))
        self.rows = []
        for a in self.apps:
            ws = a["workspace"]
            detail = f"workspace {ws}" if ws.lstrip("-").isdigit() else ws
            if a.get("windows", 1) > 1:
                detail += f" · {a['windows']} windows"
            row = Row(a["name"], detail)
            self.rows.append(row)
            page.append(row.widget)
        threads = self.saved.get("threads") or []
        if threads:
            page.append(rule_heading("Agent threads", str(len(threads)), spaced=True))
            row = Row("agentmux", ", ".join(threads))
            row.set("next", "back with agentmux")
            page.append(row.widget)
        self.restore_btn = button("Restore", lambda _b: self.restore(), "primary")
        self.skip_btn = button("Not now", lambda _b: self.close(), "flat")
        actions = box(False, 8, self.skip_btn, self.restore_btn)
        actions.set_halign(Gtk.Align.END)
        actions.set_margin_top(16)
        page.append(actions)
        GLib.idle_add(lambda: self.restore_btn.grab_focus() and False)
        return scrolled(page)

    def restore(self):
        if self.started:
            return
        self.started = True
        self.restore_btn.set_sensitive(False)
        self.skip_btn.set_label("Stop")
        self.hints = [("Esc", "stop")]
        self.host.show_hints()
        threading.Thread(target=self.open_all, daemon=True).start()

    def open_all(self):
        """In a thread: one app after the other (open_gently waits for each), the rows following."""
        skip = set(sessionlib.already_open(self.apps))
        for i, (app, row) in enumerate(zip(self.apps, self.rows)):
            if self.stopped:
                return
            if i in skip:
                GLib.idle_add(row.set, "done", "already open")
                continue
            GLib.idle_add(row.set, "busy", "opening…")
            GLib.idle_add(self.set_subtitle, f"opening {i + 1} of {len(self.apps)}")
            took = sessionlib.open_gently(app, lambda: self.stopped)
            if took is None:
                GLib.idle_add(row.set, "stuck", "no window")
            else:
                GLib.idle_add(row.set, "done", f"open · {took:.1f} s")
        GLib.idle_add(self.set_subtitle, "restored")
        GLib.timeout_add(1500, lambda: self.close() or False)

    def key(self, keyval, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.restore()
            return True
        return False

    def close(self):
        self.stopped = True
        super().close()


def main():
    saved = sessionlib.pending()
    if not saved or not saved.get("apps"):
        sessionlib.answered()
        return
    run(Restore(saved), "panels.restore", (620, 460))


if __name__ == "__main__":
    main()
