# panelkit.py — the shared look and plumbing of the Control Center cards (Tokyo Night).
#
# A card is a Panel: tick() refreshes its data, draw(w, h) returns its screen, key(k) handles a
# key. run(panel) shows one card on its own (the Super + K / sysmon popups); controlcenter.py
# draws all of them in one window. Every card has the same rows:
#
#   row 0     icon  Title                      details
#   row 1     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   row 2
#   row 3…    body (cut to fit: CHROME rows fewer than the card)
#   …         blank
#   last row  key hints / status
import os, select, shutil, subprocess, sys, termios, time, tty

CARD = os.environ.get("CONTROL_CENTER") == "1"  # drawn inside the Control Center: no margins
CHROME = 5  # rows around the body: header (2) + blank above, blank + footer below
SIZE = None  # (cols, rows) of the card being drawn; None = the whole terminal

def rgb(h): return f"\033[38;2;{int(h[1:3],16)};{int(h[3:5],16)};{int(h[5:7],16)}m"
def bg(h): return f"\033[48;2;{int(h[1:3],16)};{int(h[3:5],16)};{int(h[5:7],16)}m"

FG, DIM, ACCENT, KEY, TRACK = rgb("#c0caf5"), rgb("#565f89"), rgb("#6b8fe0"), rgb("#e0af68"), rgb("#292e42")
GREEN, YELLOW, RED, CYAN, MAGENTA = rgb("#9ece6a"), rgb("#e0af68"), rgb("#f7768e"), rgb("#7dcfff"), rgb("#bb9af7")
BORDER = rgb("#3b4261")
SELBG = bg("#292e42")
BOLD, RESET = "\033[1m", "\033[0m"


# ---- text --------------------------------------------------------------------------------------
def visible_len(text):
    out, skip = 0, False
    for ch in text:
        if ch == "\033": skip = True
        elif skip and ch == "m": skip = False
        elif not skip: out += 1
    return out


def clip(text, n):
    """Coloured text cut or padded to exactly n visible columns."""
    out, seen, skip = [], 0, False
    for ch in text:
        if ch == "\033": skip = True
        if skip:
            out.append(ch)
            if ch == "m": skip = False
            continue
        if seen == n:
            break
        out.append(ch)
        seen += 1
    return "".join(out) + RESET + " " * (n - seen)


def fit(text, n):
    """Plain text cut to n columns, with … when it doesn't fit."""
    return text if len(text) <= n else text[:max(n - 1, 0)] + "…"


def spread(left, right, w):
    """left and right on one line of width w."""
    return left + " " * max(w - visible_len(left) - visible_len(right), 1) + right


# ---- card parts --------------------------------------------------------------------------------
def frame(cap=90):
    """(cols, rows, w, pad): content width w, centred with pad, in the card's cols x rows.
    Inside the Control Center a card uses its full width; the frame around it is the margin."""
    cols, rows = SIZE or shutil.get_terminal_size()
    w = cols if CARD else max(min(cols - 4, cap), 10)
    return cols, rows, w, " " * max((cols - w) // 2, 0)


def card(head, body, footer, rows, pad, center=False):
    """Screen text for a card: header, blank row, body, blank row, footer on the last row.
    center: a popup with room to spare sits in the middle of the terminal instead."""
    room = max(rows - CHROME, 0)
    body = body[:room]
    if center and not CARD:
        out = head + [""] + body + [""] + [footer]
        return "\n" * max((rows - len(out)) // 2, 0) + "\n".join(pad + l for l in out)
    out = head + [""] + body + [""] * (room - len(body) + 1) + [footer]
    return "\n".join(pad + l for l in out[:rows])


def header(icon, title, sub, w):
    """Card title: icon + bold title, dim details on the right, then an underline whose
    first part (under the title) is the accent colour."""
    head = spread(f"{ACCENT}{icon}{RESET}  {FG}{BOLD}{title}{RESET}", f"{DIM}{sub}{RESET}", w)
    lead = len(title) + 3
    return [head, f"{ACCENT}{'━' * lead}{TRACK}{'━' * (w - lead)}{RESET}"]


def section(title, w, right=""):
    """Sub-heading: accent title, a thin rule, optional dim text at the end."""
    right = f" {DIM}{right}{RESET}" if right else ""
    rule = w - len(title) - 1 - visible_len(right)
    return f"{ACCENT}{BOLD}{title}{RESET} {TRACK}{'─' * max(rule, 0)}{RESET}{right}"


def hints(pairs, w):
    """Footer of key hints: 'key label' pairs, keys bright, labels dim; drops what doesn't fit."""
    out, used = [], 0
    for key, label in pairs:
        n = len(key) + 1 + len(label) + (3 if out else 0)
        if used + n > w:
            break
        out.append(f"{FG}{key}{RESET} {DIM}{label}{RESET}")
        used += n
    return f"{TRACK} · {RESET}".join(out)


def highlight(row, w):
    """A selected row: subtle background across the full width."""
    return SELBG + row.replace(RESET, RESET + SELBG) + " " * max(w - visible_len(row), 0) + RESET


# ---- panels and the terminal -------------------------------------------------------------------
class Panel:
    interval = 1.0                   # seconds between tick()s

    def tick(self): pass             # refresh data
    def draw(self, w, h): return ""  # the screen, as lines joined by \n
    def key(self, k): pass           # return "quit" to close a standalone card


_term = None  # (fd, termios settings to restore) while a screen is up


def enter_screen():
    # alt screen, no cursor, no line wrap (long lines are clipped), mouse: clicks + wheel
    sys.stdout.write("\033[?1049h\033[?25l\033[?7l\033[?1000h\033[?1006h")
    sys.stdout.flush()


def leave_screen():
    sys.stdout.write("\033[?1000l\033[?1006l\033[?7h\033[?25h\033[?1049l")
    sys.stdout.flush()


def suspend(argv):
    """Run a full-screen program (nmtui, wiremix) in place of the cards, then come back."""
    fd, old = _term
    leave_screen()
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    try:
        subprocess.run(argv)
    finally:
        tty.setcbreak(fd)
        enter_screen()


KEYS = {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT", "[5~": "PGUP", "[6~": "PGDN",
        "[H": "HOME", "[F": "END", "[1~": "HOME", "[4~": "END", "[Z": "BTAB", "[3~": "DEL"}


def read_key(fd):
    """One key: a character, or UP DOWN LEFT RIGHT PGUP PGDN HOME END ENTER ESC TAB BTAB
    BACKSPACE WHEELUP WHEELDOWN, or ("CLICK", col, row) counted from 0."""
    ch = os.read(fd, 1)
    if ch in (b"\r", b"\n"): return "ENTER"
    if ch == b"\t": return "TAB"
    if ch in (b"\x7f", b"\b"): return "BACKSPACE"
    if ch != b"\x1b":
        while ch and ch[0] >= 0xC0 and len(ch) < 4:  # the rest of a UTF-8 character
            try:
                return ch.decode()
            except UnicodeDecodeError:
                ch += os.read(fd, 1)
        return ch.decode(errors="ignore")
    if not select.select([fd], [], [], 0.03)[0]:
        return "ESC"
    seq = os.read(fd, 32).decode(errors="ignore")
    if seq.startswith("[<"):  # SGR mouse: [<button;col;row M(press)/m(release)
        try:
            b, x, y = seq[2:-1].split(";")
            b, x, y = int(b), int(x) - 1, int(y) - 1
        except ValueError:
            return ""
        if b == 64: return "WHEELUP"
        if b == 65: return "WHEELDOWN"
        return ("CLICK", x, y) if b == 0 and seq.endswith("M") else ""
    return KEYS.get(seq, "")


def run(panel):
    """Show one panel full screen until its key() says "quit" (or Ctrl+C)."""
    global _term
    fd = sys.stdin.fileno()
    _term = (fd, termios.tcgetattr(fd))
    tty.setcbreak(fd)
    enter_screen()
    try:
        panel.tick()
        last = time.time()
        while True:
            cols, rows = shutil.get_terminal_size()
            sys.stdout.write("\033[H\033[2J" + panel.draw(cols, rows))
            sys.stdout.flush()
            wait = max(panel.interval - (time.time() - last), 0)
            if select.select([fd], [], [], wait)[0]:
                if panel.key(read_key(fd)) == "quit":
                    break
            if time.time() - last >= panel.interval:
                panel.tick()
                last = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        leave_screen()
        termios.tcsetattr(fd, termios.TCSADRAIN, _term[1])
