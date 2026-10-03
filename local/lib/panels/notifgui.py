#!/usr/bin/env python3
# notifgui.py — the notification panel (GTK, Tokyo Night): the bell beside the clock, or Super + Ctrl + N.
# Running it again closes it. Opens in the middle, like every panel (modules/windowrules.lua).
#
# Every notification since the log began (config/hypr/scripts/notifications.py keeps it: mako logs each
# one), newest first, by day; the new ones (since the panel last opened) have the blue edge. A click on
# one opens what it's about (an agentmux one: its thread; one still on screen: its own action); × takes
# it out. At the top: do not disturb, Clear all. At the bottom: what notifies at all (Settings: hide
# an app's notifications; agentmux's settings: which agent events).
#
#   Enter  open the selected one     Delete  dismiss it     Esc  close
import datetime, importlib.machinery, importlib.util, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import GLib, Gdk, Gtk, View, box, button, label, rule_heading, run, scrolled, switch

_loader = importlib.machinery.SourceFileLoader("notifications", os.path.expanduser("~/.config/hypr/scripts/notifications.py"))
notes = importlib.util.module_from_spec(importlib.util.spec_from_loader("notifications", _loader))
_loader.exec_module(notes)

AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
FOCUS = os.path.expanduser("~/.config/hypr/scripts/focus-or-launch.sh")
# a glyph per app (the rest get the bell)
ICONS = {"agentmux": "\U000f06a9", "notify-send": "\U000f009a", "Spotify": "\U000f04c7"}

CSS = """
.note { padding: 9px 12px 9px 0; }
.edge { min-width: 2px; }
.note.new .edge { background: #6b8fe0; }
.notes-bar { padding: 4px 12px 8px 12px; border-bottom: 1px solid alpha(#c0caf5, .07); }
.note.urgent .note-icon { color: #f7768e; }
.note-icon { color: #6b8fe0; min-width: 18px; }
.note-summary { font-weight: 700; }
.note-body { color: #a9b1d6; }
.note-meta { color: #565f89; font-size: 9pt; }
.empty { color: #565f89; margin-top: 60px; }
.dnd-label { color: #a9b1d6; }
"""


def when(ts):
    """5m, 3h, or the time of day for older ones today; HH:MM on earlier days (the day is the heading)."""
    age = time.time() - ts
    if age < 60:
        return "now"
    if age < 3600:
        return f"{int(age // 60)}m"
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M")


def day(ts):
    d = datetime.date.fromtimestamp(ts)
    today = datetime.date.today()
    return "Today" if d == today else "Yesterday" if d == today - datetime.timedelta(days=1) else d.strftime("%a %d %b")


def agentmux_thread(item):
    """The agentmux thread a notification is about: "Claude Code · inkwell #2" -> "inkwell·2" (the
    project's folder name as agentmux names its sessions: lib.session_base)."""
    m = re.search(r"· (.+) #(\d+)$", item["summary"])
    if item["app"] != "agentmux" or not m:
        return None
    return re.sub(r"[.:]", "_", m[1]).lstrip("_") + "·" + m[2]


class Notifications(View):
    title = "Notifications"
    icon = "\U000f009a"
    interval = 2.0
    hints = [("Enter", "open"), ("Delete", "dismiss"), ("Esc", "close")]
    css = CSS

    def __init__(self):
        super().__init__()
        self.stamp = None
        data = notes.load()
        self.new_since = data.get("seen", 0)   # what's new is what came since the panel last opened
        self.items = []

    def build(self):
        # do not disturb and Clear all on a row of their own under the header
        self.dnd = switch(notes.silenced(), self.set_dnd)
        self.dnd.set_valign(Gtk.Align.CENTER)
        self.dnd.set_tooltip_text("Nothing pops up (right-click the bell, or Super + Ctrl + ,)")
        dnd = label("Do not disturb", "dnd-label")
        dnd.set_hexpand(True)
        toolbar = box(False, 10, self.dnd, dnd, button("Clear all", self.clear, "flat"), classes=("notes-bar",))
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.connect("row-activated", lambda _l, row: self.open(row.item))
        page = box(True, 0, toolbar, scrolled(self.list))
        for edge in ("start", "end"):
            getattr(page, f"set_margin_{edge}")(10)
        page.set_margin_top(8)
        more = box(False, 4, label("Choose what notifies:", "dim"),
                   button("Apps", lambda: self.settings(["python3", os.path.expanduser("~/.local/lib/panels/settingsgui.py")]), "flat",
                          tooltip="Settings → Power & bar → Notifications: hide an app's notifications, corner, how long they stay"),
                   button("Agents", lambda: self.settings([AGENTMUX, "settings"]), "flat",
                          tooltip="agentmux's settings: needs you · finished · error · quit, each on or off"))
        more.set_margin_top(6)
        page.append(more)
        self.fill()
        GLib.idle_add(lambda: notes.seen() or False)   # opened: everything here has been seen
        return page

    def refresh(self):
        self.fill()
        if self.dnd.get_active() != notes.silenced():   # changed elsewhere (the bell, the key)
            self.dnd.quiet = True
            self.dnd.set_active(notes.silenced())
            self.dnd.quiet = False

    def fill(self):
        try:
            stamp = os.stat(notes.LOG).st_mtime
        except OSError:
            stamp = 0
        if stamp == self.stamp and self.list.get_first_child() is not None:
            return
        self.stamp = stamp
        self.items = list(reversed(notes.load()["items"]))
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
        n_new = sum(1 for i in self.items if i["time"] > self.new_since)
        self.set_subtitle(("silenced · " if notes.silenced() else "")
                          + (f"{n_new} new · " if n_new else "") + f"{len(self.items)} in all")
        if not self.items:
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.item = None
            row.set_child(label("No notifications", "empty", xalign=0.5))
            self.list.append(row)
            return
        last_day = None
        for item in self.items:
            if day(item["time"]) != last_day:
                last_day = day(item["time"])
                head = Gtk.ListBoxRow(activatable=False, selectable=False)
                head.item = None
                heading = rule_heading(last_day)
                heading.set_margin_top(4 if last_day == day(self.items[0]["time"]) else 12)   # air above all but the first
                head.set_child(heading)
                self.list.append(head)
            self.list.append(self.row(item))

    def row(self, item):
        classes = ["note"] + (["new"] if item["time"] > self.new_since else []) \
            + (["urgent"] if item.get("urgency") == "critical" else [])
        icon = label(ICONS.get(item["app"], "\U000f009a"), "note-icon")
        icon.set_valign(Gtk.Align.START)
        top = box(False, 8, label(item["summary"] or item["app"], "note-summary", ellipsize=True),
                  label(when(item["time"]), "note-meta", xalign=1.0))
        text = box(True, 2, top)
        if item["body"]:
            body = label(item["body"], "note-body", wrap=True)
            body.set_lines(4)
            text.append(body)
        if item["app"] and item["summary"]:
            text.append(label(item["app"], "note-meta"))
        text.set_hexpand(True)
        close = button("×", lambda: self.dismiss(item), "flat", tooltip="Dismiss (Delete)")
        close.set_valign(Gtk.Align.START)
        content = box(False, 10, Gtk.Box(css_classes=["edge"]), icon, text, close, classes=classes)
        row = Gtk.ListBoxRow()
        row.item = item
        row.set_child(content)
        return row

    # ---- acting ----
    def open(self, item):
        """What the notification is about: an agentmux thread, else its own action if it's still up."""
        if not item:
            return
        thread = agentmux_thread(item)
        if thread:
            subprocess.Popen([AGENTMUX, "show", thread], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.Popen([FOCUS, "agentmux", "kitty", "--config", os.path.expanduser("~/.config/kitty/agentmux.conf"),
                              "--class", "agentmux", "-e", AGENTMUX], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif item["id"] in notes.on_screen() and "default" in item.get("actions", []):
            notes.mako("invoke", "-n", str(item["id"]), "default")
        else:
            self.say("Nothing to open: it was only a message")
            return
        self.close()

    def dismiss(self, item):
        notes.dismiss(item["time"])
        self.fill()

    def clear(self):
        notes.clear()
        self.fill()

    def set_dnd(self, on):
        notes.dnd("on" if on else "off")
        self.fill_later()

    def fill_later(self):
        self.stamp = None
        GLib.timeout_add(300, lambda: self.fill() or False)

    def settings(self, cmd):
        subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.close()

    def key(self, keyval, state):
        row = self.list.get_selected_row()
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and row is not None and row.item:
            self.dismiss(row.item)
            return True
        return False


def make():
    return Notifications()


def main():
    run(make(), "panels.notifications", (460, 620))


if __name__ == "__main__":
    main()
