#!/usr/bin/env python3
# askgui.py — Ask, a chat about your projects: a popup like every other panel. The Projects panel opens it
# (?, or Ask):  askgui.py [the selected project, under ~/code]
# Read-only (asklib.py says how): a free model in a sandbox answers from data this panel reads for it;
# what it suggests doing (Sync, Check out, Push, Commit…) is a button, and only a click runs it, through
# the same code as the panel's own buttons. The chats are kept (asklib.SESSIONS): the side list has them,
# newest first; a chat's bin deletes it (a second click within 3 s).
import os, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, dropdown, label, run, scrolled
import asklib
import projectsgui as pg

pj = pg.pj
HERE = os.path.dirname(os.path.abspath(__file__))
TRASH = "\uf014"
CSS = """
.ask-log { padding: 4px 2px; }
.ask-you { color: #6b8fe0; font-weight: 700; }
.ask-ai { color: #a9b1d6; font-weight: 700; }
.ask-msg { padding-bottom: 14px; border-bottom: 1px solid alpha(#3b4261, .5); }
.ask-looked { color: #565f89; font-size: 9pt; }
.chats > row { padding: 6px 4px 6px 10px; }
.chat-when { color: #565f89; font-size: 9pt; }
button.chat-del { padding: 2px 8px; min-height: 0; color: #565f89; }
button.chat-del:hover { color: #f7768e; }
button.chat-del.armed { background: #f7768e; color: #16161e; font-weight: 700; }   /* over .flat's none */
dropdown.model > button { min-width: 0; padding: 3px 10px; }
"""
INTRO = ("Ask about your projects: what changed, when a branch was pushed and by whom, what's not pushed, "
         "what's behind GitHub… Ask it to sync, push or commit and it gives you a button: nothing happens "
         "unless you click it.\n\n"
         "Read-only: a free model (through opencode) in a sandbox that can't see or change your files answers "
         "from what this panel reads for it: the project list, branches, commit messages and authors, the "
         "names of changed files; never a file's contents. Free models may keep what they're sent.")


def when(t):
    secs = time.time() - t
    if secs < 60:
        return "just now"
    for n, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if secs >= n:
            return f"{int(secs // n)} {unit} ago"


class Ask(View):
    icon = "\uea62"   # as the Projects panel
    interval = 0
    css = CSS
    hints = [("Enter", "ask"), ("Ctrl+N", "new chat"), ("Esc", "close")]
    title, subtitle = "Ask about your projects", "read-only"

    def __init__(self, selected=""):
        super().__init__()
        self.selected = selected.strip("/")
        self.overview, self.busy = None, None   # busy: the id of the chat waiting for an answer
        self.looked = []                         # its queries so far, shown while it waits
        self.armed, self.armed_id = 0, None
        self.model = pj.ai_model()
        self.chat = asklib.new_session(self.selected)

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

        self.log = box(True, 14, classes=("ask-log",))
        self.scroller = scrolled(self.log)
        self.entry = Gtk.Entry(placeholder_text="reading your projects…", hexpand=True)
        self.entry.set_sensitive(False)
        self.entry.connect("activate", lambda *_: self.send())
        self.models = box(False, 0)
        self.paint_models([self.model])
        self.go = button("Ask", self.send, "primary", tooltip="Enter")
        self.go.set_sensitive(False)
        main = box(True, 10, self.scroller, box(False, 8, self.entry, self.models, self.go))
        main.set_hexpand(True)
        for edge in ("start", "end", "top", "bottom"):
            getattr(main, f"set_margin_{edge}")(18)
        threading.Thread(target=lambda: GLib.idle_add(self.paint_models, pj.ai_models()), daemon=True).start()
        threading.Thread(target=self.read_projects, daemon=True).start()
        self.paint_chats()
        self.paint_log()
        return box(False, 0, side, main)

    def read_projects(self):
        text = asklib.overview()

        def ready():
            self.overview = text
            self.entry.set_placeholder_text("e.g. when was dev last pushed here, and by whom?" if self.selected
                                            else "e.g. which projects have work that isn't pushed?")
            self.entry.set_sensitive(True)
            self.go.set_sensitive(not self.busy)
            self.entry.grab_focus()
            return False
        GLib.idle_add(ready)

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
        threading.Thread(target=pj.set_ai_model, args=(model,), daemon=True).start()

    # ---- the chats ---------------------------------------------------------------------------------
    def paint_chats(self):
        self.disarm()
        self.chats.remove_all()
        for c in asklib.sessions():
            title = label(c["title"] or "(untitled)", ellipsize=True)
            title.set_hexpand(True)
            busy = c["id"] == self.busy
            info = box(True, 2, title, label("answering…" if busy else when(c["updated"]), "chat-when"))
            info.set_hexpand(True)
            delete = button(TRASH, lambda b, sid=c["id"]: self.delete_chat(b, sid), "flat", "chat-del",
                            tooltip="delete this chat")
            delete.set_valign(Gtk.Align.CENTER)
            delete.set_sensitive(not busy)
            row = Gtk.ListBoxRow(child=box(False, 6, info, delete))
            row.chat_id = c["id"]
            self.chats.append(row)
            if c["id"] == self.chat["id"]:
                self.chats.select_row(row)

    def new_chat(self):
        self.chat = asklib.new_session(self.selected)
        self.chats.unselect_all()
        self.paint_log()
        self.entry.grab_focus()

    def open_chat(self, sid):
        chat = next((c for c in asklib.sessions() if c["id"] == sid), None)
        if chat:
            self.chat = chat
            self.paint_log()

    def delete_chat(self, btn, sid):
        if self.armed_id != sid:   # first click arms it, a second one within 3 s deletes
            self.disarm()
            btn.set_label("delete?")
            btn.add_css_class("armed")
            self.armed_btn, self.armed_id = btn, sid
            self.armed = GLib.timeout_add(3000, self.disarm)
            return
        self.disarm()
        asklib.delete_session(sid)
        if self.chat["id"] == sid:
            self.chat = asklib.new_session(self.selected)
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
            self.log.append(label(INTRO, "dim", wrap=True))
        for m in self.chat["messages"]:
            self.paint_message(m)
        if self.busy == self.chat["id"]:
            self.waiting = box(True, 4, label("thinking…", "ask-looked"),
                               *[label(f"looked at: {w}", "ask-looked") for w in self.looked], classes=("ask-msg",))
            self.log.append(self.waiting)
        GLib.idle_add(self.to_bottom)

    def paint_message(self, m):
        body = label(m["text"], *(() if m.get("ok", True) else ("red",)), wrap=True)
        body.set_selectable(True)   # for copying a hash or a date
        if m["who"] == "you":
            self.log.append(box(True, 4, label("you", "ask-you"), body, classes=("ask-msg",)))
            return
        msg = box(True, 4, label(m["model"].split("/")[-1].removesuffix("-free"), "ask-ai"), body,
                  *[label(f"looked at: {w}", "ask-looked") for w in m.get("looked", [])], classes=("ask-msg",))
        # a kept chat's buttons are checked again: the projects may have changed since
        done = m.setdefault("done", {})
        acts = [a for a in asklib.actions(m.get("actions", [])) if a["label"] not in done]
        acts += [a for a in m.get("actions", []) if a["label"] in done]
        if acts:
            row = box(False, 8)
            row.set_margin_top(6)
            for a in acts:
                b = button(a["label"], lambda b, a=a, m=m: self.act(b, a, m), tooltip="runs only when you click it")
                if a["label"] in done:
                    ok, _ = done[a["label"]]
                    b.set_label(a["label"] + (" ✓" if ok else " ✗"))
                    b.set_sensitive(False)
                row.append(b)
            msg.append(row)
            for a in acts:
                if a["label"] in done:
                    ok, text = done[a["label"]]
                    msg.append(label(text, "green" if ok else "red", wrap=True))
        self.log.append(msg)

    def to_bottom(self):
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def send(self):
        question = self.entry.get_text().strip()
        if not question or self.busy or self.overview is None:
            return
        chat = self.chat
        self.busy, self.looked = chat["id"], []
        self.entry.set_text("")
        self.go.set_sensitive(False)
        chat["title"] = chat["title"] or question[:80]
        chat["messages"].append({"who": "you", "text": question})
        chat["updated"] = time.time()
        asklib.save_session(chat)
        self.paint_chats()
        self.paint_log()

        def looked(what):   # each query, as it's run: you see what it read
            def show():
                self.looked.append(what)
                if self.chat["id"] == chat["id"]:
                    self.waiting.append(label(f"looked at: {what}", "ask-looked"))
                    self.to_bottom()
                return False
            GLib.idle_add(show)

        model = self.model

        def go():
            ok, text, actions = asklib.ask(question, chat["history"], self.overview, chat["selected"], model, looked)
            GLib.idle_add(lambda: self.answered(chat, model, ok, text, actions) and False)
        threading.Thread(target=go, daemon=True).start()

    def answered(self, chat, model, ok, text, actions):
        chat["messages"].append({"who": "ai", "model": model, "ok": ok, "text": text if ok else f"no answer: {text}",
                                 "looked": self.looked, "actions": actions, "done": {}})
        chat["updated"] = time.time()
        if chat["id"] in {c["id"] for c in asklib.sessions()}:   # unless it was deleted meanwhile
            asklib.save_session(chat)
        self.busy, self.looked = None, []
        self.go.set_sensitive(self.overview is not None)
        self.paint_chats()
        if self.chat["id"] == chat["id"]:
            self.paint_log()
            self.entry.grab_focus()

    # ---- actions: only from a click, by the panel's own code ---------------------------------------
    def act(self, btn, a, m):
        btn.set_sensitive(False)
        chat = self.chat
        if a["do"] == "commit":   # its own popup: the message and the files are yours to check
            subprocess.Popen(["uwsm", "app", "--", "python3", os.path.join(HERE, "commitgui.py"), a["project"]],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.done(chat, a, m, True, "the Commit popup is open")
            return
        btn.set_label(a["label"] + " …")

        def go():
            ok, msg = self.run_action(a)
            GLib.idle_add(lambda: self.done(chat, a, m, ok, msg) and False)
        threading.Thread(target=go, daemon=True).start()

    def run_action(self, a):
        """(ok, what happened). The same pj calls as the Projects panel's buttons."""
        if a["do"] == "sync_all":
            repos = [e["path"] for e in pj.load()["projects"] if pj.is_repo(f"{pj.ROOT}/{e['path']}")]
            with ThreadPoolExecutor(8) as pool:
                reps = list(pool.map(pj.sync, repos))
            return True, pg.sync_summary(reps)
        path = f"{pj.ROOT}/{a['project']}"
        if a["do"] == "sync":
            rep = pj.sync(a["project"])
            return not rep["error"], pg.sync_summary([rep])
        if a["do"] == "push":
            return pj.push(path)
        if a["do"] == "checkout":
            if pj.state(a["project"])[0]:
                return False, "it has uncommitted changes: commit or stash them before switching"
            local = {b["name"] for b in pj.branch_states(path) if b["local"]}
            name = a["branch"] if a["branch"] in local else a["branch"].split("/", 1)[-1]
            ok, out = pj.git(path, "switch", "-q", name)
            return ok, f"on {name}" if ok else pj.last_line(out, "switch failed")
        return False, "unknown action"

    def done(self, chat, a, m, ok, msg):
        m.setdefault("done", {})[a["label"]] = [ok, msg]
        # the model knows what you did, for the next question
        chat["history"].append(("data", f"The user clicked \"{a['label']}\": {'done' if ok else 'failed'}: {msg}"))
        chat["updated"] = time.time()
        asklib.save_session(chat)
        if self.chat["id"] == chat["id"]:
            self.paint_log()
        return False

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.new_chat()
            return True
        return False


if __name__ == "__main__":
    run(Ask(sys.argv[1] if len(sys.argv) > 1 else ""), "panels.ask", (1100, 680))
