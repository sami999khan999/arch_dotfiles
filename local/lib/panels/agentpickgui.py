#!/usr/bin/env python3
# agentpickgui.py — agentmux's popups as GTK windows (Tokyo Night, the blurred backdrop of every
# panel). agentmux runs them (`agentmux open`, Ctrl+Alt+N with more than one agent, Ctrl+Alt+K,
# Ctrl+Alt+Q); running one again closes it. The choice goes back to agentmux: `agentmux project
# <folder>` / `new-thread <agent>` / `account …` / `quit --yes`.
#   agentpickgui.py harness   New thread: which agent (the installed ones; the others shown greyed)
#   agentpickgui.py account [thread]
#                             Accounts: the agents at the left (Claude Code, Codex, Antigravity; Tab
#                             or a click), the picked one's logins at the right (who, plan, each usage
#                             window with its reset); ↵ puts the thread on it (same conversation),
#                             Ctrl+D or Make default: new threads start on it. A new name adds one.
#   agentpickgui.py project   Open project: a folder browser. The first time it asks for the projects
#                             folder (the root, remembered in agentmux's state.json); after that it opens there.
#   agentpickgui.py quit [what's running…]
#                             Quit agentmux? What's still running (one line each), what comes back;
#                             Quit (↵) or Cancel (Esc).
# Type to filter (the shortcut list's fuzzy matching; matched letters in blue), ↑↓ to move, ↵ or a
# click to choose, Esc to clear the search, then close.
# Browser: a click or → goes into a folder, ← / Backspace goes up (above the root too, to open a
# project anywhere), ↵ opens the selected folder, Ctrl+R makes the shown folder the root. A query
# starting with / or ~ is a path: it lists that folder's matching subfolders.
import os, re, shlex, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "agentmux"))
from gtkkit import Gdk, GLib, Gtk, View, box, button, clear, label, level, recolor, rule_heading, run, scrolled
try:   # the Accounts panel's Log in page is a terminal (vte4, setup/packages.txt); without it, a kitty
    import gi
    gi.require_version("Vte", "3.91")
    from gi.repository import Pango, Vte
except (ImportError, ValueError):
    Vte = None
from keys import fuzzy
from keysgui import marked
import lib

AGENTMUX = os.path.expanduser("~/.local/bin/agentmux")
HIT = recolor("#7aa2f7")   # matched letters, as in the shortcut list
SKIP = {"node_modules", "dist", "build", ".next", "target", ".venv", "venv", "vendor", "__pycache__"}
SEARCH_DEPTH = 3       # how deep a query searches below the shown folder
SEARCH_LIMIT = 4000    # folders indexed for a query, at most
CSS = """
.pick-list > row { padding: 7px 12px; }
.pick-list > row.off { opacity: .45; }
.pick-glyph { min-width: 18px; }
.crumbs button { padding: 2px 6px; min-height: 0; }
/* Accounts: a card per account, roomy; the selected one lifted and edged in the accent */
.pick-list.accounts > row { padding: 0; margin-bottom: 10px; background: transparent; }
.pick-list.accounts > row:not(:first-child) .rule-head { margin-top: 10px; }
.pick-list.accounts > row:first-child .rule-head { margin-top: 4px; }
.acct-side { min-width: 230px; margin: 18px 0 18px 18px; background: transparent; }
.acct-side > row { padding: 10px 14px; border-left: 2px solid transparent; }
.acct-side > row:selected { background: #292e42; border-left-color: #6b8fe0; }
.acct-card { padding: 14px 18px 16px 18px; border: 1px solid alpha(#3b4261, .7); }
.pick-list.accounts > row:hover .acct-card { background: alpha(#292e42, .45); }
.pick-list.accounts > row:selected .acct-card { background: alpha(#292e42, .35); border-color: #6b8fe0; }
.acct-name { font-size: 11pt; }
.acct-rename { min-height: 0; padding: 1px 6px; font-weight: 700; }
.login-term { border: 1px solid alpha(#3b4261, .7); padding: 8px; background: #1a1b26; }
.acct-card button { padding: 0 8px; min-height: 0; }   /* as tall as the tags beside it */
.acct-who { margin-left: 28px; margin-bottom: 6px; }
.acct-note { margin-left: 28px; margin-top: 8px; }
.acct-add { padding: 6px 18px; }
.usage { margin-left: 28px; margin-top: 6px; }
.usage.today { margin-top: 12px; padding-top: 10px; border-top: 1px solid alpha(#3b4261, .7); }
.usage levelbar trough { background: #292e42; border: none; padding: 0; min-height: 8px; }
.usage levelbar block { min-height: 8px; border: none; }
.usage levelbar block.empty { background: transparent; }
.usage levelbar.green block.filled { background: #9ece6a; }
.usage levelbar.yellow block.filled { background: #e0af68; }
.usage levelbar.red block.filled { background: #f7768e; }
"""


# the login terminal's 16 colours, Tokyo Night's (as kitty's), each one of the theme's (recolor)
TERM_FG, TERM_BG = "#c0caf5", "#1a1b26"
TERM_PALETTE = ["#32344a", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
                "#444b6a", "#ff7a93", "#b9f27c", "#ff9e64", "#7da6ff", "#ad8ee6", "#73daca", "#c0caf5"]


def rgba(colour):
    c = Gdk.RGBA()
    c.parse(recolor(colour))
    return c


def tilde(path):
    return "~" + path[len(lib.HOME):] if path == lib.HOME or path.startswith(lib.HOME + "/") else path


def subdirs(path):
    """The visible subfolders of path, sorted (case-insensitive)."""
    try:
        with os.scandir(path) as it:
            names = [e.name for e in it if not e.name.startswith(".") and e.name not in SKIP
                     and e.is_dir(follow_symlinks=True)]
    except OSError:
        return []
    return [os.path.join(path, n) for n in sorted(names, key=str.lower)]


def is_repo(path):
    return os.path.exists(os.path.join(path, ".git"))


def branch(path):
    """The checked-out branch of a git repo ("" if not one), read from .git/HEAD."""
    try:
        head = open(os.path.join(path, ".git", "HEAD")).read().strip()
    except OSError:   # not a repo, a worktree (.git is a file), or unreadable
        return ""
    return head[16:] if head.startswith("ref: refs/heads/") else head[:7]


class Picker(View):
    """A search line over a list; subclasses fill rows() and say what a choice does."""
    css = CSS
    interval = 0
    placeholder = "type to filter"

    def __init__(self):
        super().__init__()
        self.items = []   # what each list row stands for (None: not choosable)

    def build(self):
        self.search = Gtk.Entry(placeholder_text=self.placeholder, hexpand=True)
        self.search.connect("changed", lambda *_: self.paint())
        self.search.connect("activate", lambda *_: self.choose_selected())
        early = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        early.connect("key-pressed", lambda _c, keyval, _code, state: self.key_early(keyval, state))
        self.search.add_controller(early)   # before the text field turns ← → Backspace into editing
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("pick-list")
        self.list.connect("row-activated", lambda _l, row: self.clicked(row.get_index()))
        self.scroll = scrolled(self.list)
        self.scroll.set_margin_top(10)
        page = box(True, 0, *self.above(), self.search, self.scroll)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        self.paint()
        GLib.idle_add(lambda: self.search.grab_focus() and False)   # once the window is up
        return page

    def above(self):
        """Widgets over the search line."""
        return []

    def key_early(self, keyval, state):
        """A key in the search line, before it edits with it. True: used here."""
        return False

    def paint(self, select=0):
        clear(self.list)
        self.items = []
        for item, child, on in self.rows(self.search.get_text().strip()):
            row = Gtk.ListBoxRow(child=child, activatable=on, selectable=on)
            if not on:
                row.add_css_class("off")
            self.list.append(row)
            self.items.append(item if on else None)
        self.select(select)

    def select(self, i):
        on = [n for n, it in enumerate(self.items) if it is not None]
        if not on:
            return
        n = min(on, key=lambda n: abs(n - i))
        row = self.list.get_row_at_index(n)
        self.list.select_row(row)
        GLib.idle_add(lambda: self.reveal(row) and False)

    def reveal(self, row):
        """Scroll the list so the selected row is in view."""
        ok, rect = row.compute_bounds(self.list)
        if not ok:
            return
        adj = self.scroll.get_vadjustment()
        top, bottom = rect.get_y(), rect.get_y() + rect.get_height()
        if top < adj.get_value():
            adj.set_value(top)
        elif bottom > adj.get_value() + adj.get_page_size():
            adj.set_value(bottom - adj.get_page_size())

    def selected(self):
        row = self.list.get_selected_row()
        return row.get_index() if row else -1

    def move(self, d):
        """d rows that way (PgUp / PgDn: 10), to a choosable one; it stops at the ends."""
        here = self.selected()
        if here < 0 or not self.items:
            return
        i = max(0, min(here + d, len(self.items) - 1))
        while self.items[i] is None and i != here:   # back towards where it was
            i += -1 if d > 0 else 1
        self.select(i)

    def choose_selected(self):
        i = self.selected()
        if 0 <= i < len(self.items) and self.items[i] is not None:
            self.choose(self.items[i])

    def clicked(self, i):
        self.choose(self.items[i])

    def key(self, keyval, state):
        if keyval == Gdk.KEY_Down:
            self.move(1)
        elif keyval == Gdk.KEY_Up:
            self.move(-1)
        elif keyval == Gdk.KEY_Page_Down:
            self.move(10)
        elif keyval == Gdk.KEY_Page_Up:
            self.move(-10)
        elif keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.close()
        else:
            return False
        return True

    def done(self, *args):
        """Hand the choice to agentmux and close."""
        subprocess.Popen([AGENTMUX, *args], start_new_session=True)
        self.close()


# ---- New thread: which agent --------------------------------------------------------------------
class Harness(Picker):
    title, subtitle = "New thread", "which agent?"
    icon = "\U000f06a9"
    hints = [("Enter", "start"), ("↑↓", "move"), ("Esc", "cancel")]

    def rows(self, q):
        installed = {k for k, _ in lib.installed_harnesses()}
        last = lib.load_state().get("harness")
        found = []
        for k, (name, cmd) in lib.HARNESSES.items():
            m = fuzzy(q, name, cmd) if q else (0, set(), set())
            if m:
                found.append((-m[0], k, name, cmd, m[1], m[2]))
        found.sort(key=lambda f: (f[0], f[1] not in installed))
        for _, k, name, cmd, nh, ch in found:
            on = k in installed
            text = box(True, 2, label(marked(name, nh, HIT), "bold", markup=True),
                       label(marked(cmd, ch, HIT) if on else "not installed", "setting-sub", markup=on))
            text.set_hexpand(True)
            line = box(False, 12, label("✳", "accent" if on else "dim", "pick-glyph"), text)
            if on and k == last:
                line.append(label("last used", "green"))
            yield k, line, on

    def choose(self, harness):
        self.done("new-thread", harness)


# ---- Accounts: usage, the default, which login a thread runs on ----------------------------------
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")   # not strftime's: it follows the locale (সোম)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def until(ts):
    """When a usage window resets: "22:50 · in 12m" today, "Mon 09:00 · in 2d 3h" later."""
    left, at = ts - time.time(), time.localtime(ts)
    when = f"{at.tm_hour:02}:{at.tm_min:02}"
    if not (left < 86400 and at.tm_mday == time.localtime().tm_mday):
        when = f"{DAYS[at.tm_wday]} {when}" if left < 6 * 86400 else f"{MONTHS[at.tm_mon - 1]} {at.tm_mday:02}"
    d, h, m = int(left // 86400), int(left % 86400 // 3600), int(left % 3600 // 60)
    return f"{when} · in " + (f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{max(m, 1)}m")


def budget(pct, reset, start):
    """Today's even share of a long window (weekly, monthly): what was left of it when today began,
    spread over the days from then to its reset (the last one partly). start: [percent, reset] at the
    start of today (lib.day_start), None before today's first reading. Returns (share, used today,
    left of the window at the start of today, days)."""
    now = time.localtime()
    midnight = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    if not start:
        begun = pct   # the first reading today: nothing counted as used yet
    else:
        # the same window (its reset time wobbles by moments between answers), else it reset during
        # today: a fresh window
        begun = start[0] if abs(start[1] - reset) < 3600 else 0.0
    days = max((reset - midnight) / 86400, 1.0)   # a last part-day gets all that's left
    return (100 - begun) / days, max(pct - begun, 0.0), 100 - begun, (reset - midnight) / 86400


def ago(ts):
    s = time.time() - ts
    return "just now" if s < 120 else f"{int(s // 60)}m ago" if s < 3600 else f"{int(s // 3600)}h ago" \
        if s < 86400 else f"{int(s // 86400)}d ago"


class Account(Picker):
    """The agents down the left (Claude Code, Codex, Antigravity: each with its default account); the
    picked one's accounts at the right, a card each: who's logged in, the plan, each usage window (bar,
    percent, when it resets). The default (●) is what new threads of that agent start on; Enter puts
    the shown thread on the selected account."""
    title = "Accounts"
    icon = "\U000f0004"
    placeholder = "filter, or a new account's name"
    interval = 60   # usage, asked again while it's open

    def __init__(self, thread=""):
        super().__init__()
        t = next((t for t in lib.threads() if t["name"] == thread and t["harness"] in lib.ACCOUNT_AGENTS), None)
        self.thread = t["name"] if t else ""
        self.current = (t["harness"], t["account"] or "main") if t else None
        installed = {k for k, _ in lib.installed_harnesses()}
        self.harnesses = [h for h in lib.ACCOUNT_AGENTS if h in installed]
        self.h = t["harness"] if t else (self.harnesses or ["claude"])[0]   # the agent shown
        self.subtitle = f"thread #{self.thread.rsplit(lib.SEP, 1)[-1]}" if t else "no thread shown"
        # what was read last (instant), then asked live in the background (refresh)
        self.usage = {(h, a): lib.account_usage(h, a) for h in self.harnesses for a in lib.accounts(h)}
        self.renaming = None   # (harness, account) whose name is being typed
        self.removing = None   # (harness, account) whose Remove was clicked once
        self.fresh = None      # (harness, account) just added: kept only if its login works
        self.set_hints()

    def set_hints(self):
        here = self.current and self.current[0] == self.h
        self.hints = [("Enter", "switch the thread" if here else "make default"),
                      *([("Ctrl+D", "make default")] if here else []), ("F2", "rename"), ("Del", "remove"), ("Tab", "agent"),
                      ("↑↓", "move"), ("Esc", "close")]
        if self.host:
            self.host.show_hints()

    def build(self):
        page = super().build()
        self.list.add_css_class("accounts")
        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["acct-side"])
        self.side.connect("row-activated", lambda _l, row: self.show_agent(self.harnesses[row.get_index()]))
        self.paint_side()
        self.select_next()
        self.refresh()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.add_named(box(False, 0, self.side, page), "cards")
        return self.stack

    def paint_side(self):
        """An entry per agent: its name, how many accounts, the default one."""
        clear(self.side)
        for h in self.harnesses:
            names = lib.accounts(h)
            n = f"{len(names)} account{'s' * (len(names) > 1)}"
            sub = label(f"{n} · ● {lib.account_label(h, lib.default_account(h))}", "setting-sub", ellipsize=True)
            sub.set_max_width_chars(28)   # a long account name doesn't widen the column
            row = Gtk.ListBoxRow(child=box(True, 3, label(lib.HARNESSES[h][0], "bold"), sub))
            self.side.append(row)
            if h == self.h:
                self.side.select_row(row)

    def show_agent(self, h):
        self.h = h
        self.paint_side()
        self.set_hints()
        self.search.set_text("") if self.search.get_text() else self.paint()
        self.select_next()
        self.search.grab_focus()

    def select_next(self):
        """The thread's next account selected (what you open it for), else the one after the default."""
        names = lib.accounts(self.h)
        here = self.current[1] if self.current and self.current[0] == self.h else lib.default_account(self.h)
        want = ("use", self.h, names[(names.index(here) + 1) % len(names)])
        self.select(next((n for n, it in enumerate(self.items) if it == want), 0))

    def refresh(self):
        def fetch():
            got = {(h, a): lib.account_usage(h, a, live=True) for h in self.harnesses for a in lib.accounts(h)}
            GLib.idle_add(lambda: self.got(got) and False)
        threading.Thread(target=fetch, daemon=True).start()

    def got(self, usage):
        self.usage = usage
        self.paint(self.selected())

    @staticmethod
    def bar(value, top, colour):
        bar = Gtk.LevelBar(min_value=0, max_value=max(top, 0.01), hexpand=True)
        for offset in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH, Gtk.LEVEL_BAR_OFFSET_FULL):
            bar.remove_offset_value(offset)   # one colour, from level()
        bar.set_value(min(value, top))
        bar.add_css_class(colour)
        return bar

    def window(self, name, pct, reset, covers=""):
        """One usage window: its name and percent, what it covers (agy's groups), a bar, what's left and
        when it resets."""
        top = box(False, 8, label(name, "sub", ellipsize=True), label(f"{pct:.0f}% used", level(pct), "bold"))
        top.get_first_child().set_hexpand(True)
        when = f"{100 - pct:.0f}% left · resets {until(reset)}" if reset else "full · starts counting with the next use"
        col = box(True, 6, top, *([label(covers, "setting-sub", ellipsize=True)] if covers else []),
                  self.bar(pct, 100, level(pct)), label(when, "setting-sub", ellipsize=True))
        col.set_hexpand(True)
        return col

    def today(self, name, pct, reset, start):
        """Today's share of a long window, to make it last to the reset: how much of it is used."""
        share, used, left, days = budget(pct, reset, start)
        ratio = used / share * 100 if share else 100
        colour = level(ratio)
        head = box(False, 8, label(f"today · {name} budget", "sub"),
                   label(f"{used:.1f}% of {share:.1f}%", colour, "bold"))
        head.get_first_child().set_hexpand(True)
        rest = share - used
        if days <= 1:
            plan = f"the last day: all {left:.0f}% left is today's"
        else:
            plan = f"{left:.0f}% left over {days:.1f} days: {share:.1f}% a day"
        state = f"{rest:.1f}% left today" if rest >= 0.05 else \
            f"{-rest:.1f}% over today's share" if rest < -0.05 else "today's share used up"
        foot = box(False, 8, label(plan, "setting-sub", ellipsize=True), label(state, colour if rest < 0.05 else "setting-sub"))
        foot.get_first_child().set_hexpand(True)
        return box(True, 6, head, self.bar(used, share, colour), foot, classes=("usage", "today"))

    def card(self, h, a, default, q):
        """An account's card, or None if the search leaves it out."""
        me = lib.account_login(h, a)
        email, shown = me.get("email"), lib.account_label(h, a)
        m = fuzzy(q, shown, email or "") if q else (0, set(), set())
        if not m:
            return None
        plan = label(me.get("plan", "").capitalize(), "accent", "acct-plan")
        plan.set_hexpand(True)   # the tags and the buttons at the right end
        dot = label("●" if a == default else "○", "green" if a == default else "dim", "pick-glyph")
        if self.renaming == (h, a):   # its name, being typed: Enter keeps it, Esc doesn't
            name = Gtk.Entry(text=shown, placeholder_text=a, width_chars=18, max_length=lib.NAME_MAX,
                             css_classes=["acct-rename"])
            name.connect("activate", lambda e: self.rename(h, a, e.get_text()))
            GLib.idle_add(lambda: name.grab_focus() and False)
        else:
            name = label(marked(shown, m[1], HIT), "bold", "acct-name", markup=True)   # at most NAME_MAX long
        head = box(False, 10, dot, name, plan)
        here = self.current == (h, a)
        if here:
            head.append(label("this thread", "accent"))
        if here and a == default:
            head.append(label("·", "dim"))
        if a == default:   # the green dot: the one new threads start on
            head.append(label("default", "green"))
        elif email:
            head.append(button("Make default", lambda: self.make_default(h, a), "flat"))
        head.append(button("Rename", lambda: self.start_rename(h, a), "flat"))
        if a != "main":   # a second click removes it (Delete twice): it logs the account out
            sure = self.removing == (h, a)
            head.append(button("Click again to remove" if sure else "Remove", lambda: self.remove(h, a),
                               "danger" if sure else "flat"))
        if email:
            org = me.get("org", "")
            who = marked(email, m[2], HIT) + (f"  ·  {GLib.markup_escape_text(org)}"
                                              if org and org != f"{email}'s Organization" else "")
        else:
            who = "not logged in · Log in opens a terminal for the agent's own login"
            head.append(button("Log in", lambda: self.login(h, a), "accent"))
        card = box(True, 4, head, label(who, "setting-sub", "acct-who", markup=True, ellipsize=True),
                   classes=("acct-card",))
        use = self.usage.get((h, a), {})
        if email and use:
            ws = use["windows"]
            for i in range(0, len(ws), 2):   # two columns, lined up card to card
                cols = [self.window(*w) for w in ws[i:i + 2]]
                row = box(False, 28, *cols, *([Gtk.Box()] if len(cols) == 1 else []), classes=("usage",))
                row.set_homogeneous(True)
                card.append(row)
            for name, pct, reset, _ in ws:   # the weekly (or monthly) limit, spread to its reset
                if name in ("weekly", "monthly") and reset:
                    card.append(self.today(name, pct, reset, use.get("today", {}).get(name)))
                    break
            if time.time() - use["at"] > 600:   # what was read last; its token too old to ask now
                card.append(label(f"as of {ago(use['at'])} · updates once a thread runs on it", "dim", "acct-note"))
        elif email:
            card.append(label("usage not known yet · shows once a thread has run on it", "dim", "acct-note"))
        return card

    def rows(self, q):
        h = self.h
        default, names = lib.default_account(h), lib.accounts(h)
        note = {"agy": "one account at a time"}.get(h, "")
        yield None, rule_heading(lib.HARNESSES[h][0], note), False
        for a in names:
            c = self.card(h, a, default, q)
            if c:
                yield ("use", h, a), c, True
        if q and all(lib.account_label(h, a).lower() != q.lower() for a in names):   # a new one by that name
            ok = len(q) <= lib.NAME_MAX
            line = box(False, 12, label("+", "accent" if ok else "dim", "pick-glyph"),
                       label(f"Add {lib.HARNESSES[h][0]} account “{q}”" if ok else f"A name is at most {lib.NAME_MAX} characters",
                             "accent" if ok else "dim"), classes=("acct-add",))
            yield ("add", h, q), line, ok
        elif not q:   # straight to its login: named after its email once logged in
            yield ("new", h, None), box(False, 12, label("+", "accent", "pick-glyph"),
                                        label(f"Add {lib.HARNESSES[h][0]} account", "accent"), classes=("acct-add",)), True

    def make_default(self, h, a):
        lib.set_default_account(h, a)
        subprocess.Popen([AGENTMUX, "account", "default", a, h], start_new_session=True,
                         stdout=subprocess.DEVNULL)   # agentmux's home screen shows it
        self.paint_side()
        self.paint(self.selected())
        self.say(f"{lib.account_label(h, a)} is the default: new {lib.HARNESSES[h][0]} threads start on it")

    def login(self, h, a):
        """The agent's own login for the account (agentmux account login: no thread, the running ones,
        agy's too, left alone), on a page of the panel that's a terminal: Back, or the login ending,
        returns to the cards. Without vte4, in a kitty, and the panel comes back after it."""
        argv = [AGENTMUX, "account", "login", a, h]
        if Vte is None:
            again = ["python3", os.path.abspath(__file__), "account", self.thread]
            script = f"{shlex.join(argv)}; exec {shlex.join(again)}"   # the login, then this panel again
            subprocess.Popen(["uwsm", "app", "--", "kitty", "--class=TUI.float", f"--title=Log in · {lib.account_label(h, a)}",
                              "-e", "sh", "-c", script], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self.close()
        term = Vte.Terminal(hexpand=True, vexpand=True, css_classes=["login-term"])
        term.set_font(Pango.FontDescription("JetBrainsMono Nerd Font 10"))
        term.set_colors(rgba(TERM_FG), rgba(TERM_BG), [rgba(c) for c in TERM_PALETTE])
        term.connect("child-exited", lambda _t, _status: self.login_done(h, a))
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)   # before the terminal
        keys.connect("key-pressed", lambda _c, keyval, _code, state: self.login_key(term, keyval, state))
        term.add_controller(keys)
        # the sign-in link opens on a click: agy's own "Click here" (an OSC 8 link) or any URL it prints
        # (agy can't open the browser itself here: its D-Bus is out of reach)
        term.set_allow_hyperlink(True)
        tag = term.match_add_regex(Vte.Regex.new_for_match(r"https?://[^\s\"'<>]+", -1, 0x00000400), 0)   # PCRE2_MULTILINE
        term.match_set_cursor_name(tag, "pointer")
        click = Gtk.GestureClick(button=1)
        click.connect("released", lambda _g, _n, x, y: self.open_link(term, x, y))
        term.add_controller(click)
        middle = Gtk.GestureClick(button=2, propagation_phase=Gtk.PropagationPhase.CAPTURE)   # the selection, typed in
        middle.connect("pressed", lambda g, *_: (g.set_state(Gtk.EventSequenceState.CLAIMED), self.paste(term, True)))
        term.add_controller(middle)
        title = label(f"Log in \u00b7 {lib.HARNESSES[h][0]} \u00b7 {lib.account_label(h, a)}", "bold")
        title.set_hexpand(True)
        head = box(False, 10, button("\u2039 Back", self.back, "flat"), title,
                   button("Paste", lambda: self.paste(term), "flat"))
        page = box(True, 10, head, term)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        old = self.stack.get_child_by_name("login")
        if old:
            self.stack.remove(old)
        self.stack.add_named(page, "login")
        self.stack.set_visible_child_name("login")
        self.login_pid = None
        term.spawn_async(Vte.PtyFlags.DEFAULT, lib.HOME, argv + ["--no-wait"], None, GLib.SpawnFlags.DEFAULT,
                         None, None, -1, None, lambda _t, pid, _err, _data: setattr(self, "login_pid", pid), None)
        term.grab_focus()
        self.hints = [("click", "a link opens it"), ("Ctrl+C", "copy"), ("Ctrl+V / middle-click", "paste"),
                      ("Ctrl+W", "back")]
        self.host.show_hints()

    def login_key(self, term, keyval, state):
        """Ctrl+W (or Alt+←) leaves the login page; Ctrl+C copies the selection (with none it's Ctrl+C),
        Ctrl+V pastes (a sign-in code); every other key is the terminal's."""
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if (ctrl and keyval in (Gdk.KEY_w, Gdk.KEY_W)) or (state & Gdk.ModifierType.ALT_MASK and keyval == Gdk.KEY_Left):
            self.back()
            return True
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C) and term.get_has_selection():
            term.copy_clipboard_format(Vte.Format.TEXT)
            term.unselect_all()
            self.say("Copied")
            return True
        if ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self.paste(term)
            return True
        return False

    def paste(self, term, primary=False):
        """The clipboard's text typed into the login, as keys (a terminal's own paste comes in paste
        brackets, which agy's code field ignored): a sign-in code, one line."""
        display = term.get_display()
        board = display.get_primary_clipboard() if primary else display.get_clipboard()

        def got(_board, result):
            try:
                text = board.read_text_finish(result) or ""
            except GLib.Error:
                text = ""
            text = text.strip()
            if not text:
                return self.say("Nothing to paste: copy the code first", "bad")
            term.feed_child(text.encode())
            term.grab_focus()
            self.say(f"Pasted {len(text)} characters: Enter sends it")
        board.read_text_async(None, got)

    def open_link(self, term, x, y):
        """A click on a link (not the end of a drag that selected text) opens it in the browser."""
        if term.get_has_selection():
            return
        url = term.check_hyperlink_at(x, y) or (term.check_match_at(x, y) or (None,))[0]
        if url:
            subprocess.Popen(["xdg-open", url], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.say("Opened the sign-in page in the browser")

    def back(self):
        """To the cards, ending a login still running (it tidies up after itself: agy's own home goes)."""
        if self.login_pid:
            try:
                os.kill(self.login_pid, 15)
            except OSError:
                pass
        self.stack.set_visible_child_name("cards")
        self.set_hints()
        self.search.grab_focus()

    def login_done(self, h, a):
        """The login ended: a new account is named after its email, or goes again if it isn't logged in
        (or is one already there, the same email)."""
        self.login_pid = None
        fresh, self.fresh = self.fresh == (h, a), None
        who = lib.account_login(h, a).get("email")
        same = lib.name_from_email(h, a) if who else None
        if fresh and (not who or same):
            lib.remove_account(h, a)
            msg = (f"That's {lib.account_label(h, same)} already: not added again" if same
                   else "Not logged in: the account wasn't added")
        else:
            msg = f"{lib.account_label(h, a)}: logged in as {who}" if who else f"{lib.account_label(h, a)}: not logged in"
        self.usage[(h, a)] = lib.account_usage(h, a)
        self.back()
        self.paint_side()
        self.paint(self.selected())
        self.refresh()   # its usage, now that it can be asked
        self.say(msg, "ok" if who and not same else "bad")

    def remove(self, h, a):
        if self.removing != (h, a):   # first: ask
            self.removing = (h, a)
            self.say(f"Removing {lib.account_label(h, a)} logs it out here and drops it on every PC: again to confirm")
            return self.paint(self.selected())
        self.removing = None
        shown = lib.account_label(h, a)
        err = lib.remove_account(h, a)
        if err:
            self.paint(self.selected())
            return self.say(err, "bad")
        self.paint_side()
        self.paint(max(self.selected() - 1, 0))
        self.say(f"{shown} removed")

    def start_rename(self, h, a):
        self.renaming = (h, a)
        self.paint(next((n for n, it in enumerate(self.items) if it == ("use", h, a)), self.selected()))

    def rename(self, h, a, name):
        """The name the account is shown by, everywhere ("": its own again)."""
        self.renaming = None
        lib.set_account_label(h, a, name)
        subprocess.Popen([AGENTMUX, "account", "name", a, name, h], start_new_session=True,
                         stdout=subprocess.DEVNULL)   # agentmux's status bar and sidebars show it
        self.paint_side()
        self.paint(self.selected())
        self.search.grab_focus()
        self.say(f"Shown as {lib.account_label(h, a)}")

    def choose(self, item):
        kind, h, a = item
        if kind in ("new", "add"):   # + Add account (or a name typed in the search): made, and its login runs
            a, err = lib.new_account(h, a or "")
            if err:
                return self.say(err, "bad")
            self.fresh = (h, a)   # not logged in when the login ends: it goes again
            self.usage[(h, a)] = {}
            self.search.set_text("") if self.search.get_text() else self.paint()   # with it listed
            self.paint_side()
            return self.login(h, a)
        if self.current and self.current[0] == h:
            if a != self.current[1]:
                self.done("account", "switch", self.thread, a)
        else:
            self.make_default(h, a)

    def key_early(self, keyval, state):
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) and len(self.harnesses) > 1:   # the next agent
            step = -1 if keyval == Gdk.KEY_ISO_Left_Tab or state & Gdk.ModifierType.SHIFT_MASK else 1
            self.show_agent(self.harnesses[(self.harnesses.index(self.h) + step) % len(self.harnesses)])
            return True
        return False

    def key(self, keyval, state):
        if (self.renaming or self.removing) and keyval == Gdk.KEY_Escape:   # not after all
            self.renaming = self.removing = None
            self.paint(self.selected())
            self.search.grab_focus()
            return True
        if keyval == Gdk.KEY_Delete and not self.search.get_text():
            i = self.selected()
            if 0 <= i < len(self.items) and self.items[i] and self.items[i][0] == "use" and self.items[i][2] != "main":
                self.remove(*self.items[i][1:])
            return True
        if keyval == Gdk.KEY_F2:
            i = self.selected()
            if 0 <= i < len(self.items) and self.items[i] and self.items[i][0] == "use":
                self.start_rename(*self.items[i][1:])
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_d, Gdk.KEY_D):
            i = self.selected()
            if 0 <= i < len(self.items) and self.items[i] and self.items[i][0] == "use":
                self.make_default(*self.items[i][1:])
            return True
        return super().key(keyval, state)


# ---- Open project: a folder browser -------------------------------------------------------------
class Project(Picker):
    title = "Open project"
    icon = ""
    placeholder = "search, or type a path"

    def __init__(self):
        super().__init__()
        self.root = lib.projects_root()
        self.setup = self.root is None            # first run: choosing the projects folder
        self.cwd = self.root or lib.HOME
        self.children = subdirs(self.cwd)
        self.opened = set(lib.projects())
        self.index = {}                           # folder -> every folder below it (for search), built lazily
        if self.setup:
            self.title = "Choose your projects folder"
        self.subtitle = "" if self.setup else "root " + tilde(self.root)
        self.set_hints()

    def set_hints(self):
        self.hints = [("Enter", "use as root" if self.setup else "open"), ("→ / click", "into"),
                      ("←", "back"), *([] if self.setup else [("Ctrl+R", "make root")]), ("Esc", "close")]

    def above(self):
        self.crumbs = box(False, 2, classes=("crumbs",))
        self.crumbs.set_margin_bottom(12)
        return [self.crumbs]

    def build(self):
        page = super().build()
        self.go(self.cwd)
        return page

    # ---- moving around ----
    def go(self, path, select=None):
        self.cwd = os.path.abspath(os.path.expanduser(path))
        self.children = subdirs(self.cwd)
        self.draw_crumbs()
        if self.search.get_text():
            self.search.set_text("")   # repaints
        else:
            self.paint()
        i = next((n for n, it in enumerate(self.items) if it and it[1] == select), None)
        if i is None:   # the first folder, not "open this folder"
            i = 1 if self.children else 0
        self.select(i)

    def up(self):
        parent = os.path.dirname(self.cwd)
        if parent != self.cwd:
            self.go(parent, select=self.cwd)

    def draw_crumbs(self):
        """~ › code › starter-kits, each part a button that goes there."""
        clear(self.crumbs)
        parts, path = [], self.cwd
        while True:
            parts.append(path)
            parent = os.path.dirname(path)
            if path == lib.HOME or parent == path:
                break
            path = parent
        for k, p in enumerate(reversed(parts)):
            if k:
                self.crumbs.append(label("›", "dim"))
            name = tilde(p) if p == lib.HOME or os.path.dirname(p) == p else os.path.basename(p)
            b = button(name, lambda p=p: self.go(p), "flat")
            if p == self.cwd:
                b.add_css_class("bold")
            self.crumbs.append(b)

    # ---- the list ----
    def rows(self, q):
        if not q:
            here = "Use this folder as the projects root" if self.setup else "Open this folder"
            line = box(False, 12, label("", "green", "pick-glyph"), label(here, "green"),
                       label(tilde(self.cwd), "dim", xalign=1.0, ellipsize=True))
            yield ("here", self.cwd), line, True
            for p in self.children:
                yield ("dir", p), self.folder(p, os.path.basename(p), ""), True
            return
        if q[0] in "/~":   # a path: the typed folder's subfolders that start with what follows the last /
            base, _, prefix = os.path.expanduser(q).rpartition("/")
            base = base or "/"
            for p in subdirs(base):
                name = os.path.basename(p)
                if name.lower().startswith(prefix.lower()):
                    yield ("dir", p), self.folder(p, name, tilde(base), set(range(len(prefix)))), True
            return
        found = []
        for p in self.search_index():
            rel = os.path.relpath(p, self.cwd)
            m = fuzzy(q, os.path.basename(p), rel)
            if m:
                found.append((m[0] - rel.count("/") * 5, p, m[1], m[2], rel))
        found.sort(key=lambda f: -f[0])
        for _, p, nh, dh, rel in found[:300]:
            yield ("dir", p), self.folder(p, os.path.basename(p), rel, nh, dh), True

    def folder(self, path, name, where, nh=(), dh=()):
        """A folder's row: ● if it's an open project, a branch glyph for a git repo; where it is
        (search results) under the name; the branch at the right."""
        b = branch(path)
        if path in self.opened:
            glyph = label("●", "accent", "pick-glyph")
        else:
            glyph = label("" if b else "", "amber" if b else "dim", "pick-glyph")
        text = box(True, 2, label(marked(name, nh, HIT), *(("bold",) if b else ()), markup=True))
        if where:
            text.append(label(marked(where, dh, HIT), "setting-sub", markup=True, ellipsize=True))
        text.set_hexpand(True)
        return box(False, 12, glyph, text, label(b, "dim"))

    def search_index(self):
        """Every folder up to SEARCH_DEPTH below the shown one (cached per folder)."""
        if self.cwd not in self.index:
            out, todo = [], [(self.cwd, 0)]
            while todo and len(out) < SEARCH_LIMIT:
                path, depth = todo.pop(0)
                for p in subdirs(path):
                    out.append(p)
                    if depth + 1 < SEARCH_DEPTH and not is_repo(p):   # a repo's insides aren't projects
                        todo.append((p, depth + 1))
            self.index[self.cwd] = out
        return self.index[self.cwd]

    # ---- choosing ----
    def choose(self, item, into=False):
        kind, path = item
        typed_path = self.search.get_text().strip()[:1] in ("/", "~")
        if kind == "dir" and (into or (typed_path and not self.setup)):
            return self.go(path)   # a typed path: ↵ goes into the folder, so the path can be extended
        if self.setup:   # the root is set; now browse it and pick a project
            lib.set_projects_root(path)
            self.root, self.setup = path, False
            self.window.set_title(Project.title)
            self.set_subtitle("root " + tilde(path))
            self.set_hints()
            self.host.show_hints()
            self.say(f"Projects folder: {tilde(path)}")
            return self.go(path)
        self.done("project", path)

    def clicked(self, i):
        self.choose(self.items[i], into=True)

    def key_early(self, keyval, state):
        text = self.search.get_text()
        if keyval == Gdk.KEY_Right and self.search.get_position() == len(text):   # the caret at the end
            i = self.selected()
            if i >= 0 and self.items[i]:
                self.choose(self.items[i], into=True)
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_BackSpace) and not text:
            self.up()
            return True
        return False

    def key(self, keyval, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if ctrl and keyval in (Gdk.KEY_r, Gdk.KEY_R) and not self.setup:
            lib.set_projects_root(self.cwd)
            self.root = self.cwd
            self.set_subtitle("root " + tilde(self.cwd))
            self.say(f"Projects folder is now {tilde(self.cwd)}")
            return True
        return super().key(keyval, state)


# ---- Quit agentmux: are you sure? ---------------------------------------------------------------
class Quit(View):
    """Ctrl+Alt+Q: what quitting cuts short (agentmux passes it, a line each), what survives it, and
    the two buttons. Enter quits, Esc cancels."""
    title, subtitle = "Quit agentmux", ""
    icon = "\U000f06a9"
    interval = 0
    hints = [("Enter", "quit"), ("Esc", "cancel")]

    def __init__(self, running):
        super().__init__()
        self.running = running

    def build(self):
        rows = [label("Still running: it stops." if self.running else "Nothing is running.",
                      "bold" if self.running else "dim")]
        for line in self.running:
            rows.append(box(False, 10, label("\u25cf", "amber", "pick-glyph"), label(line, ellipsize=True)))
        notes = box(True, 4,
                    label("Threads come back the next time it opens.", "dim"),
                    label("Threads open in VS Code keep running. Terminals don't come back.", "dim"))
        notes.set_margin_top(14)
        quit_btn = button("Quit agentmux", self.confirm, "danger")
        buttons = box(False, 8, label(""), button("Cancel", self.close, "flat"), quit_btn)
        buttons.get_first_child().set_hexpand(True)
        buttons.set_margin_top(18)
        page = box(True, 6, *rows, notes, buttons)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        GLib.idle_add(lambda: quit_btn.grab_focus() and False)
        return page

    def confirm(self):
        subprocess.Popen([AGENTMUX, "quit", "--yes"], start_new_session=True)
        self.close()

    def key(self, keyval, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.confirm()
        elif keyval == Gdk.KEY_Escape:
            self.close()
        else:
            return False
        return True


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "project"
    if kind == "harness":
        run(Harness(), "panels.newthread", (620, 440))
    elif kind == "account":
        run(Account(sys.argv[2] if len(sys.argv) > 2 else ""), "panels.agentaccounts", (1040, 700))
    elif kind == "quit":
        run(Quit(sys.argv[2:]), "panels.quitagentmux", (560, 300))
    else:
        run(Project(), "panels.openproject", (820, 600))


if __name__ == "__main__":
    main()
