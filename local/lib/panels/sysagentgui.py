#!/usr/bin/env python3
# sysagentgui.py — the System agent (Super + Ctrl + S, the sparkles in the bar): ask a free AI model (through
# opencode) about this PC — config, logs, services, processes, Hyprland, packages, your files — in a popup
# like every other panel. Strictly read-only: sysagentlib.py says how (its tools only read, in a sandbox
# where nothing can be written). Each tool call shows under the answer. The chats are kept (the side list,
# newest first); a chat's bin deletes it (a second click within 3 s).
import os, re, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import GLib, Gdk, Gtk, Pango, View, box, button, dropdown, label, recolor, run, scrolled
import sysagentlib as lib

TRASH = ""
CSS = """
.ag-log { padding: 4px 2px; }
.ag-you { color: #6b8fe0; font-weight: 700; }
.ag-ai { color: #a9b1d6; font-weight: 700; }
.ag-msg { padding-bottom: 14px; border-bottom: 1px solid alpha(#3b4261, .5); }
.ag-looked { color: #565f89; font-size: 9pt; }
.ag-code { font-family: "JetBrainsMono Nerd Font"; font-size: 9.5pt; color: #c0caf5; background: #16161e;
           border: 1px solid alpha(#3b4261, .7); padding: 8px 12px; }
.ag-h { color: #6b8fe0; font-weight: 700; }
.ag-mark { color: #565f89; }
.ag-source { color: #565f89; font-size: 9pt; }
.ag-table { background: #16161e; border: 1px solid alpha(#3b4261, .7); padding: 8px 12px; }
.ag-th { color: #a9b1d6; font-weight: 700; }
button.ag-copy { padding: 1px 8px; min-height: 0; color: #565f89; font-size: 9pt; }
button.ag-copy:hover { color: #c0caf5; }
.chats > row { padding: 6px 4px 6px 10px; }
.chat-when { color: #565f89; font-size: 9pt; }
button.chat-del { padding: 2px 8px; min-height: 0; color: #565f89; }
button.chat-del:hover { color: #f7768e; }
button.chat-del.armed { background: #f7768e; color: #16161e; font-weight: 700; }   /* over .flat's none */
dropdown.model > button { min-width: 0; padding: 3px 10px; }
"""
INTRO = ("Ask about this PC: why a service failed, what's using the memory, where a setting lives, what a "
         "config file does, which packages are installed, what Hyprland sees…\n\n"
         "Strictly read-only. The model (free, through opencode) has none of your files: it can only use "
         "this panel's tools, and they only read: files, folders, searches, logs, services, processes and "
         "Hyprland's state, with commands run in a sandbox where the whole filesystem is read-only, with no "
         "network and no way to reach your programs. Keys, tokens, browser data, shell history and .env files "
         "are hidden from it. When something needs changing, it tells you what to run. Free models may keep "
         "what they're sent.")


def when(t):
    secs = time.time() - t
    if secs < 60:
        return "just now"
    for n, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if secs >= n:
            return f"{int(secs // n)} {unit} ago"


def call_text(name, args):
    """A tool call in a few words, as it's shown: "read_file ~/.config/hypr/hyprland.lua"."""
    vals = [str(v) for v in (args or {}).values() if v not in ("", None, False)]
    return f"{name} " + " · ".join(vals)[:160]


def inline(text):
    """Markdown's `code`, **bold** and *italics* as Pango markup, the rest escaped."""
    out = GLib.markup_escape_text(text)
    out = re.sub(r"`([^`\n]+)`", lambda m: recolor(f"<span font_family='JetBrainsMono Nerd Font' foreground='#7dcfff'>"
                                                    f"{m.group(1)}</span>"), out)
    out = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", out)
    return re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<i>\1</i>", out)


def blocks(text):
    """An answer's Markdown as blocks: ("code", text), ("table", rows), ("h", text), ("li", marker, text,
    depth), ("p", text). Lists keep their marker (•, 1.); a line under an item continues it."""
    out, lines, i = [], text.replace("\t", "    ").split("\n"), 0
    while i < len(lines):
        line = lines[i]
        bare = line.strip()
        if bare.startswith("```"):   # a fence, at any indent (inside a list item too): its lines dedented
            body, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            pad = min((len(l) - len(l.lstrip()) for l in body if l.strip()), default=0)
            out.append(("code", "\n".join(l[pad:] for l in body).strip("\n")))
        elif bare.startswith("|") and bare.endswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells if c):   # the |---| line
                    rows.append(cells)
                i += 1
            out.append(("table", rows))
            continue
        elif m := re.match(r"#{1,6}\s+(.*)", bare):
            out.append(("h", m.group(1).strip("* ")))
        elif re.fullmatch(r"\*\*[^*]+\*\*:?", bare):   # a line all in bold: a heading too
            out.append(("h", bare.strip("*:")))
        elif m := re.match(r"(\s*)([-*+]|\d+[.)])\s+(.*)", line):
            marker = "\u2022" if m.group(2) in "-*+" else m.group(2)
            out.append(["li", marker, m.group(3), len(m.group(1)) // 2])
        elif bare and out and out[-1][0] == "li" and line.startswith(" "):
            out[-1][2] += " " + bare
        elif bare and out and out[-1][0] == "p":
            out[-1] = ("p", out[-1][1] + "\n" + bare)
        elif bare:
            out.append(("p", bare))
        else:
            out.append(("gap",))
        i += 1
    return out


def looked_label(what):
    """A "read: …" line (a tool call): one line, cut to the window with the whole call in its tooltip. A
    long query didn't wrap, so it widened the window past its size, to the whole screen."""
    w = label(f"read: {what}", "ag-looked", ellipsize=True)
    w.set_tooltip_text(what)
    return w


def text_label(markup, *classes):
    w = label("", *classes, wrap=True)
    w.set_markup(markup)
    w.set_selectable(True)
    w.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return w


def rendered(text, bad=False):
    """An answer as widgets: headings, lists with a hanging indent, tables and code in boxes, text."""
    out = box(True, 4)
    for blk in blocks(text):
        kind = blk[0]
        if kind == "gap":
            if out.get_last_child() and not getattr(out.get_last_child(), "is_gap", False):
                gap = Gtk.Box(height_request=6)
                gap.is_gap = True
                out.append(gap)
        elif kind == "code":
            code = text_label(GLib.markup_escape_text(blk[1]), "ag-code")
            code.set_margin_top(4)
            code.set_margin_bottom(4)
            out.append(code)
        elif kind == "table":
            grid = Gtk.Grid(column_spacing=18, row_spacing=3, css_classes=["ag-table"])
            for r, row in enumerate(blk[1]):
                for c, cell in enumerate(row):
                    grid.attach(text_label(inline(cell), *(("ag-th",) if r == 0 else ())), c, r, 1, 1)
            out.append(grid)
        elif kind == "h":
            h = text_label(inline(blk[1]), "ag-h")
            if out.get_first_child():
                h.set_margin_top(8)
            out.append(h)
        elif kind == "li":
            _, marker, body, depth = blk
            mark = label(marker, "ag-mark", xalign=1.0)
            mark.set_size_request(22, -1)
            mark.set_valign(Gtk.Align.START)
            item = text_label(inline(body))
            item.set_hexpand(True)
            row = box(False, 8, mark, item)
            row.set_margin_start(depth * 22)
            out.append(row)
        else:
            source = blk[1].strip("*_ ").lower().startswith(("source", "from:"))
            out.append(text_label(inline(blk[1]), *(("red",) if bad else ("ag-source",) if source else ())))
    return out


class SysAgent(View):
    icon = "\U000f0674"   # nf-md-creation (sparkles)
    interval = 0
    css = CSS
    hints = [("Enter", "ask"), ("Ctrl+N", "new chat"), ("Esc", "close")]
    title, subtitle = "System agent", "read-only"
    intro = INTRO
    # where the chats are kept and what answers: another agent's window (dbaskgui.py) swaps these
    chats_dir = lib.CHATS

    def __init__(self):
        super().__init__()
        self.busy = None                  # the id of the chat waiting for an answer
        self.looked = []                  # its tool calls so far, shown while it waits
        self.armed, self.armed_id = 0, None
        self.model = lib.model()
        self.chat = lib.new_chat()

    def build(self):
        self.chats = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.chats.add_css_class("chats")
        self.chats.connect("row-activated", lambda _l, row: self.open_chat(row.chat_id))
        side = box(True, 8, button("+ New chat", self.new_chat, tooltip="Ctrl+N"), scrolled(self.chats),
                   classes=("side",))
        side.set_size_request(260, -1)
        side.set_hexpand(False)
        for edge in ("start", "end", "top", "bottom"):
            getattr(side, f"set_margin_{edge}")(14)

        self.log = box(True, 14, classes=("ag-log",))
        self.scroller = scrolled(self.log)
        self.entry = Gtk.Entry(placeholder_text="e.g. why did a service fail at boot?", hexpand=True)
        self.entry.connect("activate", lambda *_: self.send())
        self.models = box(False, 0)
        self.paint_models([self.model])
        self.go = button("Ask", self.send, "primary", tooltip="Enter")
        main = box(True, 10, self.scroller, box(False, 8, self.entry, self.models, self.go))
        main.set_hexpand(True)
        for edge in ("start", "end", "top", "bottom"):
            getattr(main, f"set_margin_{edge}")(18)
        threading.Thread(target=lambda: GLib.idle_add(self.paint_models, lib.models()), daemon=True).start()
        self.paint_chats()
        self.paint_log()
        GLib.idle_add(lambda: self.entry.grab_focus() and False)
        return box(False, 0, side, main)

    def paint_models(self, models):
        if self.model not in models:
            models = [self.model] + models
        dd = dropdown([(m, m.split("/")[-1].removesuffix("-free")) for m in models], self.model, self.pick_model)
        dd.add_css_class("model")
        dd.set_tooltip_text("the model that answers (free, through opencode); kept for next time")
        while self.models.get_first_child():
            self.models.remove(self.models.get_first_child())
        self.models.append(dd)
        return False

    def pick_model(self, model):
        self.model = model
        lib.set_model(model)

    # ---- the chats ---------------------------------------------------------------------------------
    def paint_chats(self):
        self.disarm()
        self.chats.remove_all()
        for c in lib.chats(self.chats_dir):
            title = label(c["title"] or "(untitled)", ellipsize=True)
            title.set_hexpand(True)
            busy = c["id"] == self.busy
            info = box(True, 2, title, label("answering…" if busy else when(c["updated"]), "chat-when"))
            info.set_hexpand(True)
            # its own clicked handler: gtkkit's button() calls on_click() without the button, which this needs
            delete = Gtk.Button(label=TRASH, css_classes=["flat", "chat-del"],
                                tooltip_text="delete this chat (stops its answer)" if busy else "delete this chat")
            delete.connect("clicked", lambda b, cid=c["id"]: self.delete_chat(b, cid))
            delete.set_valign(Gtk.Align.CENTER)
            row = Gtk.ListBoxRow(child=box(False, 6, info, delete))
            row.chat_id = c["id"]
            self.chats.append(row)
            if c["id"] == self.chat["id"]:
                self.chats.select_row(row)

    def new_chat(self):
        self.chat = lib.new_chat()
        self.chats.unselect_all()
        self.paint_log()
        self.entry.grab_focus()

    def open_chat(self, cid):
        chat = next((c for c in lib.chats(self.chats_dir) if c["id"] == cid), None)
        if chat:
            self.chat = chat
            self.paint_log()

    def delete_chat(self, btn, cid):
        if self.armed_id != cid:   # first click arms it, a second one within 3 s deletes
            self.disarm()
            btn.set_label("delete?")
            btn.add_css_class("armed")
            self.armed_btn, self.armed_id = btn, cid
            self.armed = GLib.timeout_add(3000, self.disarm)
            return
        self.disarm()
        if cid == self.busy:   # still answering: stop it (the model run is killed); a late answer is dropped
            self.stop.set()
            self.busy, self.looked = None, []
            self.go.set_sensitive(True)
        lib.delete_chat(cid, self.chats_dir)
        if self.chat["id"] == cid:
            self.chat = lib.new_chat()
            self.paint_log()
        self.paint_chats()

    def disarm(self):
        if self.armed:
            GLib.source_remove(self.armed)
            self.armed = 0
        if self.armed_id:
            self.armed_btn.set_label(TRASH)
            self.armed_btn.remove_css_class("armed")
            self.armed_id = None
        return False

    # ---- the conversation -------------------------------------------------------------------------
    def paint_log(self):
        while self.log.get_first_child():
            self.log.remove(self.log.get_first_child())
        if not self.chat["messages"]:
            self.log.append(label(self.intro, "dim", wrap=True))
        for m in self.chat["messages"]:
            self.paint_message(m)
        if self.busy == self.chat["id"]:
            self.waiting = box(True, 4, label("thinking…", "ag-looked"),
                               *[looked_label(w) for w in self.looked], classes=("ag-msg",))
            self.log.append(self.waiting)
        GLib.idle_add(self.to_bottom)

    def paint_message(self, m):
        if m["who"] == "you":
            body = label(m["text"], wrap=True)
            body.set_selectable(True)
            self.log.append(box(True, 4, label("you", "ag-you"), body, classes=("ag-msg",)))
            return
        name = label(m["model"].split("/")[-1].removesuffix("-free"), "ag-ai")
        name.set_hexpand(True)
        copy = Gtk.Button(label="Copy", css_classes=["flat", "ag-copy"], tooltip_text="copy the whole answer")
        copy.connect("clicked", lambda b, t=m["text"]: self.copy(b, t))
        self.log.append(box(True, 6, box(False, 8, name, copy), rendered(lib.clean(m["text"]), not m.get("ok", True)),
                            *[looked_label(w) for w in m.get("looked", [])], classes=("ag-msg",)))

    def copy(self, btn, text):
        btn.get_clipboard().set(text)
        btn.set_label("Copied")
        GLib.timeout_add(1500, lambda: btn.set_label("Copy") and False)

    def to_bottom(self):
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def send(self):
        question = self.entry.get_text().strip()
        if not question or self.busy:
            return
        chat = self.chat
        self.busy, self.looked = chat["id"], []
        stop = self.stop = threading.Event()   # set: the answer is no longer wanted (its chat deleted)
        self.entry.set_text("")
        self.go.set_sensitive(False)
        chat["title"] = chat["title"] or question[:80]
        chat["messages"].append({"who": "you", "text": question})
        chat["updated"] = time.time()
        lib.save_chat(chat, self.chats_dir)
        self.paint_chats()
        self.paint_log()

        def on_call(name, args):   # each tool call, as it's made: you see what it read
            what = call_text(name, args)

            def show():
                if self.busy != chat["id"]:
                    return False
                self.looked.append(what)
                if self.chat["id"] == chat["id"]:
                    self.waiting.append(looked_label(what))
                    self.to_bottom()
                return False
            GLib.idle_add(show)

        model = self.model

        def go():
            ok, text = self.answer(question, chat, model, on_call, stop)
            GLib.idle_add(lambda: self.answered(chat, model, ok, text) and False)
        threading.Thread(target=go, daemon=True).start()

    def answer(self, question, chat, model, on_call, stop):
        """(ok, text): the model's answer (chat["history"] updated). Runs in a thread."""
        return lib.ask(question, chat["history"], model, on_call, stop)

    def answered(self, chat, model, ok, text):
        if self.busy != chat["id"]:   # its chat was deleted (and the answer stopped) meanwhile: dropped
            return
        chat["messages"].append({"who": "ai", "model": model, "ok": ok, "text": text if ok else f"no answer: {text}",
                                 "looked": self.looked})
        chat["updated"] = time.time()
        lib.save_chat(chat, self.chats_dir)
        self.busy, self.looked = None, []
        self.go.set_sensitive(True)
        self.paint_chats()
        if self.chat["id"] == chat["id"]:
            self.paint_log()
            self.entry.grab_focus()

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.new_chat()
            return True
        return False


if __name__ == "__main__":
    run(SysAgent(), "panels.sysagent", (1100, 680))
