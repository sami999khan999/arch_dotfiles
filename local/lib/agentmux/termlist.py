#!/usr/bin/env python3
# termlist.py — the list of terminals at the right of agentmux's terminals column, as in VS Code's
# terminal panel:  termlist.py <project>
# One row per terminal (a window of the project's "<project>·terms" session, shown full height left of
# this list); a split lists each of its halves, joined by ┌ │ └. The shown one has the blue edge; the
# shown and the hovered row show a split button (that terminal in two) and a × (closes that terminal
# alone). The header has + (new terminal), split, and ─ (minimize: hide the column, the shells keep
# running); at its left, › collapses the list to its icons and ‹ expands it back (remembered).
# Long lists scroll (the wheel, ↑ more / ↓ more). Drag its left border to resize it (the width is
# remembered); very narrow, it shows only the icons.
#
# Kept current like the sidebars: a control-mode client on the agents server subscribes to the terms
# sessions' windows and panes; tmux sends a line only when something changed. No polling.
#
# keys   ↑↓ / j k  move     ↵ / click  show it     x / Delete  close it     n new     s split it
#        c  collapse / expand
import os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
from term import CLOSE, PAD, App, Line, fit, width

AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
FS, RS = "\x1f", "\x1e"
FIELDS = ["session", "window", "window_active", "pane", "pane_active", "command", "panes"]
SUB = ("#{S:#{?#{m:*" + lib.SEP + "terms,#{session_name}},#{W:#{P:" + FS.join(
    ["#{session_name}", "#{window_index}", "#{window_active}", "#{pane_id}", "#{pane_active}",
     "#{pane_current_command}", "#{window_panes}"]) + RS + "}},}}")
TERM_ICON, SPLIT_ICON, MINIMIZE = "\uf489", "\ueb56", "\ueaba"
COLLAPSE, EXPAND = "\ueab6", "\ueab5"   # chevrons: › folds the list to its icons, ‹ opens it
# the header's buttons, left to right: (text, colour, what it runs). Too narrow for all of them, the
# split goes first, then minimize (Ctrl+Alt+D, Ctrl+Alt+3 still do it).
BUTTONS = [("+", "accent", ["term"]), (SPLIT_ICON, "muted", ["split-term"]), (MINIMIZE, "muted", ["toggle", "terms"])]
DROP = [1, 2]
ICONS_ONLY = 9   # narrower than this, the rows are only their icons (no name, no buttons)
MIN_WIDTH = 4    # the icons need this much (agentmux MIN["termlist"])


def run(*args):
    """An agentmux command, in its own session: hiding the column kills this pane, not the command."""
    subprocess.Popen([AGENTMUX, *args], start_new_session=True)


class TermList(App):
    focused = False
    motion = True   # the hovered row shows its buttons

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.session = f"{lib.session_base(project)}{lib.SEP}terms"
        self.panes = []          # [dict(FIELDS)] of this project's terminals, in order
        self.seen = False        # the session was there once: when it goes, the column closes
        self.sel = None          # the pane chosen with the keyboard (None: the shown one)
        self.hover = None
        self.scroll, self.free = 0, False
        self.ys, self.row_buttons, self.button_xs = {}, {}, []
        self.width = 20
        self.chevron = (PAD, "collapse")   # the header's ‹ / ›: its column, what it does
        self.ctl, self.buf = None, ""

    # ---- the control-mode client (as in sidebar.py) ----
    def connect(self):
        lib.agents("new-session", "-d", "-s", lib.WATCH)
        if self.ctl:
            self.fds.pop(self.ctl.stdout.fileno(), None)
        self.ctl = subprocess.Popen(lib.AGENTS + ["-C", "attach", "-t", f"={lib.WATCH}"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                                    env=lib.no_tmux_env())
        self.ctl.stdin.write(f"refresh-client -B 'terms::{SUB}'\n")
        self.ctl.stdin.flush()
        os.set_blocking(self.ctl.stdout.fileno(), False)
        self.watch(self.ctl.stdout.fileno(), self.read_ctl)

    def read_ctl(self):
        try:
            chunk = self.ctl.stdout.read() or ""
        except (OSError, ValueError, TypeError):
            chunk = ""
        if not chunk and self.ctl.poll() is not None:
            time.sleep(1)
            self.buf = ""
            self.connect()
            return
        self.buf += chunk
        *lines, self.buf = self.buf.split("\n")
        for line in lines:
            if line.startswith("%subscription-changed terms ") and " : " in line:
                self.parse_panes(line.split(" : ", 1)[1].replace("\\037", FS).replace("\\036", RS))

    def parse_panes(self, value):
        rows = []
        for rec in value.split(RS):
            parts = rec.split(FS)
            if len(parts) == len(FIELDS) and parts[0] == self.session:
                rows.append(dict(zip(FIELDS, parts)))
        self.panes = rows
        if rows:
            self.seen = True
        elif self.seen:   # its last shell ended: the column closes
            run("terms-closed", self.project)
            self.quit = True

    # ---- drawing ----
    def shown(self):
        return next((p["pane"] for p in self.panes if p["window_active"] == "1" and p["pane_active"] == "1"), None)

    def render(self, w, h):
        if w < MIN_WIDTH and w != self.width:
            # squeezed narrower than its icons (however that happened: a resize, the column opening
            # small): agentmux puts it back to its remembered width
            run("save-widths")
        self.width = w
        self.ys, self.row_buttons = {}, {}
        narrow = w < ICONS_ONLY
        pad = 1 if narrow else PAD
        # header: the chevron over the icons' column; expanded, the count beside it (underlined in blue,
        # like a sidebar's title) and the buttons at the right; collapsed, only the chevron
        lead = 2   # the rows' icons sit in column 2 (edge, tree, icon)
        if narrow:
            top = Line().pad(lead).add(EXPAND, "accent")
            under = Line().pad(1).add("━" * max(w - 2, 0), "overlay")
            self.chevron, self.button_xs = (lead, "expand"), []
        else:
            title = f"{COLLAPSE} {len(self.panes)}"
            top = Line().pad(lead).add(COLLAPSE, "accent").add(f" {len(self.panes)}", "sub", bold=True)
            self.chevron = (lead, "collapse")
            buttons = list(range(len(BUTTONS)))   # the split goes first when they don't fit, then minimize
            while buttons and sum(width(BUTTONS[i][0]) + 2 for i in buttons) - 2 > w - PAD - top.w - 2:
                buttons.remove(next(i for i in DROP + [0] if i in buttons))
            x, self.button_xs = w - PAD, []
            for i in reversed(buttons):   # laid out from the right edge
                x -= width(BUTTONS[i][0])
                self.button_xs.insert(0, (x, x + width(BUTTONS[i][0]), BUTTONS[i][2]))
                x -= 2
            for i, (x0, _, _) in zip(buttons, self.button_xs):
                top.pad(x0 - top.w).add(BUTTONS[i][0], BUTTONS[i][1])
            under = Line().pad(lead).add("━" * width(title), "accent").add("━" * max(w - lead - PAD - width(title), 0), "overlay")
        lines = [top, under, Line()]
        # the rows: one per pane, a split's halves joined by ┌ │ └
        shown = self.shown()
        current = self.sel if any(p["pane"] == self.sel for p in self.panes) else shown
        body = []
        for p in self.panes:
            same = [q for q in self.panes if q["window"] == p["window"]]
            k, n = same.index(p), len(same)
            body.append((p, " " if n == 1 else "┌" if k == 0 else "└" if k == n - 1 else "│", n > 1))
        room = max(h - len(lines), 1)
        at = next((n for n, (p, *_) in enumerate(body) if p["pane"] == current), 0)
        if len(body) <= room:
            self.scroll = 0
        elif not self.free:
            self.scroll = min(max(self.scroll, at - room + 1), at)
        self.scroll = max(0, min(self.scroll, max(len(body) - room, 0)))
        for n in range(self.scroll, min(len(body), self.scroll + room)):
            p, tree, split = body[n]
            sel, hov, on = p["pane"] == current, p["pane"] == self.hover, p["pane"] == shown
            bg = "overlay" if sel else ("hover" if hov else None)
            l = Line(bg).add("▎" if on else " ", "accent").add(tree, "line")
            l.add(SPLIT_ICON if split else TERM_ICON, "accent" if on else "muted")
            if not narrow:
                # the shown and the hovered row: split it, close it (as VS Code's on hover); the name
                # gives way first, then the split button
                acts = [(SPLIT_ICON, ["split-term", p["pane"]]), (CLOSE, ["close-term", p["pane"]])] if sel or hov else []
                need = lambda a: sum(width(t) + 1 for t, _ in a)   # " ◫ ×": a space before each
                if acts and w - l.w - 1 - PAD - need(acts) < 3:
                    acts = acts[1:]
                l.add(" ").add(fit(p["command"], max(w - l.w - PAD - need(acts), 0)), "text" if on else "sub", bold=on)
                x = w - PAD
                spots = []
                for text, cmd in reversed(acts):
                    x -= width(text)
                    spots.insert(0, (x, text, cmd))
                    x -= 1
                for x0, text, cmd in spots:
                    l.pad(x0 - l.w).add(text, "muted")
                self.row_buttons[len(lines)] = [(x0, x0 + width(text), cmd) for x0, text, cmd in spots]
            self.ys[len(lines)] = p["pane"]
            lines.append(l)
        if self.scroll > 0:
            lines[3] = Line().pad(pad).add("↑" if narrow else "↑ more", "muted")
            self.ys.pop(3, None), self.row_buttons.pop(3, None)
        if self.scroll + room < len(body):
            lines[-1] = Line().pad(pad).add("↓" if narrow else "↓ more", "muted")
            self.ys.pop(len(lines) - 1, None), self.row_buttons.pop(len(lines) - 1, None)
        return lines

    # ---- acting ----
    def show(self, pane):
        """That terminal in the column (its window and its half of a split), with the keyboard."""
        lib.agents("select-window", "-t", pane)
        lib.agents("select-pane", "-t", pane)
        run("focus", "terms")
        self.sel = None

    def key(self, k):
        order = [p["pane"] for p in self.panes]
        current = self.sel if self.sel in order else self.shown()
        if isinstance(k, tuple) and k[0] == "hover":
            pane = self.ys.get(k[2])
            self.still = pane == self.hover
            self.hover = pane
        elif isinstance(k, tuple) and k[0] == "wheel":
            self.scroll += 2 * k[1]
            self.free = True
        elif isinstance(k, tuple) and k[0] == "click":
            _, x, y = k
            hit = lambda spots: next((cmd for x0, x1, cmd in spots if x0 - 1 <= x <= x1), None)
            if y == 0 and self.chevron[0] - 1 <= x <= self.chevron[0] + 1:
                run("collapse-list", self.chevron[1])
            elif y == 0:
                cmd = hit(self.button_xs)
                if cmd:
                    run(*cmd)
            elif hit(self.row_buttons.get(y, [])):
                run(*hit(self.row_buttons[y]))
                self.sel = None
            elif y in self.ys:
                self.free = False
                self.show(self.ys[y])
        elif k in ("down", "j", "up", "k") and order:
            i = order.index(current) if current in order else 0
            i = max(0, min(i + (1 if k in ("down", "j") else -1), len(order) - 1))
            self.sel, self.free = order[i], False
        elif k in ("enter", "l") and current:
            self.show(current)
        elif k in ("x", "delete") and current:
            run("close-term", current)
            self.sel = None
        elif k == "n":
            run("term")
        elif k == "c":
            run("collapse-list", "expand" if self.width < ICONS_ONLY else "collapse")
        elif k == "s" and current:
            run("split-term", current)

    def on_focus(self):
        if not self.focused:
            self.sel = None   # without the keyboard the highlight follows the shown terminal


if __name__ == "__main__":
    tl = TermList(sys.argv[1])
    lib.app("set-option", "-g", "@pid_termlist", str(os.getpid()))
    tl.connect()
    tl.run()
