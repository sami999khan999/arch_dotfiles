#!/usr/bin/env python3
# term.py — the little terminal UI layer agentmux's screens (sidebars, home) draw with: exact
# Tokyo Night in 24-bit colour (curses only has the 256-colour approximations), the panels' look as
# building blocks (header with the blue-then-grey underline, rule headings, selection rows, key hints),
# raw keyboard + mouse input, and a loop that sleeps in select() until a key, a resize, a signal or one
# of the app's own file descriptors (the sidebars' tmux control client) has something.
import os, re, select, signal, sys, termios, time, traceback, tty, unicodedata

LOG = os.path.expanduser("~/.cache/agentmux/errors.log")


def log_error():
    """An exception in one event goes to ~/.cache/agentmux/errors.log; the screen keeps running."""
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"--- {time.strftime('%F %T')} {os.path.basename(sys.argv[0])} {' '.join(sys.argv[1:])}\n")
        traceback.print_exc(file=f)

# Spacing, the same on every screen: PAD columns of margin left and right; a header on rows 0-1 (title,
# underline), a blank row, content from row 3; the bottom row is the key hints, the one above it the
# message line.
PAD = 2
TOP = 3

# the theme (AGENTS.md "Colours"; the GTK panels use the same)
T = {"bg": "#16161e", "text": "#c0caf5", "sub": "#a9b1d6", "muted": "#565f89", "line": "#3b4261",
     "overlay": "#292e42", "hover": "#1f2233", "accent": "#6b8fe0", "green": "#9ece6a", "amber": "#e0af68", "red": "#f7768e",
     "cyan": "#7dcfff"}


def _rgb(hexcolour):
    h = T.get(hexcolour, hexcolour).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def sgr(fg=None, bg=None, bold=False):
    codes = ["0"]
    if bold:
        codes.append("1")
    if fg:
        codes.append("38;2;%d;%d;%d" % _rgb(fg))
    if bg:
        codes.append("48;2;%d;%d;%d" % _rgb(bg))
    return "\x1b[" + ";".join(codes) + "m"


def width(s):
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return w


def fit(s, w):
    """s cut to w cells, with … when cut."""
    if w <= 0:
        return ""
    if width(s) <= w:
        return s
    out, used = "", 0
    for ch in s:
        cw = width(ch)
        if used + cw > w - 1:
            break
        out += ch
        used += cw
    return out + "…"


class Line:
    """One screen row: text segments, and a background the rest of the row is filled with."""

    def __init__(self, bg=None):
        self.bg = bg
        self.parts = []   # (text, fg, bg, bold)

    def add(self, text, fg="text", bg=None, bold=False):
        self.parts.append((text, fg, bg or self.bg, bold))
        return self

    def pad(self, n):
        return self.add(" " * max(n, 0))

    @property
    def w(self):
        return sum(width(t) for t, *_ in self.parts)

    def right(self, text, fg="muted", total=0, bold=False, margin=PAD):
        """text at the right end of a row total cells wide."""
        self.pad(total - self.w - width(text) - margin)
        return self.add(text, fg, bold=bold)

    def render(self, total):
        out, used = "", 0
        for text, fg, bg, bold in self.parts:
            if used >= total:
                break
            text = fit(text, total - used) if used + width(text) > total else text
            out += sgr(fg, bg, bold) + text
            used += width(text)
        return out + sgr(None, self.bg) + " " * max(total - used, 0) + "\x1b[0m"


# ---- the panels' building blocks --------------------------------------------------------------
CLOSE = "×"


def header(icon, title, detail, total, closable=False):
    """Two rows: icon + bold title with dim detail at the right (and a × that closes the panel), then
    the underline that's blue under the icon and title and grey after (as on every GTK panel and
    Control Center card). Click the × = column total - PAD - 1 on row 0."""
    lead = (f"{icon}  " if icon else "") + title
    top = Line().pad(PAD).add(f"{icon}  " if icon else "", "accent").add(title, "text", bold=True)
    tail = (detail + "  " if detail else "") + (CLOSE if closable else "")
    if tail:
        top.right(detail + ("  " if detail and closable else ""), "muted", total,
                  margin=PAD + (1 if closable else 0)) if detail else top.pad(total - top.w - PAD - 1)
        if closable:
            top.add(CLOSE, "muted")
    rest = total - 2 * PAD - width(lead)
    under = Line().pad(PAD).add("━" * width(lead), "accent").add("━" * max(rest, 0), "overlay")
    return [top, under]


def is_close(click, total):
    """True if a ("click", x, y) hit the header's ×."""
    return click[2] == 0 and click[1] >= total - PAD - 2


def rule(title, total, fg="accent"):
    """A section heading: bold title and a thin rule to the right margin."""
    return Line().pad(PAD).add(title + " ", fg, bold=True).add("─" * max(total - 2 * PAD - width(title) - 1, 0), "line")


def hints(pairs, total):
    """Key hints: keys bright, what they do dim, a dark " · " between."""
    l = Line().pad(PAD)
    for i, (k, what) in enumerate(pairs):
        if i:
            l.add(" · ", "line")
        l.add(k, "text").add(" " + what, "muted")
    return l


def marked(line, text, hits, fg, bg=None, bold=False, hit_fg="accent"):
    """Add text to line with the characters at hits (fuzzy matches) in hit_fg."""
    run, on = "", None
    for i, ch in enumerate(text):
        h = i in hits
        if on is not None and h != on:
            line.add(run, hit_fg if on else fg, bg, bold or on)
            run = ""
        run += ch
        on = h
    if run:
        line.add(run, hit_fg if on else fg, bg, bold or on)
    return line


def entry(w, sel, glyph, gfg, name, details=(), hits=(), fg="sub", bold=True):
    """One item of a list as the Projects / Threads sidebars draw theirs: a glyph and the name, dim
    detail lines under it ([(text, fg, hits)]), a blue edge and the overlay band when selected.
    Returns its lines; the caller adds a blank one between items."""
    bg = "overlay" if sel else None
    bar = ("▎", "accent") if sel else (" ", None)
    l = Line(bg).add(*bar).pad(PAD - 1).add(glyph, gfg).add(" ")
    marked(l, fit(name, max(w - l.w - PAD, 1)), hits, "text" if sel else fg, bg, bold=bold)
    out = [l]
    for text, dfg, dhits in details:
        d = Line(bg).add(*bar).pad(PAD + 1)
        marked(d, fit(text, max(w - d.w - PAD, 0)), dhits, dfg, bg, hit_fg="sub")
        out.append(d)
    return out


# ---- the app loop ------------------------------------------------------------------------------
KEYS = {"\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left", "\x1bOA": "up", "\x1bOB": "down",
        "\x1bOC": "right", "\x1bOD": "left", "\x1b[H": "home", "\x1b[F": "end", "\x1b[1~": "home", "\x1b[4~": "end",
        "\x1b[5~": "pgup", "\x1b[6~": "pgdn", "\x1b[3~": "delete", "\x1b[Z": "btab", "\x1b\r": "alt-enter",
        "\x1b[13;2u": "shift-enter", "\x1b[13;3u": "alt-enter", "\x1b[13;5u": "ctrl-enter", "\x1b[27;2;13~": "shift-enter",
        "\x1b[I": "focus-in", "\x1b[O": "focus-out",
        "\r": "enter", "\n": "ctrl-j", "\t": "tab", "\x7f": "backspace", "\x08": "backspace", "\x15": "ctrl-u",
        "\x17": "ctrl-w", "\x03": "ctrl-c", "\x1b": "esc"}
MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
CSI = re.compile(r"\x1b(\[[0-9;<?]*[ -/]*[@-~]|O.|.)?")


class App:
    """Subclass: render(w, h) -> [Line], key(k) with k a name from KEYS, a printable character,
    ("click", x, y), ("wheel", ±1, x, y) or (motion = True) ("hover", x, y). self.cursor = (y, x) shows the text cursor there.
    self.watch(fd, callback) adds a file descriptor to wait on; self.quit = True ends run()."""
    cursor = None
    motion = False   # True: pointer moves arrive as ("hover", x, y) too (any-event mouse mode)
    still = False    # set in key(): nothing changed, skip the next redraw (a hover over the same row)
    focused = True   # the pane has the keyboard (focus events, which tmux sends with focus-events on)

    def __init__(self):
        self.quit = False
        self.fds = {}
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_w, False)
        for sig in (signal.SIGWINCH, signal.SIGUSR1):
            signal.signal(sig, lambda s, _f: self._wake(s))
        self.woken = set()

    def _wake(self, sig):
        self.woken.add(sig)
        try:
            os.write(self.wake_w, b"x")
        except OSError:
            pass

    def watch(self, fd, callback):
        self.fds[fd] = callback

    def on_signal(self):
        """SIGUSR1 arrived (agentmux: something changed). Override to reload."""

    def draw(self):
        try:
            w, h = os.get_terminal_size()
        except OSError:
            w, h = 80, 24
        lines = self.render(w, h)[:h]
        lines += [Line() for _ in range(h - len(lines))]
        out = "\x1b[?25l\x1b[H" + "\r\n".join(l.render(w) for l in lines)
        if self.cursor:
            out += f"\x1b[{self.cursor[0] + 1};{self.cursor[1] + 1}H\x1b[?25h\x1b[6 q"
        sys.stdout.write(out)
        sys.stdout.flush()

    def parse(self, data):
        keys, i = [], 0
        while i < len(data):
            m = MOUSE.match(data, i)
            if m:
                b, x, y, kind = int(m[1]), int(m[2]) - 1, int(m[3]) - 1, m[4]
                if b in (64, 65):
                    keys.append(("wheel", -1 if b == 64 else 1, x, y))
                elif b == 0 and kind == "M":
                    keys.append(("click", x, y))
                elif b == 35:   # the pointer moved, no button (motion = True)
                    keys.append(("hover", x, y))
                i = m.end()
                continue
            if data[i] == "\x1b":
                m = CSI.match(data, i)
                seq = m.group(0) if m else "\x1b"
                if seq == "\x1b" and data[i + 1:i + 2] == "\r":
                    seq = "\x1b\r"
                keys.append(KEYS.get(seq, "alt-" + seq[1] if len(seq) == 2 else seq))
                i += len(seq)
                continue
            ch = data[i]
            keys.append(KEYS.get(ch, ch))
            i += 1
        return keys

    def run(self):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        # alternate screen, no cursor, SGR mouse, focus in/out reports
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h\x1b[?1004h" + ("\x1b[?1003h" if self.motion else ""))
        try:
            while not self.quit:
                try:
                    if self.still:   # the last event changed nothing on screen
                        self.still = False
                    else:
                        self.draw()
                except Exception:
                    log_error()
                try:
                    ready, _, _ = select.select([fd, self.wake_r, *self.fds], [], [], self.timeout())
                except InterruptedError:
                    continue
                try:
                    self.handle(fd, ready)
                except Exception:
                    log_error()
        finally:
            sys.stdout.write("\x1b[?1003l\x1b[?1004l\x1b[?1006l\x1b[?1000l\x1b[?25h\x1b[0 q\x1b[?1049l")
            sys.stdout.flush()
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def handle(self, fd, ready):
        if not ready:
            self.tick()
        for r in ready:
            if r == self.wake_r:
                os.read(self.wake_r, 256)
                if signal.SIGUSR1 in self.woken:
                    self.on_signal()
                self.woken.clear()
            elif r == fd:
                data = os.read(fd, 4096).decode(errors="replace")
                if data == "\x1b":   # a lone Esc, or the start of a sequence still coming
                    more, _, _ = select.select([fd], [], [], 0.03)
                    if more:
                        data += os.read(fd, 4096).decode(errors="replace")
                for k in self.parse(data):
                    if k in ("focus-in", "focus-out"):
                        self.focused = k == "focus-in"
                        self.on_focus()
                        continue
                    self.key(k)
            else:
                self.fds[r]()

    def on_focus(self):
        """The pane gained or lost the keyboard (self.focused). Override if it matters."""

    def timeout(self):
        """Seconds to sleep without events before tick() (None: forever)."""
        return None

    def tick(self):
        pass
