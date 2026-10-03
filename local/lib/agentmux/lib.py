#!/usr/bin/env python3
# lib.py — what agentmux's parts share: the two tmux servers, threads, projects, harnesses, the
# VS Code kittys (kitty-pair) and adopting an agent running in one of them.
#
# Two tmux servers:
#   agents    (tmux -L agents)    every thread is a session here: an agent (claude, opencode…) or a
#             project's terminals. The kitty beside a VS Code window attaches to one of them, and
#             agentmux attaches to the same one from its middle pane: two clients, one process.
#   agentmux  (tmux -L agentmux)  the workspace itself: projects pane, agents pane, the nested client
#             showing the thread, the terminals column. Only layout; nothing runs here for long.
import contextlib, copy, fcntl, json, os, re, shutil, socket, subprocess, time

HOME = os.path.expanduser("~")
HOSTNAME = socket.gethostname()
CONF = f"{HOME}/.config/agentmux"
RUN = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/agentmux"
STATE = f"{os.environ.get('XDG_STATE_HOME', HOME + '/.local/state')}/agentmux/state.json"
AGENTS = ["tmux", "-L", "agents", "-f", f"{CONF}/agents.conf"]
APP = ["tmux", "-L", "agentmux", "-f", f"{CONF}/app.conf"]
SEP = "·"              # session names: "<project>·<n>", "<project>·terms" (tmux forbids . and :)
WATCH = "_watch"       # hidden session the sidebars' control-mode clients sit on
WORKING = "◐◓◑◒⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"   # spinner glyphs harnesses put in their title while busy

# name -> (label, command). Offered only when the command exists.
HARNESSES = {
    "claude": ("Claude Code", "claude"),
    "opencode": ("opencode", "opencode"),
    "agy": ("Antigravity (agy)", "agy"),
    "codex": ("Codex", "codex"),
}
# how each takes a first message on its command line ($p); None: typed in once it's up
PROMPT_ARGS = {"claude": '"$p"', "codex": '"$p"', "opencode": '--prompt "$p"', "agy": None}
# how each continues the latest session in the folder it starts in; None: it can't, start fresh
CONTINUE_ARGS = {"claude": "--continue", "opencode": "--continue", "codex": "resume --last", "agy": None}
# the process name the agent runs as, when it isn't the command (node-based CLIs)
PROCESS = {"claude": {"claude"}, "opencode": {"opencode", ".opencode"}, "agy": {"agy", "antigravity"},
           "codex": {"codex", "node"}}


def installed_harnesses():
    return [(k, label) for k, (label, cmd) in HARNESSES.items() if shutil.which(cmd)]


# ---- running things ----------------------------------------------------------------------------
def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.rstrip("\n")
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def agents(*args):
    return run(AGENTS + list(args))


def app(*args):
    return run(APP + list(args))


def no_tmux_env():
    """Environment for a tmux client started from inside tmux (nested clients, popups)."""
    env = dict(os.environ)
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    return env


# ---- state (projects opened, current project and thread, column widths) -----------------------
# state.json is read and written by several processes at once (both sidebars, the agents' hooks,
# agentmux itself). Saving swaps in a whole new file (never a half-written one: a reader that caught
# one got {} and a save then wrote that back, dropping the project order), under a lock, and writes
# only the keys this process changed, on top of the file as it is then (no lost updates).
class State(dict):
    """state.json as loaded; .loaded is what it held then (save_state's diff)."""


def _read_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def _state_lock():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load_state():
    data = _read_state()
    s = State(data)
    s.loaded = copy.deepcopy(data)
    return s


def save_state(state):
    with _state_lock():
        current = _read_state()
        base = getattr(state, "loaded", {})
        for k in set(base) | set(state):
            if k not in state:
                current.pop(k, None)
            elif k not in base or base[k] != state[k]:
                current[k] = state[k]
        tmp = f"{STATE}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(current, f, indent=1)
        os.replace(tmp, STATE)   # STATE is a plain file in ~/.local/state, not a dotfiles symlink
    if isinstance(state, State):
        state.loaded = copy.deepcopy(current)


def update_state(**changes):
    s = load_state()
    s.update(changes)
    save_state(s)
    return s


# ---- threads -----------------------------------------------------------------------------------
# What the agent's screen says, read by tmux itself (no process, no polling of ours), as a fallback
# and for what an agent's hooks don't report. near(): the pattern within the pane's last lines, so
# the same words in the agent's output don't count. Per agent (the session's @harness):
#   busy: the status line offers to interrupt ("esc to interrupt", opencode "esc interrupt"); agy's
#         says "esc to cancel" while it works ("? for shortcuts" when idle); Claude Code also lists a
#         running background subagent above its prompt ("◯ general-purpose  <task>  12s"), even
#         when the subagent itself waits on a background shell (its hooks then say it stopped)
#   asks: a dialog waits for you: Claude's dialogs and agy's say "Esc to cancel"; Codex asks to
#         "Press a number to choose" (permissions, questions) or "Implement this plan?"; opencode shows
#         "Permission required"; every agy dialog has a "↑/↓ Navigate · …" line
#         just above its footer
# The title's spinner alone isn't enough: inside tmux Claude Code keeps ✳ in its title while working.
def near(pattern, lines):
    return f"#{{e|>:#{{C/i:{pattern}}},#{{e|-:#{{pane_height}},{lines}}}}}"


def per_harness(cases, default):
    out = default
    for harness, fmt in reversed(cases):
        out = f"#{{?#{{==:#{{@harness}},{harness}}},{fmt},{out}}}"
    return out


def anywhere(pattern):
    return f"#{{?#{{C/i:{pattern}}},1,0}}"


# agy draws only as tall as its content (blank rows below), so its footer is found anywhere on screen
BUSY = per_harness([("agy", "#{?#{&&:" + anywhere("esc to cancel") + ",#{!:" + anywhere("↑/↓ navigate") + "}},1,0}"),
                    ("claude", "#{||:" + near("esc*interrupt", 6) + "," + near("◯ ", 10) + "}")],
                   near("esc*interrupt", 6))
ASKS = per_harness([("codex", "#{||:" + near("press a number to choose", 10) + "," + near("implement this plan", 16) + "}"),
                    ("opencode", near("permission required", 20)),
                    ("agy", anywhere("↑/↓ navigate"))],
                   near("esc to cancel", 6))
# reported: the state the agent's hooks last reported (agentmux notify sets @agent_state); subs: how
# many of its subagents run (@subagents)
FIELDS = ["name", "project", "harness", "kind", "title", "attached", "command", "created", "activity", "busy",
          "asks", "reported", "subs", "pane"]
FORMAT = "\t".join(["#{session_name}", "#{@project}", "#{@harness}", "#{@kind}", "#{pane_title}",
                    "#{session_attached}", "#{pane_current_command}", "#{session_created}",
                    "#{session_activity}", BUSY, ASKS, "#{@agent_state}", "#{@subagents}", "#{pane_id}"])


def threads():
    """Every session on the agents server, as dicts (FIELDS), oldest first. Hidden ones left out."""
    rc, out = agents("list-sessions", "-F", FORMAT)
    if rc != 0:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != len(FIELDS):
            continue
        t = dict(zip(FIELDS, parts))
        if t["name"].startswith("_"):
            continue
        for k in ("attached", "created", "activity"):
            t[k] = int(t[k] or 0)
        rows.append(t)
    rows.sort(key=lambda t: t["created"])
    return rows


def subagents_of(t):
    try:
        return int(t.get("subs") or 0)
    except ValueError:
        return 0


SCREEN_READ = {"claude", "codex", "opencode"}   # agents whose dialogs ASKS recognises on screen
HOOK_DONE = {"claude", "codex", "agy"}           # agents whose hooks say when they finished (agentmux hooks)


def state_of(t):
    """needs (a permission prompt or question waits for you), error (it stopped on an error), working,
    idle, or shell (no agent running in it). The screen and the hooks' report together: a dialog on
    screen always counts; a reported "needs" / "error" until the agent works again; "working" from the
    screen, or from the hooks for an agent whose screen we can't read (agy)."""
    if t.get("command") in SHELLS:
        return "shell"
    busy, said = str(t.get("busy")) == "1", t.get("reported", "")
    # a dialog is on screen or not: for an agent whose screen we read, that decides (a reported "needs"
    # stays set when you dismiss the dialog with Esc: no hook fires for that)
    if str(t.get("asks")) == "1" or (said == "needs" and not busy and t.get("harness") not in SCREEN_READ):
        return "needs"
    if said == "error" and not busy:
        return "error"
    if busy or (t.get("title", "")[:1] in WORKING) or (said == "working" and t.get("harness") == "agy"):
        return "working"
    if subagents_of(t) or said == "background":   # its prompt is idle, but subagents / background work run
        return "working"
    return "idle"


# interactive shells: a thread showing one has no agent running. Not "sh": that's the wrapper an
# agent runs under (new_thread), which tmux names the pane after while the agent is up.
SHELLS = {"fish", "bash", "zsh"}


def clean_title(title):
    """The thread's title without the harness's spinner / status glyph; "" while it has none of its
    own (tmux's default title is the host name)."""
    t = re.sub(r"^[^\w(\[]+\s*", "", title or "").strip()
    return "" if t == HOSTNAME else t


def project_name(path):
    return os.path.basename(path.rstrip("/")) or path


def session_base(path):
    """The project's name as a session name: tmux can't target a name with . or : in it (it reads
    them as window / pane separators), so they become _ ("my.app" -> "my_app·1")."""
    return re.sub(r"[.:]", "_", project_name(path))


def next_name(path):
    base, used = session_base(path), {t["name"] for t in threads()}
    n = 1
    while f"{base}{SEP}{n}" in used:
        n += 1
    return f"{base}{SEP}{n}"


def new_session(name, path, kind, harness="", command=None):
    """A detached session on the agents server, tagged with its project. command: argv to run
    instead of the login shell (run directly, not through it). An agent thread is remembered for
    its project (remember_agent)."""
    agents("new-session", "-d", "-s", name, "-c", path, *(command or []))
    for opt, val in (("@project", path), ("@kind", kind), ("@harness", harness)):
        agents("set-option", "-t", f"={name}:", opt, val)
    if kind == "agent" and harness:
        remember_agent(path, harness)


# ---- settings (config/agentmux/settings.json, edited in the settings panel: Ctrl+Alt+S) ----------
SETTINGS = f"{HOME}/.config/agentmux/settings.json"
# every shortcut: id -> (what it does, agentmux command or tmux command, default key in tmux's names)
ACTIONS = [
    ("focus-projects", "Focus Projects", "focus projects", "C-M-p"),
    ("focus-threads", "Focus Threads", "focus agents", "C-M-a"),
    ("focus-thread", "Focus the thread", "focus main", "C-M-m"),
    ("focus-terminals", "Focus the terminals", "focus terms", "C-M-e"),
    ("focus-next", "Focus the next part", "focus-next", "C-M-f"),
    ("new-thread", "New thread", "new-thread", "C-M-n"),
    ("open-project", "Open project", "open", "C-M-o"),
    ("new-terminal", "New terminal", "term", "C-M-t"),
    ("split-terminal", "Split terminal", "split-term", "C-M-d"),
    ("next-terminal", "Next terminal", "next-term 1", "C-M-Down"),
    ("previous-terminal", "Previous terminal", "next-term -1", "C-M-Up"),
    ("close-terminal", "Close terminal", "close-term", "C-M-w"),
    ("close-thread", "Close (selected row or thread)", "close-thread", "C-M-x"),
    ("toggle-projects", "Show / hide Projects", "toggle projects", "C-M-1"),
    ("toggle-threads", "Show / hide Threads", "toggle agents", "C-M-2"),
    ("toggle-terminals", "Show / hide terminals", "toggle terms", "C-M-3"),
    ("maximize", "Maximize the focused panel (again: back)", "maximize", "C-M-z"),
    ("resize-left", "Narrower (focused column)", "tmux:resize-pane -L 3", "C-M-Left"),
    ("resize-right", "Wider (focused column)", "tmux:resize-pane -R 3", "C-M-Right"),
    ("settings", "Settings", "settings", "C-M-s"),
    ("reload", "Reload agentmux", "reload", "C-M-r"),
    ("help", "Show the shortcuts", "click help", "C-M-?"),
    # bound on the agents server (agents:), so it works wherever a thread shows: agentmux and the
    # kitty beside VS Code. Reads the selection in the project's VS Code window (Audio Cursor).
    ("read-aloud", "Read the selection aloud", "agents:speak", "C-M-l"),
]
# the other settings: id -> (section, label, default)
OPTIONS = [
    ("notify-needs", "Notifications", "When an agent needs you (permission, question)", True),
    ("notify-done", "Notifications", "When an agent finishes", True),
    ("notify-error", "Notifications", "When an agent stops on an error", True),
    ("notify-exited", "Notifications", "When an agent quits", True),
    ("resume-on-open", "Projects", "Opening a project resumes its agents' last sessions", True),
]


def load_settings():
    """{"keys": {id: key}, "options": {id: bool}}, defaults filled in for whatever the file leaves out."""
    try:
        s = json.load(open(SETTINGS))
    except (OSError, ValueError):
        s = {}
    keys = {a: k for a, _, _, k in ACTIONS}
    keys.update({a: k for a, k in s.get("keys", {}).items() if a in keys})
    opts = {o: d for o, _, _, d in OPTIONS}
    opts.update({o: bool(v) for o, v in s.get("options", {}).items() if o in opts})
    return {"keys": keys, "options": opts}


def save_settings(settings):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:   # in place: config/agentmux is a dotfiles symlink
        json.dump(settings, f, indent=2)
        f.write("\n")


def option(name):
    return load_settings()["options"].get(name, True)


# keys: tmux's names ("C-M-p", "M-F1", "C-M-Left") <-> how they're shown and typed ("Ctrl+Alt+P")
_SPECIAL = {"left": "Left", "right": "Right", "up": "Up", "down": "Down", "space": "Space", "tab": "Tab",
            "enter": "Enter", "return": "Enter", "esc": "Escape", "escape": "Escape", "home": "Home", "end": "End",
            "pageup": "PPage", "pgup": "PPage", "pagedown": "NPage", "pgdn": "NPage", "delete": "DC", "del": "DC",
            "insert": "IC", "backspace": "BSpace"}
_SHOW = {"Left": "←", "Right": "→", "Up": "↑", "Down": "↓", "PPage": "PgUp", "NPage": "PgDn", "DC": "Delete",
         "IC": "Insert", "BSpace": "Backspace", "Escape": "Esc"}


def key_label(key):
    """"C-M-p" -> "Ctrl+Alt+P"."""
    parts, mods = key.split("-"), []
    while len(parts) > 1 and parts[0] in ("C", "M", "S"):
        mods.append({"C": "Ctrl", "M": "Alt", "S": "Shift"}[parts.pop(0)])
    name = "-".join(parts)
    if len(name) == 1 and name.isupper():
        mods.append("Shift")
    name = _SHOW.get(name, name.upper() if len(name) == 1 else name)
    return "+".join(mods + [name])


def parse_key(text):
    """"ctrl+alt+p" / "Alt+F2" / "C-M-p" -> tmux's name ("C-M-p"), or None if it isn't a key."""
    text = text.strip()
    if re.fullmatch(r"((C|M|S)-)*\S+", text) and "+" not in text:
        parts = text.split("-")
    else:
        parts = [p for p in re.split(r"\s*\+\s*", text) if p]
    if not parts:
        return None
    *mods, name = parts
    order = {"C": 0, "M": 1, "S": 2}
    out = []
    for m in mods:
        m = {"ctrl": "C", "control": "C", "c": "C", "alt": "M", "meta": "M", "m": "M", "shift": "S", "s": "S"}.get(m.lower())
        if not m:
            return None
        if m not in out:
            out.append(m)
    low = name.lower()
    if low in _SPECIAL:
        name = _SPECIAL[low]
    elif re.fullmatch(r"f([1-9]|1[0-2])", low):
        name = low.upper()
    elif len(name) == 1 and name.isprintable():
        name = name.lower()
    else:
        return None
    if "S" in out and len(name) == 1 and name.isalpha():   # tmux calls Shift+k "K"
        out.remove("S")
        name = name.upper()
    return "-".join(sorted(out, key=order.get) + [name])


# ---- the agents a project uses ------------------------------------------------------------------
# state "agents": project -> the agents used there, oldest first. Opening a project with nothing
# running resumes each of them (agentmux resume_latest). Closing a thread on purpose forgets its
# agent; closing the project, an agent quitting, a restart don't.
def remember_agent(path, harness):
    s = load_state()
    used = s.setdefault("agents", {}).setdefault(path, [])
    if harness in used:
        used.remove(harness)
    used.append(harness)
    save_state(s)


def mark_finished(names, done, key="finished"):
    """state "finished": threads whose agent finished its work since you last looked at them (the
    sidebars mark them when a thread goes from working to idle, and unmark them once it is shown)."""
    s = load_state()
    have = set(s.get(key, []))
    new = (have | set(names)) if done else (have - set(names))
    if new != have:
        s[key] = sorted(new)
        save_state(s)
    return new - have   # the ones marked just now (both sidebars see a finish; only one acts on it)


def project_agents(path):
    return list(load_state().get("agents", {}).get(path, []))


def forget_thread(name):
    """A thread is being closed on purpose: its agent is no longer one the project uses, unless
    another thread of it is still open there."""
    ts = threads()
    t = next((t for t in ts if t["name"] == name), None)
    if not t or not t["harness"]:
        return
    if any(o["name"] != name and o["project"] == t["project"] and o["harness"] == t["harness"] for o in ts):
        return
    s = load_state()
    used = s.get("agents", {}).get(t["project"], [])
    if t["harness"] in used:
        used.remove(t["harness"])
        save_state(s)


def new_thread(path, harness, prompt_file=None, resume=False):
    """A new agent thread in path running the harness: a fresh session, or (resume) the latest one
    of that agent in path. prompt_file's text, if given, is its first message (the file is removed).
    Quitting the agent leaves a shell in the thread. Any number of threads, of any agent, per project:
    each is its own session named <project>·<n>."""
    name = next_name(path)
    cmd = HARNESSES.get(harness, ("", harness))[1]
    how = PROMPT_ARGS.get(harness, '"$p"')
    if resume and CONTINUE_ARGS.get(harness):
        cmd = f"{cmd} {CONTINUE_ARGS[harness]}"
    script = 'exec "${SHELL:-/bin/sh}"'
    if cmd:
        if prompt_file and how:
            script = (f'p=$(cat "$1"); rm -f "$1"; {cmd} {how}; ' + script)
        else:
            script = f"{cmd}; " + script
    new_session(name, path, "agent", harness, ["sh", "-c", script, "agentmux", prompt_file or ""])
    if prompt_file and cmd and how is None:   # no prompt flag: type it in once the agent is up
        text = open(prompt_file).read()
        os.remove(prompt_file)
        subprocess.Popen(["sh", "-c", 'sleep 3; "$@"', "agentmux", *AGENTS, "send-keys", "-t", f"={name}:", "-l", text],
                         start_new_session=True)
        subprocess.Popen(["sh", "-c", 'sleep 3.2; "$@"', "agentmux", *AGENTS, "send-keys", "-t", f"={name}:", "Enter"],
                         start_new_session=True)
    return name


def wait_started(name, harness, timeout=3.0):
    """Until the agent runs in the thread (not the shell starting it), so it's shown working, not
    half-started. True if it did within timeout."""
    want = PROCESS.get(harness, {HARNESSES.get(harness, ("", harness))[1]})
    end = time.time() + timeout
    while time.time() < end:
        rc, pid = agents("display-message", "-p", "-t", f"={name}:", "#{pane_pid}")
        if rc == 0 and pid.isdigit() and want & children(int(pid)):
            return True
        time.sleep(0.1)
    return False


def children(pid):
    """Names of a process's children, from /proc (no process started). The thread's pane runs
    `sh -c '<agent>; exec $SHELL'`, and tmux names that pane "sh" while the agent runs under it."""
    try:
        kids = open(f"/proc/{pid}/task/{pid}/children").read().split()
    except OSError:
        return set()
    names = set()
    for k in kids:
        try:
            names.add(open(f"/proc/{k}/comm").read().strip())
        except OSError:
            pass
    return names


def has_history(path, harness):
    """True if the agent has a session to continue in path (only Claude Code's is known on disk;
    the others are assumed to, and start fresh if they don't)."""
    if harness != "claude":
        return True
    folder = f"{HOME}/.claude/projects/" + re.sub(r"[^A-Za-z0-9]", "-", path)
    try:
        return any(f.endswith(".jsonl") for f in os.listdir(folder))
    except OSError:
        return False


def terms_session(path):
    """The project's terminals session (regular shells), made on first use: one window per terminal,
    a split is panes in one window. agentmux shows it with the list of its terminals (termlist.py)."""
    name = f"{session_base(path)}{SEP}terms"
    if agents("has-session", "-t", f"={name}")[0] != 0:
        new_session(name, path, "terms")
    return name


# ---- projects ----------------------------------------------------------------------------------
def projects(known_threads=None):
    """Opened projects: the ones opened by hand, every @project a thread has, and the folders the VS
    Code kittys are in (open in VS Code = open here). Listed in a fixed order (state "order": each
    keeps the place it first got), so they never swap places as threads and VS Code windows come and
    go; a new one goes last."""
    seen, out = set(), []
    s = load_state()
    hand = s.get("projects", [])
    ts = threads() if known_threads is None else known_threads
    for p in hand + [t["project"] for t in ts if t["project"]] + [f for _, f in pair_kittys()]:
        if p and p not in seen and os.path.isdir(p):
            seen.add(p)
            out.append(p)
    order = [p for p in s.get("order", []) if os.path.isdir(p)]
    new = [p for p in out if p not in order]
    if new or len(order) != len(s.get("order", [])):
        order += new
        s["order"] = order
        save_state(s)
    return sorted(out, key=order.index)


_git = {}   # path -> (checked at, changes): git status is cached, the branch is read every time


def git_branch(path):
    """The checked-out branch (or short commit), read from .git/HEAD: no process."""
    git = os.path.join(path, ".git")
    try:
        if os.path.isfile(git):   # a worktree / submodule: "gitdir: <path>"
            gd = open(git).read().split(":", 1)[1].strip()
            git = gd if os.path.isabs(gd) else os.path.join(path, gd)
        head = open(os.path.join(git, "HEAD")).read().strip()
    except (OSError, IndexError):
        return ""
    return head.rsplit("/", 1)[-1] if head.startswith("ref:") else head[:7]


def git_status(path, max_age=30):
    """{changes: files with uncommitted changes, ahead / behind: commits against the upstream} from one
    `git status`, at most once per max_age seconds; None for a folder that isn't a repo."""
    now = time.time()
    hit = _git.get(path)
    if hit and now - hit[0] < max_age:
        return hit[1]
    rc, out = run(["git", "-C", path, "status", "--porcelain=v2", "--branch", "--untracked-files=normal"], timeout=2)
    st = None
    if rc == 0:
        st = {"changes": 0, "ahead": 0, "behind": 0}
        for line in out.splitlines():
            if line.startswith("# branch.ab "):
                a, b = line.split()[2:4]
                st["ahead"], st["behind"] = int(a), -int(b)
            elif not line.startswith("#"):
                st["changes"] += 1
    _git[path] = (now, st)
    return st


def in_vscode(path):
    """True if a VS Code window (its kitty-pair kitty) is working in path."""
    return any(f.rstrip("/") == path.rstrip("/") and os.path.exists(sock) for sock, f in pair_kittys())


def projects_root():
    """The folder the project picker opens at (chosen on first use, state "root"); None if unset or gone."""
    root = load_state().get("root")
    return root if root and os.path.isdir(root) else None


def set_projects_root(path):
    update_state(root=path)


# ---- the VS Code kittys (kitty-pair) -----------------------------------------------------------
KRUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")


def pair_kittys():
    """[(socket, folder)] of the kittys beside VS Code windows that are still running."""
    out = []
    for sock in sorted(os.listdir(KRUN)):
        if not sock.startswith("kitty-pair-") or "." in sock:
            continue
        path = f"{KRUN}/{sock}"
        try:
            folder = open(path + ".folder").read().strip()
        except OSError:
            continue
        out.append((path, folder))
    return out


def kitty(sock, *args, timeout=5):
    return run(["kitty", "@", "--to", f"unix:{sock}", *args], timeout=timeout)


def pair_for(path):
    """The socket of the VS Code kitty working in path, or None."""
    for sock, folder in pair_kittys():
        if folder.rstrip("/") == path.rstrip("/") and kitty(sock, "ls", timeout=2)[0] == 0:
            return sock
    return None


def unshared_agents(path):
    """Agents running straight in a VS Code kitty for path (not in tmux): what Adopt can take in.
    [(socket, window id, title, harness)]"""
    out = []
    for sock, folder in pair_kittys():
        if folder.rstrip("/") != path.rstrip("/"):
            continue
        rc, raw = kitty(sock, "ls", timeout=2)
        if rc != 0:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for osw in data:
            for tab in osw["tabs"]:
                for w in tab["windows"]:
                    names = {os.path.basename(p["cmdline"][0]) for p in w.get("foreground_processes", []) if p.get("cmdline")}
                    if "tmux" in names:
                        continue
                    harness = next((h for h, (_, cmd) in HARNESSES.items() if cmd in names), None)
                    if harness:
                        out.append((sock, w["id"], w.get("title", ""), harness))
    return out


def show_in_pair(name, path):
    """Open the thread as a new tab in the project's VS Code kitty (if there is one): synced both ways."""
    sock = pair_for(path)
    if sock:
        kitty(sock, "launch", "--type=tab", "--cwd", path, *AGENTS, "attach", "-t", f"={name}")
    return bool(sock)


# ---- adopt -------------------------------------------------------------------------------------
def claude_session(cwd, title):
    """The Claude Code session id whose ai-title is title, in cwd's project folder; newest first."""
    folder = f"{HOME}/.claude/projects/" + re.sub(r"[^A-Za-z0-9]", "-", cwd)
    want = f'"aiTitle":{json.dumps(clean_title(title))}'
    try:
        files = sorted((f for f in os.listdir(folder) if f.endswith(".jsonl")),
                       key=lambda f: os.path.getmtime(f"{folder}/{f}"), reverse=True)
    except OSError:
        return None
    for f in files:
        try:
            with open(f"{folder}/{f}", errors="replace") as fh:
                if want in fh.read():
                    return f.removesuffix(".jsonl")
        except OSError:
            continue
    return None


def adopt(sock, window_id, title, harness, path):
    """Restart an agent running straight in a VS Code kitty inside tmux, same conversation:
    /exit it, start a tmux thread in that same kitty tab, resume the session there.
    Returns (thread name, message)."""
    resume = ""
    if harness == "claude":
        sid = claude_session(path, title)
        resume = f"claude --resume {sid}" if sid else "claude --continue"
    else:
        resume = HARNESSES[harness][1]
    match = f"id:{window_id}"
    kitty(sock, "send-text", "--match", match, "/exit\r")
    for _ in range(40):   # wait for its shell prompt (up to 10 s)
        time.sleep(0.25)
        rc, raw = kitty(sock, "ls", "--match", match, timeout=2)
        try:
            w = json.loads(raw)[0]["tabs"][0]["windows"][0]
            names = {os.path.basename(p["cmdline"][0]) for p in w.get("foreground_processes", []) if p.get("cmdline")}
        except (ValueError, IndexError, KeyError):
            names = set()
        if names and names <= SHELLS:
            break
    else:
        return None, "It didn't exit (busy?). Nothing was changed after /exit."
    name = next_name(path)
    new_session(name, path, "agent", harness)
    agents("send-keys", "-t", f"={name}:", resume, "Enter")
    attach = " ".join(AGENTS + ["attach", "-t", f"={name}"])
    kitty(sock, "send-text", "--match", match, f"\x15{attach}\r")
    return name, f"Adopted as {name}: {resume}"
