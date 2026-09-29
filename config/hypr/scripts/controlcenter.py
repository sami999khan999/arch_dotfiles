#!/usr/bin/env python3
# controlcenter.py — the Control Center (workspace 10): all five cards in one terminal window.
#
#   ┌──────────────────────┐ ┌──────────────────────┐
#   │ Workspaces           │ │ System               │
#   └──────────────────────┘ └──────────────────────┘
#   ┌─────────────┐ ┌────────────┐ ┌─────────────┐
#   │ Shortcuts   │ │ Audio      │ │ Network     │
#   └─────────────┘ └────────────┘ └─────────────┘
#
# The cards sit on one character grid, so every gap is the same: one column between cards side
# by side, none between the rows (a border row is taller than a column is wide, so the two gaps
# come out nearly equal on screen). Inside each border, one column of padding on both sides.
#
#   Tab / Shift+Tab or a click   move between cards (the focused card has a blue border)
#   everything else             goes to the focused card
import importlib.machinery, importlib.util, os, select, shutil, sys, termios, time, traceback, tty

os.environ["CONTROL_CENTER"] = "1"  # before the cards import panelkit: they draw edge to edge
import panelkit
from panelkit import ACCENT, BORDER, DIM, RED, RESET, clip, enter_screen, leave_screen, read_key
from sysmon import SystemPanel
from keys import ShortcutsPanel
from audiopanel import AudioPanel
from netpanel import NetworkPanel


def load_wsgroups():
    path = os.path.expanduser("~/.local/bin/wsgroups")
    loader = importlib.machinery.SourceFileLoader("wsgroups", path)
    spec = importlib.util.spec_from_loader("wsgroups", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Card:
    def __init__(self, panel):
        self.panel, self.box, self.due = panel, (0, 0, 0, 0), 0.0

    def safe(self, fn, *args):
        """A card that crashes shows its error instead of taking the whole window down."""
        try:
            return fn(*args)
        except Exception:
            err = traceback.format_exc().strip().splitlines()[-1]
            return f"{RED}this card hit an error{RESET}\n{DIM}{err}{RESET}"


def layout(cards, cols, rows):
    """Place the cards: top row two halves (Workspaces, System), bottom row three equal columns."""
    base, extra = divmod(cols - 2, 3)
    widths = [base, base, base]
    if extra == 1:
        widths[1] += 1                     # the odd column goes in the middle: stays symmetric
    elif extra == 2:
        widths[0] += 1; widths[2] += 1
    xs = [0, widths[0] + 1, widths[0] + widths[1] + 2]
    top = rows // 2
    ws, system, shortcuts, audio, network = cards
    half = (cols - 1) // 2
    ws.box        = (0,        0, half, top)
    system.box    = (half + 1, 0, cols - half - 1, top)
    shortcuts.box = (xs[0], top, widths[0], rows - top)
    audio.box     = (xs[1], top, widths[1], rows - top)
    network.box   = (xs[2], top, widths[2], rows - top)


def paint(cards, focus, cols, rows):
    screen = [[] for _ in range(rows)]  # per row: (x, text) pieces
    for card in cards:
        x, y, w, h = card.box
        if w < 6 or h < 3:
            continue
        cw, ch = w - 4, h - 2
        panelkit.SIZE = (cw, ch)
        lines = card.safe(card.panel.draw, cw, ch).split("\n")
        lines += [""] * (ch - len(lines))
        color = ACCENT if card is focus else BORDER
        screen[y].append((x, f"{color}┌{'─' * (w - 2)}┐{RESET}"))
        for i in range(ch):
            screen[y + 1 + i].append((x, f"{color}│{RESET} {clip(lines[i], cw)} {color}│{RESET}"))
        screen[y + h - 1].append((x, f"{color}└{'─' * (w - 2)}┘{RESET}"))
    out = []
    for pieces in screen:
        line, at = "", 0
        for x, text in sorted(pieces):
            line += " " * (x - at) + text
            at = x + panelkit.visible_len(text)
        out.append(line + " " * max(cols - at, 0))
    sys.stdout.write("\033[H" + "\r\n".join(out))
    sys.stdout.flush()


def main():
    wsgroups = load_wsgroups()
    cards = [Card(p) for p in (wsgroups.UI(), SystemPanel(), ShortcutsPanel(), AudioPanel(), NetworkPanel())]
    focus = cards[0]
    fd = sys.stdin.fileno()
    panelkit._term = (fd, termios.tcgetattr(fd))
    tty.setcbreak(fd)
    enter_screen()
    size = None
    try:
        while True:
            now = time.time()
            for card in cards:
                if now >= card.due:
                    card.safe(card.panel.tick)
                    card.due = now + card.panel.interval
            cols, rows = shutil.get_terminal_size()
            if (cols, rows) != size:
                size = (cols, rows)
                layout(cards, cols, rows)
                sys.stdout.write("\033[2J")
            paint(cards, focus, cols, rows)
            wait = max(min(c.due for c in cards) - time.time(), 0)
            if not select.select([fd], [], [], wait)[0]:
                continue
            k = read_key(fd)
            if k in ("TAB", "BTAB"):
                focus = cards[(cards.index(focus) + (1 if k == "TAB" else -1)) % len(cards)]
            elif isinstance(k, tuple):  # a click focuses the card under it
                _, cx, cy = k
                focus = next((c for c in cards if c.box[0] <= cx < c.box[0] + c.box[2]
                              and c.box[1] <= cy < c.box[1] + c.box[3]), focus)
            else:
                card = focus
                card.safe(card.panel.key, k)  # "quit" means nothing here: the Control Center stays
                sys.stdout.write("\033[2J")  # a full-screen program (nmtui, wiremix) may have run
    except KeyboardInterrupt:
        pass
    finally:
        leave_screen()
        termios.tcsetattr(fd, termios.TCSADRAIN, panelkit._term[1])


if __name__ == "__main__":
    main()
