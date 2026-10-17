#!/usr/bin/env python3
# lsendgui.py — LocalSend (Super + Ctrl + D): send files and text to the phone, and take what it sends.
#   lsendgui.py [file…]   opens with these files picked (`lsend send`, Files' "Send to phone")
# The window of lsend (local/lib/lsend/lsend.py), the service that does the networking: it connects to
# the service's socket and shows what it says — the devices seen, the requests to accept, the transfers.
# Opened by the service when the phone sends something and the window isn't up. Event-driven: a thread
# reads the socket, nothing polls.
import gc, json, os, socket, subprocess, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import (Gdk, Gio, GLib, Gtk, Pango, PanelApp, View, box, button, clear, collect, label, rule_heading,
                    scrolled)

HOME = os.path.expanduser("~")
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "lsend.sock")
DOWNLOADS = f"{HOME}/Downloads"
FILES = f"{HOME}/.local/lib/panels/filesgui.py"
G_SEND = "\U000f048a"
G_PHONE, G_DESKTOP, G_LAPTOP, G_TABLET = "\U000f011c", "\U000f0379", "\U000f0322", "\U000f04f6"
G_DOWN, G_UP, G_FILE, G_FOLDER, G_CROSS = "\U000f01da", "\U000f0552", "\U000f0214", "\U000f0256", "\U000f0156"
TYPE_GLYPH = {"mobile": G_PHONE, "desktop": G_DESKTOP, "laptop": G_LAPTOP, "tablet": G_TABLET}
STATE_TEXT = {"asking": "waiting for an answer", "receiving": "receiving", "sending": "sending", "done": "done",
              "declined": "declined", "cancelled": "cancelled", "failed": "failed"}
CSS = """
.devices { border-right: 1px solid alpha(#3b4261, .7); padding: 14px 14px 14px 20px; }
.devices list > row { padding: 8px 12px; }
.dev-glyph { color: #6b8fe0; font-size: 15pt; min-width: 28px; }
.dev-name { font-weight: 700; }
.pane { padding: 14px 20px; }
.drop { border: 1px dashed alpha(#3b4261, .9); padding: 18px; }
.drop.hover { border-color: #6b8fe0; background: alpha(#6b8fe0, .06); }
.picked list > row { padding: 3px 6px; }
.picked .x { padding: 0 6px; min-height: 0; }
.transfer { padding: 8px 0; border-bottom: 1px solid alpha(#c0caf5, .04); }
.transfer .glyph { font-size: 13pt; min-width: 26px; }
.transfer .glyph.in { color: #9ece6a; }
.transfer .glyph.out { color: #6b8fe0; }
.transfer .message { color: #c0caf5; background: alpha(#292e42, .6); padding: 6px 10px; }
progressbar trough { background: #292e42; min-height: 3px; border: none; }
progressbar progress { background: #6b8fe0; min-height: 3px; border: none; }
progressbar.done progress { background: #9ece6a; }
progressbar.bad progress { background: #f7768e; }
"""


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def folder_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path), 1
    size = count = 0
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            try:
                size += os.path.getsize(os.path.join(root, n))
                count += 1
            except OSError:
                pass
    return size, count


class LSend(View):
    title, icon = "LocalSend", G_SEND
    interval = 0
    css = CSS
    hints = [("Ctrl+O", "add files"), ("Ctrl+V", "paste"), ("Enter", "send"), ("y/n", "accept / decline"),
             ("r", "scan"), ("Esc", "close")]

    def __init__(self, paths=()):
        super().__init__()
        self.picked = [os.path.abspath(p) for p in paths if os.path.exists(p)]
        self.devices, self.transfers = [], {}   # transfers: id -> (kind "in"/"out", data)
        self.target = None                      # the chosen device's fingerprint
        self.rows = {}                          # transfer id -> its box in Activity
        self.sock = None
        self.me = None

    # ---- the window -----------------------------------------------------------------------------------
    def build(self):
        self.dev_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.dev_list.connect("row-selected", self.device_picked)
        self.dev_empty = label("None seen yet.\n\nOpen LocalSend on the phone, on the same Wi-Fi, "
                               "then scan (r).", "dim", wrap=True)
        self.scan_btn = button("Scan", self.scan, tooltip="r")
        self.scan_btn.set_focusable(False)
        left = box(True, 8, rule_heading("Devices"), self.dev_empty, scrolled(self.dev_list),
                   box(False, 0, self.scan_btn), classes=("devices",))
        left.set_size_request(280, -1)
        left.set_hexpand(False)   # a device's long name doesn't widen the column

        self.picked_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.picked_list.add_css_class("picked")
        self.drop_msg = label("Drop files or folders here", "dim", xalign=0.5)
        add_files = button(f"{G_FILE}  Files…", lambda: self.pick(False), "flat", tooltip="Ctrl+O")
        add_folder = button(f"{G_FOLDER}  Folder…", lambda: self.pick(True), "flat", tooltip="Ctrl+Shift+O")
        self.clear_btn = button("Clear", self.clear_picked, "flat")
        self.picked_scroll = scrolled(self.picked_list, vexpand=False)
        self.picked_scroll.set_max_content_height(180)
        self.picked_scroll.set_propagate_natural_height(True)
        self.drop = box(True, 8, self.drop_msg, self.picked_scroll,
                        box(False, 6, add_files, add_folder, Gtk.Box(hexpand=True), self.clear_btn), classes=("drop",))
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("drop", lambda _t, files, _x, _y: self.add([f.get_path() for f in files.get_files()
                                                                   if f.get_path()]) or True)
        target.connect("enter", lambda *_: self.drop.add_css_class("hover") or Gdk.DragAction.COPY)
        target.connect("leave", lambda *_: self.drop.remove_css_class("hover"))
        self.drop.add_controller(target)

        self.message = Gtk.Entry(hexpand=True, placeholder_text="or type a message (a link, a code…)")
        self.message.connect("activate", lambda *_: self.send())
        self.message.connect("changed", lambda *_: self.update_send())
        self.send_btn = button(f"{G_SEND}  Send", self.send, "primary", tooltip="Enter")
        self.send_btn.set_focusable(False)
        send_row = box(False, 10, self.message, self.send_btn)

        self.activity = box(True, 0)
        self.activity_empty = label("Nothing sent or received yet.", "dim")
        self.clear_activity = button("Clear", lambda: self.request({"op": "clear"}), "flat")
        self.clear_activity.set_focusable(False)
        head = rule_heading("Activity")
        head.append(self.clear_activity)
        right = box(True, 10, rule_heading("Send"), self.drop, send_row, head, self.activity_empty,
                    scrolled(self.activity), classes=("pane",))
        right.set_hexpand(True)

        self.offline = box(True, 10, label("The LocalSend service isn't running.", "red"),
                           button("Start it", self.start_service, "primary"), classes=("pane",))
        self.offline.set_valign(Gtk.Align.CENTER)
        self.offline.set_halign(Gtk.Align.CENTER)
        self.body = box(False, 0, left, right)
        self.stack = Gtk.Stack()
        self.stack.add_named(self.body, "main")
        self.stack.add_named(self.offline, "offline")
        self.fill_picked()
        self.connect()
        return self.stack

    # ---- the service ----------------------------------------------------------------------------------
    def connect(self):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SOCK)
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
        subprocess.Popen(["systemctl", "--user", "start", "lsend.service"])
        GLib.timeout_add(1200, self.connect)

    def event(self, ev):
        kind = ev.get("ev")
        if kind == "state":
            self.me = ev["me"]
            self.set_subtitle(f"this PC is “{self.me['alias']}”")
            self.devices = ev["devices"]
            self.transfers = {s["id"]: ("in", s) for s in ev["incoming"]}
            self.transfers.update({j["id"]: ("out", j) for j in ev["outgoing"]})
            self.fill_devices()
            self.fill_activity()
            if not self.devices:
                self.scan()
        elif kind == "devices":
            self.devices = ev["devices"]
            self.fill_devices()
        elif kind == "scanning":
            self.scan_btn.set_sensitive(not ev["on"])
            self.scan_btn.set_label("Scanning…" if ev["on"] else "Scan")
        elif kind in ("incoming", "outgoing"):
            data = ev.get("session") or ev.get("job")
            known = data["id"] in self.transfers
            self.transfers[data["id"]] = ("in" if kind == "incoming" else "out", data)
            if known and data["id"] in self.rows:
                self.update_row(data["id"])
            else:
                self.fill_activity()
            if kind == "incoming" and data["state"] == "asking":
                self.window.present()
        elif kind == "error":
            self.say(ev["msg"], "bad")

    def scan(self):
        self.request({"op": "scan"})

    # ---- devices --------------------------------------------------------------------------------------
    def fill_devices(self):
        clear(self.dev_list)
        self.dev_empty.set_visible(not self.devices)
        devices = sorted(self.devices, key=lambda d: d["alias"].lower())
        if devices and self.target not in [d["fingerprint"] for d in devices]:
            self.target = devices[0]["fingerprint"]
        for d in devices:
            name = label(d["alias"], "dev-name", ellipsize=True)
            sub = label(f"{d['model'] or d['type']}  ·  {d['ip']}", "dim", ellipsize=True)
            row = Gtk.ListBoxRow(child=box(False, 8, label(TYPE_GLYPH.get(d["type"], G_PHONE), "dev-glyph"),
                                           box(True, 2, name, sub)))
            row.fingerprint = d["fingerprint"]
            self.dev_list.append(row)
            if d["fingerprint"] == self.target:
                self.dev_list.select_row(row)
        self.update_send()

    def device_picked(self, _l, row):
        if row is not None:
            self.target = row.fingerprint
        self.update_send()

    def target_name(self):
        return next((d["alias"] for d in self.devices if d["fingerprint"] == self.target), None)

    # ---- what to send ---------------------------------------------------------------------------------
    def add(self, paths):
        for p in paths:
            p = os.path.abspath(p)
            if os.path.exists(p) and p not in self.picked:
                self.picked.append(p)
        self.fill_picked()

    def clear_picked(self):
        self.picked = []
        self.fill_picked()

    def fill_picked(self):
        clear(self.picked_list)
        for p in self.picked:
            size, count = folder_size(p)
            what = human(size) if os.path.isfile(p) else f"{count} files  ·  {human(size)}"
            x = button(G_CROSS, lambda p=p: (self.picked.remove(p), self.fill_picked()), "flat", "x",
                       tooltip="Leave it out")
            x.set_focusable(False)
            name = label(os.path.basename(p), ellipsize=True)
            self.picked_list.append(box(False, 8, label(G_FOLDER if os.path.isdir(p) else G_FILE, "accent"), name,
                                        label(what, "dim"), x))
        self.drop_msg.set_visible(not self.picked)
        self.picked_scroll.set_visible(bool(self.picked))
        self.clear_btn.set_visible(bool(self.picked))
        self.update_send()

    def update_send(self):
        name = self.target_name()
        ready = name is not None and (bool(self.picked) or bool(self.message.get_text().strip()))
        self.send_btn.set_sensitive(ready)
        self.send_btn.set_label(f"{G_SEND}  Send to {name}" if name else f"{G_SEND}  Send")

    def pick(self, folders):
        dialog = Gtk.FileDialog(title="Pick a folder to send" if folders else "Pick files to send", modal=True)

        def done(d, res):
            try:
                got = (d.select_multiple_folders_finish if folders else d.open_multiple_finish)(res)
            except GLib.Error:
                return   # cancelled
            self.add([got.get_item(i).get_path() for i in range(got.get_n_items()) if got.get_item(i).get_path()])
        (dialog.select_multiple_folders if folders else dialog.open_multiple)(self.window, None, done)

    def paste(self):
        """Files copied in Files (or a browser) to send; text into the message."""
        clip = self.window.get_clipboard()
        if clip.get_formats().contain_gtype(Gdk.FileList):
            def got_files(c, res):
                try:
                    files = c.read_value_finish(res)
                except GLib.Error:
                    return
                self.add([f.get_path() for f in files.get_files() if f.get_path()])
            clip.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, got_files)
        else:
            def got_text(c, res):
                try:
                    text = c.read_text_finish(res)
                except GLib.Error:
                    return
                if text:
                    self.message.set_text(text)
            clip.read_text_async(None, got_text)

    def send(self):
        name, text = self.target_name(), self.message.get_text().strip()
        if not name:
            self.say("no device to send to: open LocalSend on the phone and scan (r)", "bad")
            return
        if not self.picked and not text:
            self.say("pick files (Ctrl+O, drop them here) or type a message first")
            return
        if self.picked:
            self.request({"op": "send", "device": self.target, "paths": self.picked})
        if text:
            self.request({"op": "send", "device": self.target, "text": text})
        self.picked = []
        self.message.set_text("")
        self.fill_picked()
        self.window.set_focus(None)
        self.say(f"asking {name} to accept…")

    # ---- activity -------------------------------------------------------------------------------------
    def fill_activity(self):
        clear(self.activity)
        self.rows = {}
        for tid in reversed(list(self.transfers)):   # newest first
            row = box(True, 6, classes=("transfer",))
            self.rows[tid] = row
            self.activity.append(row)
            self.update_row(tid)
        self.activity_empty.set_visible(not self.transfers)
        self.clear_activity.set_visible(any(d["state"] not in ("asking", "receiving", "sending")
                                            for _k, d in self.transfers.values()))

    def update_row(self, tid):
        kind, d = self.transfers[tid]
        row = self.rows[tid]
        clear(row)
        files, state = d.get("files", []), d["state"]
        who = d.get("from") if kind == "in" else d.get("to")
        if d.get("text") is not None:
            what = "a message"
        elif len(files) == 1:
            what = files[0]["name"]
        else:
            what = f"{len(files)} files"
        size = f"  ·  {human(d.get('total', 0))}" if d.get("text") is None else ""
        line = f"{'from' if kind == 'in' else 'to'} {who}:  {what}{size}"
        status = STATE_TEXT.get(state, state) + (f": {d['error']}" if d.get("error") else "")
        top = box(False, 8, label(G_DOWN if kind == "in" else G_UP, "glyph", kind),
                  label(line, ellipsize=True), label(status, "red" if state == "failed" else "dim"))
        row.append(top)
        if d.get("text") is not None and (kind == "out" or state == "done"):
            msg = label(d["text"], "message", wrap=True)
            msg.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            msg.set_selectable(True)
            msg.set_can_focus(False)
            row.append(msg)
        elif len(files) > 1 and state == "asking":
            names = ", ".join(f["name"] for f in files[:4]) + (f" and {len(files) - 4} more" if len(files) > 4 else "")
            row.append(label(names, "dim", wrap=True))
        if state in ("receiving", "sending", "done", "failed") and d.get("text") is None and d.get("total"):
            bar = Gtk.ProgressBar(fraction=min(d.get("done_bytes", 0) / max(d["total"], 1), 1.0))
            bar.add_css_class("done" if state == "done" else "bad" if state == "failed" else "active")
            row.append(bar)
        actions = []
        if kind == "in" and state == "asking":
            actions += [button("Accept", lambda: self.answer(tid, True), "primary", tooltip="y"),
                        button("Decline", lambda: self.answer(tid, False), "flat", tooltip="n")]
        if state in ("receiving", "sending") or (kind == "out" and state == "asking"):
            actions.append(button("Cancel", lambda: self.request({"op": "cancel", "id": tid}), "flat"))
        if kind == "in" and state == "done" and d.get("saved"):
            first = d["saved"][0]
            actions.append(button("Open", lambda: self.launch("xdg-open", first), "flat"))
            actions.append(button("Show in Files", lambda: self.launch("python3", FILES, first), "flat"))
        if d.get("text") is not None and state == "done":
            actions.append(button("Copy", lambda: self.copy(d["text"]), "flat"))
        if actions:
            for b in actions:
                b.set_focusable(False)
            row.append(box(False, 6, *actions))

    def answer(self, tid, accept):
        self.request({"op": "answer", "id": tid, "accept": accept})

    def asking(self):
        return next((tid for tid, (k, d) in reversed(list(self.transfers.items()))
                     if k == "in" and d["state"] == "asking"), None)

    def copy(self, text):
        self.window.get_clipboard().set(text)
        self.say("copied")

    def launch(self, *argv):
        subprocess.Popen(["uwsm", "app", "--", *argv], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ---- keys -----------------------------------------------------------------------------------------
    def key(self, keyval, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        k = Gdk.keyval_to_lower(keyval)
        if ctrl and k == Gdk.KEY_o:
            self.pick(shift)
        elif ctrl and k == Gdk.KEY_v and not self.typing():
            self.paste()
        elif self.typing() or ctrl:
            return False
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.send()
        elif k == Gdk.KEY_r:
            self.scan()
        elif k in (Gdk.KEY_y, Gdk.KEY_n) and self.asking():
            self.answer(self.asking(), k == Gdk.KEY_y)
        elif keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and self.picked:
            self.picked.pop()
            self.fill_picked()
        elif k == Gdk.KEY_m:
            self.message.grab_focus()
        else:
            return False
        return True


class LSendApp(PanelApp):
    """One window: files passed while it's open are added to it (`lsend send`, Files)."""

    def __init__(self, view):
        super().__init__(view, "panels.lsend", (980, 620), toggle=True)
        self.set_flags(Gio.ApplicationFlags.HANDLES_OPEN)

    def do_open(self, files, _n, _hint):
        paths = [f.get_path() for f in files if f.get_path()]
        if self.win:
            self.view.add(paths)
            self.win.present()
            return
        self.view.picked = paths
        self.activate()


def main():
    gc.disable()   # as gtkkit.run: the collector only on the main loop (GTK is main-thread only)
    GLib.timeout_add_seconds(30, collect)
    LSendApp(LSend()).run(sys.argv)


if __name__ == "__main__":
    main()
