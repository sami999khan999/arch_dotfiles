#!/usr/bin/env python3
# sidebar.py — agentmux's two sidebars:  sidebar.py projects  |  sidebar.py agents
# In the panels' look (term.py): icon + title with the blue-then-grey underline, #292e42 selection
# rows, blue rule headings, key hints on the last line.
#
# Kept current without polling: a tmux control-mode client on the agents server subscribes to one
# format listing every thread (refresh-client -B); tmux re-checks it once a second and sends a line
# only when something changed (a thread opened or closed, a title flipped between working and idle).
# The sidebar sleeps in select() on that, the keyboard and SIGUSR1 (agentmux: the current project or
# thread changed). No processes are started while nothing happens.
#
# keys   ↑↓ / j k  move     ↵ / click  open     x or the selected row's ×: close it (asks under the
#        row: y / ↵ / the red button closes, n / Esc / a click elsewhere cancels)
#        projects: o open project     threads: n new · ↵ on a ⚠ row adopts it · r rescan the VS Code kittys
#        c or the header's ‹: collapse to a strip of the rows' icons (› or c again: back to its width)
import os, subprocess, sys, textwrap, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
from term import CLOSE, PAD, App, Line, fit, header, is_close, is_fold, rule, width

AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
FS, RS = "\x1f", "\x1e"   # field / record separators inside the subscription value
SUB = ("#{S:" + FS.join(["#{session_name}", "#{@project}", "#{@harness}", "#{@kind}", "#{pane_title}",
                         "#{session_attached}", "#{pane_current_command}", "#{session_created}",
                         "#{session_activity}", lib.BUSY, lib.ASKS, "#{@agent_state}", "#{@subagents}"]) + RS + "}")
# needs: a permission prompt or question waits for you; working: the agent is running; done: it
# finished since you last looked at that thread; idle: waiting for you, already seen; shell: it quit
STATE_GLYPH = {"needs": ("!", "red"), "error": ("✗", "red"), "working": ("◐", "green"), "done": ("✓", "amber"),
               "idle": ("✳", "sub"), "shell": ("$", "muted")}
STATE_WORD = {"needs": "needs you", "error": "error", "working": "working", "done": "done", "idle": "waiting",
              "shell": "agent exited"}
RANK = {"needs": 0, "error": 1, "working": 2, "done": 3, "idle": 4, "shell": 5}   # a badge: its most urgent thread
FOLD, UNFOLD = "\ueab5", "\ueab6"   # chevrons: ‹ collapses the sidebar to its strip, › opens it
STRIP = 9   # narrower than this (collapsed: agentmux COLLAPSED), only the rows' icons are drawn
SHORT = {"claude": "claude", "opencode": "opencode", "codex": "codex", "agy": "agy", "shell": "shell"}


def ago(ts):
    """How long since ts, short: now, 5m, 3h, 2d."""
    s = max(time.time() - ts, 0) if ts else 0
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{int(s // size)}{unit}"
    return "now"


class Row:
    def __init__(self, text, action=None, glyph="", gfg="muted", fg="sub", right="", rfg="muted",
                 bold=False, kind="item", detail=None):
        self.text, self.action, self.glyph, self.gfg, self.fg = text, action, glyph, gfg, fg
        self.right, self.rfg, self.bold, self.kind = right, rfg, bold, kind
        # dim lines under the name: a list of lines, each [(text, colour)] (one line: just the list)
        self.detail = ([detail] if detail and isinstance(detail[0], tuple) else detail) or []


def wrap_name(text, room, room2):
    """A name too long for room on two lines, broken after a - _ . or space where there's one; the second
    line has room2 (no × beside it), cut with … if it's still too long. One that fits, as it is."""
    if width(text) <= room or room < 4:
        return [text]
    head = fit(text, room + 1)[:-1]   # the most that fits, without the …
    cut = max(head.rfind(c) for c in "-_. ")
    first = head[:cut + 1] if cut >= room // 3 else head
    return [first.rstrip(), fit(text[len(first):].lstrip(), room2)]


def where_is(path):
    """A project's parent folder, short: relative to the projects root if it's inside it, else ~/…"""
    parent, root = os.path.dirname(path), lib.projects_root()
    if root and (parent + "/").startswith(root.rstrip("/") + "/"):
        return os.path.relpath(parent, root) if parent != root else os.path.basename(root)
    return parent.replace(lib.HOME, "~", 1)


class Sidebar(App):
    focused = False   # until tmux says so: a sidebar starts without the keyboard
    motion = True     # the hovered row shows its ×

    def __init__(self, role):
        super().__init__()
        self.role = role
        self.rows, self.sel, self.threads = [], 0, []
        self.scroll = 0   # the first laid-out line shown (render keeps the selection in view)
        self.free = False  # the wheel scrolled the view: it stays put until the selection moves
        self.unshared, self.unshared_at = [], 0
        self.msg, self.confirm = "", None
        self.ask_at, self.buttons_at, self.button_y, self.button_x = set(), None, None, None   # the open question's lines
        self.close_ys = {}    # screen row -> the row whose × is drawn there (the selected and the hovered one)
        self.hover = None     # the row under the pointer
        self.last_state = {}  # thread -> its state on the previous update (to see work finish)
        self.ctl, self.buf = None, ""
        self.project = None
        self.ys = {}   # screen row -> index in self.rows (for clicks)
        self.width = 80

    # ---- the control-mode client ----
    def connect(self):
        lib.agents("new-session", "-d", "-s", lib.WATCH)   # an invisible session to sit on
        if self.ctl:
            self.fds.pop(self.ctl.stdout.fileno(), None)
        self.ctl = subprocess.Popen(lib.AGENTS + ["-C", "attach", "-t", f"={lib.WATCH}"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                                    env=lib.no_tmux_env())
        self.ctl.stdin.write(f"refresh-client -B 'threads::{SUB}'\n")
        self.ctl.stdin.flush()
        os.set_blocking(self.ctl.stdout.fileno(), False)
        self.watch(self.ctl.stdout.fileno(), self.read_ctl)

    def read_ctl(self):
        try:
            chunk = self.ctl.stdout.read() or ""
        except (OSError, ValueError, TypeError):
            chunk = ""
        if not chunk and self.ctl.poll() is not None:   # server gone: start over in a moment
            time.sleep(1)
            self.buf = ""
            self.connect()
            return
        self.buf += chunk
        *lines, self.buf = self.buf.split("\n")
        for line in lines:
            if line.startswith("%subscription-changed threads ") and " : " in line:
                value = line.split(" : ", 1)[1].replace("\\037", FS).replace("\\036", RS)
                self.parse_threads(value)

    def parse_threads(self, value):
        rows = []
        for rec in value.split(RS):
            parts = rec.split(FS)
            if len(parts) != 13 or parts[0].startswith("_"):
                continue
            t = dict(zip(lib.FIELDS[:-1], parts))
            for k in ("attached", "created", "activity"):
                t[k] = int(t[k] or 0)
            rows.append(t)
        self.threads = sorted(rows, key=lambda t: t["created"])
        # an agent that went from working to idle has finished its work: marked "done" until it's looked
        # at. The notifications come from the agents' hooks (agentmux notify) or, for what only the
        # screen shows (a dialog waiting for you, the agent quitting), from here: agentmux notify-screen,
        # sent once per occurrence even though both sidebars see it.
        now = {t["name"]: lib.state_of(t) for t in rows if lib.runs_agent(t)}
        was = lambda n: self.last_state.get(n)
        finished = [n for n, st in now.items() if st == "idle" and was(n) == "working"]
        if finished:
            lib.mark_finished(finished, True)
            harness = {t["name"]: t["harness"] for t in rows}
            for n in finished:   # an agent without a finish hook (opencode): notified from here
                if harness.get(n) not in lib.HOOK_DONE:
                    subprocess.Popen([AGENTMUX, "notify-screen", n, "done"], start_new_session=True)
        working = [n for n, st in now.items() if st == "working"]
        if working:   # a new turn: done / error marks and their notices start over
            for key in ("finished", "told_done", "errored"):
                lib.mark_finished(working, False, key=key)
        if self.last_state:   # not on the first update: it's what changed that counts
            for n, st in now.items():
                if st == "needs" and was(n) not in (None, "needs"):
                    subprocess.Popen([AGENTMUX, "notify-screen", n, "needs"], start_new_session=True)
                if st == "shell" and was(n) not in (None, "shell"):
                    subprocess.Popen([AGENTMUX, "notify-screen", n, "exited"], start_new_session=True)
        lib.mark_finished([n for n, st in now.items() if st != "needs"], False, key="asked")
        # no longer waiting on you (answered, or the thread ended): its notification goes away
        for n in [n for n, st in self.last_state.items() if st in ("needs", "error") and now.get(n) not in ("needs", "error")]:
            subprocess.Popen([AGENTMUX, "clear-notices", n], start_new_session=True)
        lib.mark_finished([n for n, st in now.items() if st != "shell"], False, key="exited")
        self.last_state = now

    # ---- what's listed ----
    def build(self):
        state = lib.load_state()
        project = state.get("project", "")
        jump = project != self.project or state.get("threads", {}).get(project) != getattr(self, "shown", None)
        if project != self.project:
            self.project = project
            self.scan_unshared(force=True)
        self.shown = state.get("threads", {}).get(project)
        finished = set(state.get("finished", []))
        if self.shown in finished:   # it's on screen: seen
            lib.mark_finished([self.shown], False)
            finished.discard(self.shown)

        def look(t):
            st = lib.state_of(t)
            return "done" if st == "idle" and t["name"] in finished else st
        rows = []
        if self.role == "projects":
            for p in lib.projects(self.threads):   # the subscription's list: no tmux call per redraw
                ts = [t for t in self.threads if t["project"] == p and lib.runs_agent(t)]
                busy = sum(look(t) == "working" for t in ts)
                asks = sum(look(t) == "needs" for t in ts)
                errs = sum(look(t) == "error" for t in ts)
                done = sum(look(t) == "done" for t in ts)
                cur = p == project
                glyph, gfg = (("●", "red") if asks or errs else ("●", "green") if busy else ("●", "amber") if done
                              else ("●", "accent") if cur else ("○" if ts else "·", "muted"))
                # line 2: git (branch, changed files, commits ahead / behind) and VS Code; line 3: threads
                git = []
                branch = lib.git_branch(p)
                if branch:
                    st = lib.git_status(p) or {}
                    git.append(("\ue0a0 " + branch, "sub" if cur else "muted"))
                    if st.get("changes"):
                        git.append((f" ~{st['changes']}", "amber"))
                    if st.get("ahead"):
                        git.append((f" ↑{st['ahead']}", "green"))
                    if st.get("behind"):
                        git.append((f" ↓{st['behind']}", "red"))
                else:   # not a repo: say so, and where it is unless that's the projects root itself
                    git.append(("no git repo", "muted"))
                    parent, root = os.path.dirname(p), lib.projects_root()
                    if not root or parent.rstrip("/") != root.rstrip("/"):
                        git.append((" · " + where_is(p), "muted"))
                if lib.in_vscode(p):
                    git.append(("  \U000f0a1e", "accent"))   # open in VS Code
                # line 3: the agents, one badge each in the colour of its busiest thread
                agents, spin = {}, {}
                for t in ts:
                    key = t["harness"] or "shell"
                    agents.setdefault(key, []).append(look(t))
                    if look(t) == "working" and t["title"][:1] in lib.WORKING:   # its own spinner: it turns
                        spin[key] = t["title"][:1]
                badges = []
                for key, states in sorted(agents.items(), key=lambda kv: min(RANK[x] for x in kv[1])):
                    best = min(states, key=RANK.get)
                    g, fg = STATE_GLYPH[best]
                    g = spin.get(key, g) if best == "working" else g
                    badges += [(f"{g} {SHORT.get(key, key)}" + (f"×{len(states)}" if len(states) > 1 else ""), fg), (" ", "muted")]
                work = badges[:-1] or [("no agents", "muted")]
                # right of the name: "working" while any agent there is, else "done" if one finished
                right, rfg = (("needs you", "red") if asks else ("error", "red") if errs else ("working", "green") if busy
                              else ("done", "amber") if done
                              else ("idle", "muted") if ts else ("", "muted"))
                rows.append(Row(lib.project_name(p), ("project", p), glyph, gfg, "text" if cur else "sub",
                                right, rfg, bold=cur, detail=[git, work]))
            if not rows:
                rows += [Row("Nothing open yet", kind="note"), Row("", kind="gap")]
            rows.append(Row("Open project", ("open",), "+", "accent", "accent"))
        else:
            if not project:
                rows += [Row("Pick a project", kind="note"), Row("on the left", kind="note")]
            else:
                shown = state.get("threads", {}).get(project, "")
                mine = [t for t in self.threads if t["project"] == project and lib.runs_agent(t)]
                for t in mine:
                    st = look(t)
                    glyph, gfg = STATE_GLYPH[st]
                    if st == "working" and t["title"][:1] in lib.WORKING:
                        glyph = t["title"][:1]   # its spinner: it turns while it works
                    label = lib.HARNESSES.get(t["harness"], (t["harness"],))[0] if t["harness"] else "shell"
                    title = "shell" if st == "shell" else (lib.clean_title(t["title"]) or label)
                    cur = t["name"] == shown
                    # line 2: which agent, what it's doing, how long the thread has been up (its activity
                    # time says nothing: an idle agent's screen still redraws), and other views of it
                    what = (STATE_WORD[st], STATE_GLYPH[st][1])
                    short = SHORT.get(t["harness"], t["harness"] or "shell")
                    info = [] if title == label or st == "shell" else [(short, "muted"), (" · ", "line")]
                    info.append(what)
                    subs = lib.subagents_of(t)
                    if subs:
                        info.append((f" · {subs} agent{'s' * (subs > 1)}", "green"))
                    up = ago(t["created"])
                    if up != "now":
                        info += [(" · ", "line"), (f"up {up}", "muted")]
                    if t["attached"] > 1:
                        info += [(" · ", "line"), (f"{t['attached']} views", "muted")]
                    rows.append(Row(title, ("show", t["name"]), glyph, gfg, "text" if cur else "sub",
                                    "#" + t["name"].rsplit(lib.SEP, 1)[-1], bold=cur, detail=info))
                if not mine:
                    rows.append(Row("No threads yet", kind="note"))
                if self.unshared:
                    rows.append(Row("", kind="gap"))
                    rows.append(Row("In VS Code · not shared", kind="rule"))
                    for sock, wid, title, harness in self.unshared:
                        rows.append(Row(lib.clean_title(title) or harness, ("adopt", sock, wid, title, harness),
                                        "⚠", "amber", "sub", "adopt", "amber"))
                rows.append(Row("", kind="gap"))
                rows.append(Row("New thread", ("new",), "+", "accent", "accent"))
        # The selection is kept as the item, not its row number (a project coming or going above it
        # moved it onto another row, or onto "+ Open project"). Without the keyboard it marks the
        # current project / thread; while you move through the list it stays where you put it.
        held = self.rows[self.sel].action if self.rows and 0 <= self.sel < len(self.rows) else None
        current = ("project", project) if self.role == "projects" else ("show", self.shown)
        actions = [r.action for r in rows]
        self.rows = rows
        if jump:   # another project / thread: the view goes back to it
            self.free = False
        if (jump or not self.focused) and current in actions:
            self.sel = actions.index(current)
        elif held in actions:
            self.sel = actions.index(held)
        self.sel = max(0, min(self.sel, len(rows) - 1))
        if rows and not rows[self.sel].action:
            self.move(1)

    def scan_unshared(self, force=False):
        """Agents running straight in this project's VS Code kitty (asks kitty: on project change, on
        r, and at most every 30 s, never in a loop)."""
        project = lib.load_state().get("project", "")
        if self.role != "agents" or not project:
            self.unshared = []
            return
        if force or time.time() - self.unshared_at > 30:
            self.unshared = lib.unshared_agents(project)
            self.unshared_at = time.time()

    # ---- drawing ----
    def render(self, w, h):
        self.width = w
        self.close_ys = {}
        self.build()
        if w < STRIP:
            return self.render_strip(w, h)
        if self.role == "projects":
            n = sum(1 for r in self.rows if r.action and r.action[0] == "project")
            lines = header("", "Projects", str(n) if n else "", w, closable=True, fold=FOLD)
        else:
            lines = header("\U000f06a9", "Threads", fit(lib.project_name(self.project), w - 20) if self.project else "", w,
                           closable=True, fold=FOLD)
        lines.append(Line())
        # every row laid out first (owner: the row each line belongs to), then the part that fits is
        # shown, scrolled so the selected row is always in view; "↑ more" / "↓ more" where it's cut
        self.ys = {}
        self.ask_at, self.buttons_at = set(), None
        body, owner, close_at = [], [], {}
        pinned = []   # [(line, row)]: + Open project / + New thread, at the bottom, never scrolled away
        for i, r in enumerate(self.rows):
            if r.kind == "gap":
                body.append(Line()); owner.append(None)
                continue
            if r.kind == "rule":
                body.append(rule(r.text, w, "amber")); owner.append(None)
                continue
            if r.kind == "note":
                body.append(Line().pad(PAD + 2).add(r.text, "muted")); owner.append(None)
                continue
            sel = i == self.sel
            bg = "overlay" if sel else ("hover" if i == self.hover else None)
            closable = (sel or i == self.hover) and r.action and r.action[0] in ("project", "show")
            right, rfg = (CLOSE, "muted") if closable else (r.right, r.rfg)   # the selected one: × closes it
            # narrow: the state at the right (idle, 2m…) gives way before the name is cut; the × stays
            if right and not closable and width(r.text) + 1 + width(right) > w - (PAD + 2) - PAD:
                right = ""
            bar = ("▎", "accent") if sel else (" ", None)   # a blue edge marks the selected row
            l = Line(bg).add(*bar).pad(PAD - 1).add(r.glyph or " ", r.gfg).add(" ")
            room = w - (PAD + 2) - PAD - (width(right) + 1 if right else 0)
            # a long name wraps to a second line (a narrow sidebar), rather than ending in … at a few letters
            names = [r.text] if r.action in (("open",), ("new",)) else wrap_name(r.text, room, w - (PAD + 2) - PAD)
            l.add(fit(names[0], room), r.fg if not sel else "text", bold=r.bold)
            if right:
                l.right(right, rfg, w)
            if r.action in (("open",), ("new",)):
                pinned.append((l, i))
                continue
            if closable:
                close_at[len(body)] = i
            body.append(l); owner.append(i)
            for more in names[1:]:   # under the first, where the name starts
                body.append(Line(bg).add(*bar).pad(PAD + 1).add(more, r.fg if not sel else "text", bold=r.bold))
                owner.append(i)
            for detail in r.detail:   # the dim lines under the name
                d = Line(bg).add(*bar).pad(PAD + 1)
                for text, fg in detail:
                    d.add(fit(text, max(w - d.w - 1, 0)), fg)
                body.append(d); owner.append(i)
            if self.confirm and self.confirm["action"] == r.action:
                self.confirm_lines(body, owner, i, w)
            if r.detail:
                body.append(Line()); owner.append(None)   # air between projects / threads
        while body and owner[-1] is None and not body[-1].parts:   # the gap that led to a pinned row
            body.pop(); owner.pop()
        foot = len(pinned) + 1   # the message line, then the pinned rows on the last line(s)
        room = max(h - len(lines) - foot, 1)
        # kept in view: the open question with its row (it may not be the selected one: a hovered
        # row's ×), else the selected row. Last line last: a question taller than the room keeps its buttons.
        asked = [i for i, r in enumerate(self.rows) if self.confirm and r.action == self.confirm["action"]]
        keep = asked[0] if asked else self.sel
        mine = [n for n, o in enumerate(owner) if o == keep]
        if self.confirm:
            self.free = False   # an open question is always shown
        if len(body) <= room:
            self.scroll = 0
        elif mine and not self.free:
            first, last = mine[0], mine[-1]
            if first < self.scroll + 1:
                self.scroll = max(first - 1, 0)
            if last >= self.scroll + room - 1:
                self.scroll = last - room + 2
        self.scroll = max(0, min(self.scroll, max(len(body) - room, 0)))
        top = len(lines)
        self.button_y = None
        for n in range(self.scroll, min(len(body), self.scroll + room)):
            if owner[n] is not None and n not in self.ask_at:
                self.ys[len(lines)] = owner[n]
            if n in close_at:
                self.close_ys[len(lines)] = close_at[n]
            if n == self.buttons_at:
                self.button_y = len(lines)
            lines.append(body[n])
        if self.scroll > 0:
            lines[top] = Line().pad(PAD + 2).add("↑ more", "muted")
            self.ys.pop(top, None)
        if self.scroll + room < len(body):
            lines[-1] = Line().pad(PAD + 2).add("↓ more", "muted")
            self.ys.pop(len(lines) - 1, None)
        while len(lines) < h - foot:
            lines.append(Line())
        # the bottom: a message when there is one (else air), then the pinned rows on the pane's
        # last line. No key hints: every row has its × and its + rows, and a question opens under
        # its row with its own buttons (confirm_lines).
        lines.append(Line().pad(PAD).add(fit(self.msg, w - 2 * PAD), "muted"))
        for l, i in pinned:
            self.ys[len(lines)] = i
            lines.append(l)
        return lines

    def render_strip(self, w, h):
        """Collapsed: › (opens it again) over a strip with a row per project / thread, its state glyph
        and its initial / number (the selected one with the blue edge, a row of air between them), and
        + Open project / + New thread on the last line. A click opens one, as in the full sidebar."""
        self.ys, self.confirm = {}, None
        lines = [Line().pad((w - 1) // 2).add(UNFOLD, "accent"), Line(), Line()]
        items = [i for i, r in enumerate(self.rows) if r.action and r.action[0] in ("project", "show")]
        room = max((h - len(lines) - 1) // 2, 1)
        at = items.index(self.sel) if self.sel in items else 0
        if len(items) <= room:
            self.scroll = 0
        elif not self.free:
            self.scroll = min(max(self.scroll, at - room + 1), at)
        self.scroll = max(0, min(self.scroll, max(len(items) - room, 0)))
        for i in items[self.scroll:self.scroll + room]:
            r = self.rows[i]
            sel = i == self.sel
            tag = lib.project_name(r.action[1])[:1].upper() if r.action[0] == "project" else r.right.lstrip("#")
            l = Line("overlay" if sel else ("hover" if i == self.hover else None))
            l.add("▎" if sel else " ", "accent").add(r.glyph or " ", r.gfg).add(" ").add(fit(tag, w - 3), "text" if sel else r.fg, bold=r.bold)
            self.ys[len(lines)] = i
            lines += [l, Line()]
        if self.scroll > 0:
            lines[3] = Line().pad((w - 1) // 2).add("↑", "muted")
            self.ys.pop(3, None)
        if self.scroll + room < len(items):
            lines[-2] = Line().pad((w - 1) // 2).add("↓", "muted")
            self.ys.pop(len(lines) - 2, None)
        lines = lines[:h - 1]
        while len(lines) < h - 1:
            lines.append(Line())
        plus = next((i for i, r in enumerate(self.rows) if r.action in (("open",), ("new",))), None)
        if plus is not None:
            self.ys[len(lines)] = plus
            lines.append(Line("hover" if plus == self.hover else None).pad((w - 1) // 2).add("+", "accent"))
        return lines

    # ---- acting ----
    def act(self, action):
        if not action:
            return
        kind = action[0]
        if kind == "project":
            subprocess.Popen([AGENTMUX, "project", action[1]])
        elif kind == "open":
            subprocess.Popen([AGENTMUX, "open"])
        elif kind == "show":
            subprocess.Popen([AGENTMUX, "show", action[1]])
        elif kind == "new":
            subprocess.Popen([AGENTMUX, "new-thread"])
        elif kind == "adopt":
            _, sock, wid, title, harness = action
            detail = "It restarts in tmux, in the same kitty, with the same conversation."
            if title[:1] in lib.WORKING:
                detail = "It's working right now: the turn is cut short. " + detail
            self.ask(f"Adopt “{lib.clean_title(title) or harness}”?", detail, "Adopt",
                     lambda: self.do_adopt(sock, wid, title, harness))

    def do_adopt(self, sock, wid, title, harness):
        name, self.msg = lib.adopt(sock, wid, title, harness, self.project)
        self.scan_unshared(force=True)
        if name:
            subprocess.Popen([AGENTMUX, "show", name])

    def key(self, k):
        if self.confirm and isinstance(k, tuple) and k[0] in ("hover", "wheel"):
            self.still = k[0] == "hover"   # moving the pointer leaves the question open
            return
        if self.confirm:   # y / Enter / the red button: do it; anything else (n, Esc, a click away) cancels
            fn, self.confirm = self.confirm["fn"], None
            if isinstance(k, tuple) and k[0] == "click" and k[2] == self.button_y:
                (yes_x, cancel_x) = self.button_x
                if yes_x[0] <= k[1] < yes_x[1]:
                    fn()
            elif k in ("y", "Y", "enter"):
                fn()
            return
        self.msg = ""
        sel = self.rows[self.sel].action if self.rows else None
        if k in ("down", "j"):
            self.move(1)
        elif k in ("up", "k"):
            self.move(-1)
        elif k in ("enter", "l", "right"):
            self.act(sel)
        elif isinstance(k, tuple) and k[0] == "click" and k[2] == 0 and self.width < STRIP:
            subprocess.Popen([AGENTMUX, "collapse", self.role, "expand"])
        elif isinstance(k, tuple) and k[0] == "click" and is_fold(k, self.width):
            subprocess.Popen([AGENTMUX, "collapse", self.role, "collapse"])
        elif k == "c":
            subprocess.Popen([AGENTMUX, "collapse", self.role, "toggle"])
        elif isinstance(k, tuple) and k[0] == "click" and is_close(k, self.width):
            subprocess.Popen([AGENTMUX, "toggle", self.role])
        elif isinstance(k, tuple) and k[0] == "click" and k[2] in self.close_ys and k[1] >= self.width - PAD - 2:
            i = self.close_ys[k[2]]   # a row's × (the selected or the hovered one): ask, don't open it
            self.ask_close(self.rows[i].action, i)
        elif isinstance(k, tuple) and k[0] == "hover":
            i = self.ys.get(k[2])
            if i == self.hover:
                self.still = True   # same row: nothing to redraw
            self.hover = i
        elif isinstance(k, tuple) and k[0] == "click":
            i = self.ys.get(k[2])
            if i is not None:
                self.sel = i
                self.act(self.rows[i].action)
        elif isinstance(k, tuple) and k[0] == "wheel":   # scrolls the view; the highlight stays put
            self.scroll += 3 * k[1]
            self.free = True
        elif self.role == "projects" and k == "o":
            self.act(("open",))
        elif k in ("x", "delete") and self.width >= STRIP:   # the strip has no room for the question
            self.ask_close(sel)
        elif self.role == "agents" and k == "n":
            self.act(("new",))
        elif self.role == "agents" and k == "r":
            self.scan_unshared(force=True)
            self.msg = "rescanned"

    def ask_close(self, sel, row=None):
        """x or the ×: close the selected project or thread, once confirmed. The question says what
        ends: the threads and which agents, a view in the VS Code kitty."""
        if not sel:
            return
        if sel[0] == "project":
            ts = [t for t in self.threads if t["project"] == sel[1] and lib.runs_agent(t)]
            agents = sorted({SHORT.get(t["harness"], t["harness"] or "shell") for t in ts})
            what = (f"Ends {len(ts)} thread{'s' * (len(ts) != 1)} ({', '.join(agents)}) and its terminals"
                    if ts else "Ends its terminals")
            detail = what + ". Threads open in VS Code stay."
            self.ask(f"Close {lib.project_name(sel[1])}?", detail, "Close",
                     lambda p=sel[1]: subprocess.Popen([AGENTMUX, "close-project", p]), row)
        elif sel[0] == "show":
            t = next((t for t in self.threads if t["name"] == sel[1]), {})
            label = lib.HARNESSES.get(t.get("harness"), (t.get("harness") or "shell",))[0]
            title = lib.clean_title(t.get("title", "")) or label
            detail = f"Ends {label} in it." if t.get("harness") else "Ends its shell."
            if t.get("attached", 0) > 1:
                detail += " It's also open in the VS Code kitty: that view closes too."
            self.ask(f"Close #{sel[1].rsplit(lib.SEP, 1)[-1]} {title}?", detail, "Close",
                     lambda n=sel[1]: (lib.forget_thread(n), lib.agents("kill-session", "-t", f"={n}"),
                                       subprocess.Popen([AGENTMUX, "clear-notices", n], start_new_session=True)), row)

    def ask(self, title, detail, yes, fn, row=None):
        """A question about a row (the selected one unless given), shown under it with its buttons
        (confirm_lines)."""
        self.confirm = {"title": title, "detail": detail, "yes": yes, "fn": fn,
                        "action": self.rows[self.sel if row is None else row].action}   # the item, not its row

    def on_signal(self):
        """agentmux close-thread (Ctrl+Alt+X, remappable) asks a sidebar to close something: the
        selected row, or a given thread (state "ask"). Only the sidebar it names acts on it."""
        ask = lib.load_state().get("ask")
        if not ask or ask.get("role") != self.role:
            return
        lib.update_state(ask=None)
        self.build()
        want = ("show", ask["thread"]) if ask.get("thread") else None
        i = next((n for n, r in enumerate(self.rows) if r.action == want), self.sel) if want else self.sel
        if self.rows and self.rows[i].action and self.rows[i].action[0] in ("project", "show"):
            self.sel = i
            self.ask_close(self.rows[i].action, i)

    def confirm_lines(self, body, owner, i, w):
        """The open question under row i: the question, what it does (wrapped), then its two flat
        buttons as text (red: do it; dim: cancel), like the GTK panels' header buttons. Part of the
        row's band, with a red edge from the title to the buttons. Text buttons, not filled ones: a
        filled cell runs to the line's edges while text keeps a few pixels, so this way the band has
        the same space above the title and below the buttons."""
        c = self.confirm
        bar = ("▎", "red")
        inner = max(w - PAD - 3, 8)
        texts = [(t, "text", True) for t in textwrap.wrap(c["title"], inner)]
        texts += [(t, "sub", False) for t in textwrap.wrap(c["detail"], inner)]
        self.ask_at = set()
        for text, fg, bold in texts:
            body.append(Line("overlay").add(*bar).pad(PAD + 1).add(text, fg, bold=bold)); owner.append(i)
            self.ask_at.add(len(body) - 1)
        body.append(Line("overlay").add(*bar)); owner.append(i); self.ask_at.add(len(body) - 1)
        l = Line("overlay").add(*bar).pad(PAD + 1)
        x0 = l.w
        l.add("y ", "muted").add(c["yes"], "red", bold=True)
        x1 = l.w
        l.add("   ")
        c0 = l.w
        l.add("n ", "muted").add("Cancel", "sub")
        self.button_x = ((x0, x1), (c0, l.w))
        body.append(l); owner.append(i)
        self.buttons_at = len(body) - 1
        self.ask_at.add(self.buttons_at)

    def move(self, d):
        """The next selectable row that way (↑↓); at either end it stays. The view follows it again."""
        self.free = False
        i = self.sel + d
        while 0 <= i < len(self.rows):
            if self.rows[i].action:
                self.sel = i
                return
            i += d

    def timeout(self):
        return 30   # the one periodic look at the VS Code kittys (agents pane)

    def tick(self):
        self.scan_unshared()


if __name__ == "__main__":
    side = Sidebar(sys.argv[1] if len(sys.argv) > 1 else "agents")
    lib.app("set-option", "-g", f"@pid_{side.role}", str(os.getpid()))
    side.connect()
    side.run()
