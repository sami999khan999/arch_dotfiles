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
import contextlib, copy, fcntl, json, os, re, shlex, shutil, subprocess, time

HOME = os.path.expanduser("~")
HOSTNAME = os.uname().nodename   # socket.gethostname(), without importing socket
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
CONTINUE_ARGS = {"claude": "--continue", "opencode": "--continue", "codex": "resume --last", "agy": "--continue"}
# how each reopens one exact session ({} its id). Claude Code, Codex and agy tell us the id through their
# hooks; opencode has none, so agentmux names its session itself when it starts the thread (--session
# creates it under that id)
RESUME_ARGS = {"claude": "--resume {}", "codex": "resume {}", "agy": "--conversation {}", "opencode": "--session {}"}
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
          "asks", "reported", "subs", "account", "pane"]
FORMAT = "\t".join(["#{session_name}", "#{@project}", "#{@harness}", "#{@kind}", "#{pane_title}",
                    "#{session_attached}", "#{pane_current_command}", "#{session_created}",
                    "#{session_activity}", BUSY, ASKS, "#{@agent_state}", "#{@subagents}", "#{@account}", "#{pane_id}"])


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
# the programs agents run as, for one started by hand in a VS Code kitty's thread (no @harness).
# Not node: a dev server runs as node as often as an npm-installed agent (Codex here is its binary)
AGENT_PROCESSES = set().union(*PROCESS.values()) - {"node"}


def runs_agent(t):
    """A thread the sidebars, the home screen and the project's resume count as an agent's: one agentmux
    started for an agent (@harness; it stays listed after the agent quits, to restart or close it), or a
    VS Code kitty's thread while an agent runs in it. What else runs in a kitty (a dev server, tests,
    a build) is that kitty's terminal, not a thread."""
    return t.get("kind") == "agent" and (bool(t.get("harness")) or t.get("command") in AGENT_PROCESSES)


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
    # (a leading _ is dropped: names starting with _ are agentmux's hidden sessions, e.g. ".app")
    return re.sub(r"[.:]", "_", project_name(path)).lstrip("_") or "project"


def next_name(path):
    base, used = session_base(path), {t["name"] for t in threads()}
    n = 1
    while f"{base}{SEP}{n}" in used:
        n += 1
    return f"{base}{SEP}{n}"


def new_session(name, path, kind, harness="", command=None, project=None, account=""):
    """A detached session on the agents server, started in path and tagged with its project (path
    itself unless project says otherwise: a VS Code kitty's new tab in a subfolder) and the account its
    agent runs on. command: argv to run instead of the login shell (run directly, not through it). An
    agent thread is remembered for its project (remember_agent)."""
    project = project or path
    # a terminals session's shells skip fish's greeting (config.fish): a new terminal opens clean, at its prompt
    env = ["-e", "AGENTMUX_TERM=1"] if kind == "terms" else []
    agents("new-session", "-d", "-s", name, "-c", path, *env, *(command or []))
    for opt, val in (("@project", project), ("@kind", kind), ("@harness", harness), ("@account", account)):
        agents("set-option", "-t", f"={name}:", opt, val)
    if kind == "agent" and harness:
        remember_agent(project, harness)


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
    ("accounts", "Accounts: usage, the default one, the thread on another (same conversation)", "account pick", "C-M-k"),
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
    ("quit", "Quit agentmux (asks first; the threads come back next time)", "quit", "C-M-q"),
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


# ---- accounts: more than one login per agent ----------------------------------------------------
# An account is a config folder the agent runs with (ACCOUNT_ENV). "main" is the agent's own
# (~/.claude, ~/.codex); the others are folders in ACCOUNTS/<harness>/<name>, per PC: a login never
# goes in the repo. Everything but the login is shared with main through symlinks (ACCOUNT_SHARED):
# settings and hooks, skills, plugins and the conversations, so a thread switched to another account
# goes on with the same conversation (--resume). state "account": harness -> the default one, which new
# threads start on (agentmux account default); a thread keeps the one it started on (@account) until
# it's switched (agentmux account switch), which leaves the default as it is.
# agy keeps its login in one keyring entry, whatever folder it runs from (ACCOUNT_KEYRING): each
# account's token waits in a keyring slot of its own and agentmux swaps it into agy's entry (agy_load),
# so agy is on one account at a time: switching puts every agy thread on it (state "agy_loaded").
ACCOUNTS = f"{os.environ.get('XDG_DATA_HOME', HOME + '/.local/share')}/agentmux/accounts"
ACCOUNT_ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
ACCOUNT_HOME = {"claude": f"{HOME}/.claude", "codex": f"{HOME}/.codex"}
# a trailing / is a folder (made in main if it isn't there yet: a link to nothing can't be made into one)
ACCOUNT_SHARED = {"claude": ["settings.json", "CLAUDE.md", "keybindings.json", "history.jsonl", "skills/",
                             "plugins/", "agents/", "commands/", "output-styles/", "projects/", "file-history/",
                             "todos/", "plans/", "session-env/", "paste-cache/", "ide/"],
                  "codex": ["config.toml", "hooks.json", "AGENTS.md", "history.jsonl", "session_index.jsonl",
                            "sessions/", "rules/", "skills/", "plugins/", "prompts/"]}
# what an account's own ~/.claude.json starts with, from main's: no login or account ids (its own
# /login fills those in), but the look, the MCP servers and each folder's trust and permissions
ACCOUNT_SEED = {"claude": (f"{HOME}/.claude.json", ".claude.json", ["theme", "mcpServers", "projects", "editorMode"])}
ACCOUNT_KEYRING = {"agy": ["service", "gemini", "username", "antigravity"]}   # agy's own entry
ACCOUNT_AGENTS = ["claude", "codex", "agy"]   # the agents that can have several accounts, in the panel's order


def accounts(harness):
    """The harness's accounts, main first, then in the order they were added: the ones accounts.json
    lists (one added on another PC gets its folder here the first time it's used; one removed on
    another PC is gone here too)."""
    if harness not in ACCOUNT_AGENTS:
        return ["main"]
    return ["main"] + [a for a in account_names().get(harness, {}) if a != "main"]


def new_account(harness, name=""):
    """Add an account shown as name (any text; none: its own until it's logged in, then named after its
    email, name_from_email). Its own name (its folder's, a thread's @account) is made from it: letters,
    digits and -, unique. Returns (account, error)."""
    name = name.strip()
    if len(name) > NAME_MAX:
        return None, f"A name is at most {NAME_MAX} characters"
    if any(account_label(harness, a).lower() == name.lower() for a in accounts(harness)):
        return None, f"There's already an account called {name}"
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20].strip("-") or "account"   # (no name: account)
    account, n = base, 2
    while account == "main" or account in accounts(harness) or os.path.exists(account_dir(harness, account)):
        account, n = f"{base}-{n}", n + 1
    err = add_account(harness, account)
    if err:
        return None, err
    if name:
        set_account_label(harness, account, name)
    return account, None


def name_from_email(harness, account):
    """A just logged in account without a name of its own is named after its email (before the @;
    "sami 2" if another account has that name). Returns the account it duplicates (the same email), if any."""
    email = account_login(harness, account).get("email", "")
    if "@" not in email:
        return None
    for other in accounts(harness):
        if other != account and account_login(harness, other).get("email") == email:
            return other
    if not account_names().get(harness, {}).get(account):
        base = email.split("@")[0][:NAME_MAX - 2]
        taken = {account_label(harness, o).lower() for o in accounts(harness) if o != account}
        name, n = base, 2
        while name.lower() in taken:
            name, n = f"{base} {n}", n + 1
        set_account_label(harness, account, name)
    return None


def remove_account(harness, account):
    """Forget an account on every PC (its accounts.json entry) and its login on this one (its folder, or
    agy's keyring slot): its conversations stay, they're main's. Not main (the agent's own login: log
    out in the agent), not one a thread runs on. Returns an error or None."""
    if account == "main":
        return "That's the agent's own login: log out in the agent instead"
    shown = account_label(harness, account)
    if any(t["harness"] == harness and (t["account"] or "main") == account for t in threads()):
        return f"{shown} has threads: close them or switch them to another account first"
    if harness in ACCOUNT_KEYRING:
        if load_state().get("agy_loaded", "main") == account:
            agy_load("main")
        keyring("clear", *agy_slot(account))
    folder = account_dir(harness, account)
    if os.path.isdir(folder):   # its own files and links into main's: the links go, not what they point at
        shutil.rmtree(folder)
    names = account_names()
    names.get(harness, {}).pop(account, None)
    save_account_names(names)
    if default_account(harness) == account:
        set_default_account(harness, "main")
    return None


def login_command(harness, account):
    """(argv, env changes) that log the account in, in a terminal, touching nothing that runs: Claude
    Code and Codex log in into the account's own folder. agy: see agy_login."""
    folder = account_dir(harness, account)
    if harness == "claude":
        return ["claude", "auth", "login"], {"CLAUDE_CONFIG_DIR": folder} if account != "main" else {}
    if harness == "codex":
        return ["codex", "login"], {"CODEX_HOME": folder} if account != "main" else {}
    return None, {}


def agy_token(j):
    """A login agy saved, as its keyring entry holds it: {"token": {access_token, token_type,
    refresh_token, expiry}, "auth_method", "id_token"} (a flat token is put in that shape). None if it isn't one."""
    if isinstance(j.get("token"), dict) and j["token"].get("refresh_token"):
        return json.dumps(j)
    if j.get("refresh_token") and j.get("access_token"):
        token = {k: j[k] for k in ("access_token", "token_type", "refresh_token", "expiry") if k in j}
        return json.dumps({"token": token, "auth_method": j.get("auth_method", "consumer"), "id_token": j.get("id_token", "")})
    return None


def agy_login(account):
    """Log an agy account in without touching agy's own keyring entry (so the agy threads keep running):
    agy runs with the keyring out of reach and a home of its own, where it keeps the new login in a
    file; once that file is there it goes into the account's slot (agy's entry, if it's the loaded one)
    and agy is closed. The sign-in page opens in the real session (an xdg-open that puts its D-Bus
    and home back). Returns the account's email, or None."""
    import tempfile
    home = tempfile.mkdtemp(prefix="agy-login-", dir=os.environ.get("XDG_RUNTIME_DIR"))
    shim = f"{home}/bin"
    os.makedirs(shim)
    with open(f"{shim}/xdg-open", "w") as f:
        f.write(f'#!/bin/sh\nDBUS_SESSION_BUS_ADDRESS={shlex.quote(os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""))} '
                f'HOME={shlex.quote(HOME)} exec /usr/bin/xdg-open "$@"\n')
    os.chmod(f"{shim}/xdg-open", 0o755)
    env = dict(os.environ, HOME=home, DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent",
               PATH=f"{shim}:{os.environ.get('PATH', '')}")
    agy = subprocess.Popen([HARNESSES["agy"][1]], env=env, cwd=home)
    secret = None
    try:
        while agy.poll() is None and not secret:
            time.sleep(1)
            for root, _, files in os.walk(f"{home}/.gemini"):
                for name in files:
                    try:
                        raw = open(os.path.join(root, name), "rb").read(65536).decode()
                        if "refresh_token" in raw:
                            secret = agy_token(json.loads(raw))
                    except (OSError, ValueError, UnicodeDecodeError, AttributeError):
                        continue
    finally:
        if agy.poll() is None:
            agy.terminate()
            agy.wait(5)
        shutil.rmtree(home, ignore_errors=True)
    if not secret:
        return None
    where = ACCOUNT_KEYRING["agy"] if load_state().get("agy_loaded", "main") == account else agy_slot(account)
    label = "Password for 'antigravity' on 'gemini'" if where == ACCOUNT_KEYRING["agy"] else f"agentmux: agy account {account}"
    keyring("store", f"--label={label}", *where, secret=secret)
    return account_login("agy", account).get("email", "logged in")


def ensure_account(harness, account):
    """An account listed in accounts.json but new to this PC gets its folder before an agent runs on it."""
    if account != "main" and not os.path.isdir(account_dir(harness, account)):
        add_account(harness, account)


def default_account(harness):
    a = load_state().get("account", {}).get(harness, "main")
    return a if a in accounts(harness) else "main"


def account_dir(harness, account):
    """The account's folder (for agy only a name tag: its login is in the keyring)."""
    return ACCOUNT_HOME.get(harness, f"{ACCOUNTS}/{harness}/main") if account == "main" \
        else f"{ACCOUNTS}/{harness}/{account}"


def add_account(harness, account):
    """A new account's folder, sharing everything but the login with main. Returns an error or None."""
    if harness not in ACCOUNT_AGENTS:
        return f"{HARNESSES.get(harness, (harness,))[0]} has one account only"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", account) or account == "main":
        return "An account's name is letters, digits, - or _ (and not main)"
    folder = account_dir(harness, account)
    if os.path.exists(folder):
        return f"{account} already exists"
    os.makedirs(folder)
    names = account_names()
    if account not in names.get(harness, {}):   # listed on every PC from now on
        names.setdefault(harness, {})[account] = ""
        save_account_names(names)
    home = ACCOUNT_HOME.get(harness)
    for item in ACCOUNT_SHARED.get(harness, []):
        name = item.rstrip("/")
        if item.endswith("/"):
            os.makedirs(f"{home}/{name}", exist_ok=True)
        os.symlink(f"{home}/{name}", f"{folder}/{name}")
    if harness in ACCOUNT_SEED:
        src, dest, keys = ACCOUNT_SEED[harness]
        try:
            main = json.load(open(src))
        except (OSError, ValueError):
            main = {}
        with open(f"{folder}/{dest}", "w") as f:
            json.dump({k: main[k] for k in keys if k in main}, f, indent=2)
    return None


# config/agentmux/accounts.json: harness -> account -> the name it's shown by ("": its own). In the
# repo, so every PC lists the same accounts under the same names (logged in on each PC by itself: a
# login never goes in the repo). The account itself (its folder, a thread's @account) keeps its own
# name: main, or the one it was added as.
ACCOUNT_NAMES = f"{CONF}/accounts.json"
NAME_MAX = 24   # an account's name, at most: it shares a sidebar line with the agent and the state


def account_names():
    try:
        return json.load(open(ACCOUNT_NAMES))
    except (OSError, ValueError):
        return {}


def save_account_names(names):
    with open(ACCOUNT_NAMES, "w") as f:   # in place: config/agentmux is a dotfiles symlink
        json.dump(names, f, indent=2, ensure_ascii=False)   # in the order added: the panel lists them so
        f.write("\n")


def account_label(harness, account):
    # the names file read again only when it changed: the sidebar asks for every thread on every redraw
    names = memoized("account-names", [ACCOUNT_NAMES], account_names)
    name = names.get(harness, {}).get(account or "main") or account or "main"
    return name if len(name) <= NAME_MAX else name[:NAME_MAX - 1] + "…"   # one longer from before the limit


def set_account_label(harness, account, name):
    """Show the account as name ("" or its own: back to its own)."""
    names = account_names()
    name = name.strip()[:NAME_MAX]
    names.setdefault(harness, {})[account] = "" if name == account else name
    save_account_names(names)


def set_default_account(harness, account):
    s = load_state()
    s.setdefault("account", {})[harness] = account
    save_state(s)


def account_config(harness, account):
    """The account's own settings file, where the agent keeps who's logged in and its last usage."""
    return f"{HOME}/.claude.json" if account == "main" else f"{account_dir(harness, account)}/.claude.json"


def jwt_claims(token):
    """What a JWT says about its holder (read, not verified: it's our own login file)."""
    import base64
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def keyring(*args, secret=None):
    """secret-tool: lookup / store / clear (store reads the secret on stdin). Its output, "" if none."""
    try:
        r = subprocess.run(["secret-tool", *args], input=secret, capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def agy_slot(account):
    return ["service", "agentmux-agy", "account", account]


def agy_secret(account):
    """The account's agy token as stored: in agy's own entry if it's the one loaded, else its slot."""
    loaded = load_state().get("agy_loaded", "main")
    return keyring("lookup", *(ACCOUNT_KEYRING["agy"] if account == loaded else agy_slot(account)))


def agy_load(account):
    """Put agy on account: the loaded one's token (agy may have renewed it) back in its slot, the
    account's into agy's entry, or agy's entry emptied for one never logged in (agy asks to log in)."""
    loaded = load_state().get("agy_loaded", "main")
    if account == loaded:
        return
    live = keyring("lookup", *ACCOUNT_KEYRING["agy"])
    if live:
        keyring("store", f"--label=agentmux: agy account {loaded}", *agy_slot(loaded), secret=live)
    want = keyring("lookup", *agy_slot(account))
    if want:
        keyring("store", "--label=Password for 'antigravity' on 'gemini'", *ACCOUNT_KEYRING["agy"], secret=want)
    else:
        keyring("clear", *ACCOUNT_KEYRING["agy"])
    s = load_state()
    s["agy_loaded"] = account
    save_state(s)


def account_login(harness, account):
    """The account's login: {"token", "expires" (epoch), "email", "org", "plan", "id"}; {} when logged
    out. Claude Code: .credentials.json, who in .claude.json; Codex: auth.json, who in its id token;
    agy: its keyring token, who in its id token."""
    folder = account_dir(harness, account)
    try:
        if harness == "agy":
            from datetime import datetime
            j = json.loads(agy_secret(account) or "{}")
            t = j.get("token") or {}
            if not t.get("access_token"):
                return {}
            expiry = re.sub(r"(\.\d{6})\d+", r"\1", t.get("expiry", ""))   # Go's nanoseconds: Python takes 6 digits
            return {"token": t["access_token"], "expires": datetime.fromisoformat(expiry).timestamp() if expiry else 0,
                    "email": jwt_claims(j.get("id_token", "")).get("email", "logged in"), "org": "", "plan": "Google"}
        if harness == "claude":
            c = json.load(open(f"{folder}/.credentials.json")).get("claudeAiOauth") or {}
            if not c.get("accessToken"):
                return {}
            try:
                me = json.load(open(account_config(harness, account))).get("oauthAccount") or {}
            except (OSError, ValueError):
                me = {}
            return {"token": c["accessToken"], "expires": c.get("expiresAt", 0) / 1000,
                    "email": me.get("emailAddress", "logged in"), "org": me.get("organizationName", ""),
                    "plan": c.get("subscriptionType", "")}
        if harness == "codex":
            a = json.load(open(f"{folder}/auth.json"))
            t = a.get("tokens") or {}
            if not t.get("access_token"):
                return {"email": "API key", "plan": "api"} if a.get("OPENAI_API_KEY") else {}
            claims = jwt_claims(t.get("id_token", ""))
            return {"token": t["access_token"], "expires": jwt_claims(t["access_token"]).get("exp", 0),
                    "email": claims.get("email", "logged in"), "org": "", "id": t.get("account_id", ""),
                    "plan": claims.get("https://api.openai.com/auth", {}).get("chatgpt_plan_type", "")}
    except (OSError, ValueError):
        pass
    return {}


# what each agent's own /usage (/status) reads; undocumented, so a change there shows "not known"
USAGE_URL = {"claude": "https://api.anthropic.com/api/oauth/usage", "codex": "https://chatgpt.com/backend-api/wham/usage",
             "agy": "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"}
# agy's quota is per group of models (Gemini; Claude and GPT), asked for the account's own project
AGY_PROJECT_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
USAGE_CACHE = f"{os.environ.get('XDG_CACHE_HOME', HOME + '/.cache')}/agentmux/usage.json"


def window_name(seconds):
    return {18000: "5-hour", 604800: "weekly", 2592000: "monthly"}.get(seconds) or \
        (f"{seconds // 86400}-day" if seconds >= 86400 else f"{max(seconds // 3600, 1)}-hour")


def account_usage(harness, account, live=False):
    """{"windows": [(name, percent, reset time, what it covers)], "at": when it was read}, {} when not known. live: asked
    now with the account's own login, unless its token has expired: the agent renews it when it next
    runs, and renewing it here could race a running one and log it out. Otherwise what was read last:
    Claude Code keeps its own in .claude.json, agentmux keeps every live answer (USAGE_CACHE). A window
    whose reset time has passed is back at 0. "today": {window: [percent, reset, read at]} where each stood at
    midnight (day_start), {} before the first reading of the day."""
    from datetime import datetime
    key = f"{harness}/{account}"
    try:
        cache = json.load(open(USAGE_CACHE))
    except (OSError, ValueError):
        cache = {}
    windows, at = cache.get(key, {}).get("windows", []), cache.get(key, {}).get("at", 0)
    if harness == "claude":   # Claude Code's own, if newer
        try:
            saved = json.load(open(account_config(harness, account))).get("cachedUsageUtilization") or {}
        except (OSError, ValueError):
            saved = {}
        if saved.get("fetchedAtMs", 0) / 1000 > at:
            windows, at = None, saved["fetchedAtMs"] / 1000
            raw = saved.get("utilization") or {}
    login = account_login(harness, account)
    if live and login.get("token") and login.get("expires", 0) > time.time() + 60 and harness in USAGE_URL:
        import urllib.request
        head, body = {"Authorization": "Bearer " + login["token"]}, None
        if harness == "claude":
            head["anthropic-beta"] = "oauth-2025-04-20"
        elif harness == "codex":
            head.update({"ChatGPT-Account-Id": login.get("id", ""), "originator": "codex_cli_rs"})
        else:   # agy: a POST naming the account's project (asked once, kept with its usage)
            head.update({"Content-Type": "application/json", "User-Agent": "antigravity"})   # refused without it
            project = cache.get(key, {}).get("project")
            if not project:
                ask = json.dumps({"metadata": {"ideType": "ANTIGRAVITY", "pluginType": "GEMINI"}}).encode()
                try:
                    with urllib.request.urlopen(urllib.request.Request(AGY_PROJECT_URL, data=ask, headers=head),
                                                timeout=8) as r:
                        project = json.load(r).get("cloudaicompanionProject", "")
                except (OSError, ValueError):
                    project = ""
            body = json.dumps({"project": project}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(USAGE_URL[harness], data=body, headers=head), timeout=8) as r:
                windows, at, raw = None, time.time(), json.load(r)
        except (OSError, ValueError):
            pass
    if windows is None:   # an answer to read: Claude's five_hour / seven_day, Codex's primary / secondary
        windows = []
        if harness == "claude":
            for name, field in (("5-hour", "five_hour"), ("weekly", "seven_day")):
                w = raw.get(field) or {}
                if w.get("utilization") is not None:
                    try:
                        reset = datetime.fromisoformat(w["resets_at"]).timestamp() if w.get("resets_at") else 0
                    except ValueError:
                        reset = 0
                    windows.append((name, float(w["utilization"]), reset, ""))
        elif harness == "agy":   # a window per group's bucket: "Gemini Models", what models it covers
            for g in raw.get("groups") or []:
                covers = (g.get("description") or "").replace("Models within this group: ", "")
                buckets = g.get("buckets") or []
                for b in buckets:
                    if b.get("remainingFraction") is None:
                        continue
                    try:   # a full quota's reset is just "a week from now", moving on every ask (agy itself
                        # says "Quota available"): its week only starts with the first use
                        reset = datetime.fromisoformat(b["resetTime"].replace("Z", "+00:00")).timestamp() \
                            if b.get("resetTime") and float(b["remainingFraction"]) < 1 else 0
                    except ValueError:
                        reset = 0
                    name = g.get("displayName", "") + (f" · {b.get('window', '')}" if len(buckets) > 1 else "")
                    windows.append((name, round((1 - float(b["remainingFraction"])) * 100, 1), reset, covers))
        else:
            limits = raw.get("rate_limit") or {}
            for field in ("primary_window", "secondary_window"):
                w = limits.get(field) or {}
                if w.get("used_percent") is not None:
                    windows.append((window_name(w.get("limit_window_seconds", 0)), float(w["used_percent"]),
                                    w.get("reset_at", 0), ""))
        if windows:   # read again under the lock: the panel asks every account at once, agentmux after turns
            import fcntl
            os.makedirs(os.path.dirname(USAGE_CACHE), exist_ok=True)
            with open(USAGE_CACHE + ".lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    cache = json.load(open(USAGE_CACHE))
                except (OSError, ValueError):
                    cache = {}
                old = cache.get(key, {})
                cache[key] = {"windows": windows, "at": at, "day": day_start(old, windows, at),
                              **({"project": project} if harness == "agy" else {})}
                with open(USAGE_CACHE, "w") as f:
                    json.dump(cache, f)
    if not windows:
        return {}
    now = time.time()
    windows = [(list(w) + [""])[:4] for w in windows]   # a cache from before "what it covers"
    day = cache.get(key, {}).get("day") or {}
    return {"windows": [(n, 0.0, 0, c) if r and r < now else (n, p, r, c) for n, p, r, c in windows], "at": at,
            "today": day.get("start", {}) if day.get("date") == time.strftime("%Y-%m-%d") else {}}


def day_start(old, windows, at):
    """Where each window stood at midnight: {"date", "start": {name: [percent, reset, read at]}}. The
    reading nearest midnight: the last one before it or today's first (the usage timer reads every
    account at 00:00, so usually that one; a PC that was off or asleep reads when it's back). A window
    that has reset since the last reading stood at 0 when it did. The Accounts panel's daily budget
    counts from it, and says "since" when the nearest reading is far from midnight."""
    today = time.strftime("%Y-%m-%d")
    if (old.get("day") or {}).get("date") == today:
        return old["day"]
    now, before = time.time(), {w[0]: w for w in old.get("windows", [])}
    midnight = time.mktime(time.strptime(today, "%Y-%m-%d"))
    start = {}
    for name, pct, reset, *_ in windows:
        prev = before.get(name)
        if prev and prev[2] and prev[2] < now:   # it reset after that reading: 0 from then
            then = [0.0, reset, prev[2]]
        else:
            then = [prev[1], prev[2], old.get("at", 0)] if prev else None
        start[name] = then if then and midnight - then[2] <= at - midnight else [pct, reset, at]
    return {"date": today, "start": start}


# ---- what a thread runs on: its model, how far its account is into its limit --------------------
# Read from files (the conversation, the usage caches) and read again only when they change: the
# sidebars redraw often. agentmux usage refreshes an account's usage live after a thread's turn.
_memo = {}


def memoized(key, paths, fn):
    """fn()'s value, worked out again only once one of paths has changed (its mtime)."""
    stamp = []
    for p in paths:
        try:
            stamp.append(os.stat(p).st_mtime_ns)
        except OSError:
            stamp.append(0)
    hit = _memo.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    value = fn()
    _memo[key] = (stamp, value)
    return value


def conversation_file(harness, project, sid):
    """Where the agent keeps that conversation (None: unknown)."""
    if harness == "claude":
        return f"{HOME}/.claude/projects/" + re.sub(r"[^A-Za-z0-9]", "-", project) + f"/{sid}.jsonl"
    if harness == "codex":   # found once: its folder is the day it started
        if ("codexfile", sid) not in _memo:
            import glob
            _memo[("codexfile", sid)] = next(iter(glob.glob(f"{HOME}/.codex/sessions/*/*/*/rollout-*-{sid}.jsonl")), None)
        return _memo[("codexfile", sid)]
    if harness == "agy":
        return f"{HOME}/.gemini/antigravity-cli/conversations/{sid}.db"
    return None


def pretty_model(m):
    """claude-opus-5-5 -> Opus 5.5, gpt-6-luna -> GPT-6 Luna, gemini-3.7-flash-medium -> Gemini 3.7 Flash."""
    m = re.sub(r"-\d{8}$", "", m)   # a date
    c = re.fullmatch(r"claude-([a-z]+)-(\d+)(?:-(\d+))?", m)
    if c:
        return f"{c[1].capitalize()} {c[2]}" + (f".{c[3]}" if c[3] else "")
    g = re.fullmatch(r"gpt-([\d.]+)(?:-(.+))?", m)
    if g:
        return f"GPT-{g[1]}" + (" " + g[2].replace("-", " ").title() if g[2] else "")
    g = re.fullmatch(r"gemini-([\d.]+)-([a-z]+).*", m)
    if g:
        return f"Gemini {g[1]} {g[2].capitalize()}"
    return m


def thread_model(harness, rec):
    """The model the thread's conversation last used (rec: its state "sessions" entry), "" if unknown."""
    path = conversation_file(harness, rec.get("project", ""), rec.get("id", "")) if rec else None
    if not path:
        return ""

    def read():
        try:
            if harness == "agy":   # its last generation's settings, a protobuf blob: the model id in it
                import sqlite3
                db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
                row = db.execute("SELECT data FROM gen_metadata ORDER BY idx DESC LIMIT 1").fetchone()
                db.close()
                ids = re.findall(rb"\b((?:gemini|claude|gpt)-[a-z0-9][a-z0-9.-]*)", row[0] if row else b"")
                return pretty_model(max(ids, key=len).decode()) if ids else ""
            with open(path, "rb") as f:   # the last "model", read back from the end a chunk at a time
                end, tail = os.path.getsize(path), b""   # (a big tool output can fill the last few)
                while end > 0 and len(tail) < 4 << 20:
                    start = max(0, end - (256 << 10))
                    f.seek(start)
                    tail = f.read(end - start) + tail
                    found = [m for m in re.findall(rb'"model":\s*"([^"]+)"', tail) if not m.startswith(b"<")]
                    if found:
                        return pretty_model(found[-1].decode())
                    end = start
            return ""
        except (OSError, ValueError, Exception):   # sqlite3.Error too: a busy or odd file is just unknown
            return ""
    return memoized(("model", path), [path], read)


CONFIGURED_MODEL = {"claude": f"{HOME}/.claude/settings.json", "agy": f"{HOME}/.gemini/antigravity-cli/settings.json"}


def configured_model(harness):
    """The model the agent is set to start with (a thread that hasn't answered yet), "" if not set:
    Claude Code's "opus[1m]" -> Opus, agy's "Gemini 3.7 Flash (Medium)" -> Gemini 3.7 Flash."""
    path = CONFIGURED_MODEL.get(harness)
    if not path:
        return ""

    def read():
        try:
            m = str(json.load(open(path)).get("model") or "")
        except (OSError, ValueError):
            return ""
        m = re.sub(r"\s*[\[(].*$", "", m)
        return pretty_model(m) if "-" in m else m.capitalize() if m.islower() else m
    return memoized(("configured", path), [path], read)


def thread_limit(harness, account, model=""):
    """(window, percent used) of the thread's account, or None: its shortest window (5-hour, else
    weekly, else monthly…); agy's for the model's group (Gemini, or Claude and GPT)."""
    paths = [USAGE_CACHE] + ([account_config(harness, account)] if harness == "claude" else [])
    use = memoized(("usage", harness, account), paths, lambda: account_usage(harness, account))
    windows = use.get("windows") if use else None
    if not windows:
        return None
    if harness == "agy" and model:
        gemini = "gemini" in model.lower()
        windows = [w for w in windows if ("gemini" in w[0].lower()) == gemini] or windows
    order = ["5-hour", "weekly", "monthly"]
    name, pct = min(windows, key=lambda w: order.index(w[0]) if w[0] in order else len(order))[:2]
    return {"5-hour": "5h", "weekly": "wk", "monthly": "mo"}.get(name, "wk" if harness == "agy" else name), pct


def set_account(name, account):
    """Thread name's agent goes on with its conversation on another account (the one it's on now when
    it's out of usage): restarted in place on --resume (or --continue, its id unknown), the same
    thread. The default account stays as it is. Returns an error or None."""
    ts = threads()
    t = next((t for t in ts if t["name"] == name), None)
    if not t or not t["harness"]:
        return "No agent thread to switch"
    harness = t["harness"]
    if account not in accounts(harness):
        return f"No account {account} for {HARNESSES.get(harness, (harness,))[0]}"
    ensure_account(harness, account)
    if harness in ACCOUNT_KEYRING:   # one account at a time: every agy thread goes, so none may be working
        mine = [o for o in ts if o["harness"] == harness and o["command"] not in SHELLS]
        if any(state_of(o) == "working" for o in mine):
            return f"An {HARNESSES[harness][0]} thread is working: switch once they've all stopped"
        agy_load(account)
        for o in mine:
            if o["name"] != name:
                restart_agent(o, account)
    restart_agent(t, account)
    return None


def restart_agent(t, account):
    """Thread t's agent again, on account, on its conversation (--resume; --continue, its id unknown;
    a fresh one if it hasn't said anything yet)."""
    name, harness = t["name"], t["harness"]
    rec = load_state().get("sessions", {}).get(name)
    sid = rec.get("id") if rec else None
    fresh = False
    if sid and not session_exists(t["project"], harness, sid):
        # its id came at start (SessionStart) but nothing is on disk until the first message: --continue
        # would take the project's latest conversation, another thread's
        sid, fresh = None, True
    script = agent_script(harness, account, resume=not sid and not fresh, session=sid)
    agents("respawn-pane", "-k", "-t", f"={name}:", "sh", "-c", script, "agentmux", "")
    agents("set-option", "-t", f"={name}:", "@account", account)
    agents("set-option", "-t", f"={name}:", "@agent_state", "")   # its usage-limit error is the old account's
    if rec:   # reopened on this account after a restart
        remember_session(name, rec["project"], harness, rec["id"], account)


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


# ---- each thread's own conversation ---------------------------------------------------------------
# state "sessions": thread name -> {project, harness, id, account}: the conversation running in it, as the agent's
# hooks report it (agentmux notify; Claude Code and Codex send their session id). After the agents server
# is gone (a restart, a logout) agentmux reopens every thread on the conversation it had, under the same
# name (agentmux restore). Closing a thread on purpose, or quitting its agent, forgets it.
def remember_session(name, project, harness, sid, account=""):
    s = load_state()
    want = {"project": project, "harness": harness, "id": sid, "account": account or "main"}
    if s.get("sessions", {}).get(name) != want:
        s.setdefault("sessions", {})[name] = want
        save_state(s)


def forget_session(name):
    s = load_state()
    if name in s.get("sessions", {}):
        del s["sessions"][name]
        save_state(s)


def session_exists(path, harness, sid):
    """True if the agent still has that conversation on disk (so --resume won't just fail)."""
    if harness == "claude":
        return os.path.exists(f"{HOME}/.claude/projects/" + re.sub(r"[^A-Za-z0-9]", "-", path) + f"/{sid}.jsonl")
    if harness == "codex":
        import glob
        return bool(glob.glob(f"{HOME}/.codex/sessions/*/*/*/rollout-*-{sid}.jsonl"))
    if harness == "agy":
        return os.path.exists(f"{HOME}/.gemini/antigravity-cli/conversations/{sid}.db")
    if harness == "opencode":   # stored in its database: --session reopens it, or starts it afresh
        return True
    return False


def forget_thread(name):
    """A thread is being closed on purpose: its conversation isn't reopened after a restart, and its
    agent is no longer one the project uses, unless another thread of it is still open there."""
    forget_session(name)
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


def agent_script(harness, account, prompt_file=None, resume=False, session=None):
    """The thread's sh -c script: the agent on that account (its config folder exported, so an agent
    started again by hand in the shell after it quits is on the same one), then a shell."""
    cmd = HARNESSES.get(harness, ("", harness))[1]
    how = PROMPT_ARGS.get(harness, '"$p"')
    if session and RESUME_ARGS.get(harness):
        cmd = f"{cmd} {RESUME_ARGS[harness].format(session)}"
    elif resume and CONTINUE_ARGS.get(harness):
        cmd = f"{cmd} {CONTINUE_ARGS[harness]}"
    script = 'exec "${SHELL:-/bin/sh}"'
    if cmd:
        if prompt_file and how:
            script = f'p=$(cat "$1"); rm -f "$1"; {cmd} {how}; ' + script
        else:
            script = f"{cmd}; " + script
    var = ACCOUNT_ENV.get(harness)
    if var:
        script = (f"unset {var}; " if account == "main" else
                  f"export {var}={shlex.quote(account_dir(harness, account))}; ") + script
    return script


def new_thread(path, harness, prompt_file=None, resume=False, session=None, name=None, account=None):
    """A new agent thread in path running the harness: a fresh session, (resume) the latest one of
    that agent in path, or (session) that exact conversation. prompt_file's text, if given, is its
    first message (the file is removed). Quitting the agent leaves a shell in the thread. Any number
    of threads, of any agent, per project: each is its own session named <project>·<n> (name: that
    one, if it's free). account: the one it runs on (by default the harness's default account)."""
    if not name or agents("has-session", "-t", f"={name}")[0] == 0:
        name = next_name(path)
    if account not in accounts(harness):
        account = default_account(harness)
    ensure_account(harness, account)
    if harness in ACCOUNT_KEYRING:   # agy is on one account at a time: the default only if no agy runs
        loaded = load_state().get("agy_loaded", "main")
        if account != loaded and any(t["harness"] == harness and t["command"] not in SHELLS for t in threads()):
            account = loaded
        agy_load(account)
    if not session and not resume and harness == "opencode":   # no hooks to tell its id: name it now
        session = opencode_id()
    cmd = HARNESSES.get(harness, ("", harness))[1]
    how = PROMPT_ARGS.get(harness, '"$p"')
    script = agent_script(harness, account, prompt_file, resume, session)
    new_session(name, path, "agent", harness, ["sh", "-c", script, "agentmux", prompt_file or ""], account=account)
    if session:   # its conversation, to reopen after a restart (the others report theirs: agentmux notify)
        remember_session(name, path, harness, session, account)
    if prompt_file and cmd and how is None:   # no prompt flag: type it in once the agent is up
        text = open(prompt_file).read()
        os.remove(prompt_file)
        subprocess.Popen(["sh", "-c", 'sleep 3; "$@"', "agentmux", *AGENTS, "send-keys", "-t", f"={name}:", "-l", text],
                         start_new_session=True)
        subprocess.Popen(["sh", "-c", 'sleep 3.2; "$@"', "agentmux", *AGENTS, "send-keys", "-t", f"={name}:", "Enter"],
                         start_new_session=True)
    return name


def opencode_id():
    """A new opencode session id made the way opencode makes them (its id.ts, "descending"): "ses_",
    the inverted millisecond time x 4096 as 6 bytes of hex, 14 random base-62 characters. opencode's
    free models refuse a session whose id isn't in that form ("only from within OpenCode")."""
    import secrets, string
    now = ~(int(time.time() * 1000) * 0x1000 + 1) & 0xFFFFFFFFFFFF
    letters = string.digits + string.ascii_uppercase + string.ascii_lowercase
    return "ses_" + f"{now:012x}" + "".join(secrets.choice(letters) for _ in range(14))


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


def git_status(path, max_age=30, wait=True):
    """{changes: files with uncommitted changes, ahead / behind: commits against the upstream} from one
    `git status`, at most once per max_age seconds; None for a folder that isn't a repo. wait=False:
    what's known (maybe old, None if nothing yet), without running git: the sidebars draw with that and
    ask again in a thread (git_stale)."""
    now = time.time()
    hit = _git.get(path)
    if hit and now - hit[0] < max_age or not wait:
        return hit[1] if hit else None
    # --no-optional-locks: a plain status refreshes .git/index, which codesync then saw as a change
    rc, out = run(["git", "--no-optional-locks", "-C", path, "status", "--porcelain=v2", "--branch", "--untracked-files=normal"], timeout=2)
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


def git_stale(path, max_age=30):
    """True if git_status(path) would run git now."""
    hit = _git.get(path)
    return not hit or time.time() - hit[0] >= max_age


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
