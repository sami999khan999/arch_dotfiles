#!/usr/bin/env python3
# home.py — agentmux's middle pane when no thread is shown: a start screen in the look of Claude Code,
# in the theme's colours. A welcome box with the project, a rounded prompt box, the agent it goes to
# (Tab changes it), and the project's threads below — or, with none yet, every agent harness, a click
# on one starting it. ↵ starts a new thread in that agent with the prompt as its first message; from
# then on the middle pane is the agent's own interface.
#
#   type  the prompt     ↵  send     ⇧↵ / ^J / ⌥↵  new line     ⇥  change agent     ^U  clear
#   ↑↓    a thread / an agent (prompt empty), ↵ opens it          click one to open it
# Sleeps in select() until a key, a resize or SIGUSR1 (agentmux: the project or its threads changed).
import os, subprocess, sys, tempfile, textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
from term import PAD, App, Line, entry, fit, header, rule, width

AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
GLYPH = {"working": ("◐", "green"), "idle": ("✳", "accent"), "shell": ("$", "muted")}


def boxed(lines, x, content, border, inner_w):
    """Rows of a rounded box at column x, inner_w wide: content = [[(text, fg, bold)]]."""
    pad = " " * x
    lines.append(Line().add(pad).add("╭" + "─" * (inner_w + 2) + "╮", border))
    for row in content:
        l = Line().add(pad).add("│ ", border)
        used = 0
        for text, fg, bold in row:
            text = fit(text, inner_w - used)
            l.add(text, fg, bold=bold)
            used += width(text)
        l.pad(inner_w - used).add(" │", border)
        lines.append(l)
    lines.append(Line().add(pad).add("╰" + "─" * (inner_w + 2) + "╯", border))


class Home(App):
    def __init__(self):
        super().__init__()
        self.text = ""
        self.harnesses = lib.installed_harnesses() or [("claude", "Claude Code")]
        last = lib.load_state().get("harness")
        self.h = next((i for i, (k, _) in enumerate(self.harnesses) if k == last), 0)
        self.sel = -1          # -1: the prompt; 0…: a row of the list (a thread, or an agent)
        self.ys = {}
        self.load()

    def load(self):
        self.project = lib.load_state().get("project", "")
        self.threads = ([t for t in lib.threads() if t["project"] == self.project and t["kind"] == "agent"]
                        if self.project else [])
        self.sel = min(self.sel, len(self.rows()) - 1)

    def rows(self):
        """What the list under the welcome holds: the project's threads, or with none, the installed
        agents (clicking one starts it)."""
        if self.threads:
            return self.threads
        return self.harnesses if self.project else []

    def on_signal(self):
        self.load()

    def on_focus(self):
        pass   # render() reads self.focused: the prompt's border and cursor follow the keyboard

    def render(self, w, h):
        """Welcome at the top, the threads (or the agents) under it, the prompt pinned to the bottom
        across the whole width, as in Claude Code."""
        x = PAD
        col = max(w - 2 * PAD, 20)
        inner = col - 4
        label = self.harnesses[self.h][1]

        # ---- bottom: the prompt box and the line under it ----
        rows = []
        for para in self.text.split("\n"):
            rows.extend(textwrap.wrap(para, inner - 2, drop_whitespace=False, replace_whitespace=False) or [""])
        rows = rows[-8:]
        focused = self.sel == -1 and self.focused
        content = []
        for i, r in enumerate(rows):
            lead = ("> ", "accent" if focused else "muted", True) if i == 0 else ("  ", "text", False)
            if not self.text and i == 0:
                hint = f"Ask {label} anything…" if self.project else "Open a project first  (Ctrl+Alt+O)"
                content.append([lead, (hint, "muted", False)])
            else:
                content.append([lead, (r, "text", False)])
        bottom = []
        boxed(bottom, x, content, "accent" if focused else "line", inner)
        foot = Line().pad(x + 2).add("● ", "green" if self.project else "muted").add(label, "text", bold=True)
        if len(self.harnesses) > 1:
            foot.add("  ⇥ switch", "muted")
        keys = "Enter send · Shift+Enter new line · ↑↓ " + ("threads" if self.threads else "agents")
        if foot.w + width(keys) + 3 < w:
            foot.pad(x + col - foot.w - width(keys) - 2).add(keys, "muted")
        bottom.append(foot)
        bottom.append(Line())   # a row of air above tmux's status bar

        # ---- top: welcome ----
        lines = [Line()]
        box_w = min(inner, 60)
        if self.project:
            body = [[("✻ ", "accent", True), ("Welcome to agentmux", "text", True)], [("", "text", False)],
                    [("  " + lib.project_name(self.project), "text", True)],
                    [("  " + self.project.replace(lib.HOME, "~"), "muted", False)]]
        else:
            body = [[("✻ ", "accent", True), ("Welcome to agentmux", "text", True)], [("", "text", False)],
                    [("  No project open.  ", "sub", False), ("Ctrl+Alt+O", "amber", False), (" opens one", "sub", False)]]
        boxed(lines, x, body, "accent", box_w)
        lines.append(Line())

        # ---- middle: the project's threads, or the agents to start one in ----
        room = h - len(lines) - len(bottom) - 1
        self.ys = {}
        if room > 2:
            if self.threads:
                lines.append(Line().add(" " * x).add(" Threads ", "accent", bold=True).add("─" * max(col - 10, 0), "line"))
                for i, t in enumerate(self.threads[:room - 1]):
                    st = lib.state_of(t)
                    glyph, gfg = GLYPH[st]
                    title = "shell" if st == "shell" else (lib.clean_title(t["title"]) or t["harness"] or "shell")
                    sel = i == self.sel
                    bg = "overlay" if sel else None
                    row = Line(bg).add("  ").add(glyph, gfg).add("  ").add(fit(title, col - 22), "text" if sel else "sub")
                    row.right(f"{st}  #{t['name'].rsplit(lib.SEP, 1)[-1]}", "muted", col)
                    l = Line().add(" " * x)
                    l.parts += row.parts
                    self.ys[len(lines)] = i
                    lines.append(l)
            elif self.project:
                # every harness agentmux knows, drawn as the sidebars draw their rows; the installed
                # ones start a thread, the rest say why not
                lines += header("\U000f06a9", "Agents", "click one to start it", w)
                lines.append(Line())
                installed = [k for k, _ in self.harnesses]
                for key, (label, cmd) in lib.HARNESSES.items():
                    i = installed.index(key) if key in installed else None
                    if len(lines) + 3 > h - len(bottom):
                        break
                    if i is None:
                        rows_ = entry(w, False, "✳", "line", label, [("not installed", "line", ())], fg="line", bold=False)
                    else:
                        rows_ = entry(w, i == self.sel, "✳", "accent" if i == self.sel else "muted", label,
                                      [(cmd, "muted", ())])
                    for l in rows_:
                        if i is not None:
                            self.ys[len(lines)] = i
                        lines.append(l)
                    lines.append(Line())
        while len(lines) < h - len(bottom):
            lines.append(Line())
        top = len(lines)
        self.cursor = (top + len(rows), x + 4 + width(rows[-1])) if focused else None
        return lines + bottom

    # ---- acting ----
    def submit(self):
        if self.sel >= 0 and self.threads:
            subprocess.Popen([AGENTMUX, "show", self.threads[self.sel]["name"]])
            return
        if self.sel >= 0:   # an agent from the list: a thread in it, with the prompt if one is typed
            self.h, self.sel = self.sel, -1
        if not self.project:
            subprocess.Popen([AGENTMUX, "open"])
            return
        harness = self.harnesses[self.h][0]
        lib.update_state(harness=harness)
        args = [AGENTMUX, "new-thread", harness]
        prompt = self.text.strip()
        if prompt:   # through a file: any text, any length, no quoting trouble
            os.makedirs(lib.RUN, exist_ok=True)
            fd, path = tempfile.mkstemp(prefix="prompt-", dir=lib.RUN)
            with os.fdopen(fd, "w") as f:
                f.write(prompt)
            args += ["--prompt-file", path]
        self.text = ""
        subprocess.Popen(args)

    def key(self, k):
        if isinstance(k, tuple):
            if k[0] == "click":
                i = self.ys.get(k[2])
                if i is not None:
                    self.sel = i
                    self.submit()
                else:
                    self.sel = -1
            elif k[0] == "wheel" and self.rows():
                self.sel = max(-1, min(self.sel + k[1], len(self.rows()) - 1))
            return
        if k in ("tab", "btab"):
            self.h = (self.h + (1 if k == "tab" else -1)) % len(self.harnesses)
        elif k == "enter":
            self.submit()
        elif k in ("shift-enter", "ctrl-j", "alt-enter", "ctrl-enter"):
            self.sel = -1
            self.text += "\n"
        elif k == "up" and self.rows() and not self.text:
            self.sel = len(self.rows()) - 1 if self.sel == -1 else max(self.sel - 1, 0)
        elif k == "down" and self.sel >= 0:
            self.sel = self.sel + 1 if self.sel + 1 < len(self.rows()) else -1
        elif k == "esc":
            self.sel = -1
        elif k == "backspace":
            self.sel = -1
            self.text = self.text[:-1]
        elif k == "ctrl-u":
            self.text = ""
        elif k == "ctrl-w":
            t = self.text.rstrip()
            self.text = t.rsplit(" ", 1)[0] + " " if " " in t else ""
        elif isinstance(k, str) and len(k) == 1 and k.isprintable():
            self.sel = -1
            self.text += k


if __name__ == "__main__":
    home = Home()
    lib.app("set-option", "-g", "@pid_home", str(os.getpid()))
    home.run()
