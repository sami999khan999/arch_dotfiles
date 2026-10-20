#!/usr/bin/env python3
# downloadsgui.py — Downloads (Super + Ctrl + J, or the bar's download icon): what the download manager is
# fetching and has fetched, with each one's speed, size, time left and connections; pause, resume, cancel,
# retry; open what's done. A link pasted (Ctrl+V) or typed at the top is downloaded.
# The window of the downloads service (local/lib/downloads/downloads.py), which does the work with aria2: it
# connects to the service's socket and shows the state it sends (every second while something downloads,
# nothing while idle). Opening it clears the bar's "finished" icon.
import gc, json, os, socket, subprocess, sys, threading, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, PanelApp, View, box, button, clear, collect, label, rule_heading, scrolled

HOME = os.path.expanduser("~")
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "downloads.sock")
FILES = f"{HOME}/.local/lib/panels/filesgui.py"
G_DOWN, G_PAUSE, G_PLAY, G_CROSS = "\U000f01da", "\U000f03e4", "\U000f040a", "\U000f0156"
G_DONE, G_FAIL, G_RETRY, G_LINK = "\U000f05e0", "\U000f0028", "\U000f0450", "\U000f0337"
MOVING = ("active", "waiting", "paused")
STATE_TEXT = {"active": "downloading", "waiting": "queued", "paused": "paused", "complete": "done",
              "error": "failed", "removed": "cancelled"}
STATE_CLASS = {"active": "accent", "waiting": "dim", "paused": "amber", "complete": "green", "error": "red",
               "removed": "dim"}
CSS = """
.pane { padding: 14px 20px; }
.dl { padding: 10px 12px; margin-bottom: 6px; background: alpha(#292e42, .35); border-left: 2px solid transparent; }
.dl.active { border-left-color: #6b8fe0; }
.dl.paused { border-left-color: #e0af68; }
.dl.error { border-left-color: #f7768e; }
.dl .glyph { font-size: 14pt; min-width: 30px; }
.dl .what { font-weight: 700; }
.dl progressbar { margin-top: 6px; }
.dl.paused progressbar progress { background: #e0af68; }
progressbar trough { background: #292e42; min-height: 3px; border: none; }
progressbar progress { background: #6b8fe0; min-height: 3px; border: none; }
.empty .big { color: #3b4261; font-size: 26pt; }
"""


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def eta(secs):
    secs = int(secs)
    if secs < 60:
        return f"{secs} s left"
    if secs < 3600:
        return f"{secs // 60} min left"
    return f"{secs // 3600} h {secs // 60 % 60} min left"


def ago(t):
    d = time.time() - (t or 0)
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    return time.strftime("%d %b", time.localtime(t))


def host(url):
    return urllib.parse.urlsplit(url).hostname or "" if url else ""


class Row:
    """One download's card. Built again when its state changes; in between only its numbers move."""

    def __init__(self, view, item):
        self.view, self.status = view, None
        self.box = box(False, 10, classes=("dl",))
        self.update(item)

    def update(self, it):
        if it["status"] != self.status:
            self.build(it)
        self.fill(it)

    def build(self, it):
        self.status = st = it["status"]
        clear(self.box)
        for c in ("active", "paused", "error"):
            (self.box.add_css_class if st == c else self.box.remove_css_class)(c)
        glyph = G_DONE if st == "complete" else G_FAIL if st == "error" else G_PAUSE if st == "paused" else G_DOWN
        g = label(glyph, "glyph", STATE_CLASS.get(st, "dim"))
        g.set_valign(Gtk.Align.START)
        self.name = label("", "what", ellipsize=True)
        self.state = label("", STATE_CLASS.get(st, "dim"))
        self.details = label("", "dim", ellipsize=True)   # the whole width: speed, time left, connections…
        main = box(True, 3, self.name, self.details)
        main.set_hexpand(True)
        self.error = label("", "red", wrap=True)
        self.error.set_visible(False)
        main.append(self.error)
        self.bar = Gtk.ProgressBar()
        self.bar.set_visible(st in MOVING)
        main.append(self.bar)
        self.box.append(g)
        self.box.append(main)
        gid, req = it["gid"], self.view.request
        actions = []
        if st in ("active", "waiting"):
            actions.append(button(G_PAUSE, lambda: req({"op": "pause", "gid": gid}), "flat", tooltip="Pause"))
        if st == "paused":
            actions.append(button(G_PLAY, lambda: req({"op": "resume", "gid": gid}), "flat", tooltip="Resume"))
        if st in MOVING:
            actions.append(button(G_CROSS, lambda: req({"op": "cancel", "gid": gid}), "flat", "danger",
                                  tooltip="Cancel (the partial file goes too)"))
        if st == "complete" and it.get("path") and os.path.exists(it["path"]):
            path = it["path"]
            # then the popup goes: pinned above its backdrop, it would hide the app it opened
            actions.append(button("Open", lambda: self.view.open_after(["xdg-open", path]), "flat"))
            actions.append(button("Show in Files", lambda: self.view.open_after(["python3", FILES, path]), "flat"))
        if st in ("error", "removed") and it.get("url"):
            actions.append(button(f"{G_RETRY}  Retry", lambda: req({"op": "retry", "gid": gid}), "flat"))
        if it.get("url"):
            url = it["url"]
            actions.append(button(G_LINK, lambda: self.view.copy(url), "flat", tooltip="Copy the link"))
        if st not in MOVING:
            actions.append(button(G_CROSS, lambda: req({"op": "remove", "gid": gid}), "flat",
                                  tooltip="Take off the list (the file stays)"))
        for b in actions:
            b.set_focusable(False)
        # the state beside the buttons, centred with them: on the name's line it sat higher than they did
        self.state.set_valign(Gtk.Align.CENTER)
        self.state.set_margin_end(6)
        self.box.append(self.state)
        acts = box(False, 2, *actions)
        acts.set_valign(Gtk.Align.CENTER)
        self.box.append(acts)

    def fill(self, it):
        st, total, done = it["status"], it["total"], it["done"]
        self.name.set_text(it["name"] or it["url"] or "…")
        self.name.set_tooltip_text(it.get("path") or it.get("url") or None)
        parts = []
        if st in MOVING:
            parts.append(f"{human(done)} of {human(total)}" if total else human(done))
            if st == "active" and it["speed"]:
                parts.append(f"{human(it['speed'])}/s")
                if total > done:
                    parts.append(eta((total - done) / it["speed"]))
            if st == "active" and it["conns"]:
                parts.append(f"{it['conns']} connection{'s' if it['conns'] > 1 else ''}")
        else:
            if total:
                parts.append(human(total))
            if it.get("finished"):
                parts.append(ago(it["finished"]))
        if host(it.get("url")):
            parts.append(host(it["url"]))
        self.details.set_text("  ·  ".join(parts))
        text = STATE_TEXT.get(st, st)
        if st == "complete" and it.get("path") and not os.path.exists(it["path"]):
            text = "done  ·  the file is gone"
        if st in MOVING and total:
            text += f"  {done * 100 // total} %"
        self.state.set_text(text)
        if st in MOVING:
            self.bar.set_fraction(done / total if total else 0)
        self.error.set_text(it.get("error") or "")
        self.error.set_visible(st == "error" and bool(it.get("error")))


class Downloads(View):
    title, icon = "Downloads", G_DOWN
    interval = 0
    backdrop = False   # no blurred backdrop: you watch the page (or the bar) behind it while things download
    css = CSS
    hints = [("Ctrl+V", "download a copied link"), ("Ctrl+L", "type a link"), ("Space", "pause / resume all"),
             ("Esc", "close")]

    def __init__(self):
        super().__init__()
        self.sock = None
        self.items = []
        self.rows = {}   # gid -> Row

    # ---- the window -----------------------------------------------------------------------------------
    def build(self):
        self.link = Gtk.Entry(hexpand=True, placeholder_text="Paste a link to download it here")
        self.link.connect("activate", lambda *_: self.add_link())
        get = button(f"{G_DOWN}  Download", self.add_link, "primary", tooltip="Enter")
        get.set_focusable(False)
        self.pause_all = button(f"{G_PAUSE}  Pause all", lambda: self.request({"op": "pause_all"}), "flat")
        self.resume_all = button(f"{G_PLAY}  Resume all", lambda: self.request({"op": "resume_all"}), "flat")
        self.clear_done = button("Clear", lambda: self.request({"op": "clear"}), "flat")
        for b in (self.pause_all, self.resume_all, self.clear_done):
            b.set_focusable(False)
        self.moving_head = rule_heading("Downloading")
        self.moving_head.append(self.pause_all)
        self.moving_head.append(self.resume_all)
        self.moving = box(True, 0)
        self.done_head = rule_heading("Finished", spaced=True)
        self.done_head.append(self.clear_done)
        self.done = box(True, 0)
        self.empty = box(True, 8, label(G_DOWN, "big", xalign=0.5), label("Nothing downloaded yet", "sub", xalign=0.5),
                         label("Chrome's downloads come here, or paste a link above.", "dim", xalign=0.5),
                         classes=("empty",))
        self.empty.set_margin_top(60)
        lists = box(True, 8, self.moving_head, self.moving, self.done_head, self.done, self.empty)
        main = box(True, 12, box(False, 10, self.link, get), scrolled(lists), classes=("pane",))

        self.offline = box(True, 10, label("The download manager isn't running.", "red"),
                           button("Start it", self.start_service, "primary"), classes=("pane",))
        self.offline.set_valign(Gtk.Align.CENTER)
        self.offline.set_halign(Gtk.Align.CENTER)
        self.stack = Gtk.Stack()
        self.stack.add_named(main, "main")
        self.stack.add_named(self.offline, "offline")
        self.connect()
        # nothing focused at first: Space and Ctrl+V are the window's (Ctrl+L goes to the link box)
        GLib.idle_add(lambda: self.window.set_focus(None) and False)
        return self.stack

    # ---- the service ----------------------------------------------------------------------------------
    def connect(self):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SOCK)
            s.sendall(b'{"op": "watch"}\n{"op": "seen"}\n')
        except OSError:
            self.stack.set_visible_child_name("offline")
            return False
        self.sock = s
        self.stack.set_visible_child_name("main")
        threading.Thread(target=self.read, args=(s,), daemon=True).start()
        return False

    def read(self, s):
        try:
            for line in s.makefile(encoding="utf-8"):
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                GLib.idle_add(lambda ev=ev: self.event(ev) and False)
        except OSError:
            pass
        GLib.idle_add(lambda: self.lost(s) and False)

    def lost(self, s):
        if self.sock is s:   # the service went away (restarted): back when it is
            self.sock = None
            self.stack.set_visible_child_name("offline")
            GLib.timeout_add(1500, self.connect)

    def request(self, msg):
        if self.sock:
            try:
                self.sock.sendall((json.dumps(msg) + "\n").encode())
            except OSError:
                pass

    def start_service(self):
        subprocess.Popen(["systemctl", "--user", "start", "downloads.service"])
        GLib.timeout_add(1200, self.connect)

    def event(self, ev):
        if ev.get("ev") == "state":
            self.items = ev["items"]
            self.fill()
        elif ev.get("ev") == "error":
            self.say(ev["msg"], "bad")

    # ---- the lists ------------------------------------------------------------------------------------
    def fill(self):
        """Each download in its list (moving ones, then finished, newest first); a card is kept while the
        download stays in the same list, so only its numbers change."""
        moving = [i for i in reversed(self.items) if i["status"] in MOVING]
        done = sorted((i for i in self.items if i["status"] not in MOVING),
                      key=lambda i: -(i.get("finished") or i.get("added") or 0))
        gids = {i["gid"] for i in self.items}
        for gid in [g for g in self.rows if g not in gids]:
            del self.rows[gid]
        for parent, items in ((self.moving, moving), (self.done, done)):
            # in place if the same cards in the same order; otherwise the list is laid out again
            current = []
            child = parent.get_first_child()
            while child is not None:
                current.append(child)
                child = child.get_next_sibling()
            wanted = []
            for it in items:
                row = self.rows.get(it["gid"])
                if row is None:
                    row = self.rows[it["gid"]] = Row(self, it)
                else:
                    row.update(it)
                wanted.append(row.box)
            if current != wanted:
                for w in current:
                    parent.remove(w)
                for w in wanted:
                    if w.get_parent() is not None:
                        w.get_parent().remove(w)
                    parent.append(w)
        self.moving_head.set_visible(bool(moving))
        self.done_head.set_visible(bool(done))
        self.empty.set_visible(not self.items)
        self.pause_all.set_visible(any(i["status"] in ("active", "waiting") for i in moving))
        self.resume_all.set_visible(any(i["status"] == "paused" for i in moving))
        active = [i for i in moving if i["status"] == "active"]
        speed = sum(i["speed"] for i in active)
        self.set_subtitle(f"{len(moving)} downloading  ·  {human(speed)}/s" if moving else
                          f"{len(done)} finished" if done else "")

    # ---- doing things ---------------------------------------------------------------------------------
    def add_link(self, text=None):
        links = [w for w in (text if text is not None else self.link.get_text()).split()
                 if urllib.parse.urlsplit(w).scheme in ("http", "https", "ftp", "sftp", "magnet")]
        if not links:
            self.say("that isn't a link", "bad")
            return
        for url in links:
            self.request({"op": "add", "url": url})
        self.link.set_text("")
        self.window.set_focus(None)
        self.say(f"downloading {len(links)} link{'s' if len(links) > 1 else ''}" if len(links) > 1 else
                 "downloading it")

    def paste(self):
        def got(c, res):
            try:
                text = c.read_text_finish(res) or ""
            except GLib.Error:
                return
            self.add_link(text)
        self.window.get_clipboard().read_text_async(None, got)

    def copy(self, text):
        self.window.get_clipboard().set(text)
        self.say("link copied")

    def launch(self, *argv):
        subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def open_after(self, argv):
        self.launch(*argv)
        self.window.close()

    def key(self, keyval, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if self.typing():
            return False
        k = Gdk.keyval_to_lower(keyval)
        if ctrl and k == Gdk.KEY_v:
            self.paste()
        elif ctrl and k == Gdk.KEY_l:
            self.link.grab_focus()
        elif keyval == Gdk.KEY_space and not ctrl:
            moving = [i for i in self.items if i["status"] in ("active", "waiting")]
            self.request({"op": "pause_all" if moving else "resume_all"})
        else:
            return False
        return True


def main():
    gc.disable()   # as gtkkit.run: the collector only on the main loop (GTK is main-thread only)
    GLib.timeout_add_seconds(30, collect)
    PanelApp(Downloads(), "panels.downloads", (900, 580), toggle=True).run([])


if __name__ == "__main__":
    main()
