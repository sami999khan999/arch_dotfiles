# wstui.py — `wsgroups tui`: the workspace groups manager in a terminal (panelkit), the older twin of
# the GTK window (wsgui.py). Its own module so the wsgroups command doesn't import panelkit on every key.
import os, sys

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
from wsgroups import *   # the groups, windows and actions the manager works on
from panelkit import (Panel, run, CARD, FG, DIM, ACCENT, KEY, TRACK, GREEN as GOOD, RED as BAD, BOLD, RESET,
                      fit, frame, card, spread, header, section, hints, highlight)


class UI(Panel):
    interval = 2.0  # keep the window counts current

    def __init__(self):
        self.msg = ""
        self.mode = "main"            # main | pick
        self.pick_sel = 0
        self.ask = None               # a question on the footer row: {label, value, yesno, done}
        self.sel = (query("activeworkspace") or {}).get("id", 1)
        if not 1 <= self.sel <= 10:
            self.sel = 1

    # -- data --
    def tick(self):
        self.load()

    def load(self):
        self.groups = groups()
        self.clients = query("clients") or []
        self.active_ws = (query("activeworkspace") or {}).get("id", 1)

    def apps(self):
        """Open apps, one row per class, with the workspace they're on."""
        seen = {}
        for c in sorted(self.clients, key=lambda c: c["class"].lower()):
            if c["class"] and c["workspace"]["id"] > 0 and c["class"] not in seen:
                seen[c["class"]] = c
        return list(seen.values())

    # -- drawing --
    def paint(self, body, footer):
        """Card header, body, then the last action's result or the key hints on the bottom row."""
        cols, rows, w, pad = frame(92)
        head = header(ICON, "Workspaces", "Super + X, Super + Q…P switch windows", w)
        if self.ask and self.ask["yesno"]:
            foot = f"{BAD}{self.ask['label']}{RESET}  " + hints([("y", "yes"), ("n", "no")], w)
        elif self.ask:
            field = f"{ACCENT}{self.ask['label']}{RESET} {FG}{self.ask['value']}▏{RESET}"
            foot = spread(field, hints([("enter", "ok"), ("esc", "cancel")], 24), w)
        elif self.msg:
            foot = f"{GOOD}{fit(self.msg, w)}{RESET}"
        else:
            foot = hints(footer, w)
        return card(head, body, foot, rows, pad)

    def draw(self, w, h):
        return self.draw_pick() if self.mode == "pick" else self.draw_main()

    def group_rows(self, w):
        cw = max(w - 30, 6)  # class column: whatever the other columns leave
        lines = []
        for ws in range(1, 11):
            g = self.groups.get(ws)
            n = len(windows(ws, self.clients))
            here = f"{ACCENT}●{RESET}" if ws == self.active_ws else " "
            num = f"{ACCENT if g else DIM}{ws % 10}{RESET}"
            name = f"{FG}{BOLD}{fit(g['name'], 10):<10}{RESET}" if g else f"{DIM}{'—':<10}{RESET}"
            cls = f"{KEY}{fit(g['cls'], cw)}{RESET}" if g else f"{DIM}unassigned{RESET}"
            count = f"{GOOD}{'●' * min(n, 4)}{RESET} {DIM}{n}{RESET}" if n else f"{TRACK}○{RESET}"
            row = spread(f"{here} {num}  {name}  {cls}", count + " ", w)
            lines.append(highlight(row, w) if ws == self.sel else row)
        return lines

    def detail_rows(self, w):
        g = self.groups.get(self.sel)
        wins = windows(self.sel, self.clients)
        title = f"Workspace {self.sel % 10}" + (f" · {g['name']}" if g else "")
        lines = [section(title, w, f"{len(wins)} open" if wins else "")]
        if not g:
            return lines + [f"{DIM}No app yet. Press {RESET}{KEY}a{RESET}{DIM} to put an open one here.{RESET}"]
        for i, cmd in enumerate(g["launch"] or ["(nothing)"]):
            lines.append(f"{DIM}{'launch' if i == 0 else '':<8}{RESET}{FG}{fit(cmd, w - 8)}{RESET}")
        new = g.get("new") or (g["launch"][0] if g["launch"] else "-")
        lines.append(f"{DIM}{'Super+N':<8}{RESET}" + (f"{DIM}nothing{RESET}" if new == "-" else f"{FG}{fit(new, w - 8)}{RESET}"))
        lines.append("")
        for i, win in enumerate(wins, 1):
            cls = fit(win["class"], 16)
            lines.append(f"{KEY}{('Super+' + 'QWERTYUIOP'[i - 1] if i <= 10 else ''):<8}{RESET}{FG}{cls}{RESET}  "
                         f"{DIM}{fit(win['title'], max(w - 10 - len(cls), 4))}{RESET}")
        if not wins:
            lines.append(f"{DIM}{'':<8}no windows open{RESET}")
        return lines

    def draw_main(self):
        w = frame(92)[2]
        if w >= 90:  # wide card: the list and the selected workspace side by side
            gap = 4
            lw = (w - gap) // 2
            left, right = self.group_rows(lw), self.detail_rows(w - lw - gap)[:10]
            right += [""] * (len(left) - len(right))
            lines = [l + " " * gap + r for l, r in zip(left, right)]
        else:
            lines = self.group_rows(w) + [""] + self.detail_rows(w)
        return self.paint(lines, [("↑↓", "select"), ("a", "add app"), ("e", "edit"), ("d", "delete"),
                           ("l", "launch"), ("g", "go"), ("t", "tidy")] + ([] if CARD else [("esc", "close")]))

    def draw_pick(self):
        w = frame(92)[2]
        tw = max(w - 44, 6)
        apps = self.apps()
        lines = [section(f"Pick an open app for workspace {self.sel % 10}", w)]
        for i, c in enumerate(apps):
            owner = next((ws for ws, g in self.groups.items() if matches(c, g["cls"])), None)
            where = f"{ACCENT}group {owner}{RESET}" if owner else f"{DIM}on {c['workspace']['id']}{RESET}"
            row = spread(f"{KEY}{fit(c['class'], 24):<24}{RESET}  {DIM}{fit(c['title'], tw)}{RESET}", where + " ", w)
            lines.append(highlight(row, w) if i == self.pick_sel else row)
        lines += ["", f"{DIM}or press {RESET}{KEY}c{RESET}{DIM} to type a class yourself{RESET}"]
        return self.paint(lines, [("↑↓", "select"), ("enter", "add"), ("c", "type class"), ("esc", "back")])

    # -- input --
    def prompt(self, label, value, done):
        """Ask for a line of text on the footer row; done(text) runs on Enter."""
        self.ask = {"label": label, "value": value, "yesno": False, "done": done}

    def confirm(self, question, done):
        self.ask = {"label": question, "value": "", "yesno": True, "done": done}

    def answer(self, k):
        ask = self.ask
        if ask["yesno"]:
            self.ask = None
            if k in ("y", "Y"):
                ask["done"]("")
        elif k == "ESC":
            self.ask = None
        elif k == "ENTER":
            self.ask = None
            ask["done"](ask["value"].strip())
        elif k == "BACKSPACE":
            ask["value"] = ask["value"][:-1]
        elif k == "\x15":
            ask["value"] = ""
        elif isinstance(k, str) and len(k) == 1 and k.isprintable():
            ask["value"] += k

    def save(self, msg):
        write_conf(self.groups)
        self.msg = msg

    def add_class(self, cls, pid=None):
        err = assign(self.groups, self.sel, cls, pid)
        if err:
            self.msg = err
        else:
            self.save(f"{cls} now opens on workspace {self.sel % 10}")

    def edit(self):
        g = self.groups.get(self.sel, {"name": "", "cls": "", "launch": [], "new": ""})
        new = {}

        def got_name(v):
            new["name"] = v
            self.prompt("Window class (regex, a|b for several):", g["cls"], got_cls)

        def got_cls(v):
            new["cls"] = v
            self.prompt("Launch (commands separated by ;):", " ; ".join(g["launch"]), got_launch)

        def got_launch(v):
            if not new["name"] or not new["cls"]:
                self.msg = "name and class are required"
                return
            self.groups[self.sel] = {"name": new["name"], "cls": new["cls"], "new": g.get("new", ""),
                                     "launch": [c.strip() for c in v.split(";") if c.strip()]}
            self.save(f"saved workspace {self.sel % 10}")

        self.prompt("Name:", g["name"], got_name)

    def key(self, k):
        if self.ask:
            self.answer(k)
            return
        if not isinstance(k, str):  # mouse clicks
            return
        k = {"WHEELUP": "UP", "WHEELDOWN": "DOWN"}.get(k, k)
        self.load()
        if self.mode == "pick":
            apps = self.apps()
            if k == "ESC":                       self.mode = "main"
            elif k in ("UP", "k"):               self.pick_sel = max(self.pick_sel - 1, 0)
            elif k in ("DOWN", "j"):             self.pick_sel = min(self.pick_sel + 1, len(apps) - 1)
            elif k == "ENTER" and apps:
                c = apps[self.pick_sel]
                self.add_class(c["class"], c.get("pid")); self.mode = "main"
            elif k == "c":
                self.mode = "main"
                self.prompt("Window class:", "", lambda cls: cls and self.add_class(cls))
            return
        self.msg = ""
        if k in ("ESC", "q"):                    return "quit"
        elif k in ("UP", "k"):                   self.sel = (self.sel - 2) % 10 + 1
        elif k in ("DOWN", "j"):                 self.sel = self.sel % 10 + 1
        elif k.isdigit():                        self.sel = int(k) or 10
        elif k == "a":                           self.mode, self.pick_sel = "pick", 0
        elif k in ("e", "ENTER"):                self.edit()
        elif k == "d" and self.sel in self.groups:
            def remove(_):
                del self.groups[self.sel]
                self.save(f"removed group from workspace {self.sel % 10}")
            self.confirm(f"Remove the {self.groups[self.sel]['name']} group?", remove)
        elif k == "l":
            self.msg = "launched" if launch(self.sel, force=True) else "nothing to launch"
        elif k == "g":
            go(self.sel)
            return "quit"
        elif k == "t":
            self.msg = f"moved {tidy()} window(s) to their workspaces"
        self.load()




def main():
    run(UI())
