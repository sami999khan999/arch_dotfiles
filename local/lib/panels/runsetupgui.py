#!/usr/bin/env python3
# runsetupgui.py — Run setup, a popup like every other panel: what the Projects panel's Start (r, projects up)
# starts for a project. The Projects panel opens it (Run setup…):  runsetupgui.py <project, under ~/code>
#
#   Docker Compose   the compose files `docker compose up -d` gets (a row each; ✕ drops one, + adds one), and the
#                    .env passed to them (the project's, when the compose file's folder has none)
#   Dev commands     one row each: its name (the agentmux terminal is "▶ name"), the folder it runs in (empty:
#                    the project's), the command
#   .env             a box per .env the project wants and lacks here (an .env.example, a .sample… beside it,
#                    or the other PC's copy): Create from the example / Pull. It prints "env" on stdout then:
#                    the Projects panel reads that and loads its .env tab again
#   VS Code          open it on the project too, or not
#   Save             kept in the projects list (both PCs); Reset goes back to what's found in the folder
#                    (`projects run-setup` shows the same). A file that isn't there is marked red.
import importlib.machinery, importlib.util, os, shutil, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, rule_heading, run, scrolled

_loader = importlib.machinery.SourceFileLoader("projects", os.path.expanduser("~/.local/bin/projects"))
pj = importlib.util.module_from_spec(importlib.util.spec_from_loader("projects", _loader))
_loader.exec_module(pj)

CSS = """
.setup-page { padding: 14px 22px 14px 22px; }
.setup-row entry { padding: 6px 10px; }
.setup-row entry.missing { border-color: #f7768e; }
.setup-x { min-width: 0; min-height: 0; padding: 2px 8px; background: transparent; color: #565f89; }
.setup-x:hover { color: #f7768e; background: transparent; }
.setup-add { padding: 1px 8px; min-height: 0; }
.setup-none { padding: 2px 0; }
.setup-need { padding: 8px 8px 8px 12px; margin-top: 18px; border: 1px solid alpha(#e0af68, .7); }
.setup-need .setup-act { padding: 1px 10px; min-height: 0; }
.setup-need .env-path { font-weight: 700; }
.setup-foot { margin: 0 22px; padding: 12px 0 14px 0; border-top: 1px solid alpha(#3b4261, .7); }
"""


class RunSetup(View):
    title = "Run setup"
    icon = "\uf04b"   # nf-fa-play
    interval = 0
    css = CSS
    hints = [("Ctrl+S", "save"), ("Esc", "close")]

    def __init__(self, rel):
        super().__init__()
        self.rel = rel.strip("/")
        self.path = f"{pj.ROOT}/{self.rel}"

    def build(self):
        # the form scrolls; VS Code and the buttons stay at the bottom
        self.page = box(True, 6, classes=("setup-page",))
        self.foot = box(False, 8, classes=("setup-foot",))
        self.fill(pj.run_setup(self.rel))
        return box(True, 0, scrolled(self.page), self.foot)

    def fill(self, st):
        """The form for a setup (pj.run_setup's: saved, or found in the folder)."""
        clear(self.page)
        self.saved = st["saved"]
        self.subtitle = f"{self.rel} · {'saved for both PCs' if st['saved'] else 'found in the folder, not saved'}"
        if hasattr(self.host, "subtitle_label"):   # the window is up already (after Reset)
            self.set_subtitle(self.subtitle)

        self.page.append(self.heading("Docker Compose", "+ compose file", lambda: self.add_compose("", focus=True),
                                      first=True))
        self.no_compose = label("none", "dim", "setup-none")
        self.compose_rows = box(True, 6)
        self.env = self.entry(st.get("env_file", ""), "the .env beside each compose file")
        self.env.set_tooltip_text("the .env passed to docker compose (--env-file)")
        self.env.connect("changed", lambda e: self.mark(e, e.get_text().strip()))
        self.mark(self.env, self.env.get_text().strip())
        # a section of its own, there only with a compose file to pass it to
        self.env_row = box(True, 6, rule_heading("Env file", "passed to docker compose", spaced=True),
                           box(False, 8, self.env, self.spacer(), classes=("setup-row",)))
        for w in (self.no_compose, self.compose_rows, self.env_row):
            self.page.append(w)
        for f in st["compose"]:
            self.add_compose(f)
        self.compose_changed()

        self.page.append(self.heading("Dev commands", "+ command", lambda: self.add_command("", "", "", focus=True)))
        self.no_cmds = label("none", "dim", "setup-none")
        self.cmd_rows = box(True, 6)
        self.page.append(self.no_cmds)
        self.page.append(self.cmd_rows)
        for c in st["commands"]:
            self.add_command(c["name"], c.get("dir", ""), c["cmd"])
        self.cmds_changed()

        self.env_box = box(True, 6)
        self.page.append(self.env_box)
        self.fill_env()

        clear(self.foot)
        foot = self.foot
        self.code = Gtk.CheckButton(label="Open VS Code on it", active=st.get("code", True))
        foot.append(self.code)
        foot.append(label(""))
        foot.get_last_child().set_hexpand(True)
        if st["saved"]:
            foot.append(button("Reset to what's found", self.reset, "flat",
                               tooltip="forget the saved setup: Start finds it in the folder again"))
        foot.append(button("Cancel", self.close, "flat", tooltip="Esc"))
        foot.append(button("Save", self.save, "primary", tooltip="Ctrl+S"))

    def heading(self, title, add, fn, first=False):
        """A section's heading, its + at the end of the rule."""
        head = rule_heading(title, spaced=not first)
        b = button(add, fn, "flat", "setup-add")
        head.append(b)
        return head

    # ---- environment -------------------------------------------------------------------------------
    def fill_env(self):
        """One box with a line per .env the project wants and lacks here (an example beside it, or the other
        PC's copy): Create it from the example, or Pull it. The ones that are there are the .env tab's."""
        clear(self.env_box)
        needs = pj.env_needs(self.rel)
        if not needs:
            return
        need = box(True, 6, label(f"Missing here: {'this .env' if len(needs) == 1 else f'{len(needs)} .env files'}",
                                  "amber"), classes=("setup-need",))
        paths = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)   # the paths one column
        for n in needs:
            acts = []
            if n["there"]:
                acts.append(button("Pull", lambda n=n: self.pull(n["file"]), "primary",
                                   tooltip="decrypt the other PC's copy into the project"))
            if n["example"]:
                keys = len(pj.env_keys(open(os.path.join(self.path, n["example"]), errors="replace").read()))
                why = f"{os.path.basename(n['example'])} has {keys} key{'s' * (keys != 1)}"
                acts.append(button("Create", lambda n=n: self.create(n["file"], n["example"]),
                                   "flat" if n["there"] else "primary",
                                   tooltip=f"copy {n['example']}, then fill in its values (the .env tab)"))
            else:
                why = "the other PC has one"
            for b in acts:
                b.add_css_class("setup-act")
            path = label(n["file"], "env-path")
            paths.add_widget(path)
            need.append(box(False, 12, path, label(why, "dim", ellipsize=True), *acts))
        self.env_box.append(need)

    def create(self, f, example):
        dest = os.path.join(self.path, f)
        if os.path.exists(dest):
            return self.say(f"{f} is already there", "bad")
        shutil.copyfile(os.path.join(self.path, example), dest)
        os.chmod(dest, 0o600)
        self.say(f"made {f} from {example}: fill in its values in the .env tab")
        self.env_written()

    def pull(self, f):
        self.say("pulling…", seconds=60)

        def work():
            ok, msg = pj.env_pull([self.rel], keys=[f"{self.rel}/{f}"])
            GLib.idle_add(lambda: (self.say(msg, "ok" if ok else "bad"), self.env_written()) and False)
        threading.Thread(target=work, daemon=True).start()

    def env_written(self):
        """A .env made: its box gone, the env file no longer red, and a line to the Projects panel (it reads
        this popup's output) so its .env tab shows the file at once."""
        self.fill_env()
        self.mark(self.env, self.env.get_text().strip())
        try:
            print("env", flush=True)
        except (OSError, ValueError):   # the Projects panel closed first: no one to tell
            pass

    # ---- rows --------------------------------------------------------------------------------------
    def entry(self, text, placeholder, width=-1):
        e = Gtk.Entry(text=text, placeholder_text=placeholder)
        if width > 0:
            e.set_size_request(width, -1)
        else:
            e.set_hexpand(True)
        return e

    def remover(self, row, parent, then=None):
        x = Gtk.Button(label="✕", css_classes=["setup-x"], focusable=False, tooltip_text="drop it")
        x.connect("clicked", lambda *_: (parent.remove(row), then and then()))
        return x

    def cmds_changed(self):
        some = self.cmd_rows.get_first_child() is not None
        self.no_cmds.set_visible(not some)

    @staticmethod
    def spacer():
        """An unseen ✕: a row without one ends where the rows with one do."""
        x = Gtk.Button(label="✕", css_classes=["setup-x"], focusable=False, can_target=False)
        x.set_opacity(0)
        return x

    def compose_changed(self):
        """No compose file: say so, and no env file to pass to one."""
        some = self.compose_rows.get_first_child() is not None
        self.no_compose.set_visible(not some)
        self.env_row.set_visible(some or bool(self.env.get_text().strip()))

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
        row.append(self.remover(row, self.compose_rows, self.compose_changed))
        row.file = e
        self.compose_rows.append(row)
        self.compose_changed()
        if focus:
            e.grab_focus()

    def add_command(self, name, folder, cmd, focus=False):
        n = self.entry(name, "name", 110)
        n.set_tooltip_text("its agentmux terminal is ▶ name")
        d = self.entry(folder, "folder", 180)
        d.connect("changed", lambda e: self.mark_dir(e))
        self.mark_dir(d)
        c = self.entry(cmd, "command, e.g. pnpm run dev")
        row = box(False, 8, n, d, c, classes=("setup-row",))
        row.append(self.remover(row, self.cmd_rows, self.cmds_changed))
        row.fields = (n, d, c)
        self.cmd_rows.append(row)
        self.cmds_changed()
        if focus:
            n.grab_focus()

    def mark_dir(self, e):
        folder = e.get_text().strip().strip("/")
        missing = bool(folder) and not os.path.isdir(os.path.join(self.path, folder))
        (e.add_css_class if missing else e.remove_css_class)("missing")
        e.set_tooltip_text(f"no folder {folder} in the project" if missing else "the folder it runs in; empty: the project's")

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
