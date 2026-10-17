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
G_MESSAGE, G_SCAN = "\U000f0369", "\U000f0450"
TYPE_GLYPH = {"mobile": G_PHONE, "desktop": G_DESKTOP, "laptop": G_LAPTOP, "tablet": G_TABLET}
STATE_TEXT = {"asking": "waiting for an answer", "receiving": "receiving", "sending": "sending", "done": "done",
              "declined": "declined", "cancelled": "cancelled", "failed": "failed"}
STATE_CLASS = {"asking": "amber", "receiving": "accent", "sending": "accent", "done": "green", "failed": "red"}
CSS = """
.devices { border-right: 1px solid alpha(#3b4261, .7); padding: 14px 14px 14px 20px; }
.devices list > row { padding: 9px 12px; border-left: 2px solid transparent; }
.devices list > row:selected { border-left-color: #6b8fe0; }
.dev-glyph { color: #565f89; font-size: 15pt; min-width: 28px; }
.devices list > row:selected .dev-glyph { color: #6b8fe0; }
.dev-name { font-weight: 700; }
.dev-empty .big { color: #3b4261; font-size: 26pt; }
.pane { padding: 14px 20px; }
.drop { border: 1px dashed alpha(#3b4261, .9); padding: 14px 16px; }
.drop.hover { border-color: #6b8fe0; background: alpha(#6b8fe0, .06); }
.drop-empty { padding: 10px 0 4px 0; }
.drop-empty .big { color: #3b4261; font-size: 22pt; }
.drop.hover .drop-empty .big { color: #6b8fe0; }
.picked list > row { padding: 4px 6px; }
.picked .x { padding: 0 6px; min-height: 0; color: #565f89; }
.picked .x:hover { color: #f7768e; }
.drop-foot { border-top: 1px solid alpha(#c0caf5, .05); padding-top: 8px; }
.transfer { padding: 10px 12px; margin-bottom: 6px; background: alpha(#292e42, .35); border-left: 2px solid transparent; }
.transfer.asking { border-left-color: #e0af68; background: alpha(#292e42, .6); }
.transfer.failed { border-left-color: #f7768e; }
.transfer .glyph { font-size: 14pt; min-width: 30px; }
.transfer .glyph.in { color: #9ece6a; }
.transfer .glyph.out { color: #6b8fe0; }
.transfer .what { font-weight: 700; }
.transfer .message { color: #c0caf5; background: alpha(#16161e, .6); border-left: 2px solid #3b4261; padding: 6px 10px;
                     margin-top: 4px; }
.transfer progressbar { margin-top: 6px; }
progressbar trough { background: #292e42; min-height: 3px; border: none; }
progressbar progress { background: #6b8fe0; min-height: 3px; border: none; }
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
        self.dev_empty = box(True, 10, label(G_PHONE, "big", xalign=0.5),
                             label("No devices yet", "sub", xalign=0.5),
                             label("Open LocalSend on the phone, on the same Wi-Fi, then scan.", "dim", xalign=0.5,
                                   wrap=True), classes=("dev-empty",))
        self.dev_empty.set_margin_top(40)
        self.dev_empty.get_last_child().set_justify(Gtk.Justification.CENTER)
        self.dev_empty.get_last_child().set_max_width_chars(28)   # wraps instead of widening the column
        self.scan_btn = button(f"{G_SCAN}  Scan", self.scan, tooltip="r")
        self.scan_btn.set_focusable(False)
        left = box(True, 10, rule_heading("Devices"), self.dev_empty, scrolled(self.dev_list), self.scan_btn,
                   classes=("devices",))
        left.set_size_request(300, -1)
        left.set_hexpand(False)   # a device's long name doesn't widen the column

        self.picked_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.picked_list.add_css_class("picked")
        # empty: a centred target saying what to do; with files: their list and a footer with the total
        self.drop_msg = box(True, 6, label(G_UP, "big", xalign=0.5), label("Drop files or folders here", "sub", xalign=0.5),
                            box(False, 6, button(f"{G_FILE}  Files…", lambda: self.pick(False), tooltip="Ctrl+O"),
                                button(f"{G_FOLDER}  Folder…", lambda: self.pick(True), tooltip="Ctrl+Shift+O")),
                            label("or paste them (Ctrl+V)", "dim", xalign=0.5), classes=("drop-empty",))
        self.drop_msg.get_last_child().get_prev_sibling().set_halign(Gtk.Align.CENTER)
        self.drop_msg.get_last_child().get_prev_sibling().set_margin_top(6)
        self.picked_total = label("", "dim")
        add_files = button(f"{G_FILE}  Files…", lambda: self.pick(False), "flat", tooltip="Ctrl+O")
        add_folder = button(f"{G_FOLDER}  Folder…", lambda: self.pick(True), "flat", tooltip="Ctrl+Shift+O")
        self.picked_scroll = scrolled(self.picked_list, vexpand=False)
        self.picked_scroll.set_max_content_height(180)
        self.picked_scroll.set_propagate_natural_height(True)
        self.picked_foot = box(False, 6, self.picked_total, Gtk.Box(hexpand=True), add_files, add_folder,
                               button("Clear", self.clear_picked, "flat", "danger"), classes=("drop-foot",))
        self.drop = box(True, 8, self.drop_msg, self.picked_scroll, self.picked_foot, classes=("drop",))
        for b in [*self.drop_msg.get_last_child().get_prev_sibling(), *self.picked_foot]:
            if isinstance(b, Gtk.Button):
                b.set_focusable(False)
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
        self.activity.set_margin_top(4)
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
        total = 0
        for p in self.picked:
            size, count = folder_size(p)
            total += size
            what = human(size) if os.path.isfile(p) else f"{count} files  ·  {human(size)}"
            x = button(G_CROSS, lambda p=p: (self.picked.remove(p), self.fill_picked()), "flat", "x",
                       tooltip="Leave it out")
            x.set_focusable(False)
            name = label(os.path.basename(p), ellipsize=True)
            self.picked_list.append(box(False, 8, label(G_FOLDER if os.path.isdir(p) else G_FILE, "accent"), name,
                                        label(what, "dim"), x))
        n = len(self.picked)
        self.picked_total.set_text(f"{n} item{'s' if n != 1 else ''}  ·  {human(total)}")
        self.drop_msg.set_visible(not self.picked)
        self.picked_scroll.set_visible(bool(self.picked))
        self.picked_foot.set_visible(bool(self.picked))
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
            row = box(False, 10, classes=("transfer",))
            self.rows[tid] = row
            self.activity.append(row)
            self.update_row(tid)
        self.activity_empty.set_visible(not self.transfers)
        self.clear_activity.set_visible(any(d["state"] not in ("asking", "receiving", "sending")
                                            for _k, d in self.transfers.values()))

    def update_row(self, tid):
        """A card: what (bold) and who / how big under it, the state on the right, the actions after it;
        a bar only while it moves, the message itself when it's text."""
        kind, d = self.transfers[tid]
        row = self.rows[tid]
        clear(row)
        files, state, text = d.get("files", []), d["state"], d.get("text")
        for c in ("asking", "failed"):
            (row.add_css_class if state == c else row.remove_css_class)(c)
        who = d.get("from") if kind == "in" else d.get("to")
        if text is not None:
            what = "Message"
        elif len(files) == 1:
            what = files[0]["name"]
        else:
            what = f"{len(files)} files"
        details = f"{'from' if kind == 'in' else 'to'} {who}"
        if text is None:
            details += f"  ·  {human(d.get('total', 0))}"
        status = "wants to send" if kind == "in" and state == "asking" else STATE_TEXT.get(state, state)
        moving = state in ("receiving", "sending") and text is None and d.get("total")
        if moving:
            status += f"  {min(d.get('done_bytes', 0) * 100 // max(d['total'], 1), 100)} %"
        glyph = G_MESSAGE if text is not None else G_DOWN if kind == "in" else G_UP
        g = label(glyph, "glyph", kind)
        g.set_valign(Gtk.Align.START)
        who_line = label(details, "dim", ellipsize=True)
        who_line.set_hexpand(False)   # the state right after it; cut first so a long line can't widen the window
        # the " · " a label of its own: GTK measures a label without its trailing spaces, so ending this one
        # with them always cut it ("…") by their width
        main = box(True, 3, label(what, "what", ellipsize=True),
                   box(False, 0, who_line, label("  ·  ", "dim"), label(status, STATE_CLASS.get(state, "dim"))))
        main.set_hexpand(True)
        if d.get("error"):
            main.append(label(d["error"], "red", wrap=True))
        if text is not None and (kind == "out" or state == "done"):
            msg = label(text, "message", wrap=True)
            msg.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            msg.set_selectable(True)
            msg.set_can_focus(False)
            main.append(msg)
        elif len(files) > 1 and state == "asking":
            names = ", ".join(f["name"] for f in files[:4]) + (f" and {len(files) - 4} more" if len(files) > 4 else "")
            main.append(label(names, "sub", wrap=True))
        if moving:
            main.append(Gtk.ProgressBar(fraction=min(d.get("done_bytes", 0) / max(d["total"], 1), 1.0)))
        row.append(g)
        row.append(main)
        actions = []
        if kind == "in" and state == "asking":
            actions += [button("Accept", lambda: self.answer(tid, True), "primary", tooltip="y"),
                        button("Decline", lambda: self.answer(tid, False), tooltip="n")]
        if state in ("receiving", "sending") or (kind == "out" and state == "asking"):
            actions.append(button("Cancel", lambda: self.request({"op": "cancel", "id": tid}), "flat"))
        if kind == "in" and state == "done" and d.get("saved"):
            first = d["saved"][0]
            # then the popup goes: pinned above its backdrop, it would hide the viewer it opened
            actions.append(button("Open", lambda: self.open_after(["xdg-open", first]), "flat"))
            actions.append(button("Show in Files", lambda: self.open_after(["python3", FILES, first]), "flat"))
        if text is not None and state == "done":
            actions.append(button("Copy", lambda: self.copy(text), "flat"))
        if actions:
            for b in actions:
                b.set_focusable(False)
            acts = box(False, 4, *actions)
            acts.set_valign(Gtk.Align.CENTER)
            row.append(acts)

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

    def open_after(self, argv):
        self.launch(*argv)
        self.close()

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
