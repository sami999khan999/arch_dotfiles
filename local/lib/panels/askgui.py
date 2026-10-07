#!/usr/bin/env python3
# askgui.py — Ask, a chat about your projects: a popup like every other panel. The Projects panel opens it
# (?, or Ask):  askgui.py [the selected project, under ~/code]
# Read-only (asklib.py says how): a free model in a sandbox answers from data this panel reads for it;
# what it suggests doing (Sync, Check out, Push, Commit…) is a button, and only a click runs it, through
# the same code as the panel's own buttons.
import os, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, dropdown, label, run, scrolled
import asklib
import projectsgui as pg

pj = pg.pj
HERE = os.path.dirname(os.path.abspath(__file__))
CSS = """
.ask-log { padding: 4px 2px; }
.ask-you { color: #6b8fe0; font-weight: 700; }
.ask-ai { color: #a9b1d6; font-weight: 700; }
.ask-msg { padding-bottom: 14px; border-bottom: 1px solid alpha(#3b4261, .5); }
.ask-looked { color: #565f89; font-size: 9pt; }
dropdown.model > button { min-width: 0; padding: 3px 10px; }
"""
INTRO = ("Ask about your projects: what changed, when a branch was pushed and by whom, what's not pushed, "
         "what's behind GitHub… Ask it to sync, push or commit and it gives you a button: nothing happens "
         "unless you click it.\n\n"
         "Read-only: a free model (through opencode) in a sandbox that can't see or change your files answers "
         "from what this panel reads for it: the project list, branches, commit messages and authors, the "
         "names of changed files; never a file's contents. Free models may keep what they're sent.")


class Ask(View):
    icon = ""   # as the Projects panel
    interval = 0
    css = CSS
    hints = [("Enter", "ask"), ("Esc", "close")]
    title, subtitle = "Ask about your projects", "read-only"

    def __init__(self, selected=""):
        super().__init__()
        self.selected = selected.strip("/")
        self.history, self.overview, self.busy = [], None, False
        self.model = pj.ai_model()

    def build(self):
        self.log = box(True, 14, classes=("ask-log",))
        self.log.append(label(INTRO, "dim", wrap=True))
        self.scroller = scrolled(self.log)
        self.entry = Gtk.Entry(placeholder_text="reading your projects…", hexpand=True)
        self.entry.set_sensitive(False)
        self.entry.connect("activate", lambda *_: self.send())
        self.models = box(False, 0)
        self.paint_models([self.model])
        self.go = button("Ask", self.send, "primary", tooltip="Enter")
        self.go.set_sensitive(False)
        page = box(True, 10, self.scroller, box(False, 8, self.entry, self.models, self.go))
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        threading.Thread(target=lambda: GLib.idle_add(self.paint_models, pj.ai_models()), daemon=True).start()
        threading.Thread(target=self.read_projects, daemon=True).start()
        return page

    def read_projects(self):
        text = asklib.overview()

        def ready():
            self.overview = text
            self.entry.set_placeholder_text("e.g. when was dev last pushed here, and by whom?" if self.selected
                                            else "e.g. which projects have work that isn't pushed?")
            self.entry.set_sensitive(True)
            self.go.set_sensitive(True)
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

    # ---- the conversation -------------------------------------------------------------------------
    def add(self, who, cls, text):
        """A message: who, then its text (selectable, for copying a hash or a date). Returns its box."""
        body = label(text, wrap=True)
        body.set_selectable(True)
        msg = box(True, 4, label(who, cls), body, classes=("ask-msg",))
        self.log.append(msg)
        GLib.idle_add(self.to_bottom)
        return msg

    def to_bottom(self):
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def send(self):
        question = self.entry.get_text().strip()
        if not question or self.busy or self.overview is None:
            return
        self.busy = True
        self.entry.set_text("")
        self.go.set_sensitive(False)
        self.add("you", "ask-you", question)
        reply = box(True, 4, label("thinking…", "ask-looked"), classes=("ask-msg",))
        self.log.append(reply)
        GLib.idle_add(self.to_bottom)

        def looked(what):   # each query, as it's run: you see what it read
            GLib.idle_add(lambda: (reply.append(label(f"looked at: {what}", "ask-looked")), self.to_bottom()) and False)

        def go():
            ok, text, actions = asklib.ask(question, self.history, self.overview, self.selected, self.model, looked)
            GLib.idle_add(lambda: self.answered(reply, ok, text, actions) and False)
        threading.Thread(target=go, daemon=True).start()

    def answered(self, reply, ok, text, actions):
        reply.remove(reply.get_first_child())   # "thinking…"
        reply.prepend(label(self.model.split("/")[-1].removesuffix("-free"), "ask-ai"))
        body = label(text if ok else f"no answer: {text}", *(() if ok else ("red",)), wrap=True)
        body.set_selectable(True)
        reply.insert_child_after(body, reply.get_first_child())
        if actions:
            row = box(False, 8)
            row.set_margin_top(6)
            for a in actions:
                row.append(button(a["label"], lambda b, a=a: self.act(b, a), tooltip="runs only when you click it"))
            reply.append(row)
        self.busy = False
        self.go.set_sensitive(True)
        self.entry.grab_focus()
        GLib.idle_add(self.to_bottom)

    # ---- actions: only from a click, by the panel's own code ---------------------------------------
    def act(self, btn, a):
        btn.set_sensitive(False)
        if a["do"] == "commit":   # its own popup: the message and the files are yours to check
            subprocess.Popen(["uwsm", "app", "--", "python3", os.path.join(HERE, "commitgui.py"), a["project"]],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.done(btn, a, True, "the Commit popup is open")
            return
        btn.set_label(a["label"] + " …")

        def go():
            ok, msg = self.run_action(a)
            GLib.idle_add(lambda: self.done(btn, a, ok, msg) and False)
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

    def done(self, btn, a, ok, msg):
        btn.set_label(a["label"] + (" ✓" if ok else " ✗"))
        line = label(msg, "green" if ok else "red", wrap=True)
        btn.get_parent().get_parent().append(line)
        # the model knows what you did, for the next question
        self.history.append(("data", f"The user clicked \"{a['label']}\": {'done' if ok else 'failed'}: {msg}"))
        GLib.idle_add(self.to_bottom)
        return False

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False


if __name__ == "__main__":
    run(Ask(sys.argv[1] if len(sys.argv) > 1 else ""), "panels.ask", (900, 640))
