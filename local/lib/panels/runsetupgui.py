#!/usr/bin/env python3
# runsetupgui.py — Run setup, a popup like every other panel: what the Projects panel's Start (r, projects up)
# starts for a project. The Projects panel opens it (Run setup…):  runsetupgui.py <project, under ~/code>
#
#   Docker Compose   the compose files `docker compose up -d` gets (a row each; ✕ drops one, + adds one), and the
#                    .env passed to them (the project's, when the compose file's folder has none)
#   Dev commands     one row each: its name (the agentmux terminal is "▶ name"), the folder it runs in (empty:
#                    the project's), the command
#   VS Code          open it on the project too, or not
#   Save             kept in the projects list (both PCs); Reset goes back to what's found in the folder
#                    (`projects run-setup` shows the same). A file that isn't there is marked red.
import importlib.machinery, importlib.util, os, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, rule_heading, run, scrolled

_loader = importlib.machinery.SourceFileLoader("projects", os.path.expanduser("~/.local/bin/projects"))
pj = importlib.util.module_from_spec(importlib.util.spec_from_loader("projects", _loader))
_loader.exec_module(pj)

CSS = """
.setup-page { padding: 4px 22px 14px 22px; }
.setup-row entry { min-height: 0; padding: 3px 8px; }
.setup-row entry.missing { border-color: #f7768e; }
.setup-cols label { color: #565f89; }
.setup-x { min-width: 0; min-height: 0; padding: 2px 8px; background: transparent; color: #565f89; }
.setup-x:hover { color: #f7768e; background: transparent; }
.setup-add { padding: 3px 10px; }
.setup-note { margin: 2px 0 6px 0; }
"""


class RunSetup(View):
    title = "Run setup"
    icon = ""   # nf-fa-play
    interval = 0
    css = CSS
    hints = [("Ctrl+S", "save"), ("Esc", "close")]

    def __init__(self, rel):
        super().__init__()
        self.rel = rel.strip("/")
        self.path = f"{pj.ROOT}/{self.rel}"
        self.subtitle = self.rel

    def build(self):
        self.page = box(True, 8, classes=("setup-page",))
        self.fill(pj.run_setup(self.rel))
        return scrolled(self.page)

    def fill(self, st):
        """The form for a setup (pj.run_setup's: saved, or found in the folder)."""
        clear(self.page)
        self.saved = st["saved"]
        self.page.append(label("Saved for every PC: Start (r) runs this." if st["saved"] else
                               "Found in the folder (not saved yet): Start (r) runs this. Change it and Save to keep "
                               "your version for every PC.", "dim", "setup-note", wrap=True))

        self.page.append(rule_heading("Docker Compose", "docker compose up -d"))
        self.compose_rows = box(True, 6)
        for f in st["compose"]:
            self.add_compose(f)
        self.page.append(self.compose_rows)
        self.page.append(self.adder("+ Add a compose file", lambda: self.add_compose("", focus=True)))
        self.env = self.entry(st.get("env_file", ""), ".env passed to compose: empty, the one beside the compose file")
        self.env.connect("changed", lambda e: self.mark(e, e.get_text().strip()))
        self.mark(self.env, self.env.get_text().strip())
        self.page.append(box(False, 10, label(".env file", "dim"), self.env, classes=("setup-row",)))

        self.page.append(rule_heading("Dev commands", "each in an agentmux terminal named ▶ name"))
        # each column's title as wide as its entries (a size group: an entry's own minimum decides it)
        self.col_groups = [Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL) for _ in range(2)]
        cols = box(False, 8, classes=("setup-cols",))
        for i, text in enumerate(("name", "folder (empty: the project's)", "command")):
            l = label(text, ellipsize=i < 2)
            if i < 2:
                l.set_hexpand(False)
                self.col_groups[i].add_widget(l)
            else:
                l.set_hexpand(True)
            cols.append(l)
        self.page.append(cols)
        self.cmd_rows = box(True, 6)
        for c in st["commands"]:
            self.add_command(c["name"], c.get("dir", ""), c["cmd"])
        self.page.append(self.cmd_rows)
        self.page.append(self.adder("+ Add a command", lambda: self.add_command("", "", "", focus=True)))

        self.code = Gtk.CheckButton(label="Open VS Code on it", active=st.get("code", True))
        self.code.set_margin_top(10)
        self.page.append(self.code)
        bar = box(False, 8, label(""))
        bar.get_first_child().set_hexpand(True)
        if st["saved"]:
            bar.append(button("Reset to what's found", self.reset, "flat",
                              tooltip="forget the saved setup: Start finds it in the folder again"))
        bar.append(button("Cancel", self.close, "flat", tooltip="Esc"))
        bar.append(button("Save", self.save, "primary", tooltip="Ctrl+S"))
        bar.set_margin_top(12)
        self.page.append(bar)

    # ---- rows --------------------------------------------------------------------------------------
    def entry(self, text, placeholder, width=-1):
        e = Gtk.Entry(text=text, placeholder_text=placeholder)
        if width > 0:
            e.set_size_request(width, -1)
        else:
            e.set_hexpand(True)
        return e

    def adder(self, text, fn):
        b = button(text, fn, "flat", "setup-add")
        b.set_halign(Gtk.Align.START)
        return b

    def remover(self, row, parent):
        x = Gtk.Button(label="✕", css_classes=["setup-x"], focusable=False, tooltip_text="drop it")
        x.connect("clicked", lambda *_: parent.remove(row))
        return x

    def mark(self, e, rel_file):
        """A file that isn't in the project: the entry's border red."""
        missing = bool(rel_file) and not os.path.isfile(os.path.join(self.path, rel_file))
        (e.add_css_class if missing else e.remove_css_class)("missing")
        e.set_tooltip_text(f"no {rel_file} in the project" if missing else None)

    def add_compose(self, f, focus=False):
        e = self.entry(f, "a compose file, e.g. docker/docker-compose.yml")
        e.connect("changed", lambda e: self.mark(e, e.get_text().strip()))
        self.mark(e, f)
        row = box(False, 8, e, classes=("setup-row",))
        row.append(self.remover(row, self.compose_rows))
        row.file = e
        self.compose_rows.append(row)
        if focus:
            e.grab_focus()

    def add_command(self, name, folder, cmd, focus=False):
        n = self.entry(name, "dev", 120)
        d = self.entry(folder, "", 190)
        d.connect("changed", lambda e: self.mark_dir(e))
        self.mark_dir(d)
        c = self.entry(cmd, "pnpm run dev")
        self.col_groups[0].add_widget(n)
        self.col_groups[1].add_widget(d)
        row = box(False, 8, n, d, c, classes=("setup-row",))
        row.append(self.remover(row, self.cmd_rows))
        row.fields = (n, d, c)
        self.cmd_rows.append(row)
        if focus:
            n.grab_focus()

    def mark_dir(self, e):
        folder = e.get_text().strip().strip("/")
        missing = bool(folder) and not os.path.isdir(os.path.join(self.path, folder))
        (e.add_css_class if missing else e.remove_css_class)("missing")
        e.set_tooltip_text(f"no folder {folder} in the project" if missing else None)

    @staticmethod
    def rows(parent):
        out, r = [], parent.get_first_child()
        while r is not None:
            out.append(r)
            r = r.get_next_sibling()
        return out

    # ---- saving ------------------------------------------------------------------------------------
    def setup(self):
        commands = []
        for r in self.rows(self.cmd_rows):
            n, d, c = (f.get_text().strip() for f in r.fields)
            if c:
                commands.append({"name": n or "dev", "cmd": c, "dir": d.strip("/")})
        return {"compose": [f for f in (r.file.get_text().strip() for r in self.rows(self.compose_rows)) if f],
                "env_file": self.env.get_text().strip(), "commands": commands, "code": self.code.get_active()}

    def save(self):
        self.store(self.setup(), "saved: Start runs this on every PC")

    def reset(self):
        self.store(None, "reset: Start finds it in the folder again")

    def store(self, setup, done):
        self.say("saving…", seconds=30)

        def work():
            problem = pj.set_run_setup(self.rel, setup)
            GLib.idle_add(self.stored, problem, done)
        threading.Thread(target=work, daemon=True).start()

    def stored(self, problem, done):
        if problem and "isn't a git repo" not in problem:
            self.say(f"kept here, but {problem}", "bad", seconds=10)   # saved in the list; the push failed (offline?)
        else:
            self.say(done)
        GLib.timeout_add(700, lambda: self.close() or False)
        return False

    def key(self, keyval, state):
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.save()
            return True
        return False


def main():
    if len(sys.argv) < 2:
        sys.exit("runsetupgui.py <project, under ~/code>")
    run(RunSetup(sys.argv[1]), "panels.runsetup", (820, 600))


if __name__ == "__main__":
    main()
