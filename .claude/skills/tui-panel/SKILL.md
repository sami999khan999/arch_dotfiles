---
name: tui-panel
description: Build or change a terminal panel in this dotfiles repo — the Control Center cards (sysmon, keys, audio, network, wsgroups), the sync panel, or a new popup — using the shared panelkit.py framework. Use when a feature needs a small GUI.
---

# TUI panels (panelkit)

All panels are Python in `local/lib/panels/` (linked to `~/.local/lib/panels`), drawn with ANSI in a kitty window, sharing
`panelkit.py` for the look (Tokyo Night) and the terminal plumbing.

## A panel

```python
from panelkit import (Panel, run as show, FG, DIM, ACCENT, GREEN, RED, BOLD, RESET,
                      frame, card, header, section, spread, hints, fit, highlight)

class ThingPanel(Panel):
    interval = 1.0                       # seconds between tick()s
    def tick(self): …                    # refresh data (keep it cheap; slow work → a thread)
    def draw(self, w, h):
        cols, rows, width, pad = frame(90)          # content width, centred
        head = header("\U000f0000", "Thing", "detail on the right", width)
        body = [section("Part", width), f"{DIM}key{RESET}  {FG}value{RESET}", …]
        foot = hints([("s", "do it"), ("q", "close")], width)
        return card(head, body, foot, rows, pad)
    def key(self, k):                    # "UP", "ENTER", "ESC", "q", ("CLICK", col, row)…
        if k in ("q", "ESC"): return "quit"

if __name__ == "__main__":
    show(ThingPanel())
```

- Every card has the same rows: header + accent underline, blank, body, blank, key hints.
- Use `spread()` for left/right on one line, `fit()` to cut text, `highlight()` for a selected row,
  `clip()` for exact-width coloured text (two-column layouts: see `syncpanel.py`).
- Run a full-screen program and come back: `suspend(["nmtui"])`.
- Import code from a file without `.py` (e.g. `~/.local/bin/codesync`) with
  `importlib.machinery.SourceFileLoader`, as `syncpanel.py` and `controlcenter.py` do.

## Showing it

- Popup: `~/.config/hypr/scripts/tui.sh ~/.local/lib/panels/thing.py` (second run closes it).
  Window: class `TUI.float`, title `thing.py`, 875×600 by default. Other size: a rule in
  `config/hypr/modules/windowrules.lua` matching `tag = "floating-window", title = "^(thing\\.py)$"`.
- Bind it in `binds.lua` (`tui(panels .. "thing.py")`) or a waybar `on-click`.
- Control Center card: add the panel to `cards` and to `layout()` in `controlcenter.py`
  (top row: two halves; bottom row: three equal columns). Inside the Control Center
  `CONTROL_CENTER=1` makes cards draw edge to edge.

## Verify

Open it and screenshot (`grim /tmp/p.png`), then close it with the same `tui.sh` command.
Restart the running Control Center after changing a card:
`pkill -f 'python3 [/a-z.]*[c]ontrolcenter\.py'` (its `panel.sh` loop restarts it). If the window
itself closed: `~/.local/bin/wsgroups launch 10 --background`. Don't use a `pkill -f` pattern that
also matches your own command line.
