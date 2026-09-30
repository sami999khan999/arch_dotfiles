#!/usr/bin/env python3
# keys.py — shortcut viewer (Tokyo Night). Reads binds.lua and apps.conf live, so it never goes stale.
#   type  search (every word must match)     ↑↓ / wheel  scroll     PgUp PgDn  page     Esc  clear / close
# A bind shows its trailing "-- comment" as its description when it has one.
import os, re

HYPR = os.path.expanduser("~/.config/hypr")
BINDS, APPS = f"{HYPR}/modules/binds.lua", f"{HYPR}/apps.conf"
WSGROUPS = f"{HYPR}/workspaces.conf"

from panelkit import Panel, run, CARD, CHROME, FG, DIM, ACCENT, KEY, TRACK, BOLD, RESET, fit, frame, card, spread, header, section, hints

# ---- describing actions ------------------------------------------------------------------------
# first matching pattern wins; checked against the bind's action text
DESCRIBE = [
    (r"--incognito", "Browser (incognito)"),
    (r"launch \.\. TERMINAL", "Terminal (new window)"),
    (r"launch \.\. BROWSER", "Browser (new window)"),
    (r"launch \.\. FILE_MANAGER", "File manager (new window)"),
    (r"launch \.\. EDITOR", "Text editor"),
    (r"launch \.\. CALCULATOR", "Calculator"),
    (r"sysmon\.py", "System monitor"),
    (r"keys\.py", "This shortcut list"),
    (r'tui\("btop"\)', "btop"),
    (r'tui\("wiremix"\)', "Audio mixer"),
    (r'tui\("bluetui"\)', "Bluetooth"),
    (r'tui\("nmtui"\)', "Wi-Fi / network"),
    (r'"walker -m symbols"', "Emoji & symbols"),
    (r'"walker -m clipboard"', "Clipboard history"),
    (r'"walker"', "App launcher"),
    (r"power-menu", "Power menu"),
    (r"window\.close", "Close window"),
    (r"togglesplit", "Toggle split direction"),
    (r"window\.pseudo", "Pseudo-tile"),
    (r"float\(\{ action = \"toggle\" \}\)\)$", "Toggle floating"),
    (r'mode = "fullscreen"', "Fullscreen"),
    (r'mode = "maximized"', "Maximize (keeps bar and gaps)"),
    (r"focus\(\{ direction", "Focus window in direction"),
    (r"window\.swap", "Swap window in direction"),
    (r"workspace\.move\(\{ monitor", "Move workspace to monitor"),
    (r"into_group", "Move window into group"),
    (r'workspace = "special', "Send window to scratchpad"),
    (r"follow = false", "Move window to workspace (stay here)"),
    (r"window\.move\(\{ workspace", "Move window to workspace"),
    (r'workspace = "e\+1"', "Next workspace"),
    (r'workspace = "e-1"', "Previous workspace"),
    (r'workspace = "previous"', "Last used workspace"),
    (r"focus\(\{ workspace", "Go to workspace"),
    (r"toggle_special", "Show/hide scratchpad"),
    (r"cycle_next\(\{ next = false", "Previous window"),
    (r"cycle_next", "Next window"),
    (r'monitor = "\+1"', "Next monitor"),
    (r'monitor = "-1"', "Previous monitor"),
    (r"x = -step", "Shrink width"), (r"x = step", "Grow width"),
    (r"y = -step", "Shrink height"), (r"y = step", "Grow height"),
    (r"window\.drag", "Move window"),
    (r"window\.resize\(\)", "Resize window"),
    (r"out_of_group", "Remove window from group"),
    (r"group\.toggle", "Toggle group (tabs)"),
    (r"group\.next", "Next tab in group"),
    (r"group\.prev", "Previous tab in group"),
    (r"group\.active", "Go to tab in group"),
    (r"output-volume raise", "Volume up"), (r"output-volume lower", "Volume down"),
    (r"output-volume \+1", "Volume up (fine)"), (r"output-volume -1", "Volume down (fine)"),
    (r"output-volume mute", "Mute"), (r"input-volume mute", "Mute microphone"),
    (r"brightness raise", "Brightness up"), (r"brightness lower", "Brightness down"),
    (r"brightness \+1", "Brightness up (fine)"), (r"brightness -1", "Brightness down (fine)"),
    (r"play-pause", "Play / pause"), (r"playerctl next", "Next track"), (r"playerctl previous", "Previous track"),
    (r'"makoctl dismiss --all"', "Dismiss all notifications"),
    (r'"makoctl dismiss"', "Dismiss notification"),
    (r"makoctl invoke", "Open notification action"),
    (r"makoctl restore", "Bring back last notification"),
    (r"toggle\.sh notifications", "Do not disturb"),
    (r"SIGUSR1 waybar", "Show/hide waybar"),
    (r"toggle\.sh idle", "Keep screen awake"),
    (r"toggle\.sh nightlight", "Night light"),
    (r"wallpaper\.sh next", "Next wallpaper"),
    (r"lock-session", "Lock screen"),
    (r"screenshot\.sh region", "Screenshot (region)"),
    (r"screenshot\.sh output", "Screenshot (screen)"),
    (r"hyprpicker", "Color picker"),
    (r"zoom_factor = math\.min", "Zoom in"),
    (r"zoom_factor = 1", "Reset zoom"),
]

KEYNAMES = {
    "SUPER": "Super", "SHIFT": "Shift", "CTRL": "Ctrl", "ALT": "Alt", "RETURN": "Enter", "SPACE": "Space",
    "TAB": "Tab", "ESCAPE": "Esc", "Escape": "Esc", "comma": ",", "PRINT": "PrtSc",
    "LEFT": "←", "RIGHT": "→", "UP": "↑", "DOWN": "↓", "code:20": "-", "code:21": "=",
    "mouse:272": "Left drag", "mouse:273": "Right drag", "mouse_down": "Scroll ↓", "mouse_up": "Scroll ↑",
    "XF86AudioRaiseVolume": "Vol+", "XF86AudioLowerVolume": "Vol−", "XF86AudioMute": "Mute key",
    "XF86AudioMicMute": "Mic key", "XF86MonBrightnessUp": "Bright+", "XF86MonBrightnessDown": "Bright−",
    "XF86AudioPlay": "Play", "XF86AudioPause": "Pause", "XF86AudioNext": "Next", "XF86AudioPrev": "Prev",
    "XF86Calculator": "Calc key", "XF86PowerOff": "Power key",
}

def pretty_keys(keys):
    return " + ".join(KEYNAMES.get(k.strip(), k.strip().upper() if len(k.strip()) == 1 else k.strip())
                      for k in keys.split("+"))

def describe(action, comment):
    if comment:
        return comment[0].upper() + comment[1:]
    for pat, text in DESCRIBE:
        if re.search(pat, action):
            return text
    return action.strip()[:50]

# ---- reading binds.lua -------------------------------------------------------------------------
def key_expr(expr, loop):
    """'"SUPER + " .. key' -> 'SUPER + ←/→/↑/↓', using what the enclosing for-loop iterates over."""
    parts = []
    for tok in re.split(r"\s*\.\.\s*", expr.strip()):
        if tok.startswith('"'):
            parts.append(tok.strip('"'))
        else:
            parts.append(loop.get(re.sub(r"\(.*", "", tok), tok))
    return "".join(parts)

def read_binds():
    sections, current, loop, seen = [], None, {}, set()
    lines = open(BINDS).read().splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        m = re.match(r"^-+\s*([A-Z][A-Z &]+?)\s*-+$", s)
        if m:
            current = (m.group(1).title(), []); sections.append(current); continue
        if s.startswith("for key, dir in pairs"):
            loop = {"key": "←/→/↑/↓"}
        elif s.startswith("for ws = 1, 10"):
            loop = {"key": "1…0"}
        elif s.startswith("for mods, step"):
            loop = {"mods": ""}
        elif s.startswith("for i = 1, 5"):
            loop = {"digitCode": "1…5"}
        elif s == "end" and not line.startswith(" "):
            loop = {}
        m = re.match(r'^bind\((.+?),\s*(.*)$', s)
        if not m or current is None:
            continue
        keys = key_expr(m.group(1), loop)
        rest = m.group(2)
        comment = ""
        cm = re.search(r"\)\s*--\s*(.+)$", rest)
        if cm:
            comment, rest = cm.group(1), rest[:cm.start() + 1]
        if rest.startswith("function"):   # inline function: describe from its body
            body = []
            for nxt in lines[i + 1:]:
                if nxt.strip().startswith("end"): break
                body.append(nxt.strip())
            rest = " ".join(body)
        keys = pretty_keys(keys)
        desc = describe(rest, comment)
        if (keys, desc) in seen:
            continue
        seen.add((keys, desc))
        current[1].append((keys, desc))
    return [s for s in sections if s[1]]

def read_apps():
    rows = []
    try:
        for line in open(APPS):
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == 3:
                rows.append((pretty_keys(parts[0]), f"Jump to / open {parts[2].split()[0]}"))
    except OSError:
        pass
    return rows

def read_wsgroups():
    rows = [("Alt + 1…0", "Switch to window N in this workspace"),
            ("Super + Ctrl + 1…0", "Go to workspace, launch its apps if needed"),
            ("Super + N", "New window of this workspace's app")]
    try:
        for line in open(WSGROUPS):
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[0].isdigit():
                rows.append((f"Workspace {parts[0]}", f"{parts[1]} — {parts[2]}"))
    except OSError:
        return []
    return rows

def sections():
    out = read_binds()
    apps = read_apps()
    if apps:
        out.insert(1, ("App Hotkeys (apps.conf)", apps))
    groups = read_wsgroups()
    if groups:
        out.insert(2 if apps else 1, ("Workspace Groups (workspaces.conf)", groups))
    return out

# ---- drawing -----------------------------------------------------------------------------------
def build(query):
    rows = []
    words = query.lower().split()
    for title, items in sections():
        hits = [(k, d) for k, d in items if all(w in f"{k} {d}".lower() for w in words)]
        if not hits: continue
        if rows: rows.append(("", ""))
        rows.append(("#", title))
        rows.extend(hits)
    return rows

def render(rows, top, query):
    cols, height, w, pad = frame(90)
    kw = min(max((len(k) for k, _ in rows if k and k != "#"), default=10) + 3, w // 2)
    count = f"{len([r for r in rows if r[0] and r[0] != '#'])} shortcuts"
    box = f"{FG}{query}▏{RESET}" if query else f"{FG}▏{RESET}{DIM}type to search…{RESET}"
    search = [f"{ACCENT}\uf002{RESET}  {box}", ""]
    body = max(height - CHROME - len(search), 1)
    lines = list(search)
    if not rows:
        lines.append(f"{DIM}No shortcut matches “{query}”{RESET}")
    for key, desc in rows[top:top + body]:
        if key == "#":
            lines.append(section(desc, w))
        elif key:
            lines.append(f"{KEY}{key:<{kw}}{RESET}{FG}{fit(desc, w - kw)}{RESET}")
        else:
            lines.append("")
    more = f"{top + 1}–{min(top + body, len(rows))} of {len(rows)}" if len(rows) > body else ""
    foot = spread(hints([("↑↓", "scroll"), ("esc", "clear" if query or CARD else "close")], w), f"{DIM}{more}{RESET}", w)
    head = header("\U000f030c", "Shortcuts", count, w)
    return card(head, lines, foot, height, pad), body

class ShortcutsPanel(Panel):
    interval = 5.0  # binds.lua / apps.conf are re-read this often

    def __init__(self):
        self.query, self.top, self.body = "", 0, 10

    def tick(self):
        self.rows = build(self.query)

    def draw(self, w, h):
        text, self.body = render(self.rows, self.top, self.query)
        return text

    def key(self, k):
        last = max(len(self.rows) - self.body, 0)
        if k == "ESC":
            if not self.query:
                return "quit"
            self.query, self.top = "", 0
        elif k == "BACKSPACE":            self.query, self.top = self.query[:-1], 0
        elif k == "\x15":                 self.query, self.top = "", 0          # Ctrl+U
        elif k in ("DOWN", "WHEELDOWN"):  self.top = min(self.top + 1, last)
        elif k in ("UP", "WHEELUP"):      self.top = max(self.top - 1, 0)
        elif k == "PGDN":                 self.top = min(self.top + self.body, last)
        elif k == "PGUP":                 self.top = max(self.top - self.body, 0)
        elif k == "HOME":                 self.top = 0
        elif k == "END":                  self.top = last
        elif isinstance(k, str) and len(k) == 1 and k.isprintable():
            self.query, self.top = self.query + k, 0
        self.rows = build(self.query)


if __name__ == "__main__":
    run(ShortcutsPanel())
