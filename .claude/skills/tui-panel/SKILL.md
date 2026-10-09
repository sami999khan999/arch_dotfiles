---
name: tui-panel
description: Build or change a terminal panel in this dotfiles repo — the Control Center cards (sysmon, keys, audio, network, wsgroups), the sync panel, or a new popup — using the shared panelkit.py framework. Use when a feature needs a small GUI.
---

# TUI panels (panelkit)

All panels are Python in `local/lib/panels/` (linked to `~/.local/lib/panels`), drawn with ANSI in a kitty window, sharing
`panelkit.py` for the look (Tokyo Night) and the terminal plumbing.

**GUI panels are what's in use now** (GTK 4, `*gui.py`); the terminal ones below are the older
versions. A GUI panel is a `gtkkit.View`: `build()` returns the content, `refresh()` runs every
`interval` s while shown (a workspace app's pauses while its workspace is hidden: `hidden_refresh()`
instead, `shown_changed(shown)` when that flips; a view updated by events sets `interval = 0` and calls
`self.on_change()` if set, so its Control Center card's header follows), `say()` writes the status line, `close()` closes a popup (no-op in the
Control Center), `css` adds view-specific style. Each module has `make()` (the view; `ccgui.py`
uses it) and `main()` → `run(make(), "panels.<name>", (W, H))`. The app id is the window class:
`modules/windowrules.lua` floats and centres every `panels.*` window (add a size rule per panel);
running a popup again closes it (single-instance app), so binds call it directly — no tui.sh.
Workspace apps (Docker, Control Center) use `toggle=False` and an id outside `panels.`
(`sami.docker`, `sami.controlcenter`) so they tile and count on their workspace.
Style (the user's call): the look of the Control Center cards everywhere — icon + bold title with
dim details on the right and a blue-then-grey underline (gtkkit draws it), blue section headings
with a rule (`rule_heading`), amber for keys and window classes, green / amber / red for levels
(`level()`), solid #292e42 selection, key hints with " · " on the last line. Storage tab: per drive a Space
bar (partition map) and an Activity bar (I/O busy % from /proc/diskstats), both in level colours.
Colours are always Tokyo Night's (the colour theme swaps them: AGENTS.md "Colours"): a view's `css` is
recolored by gtkkit; Pango markup, text-tag colours and ANSI need `recolor("#565f89")`, cairo `rgbf("#6b8fe0")`
(both from gtkkit). Check a panel in another theme off-screen with `DOTFILES_THEME=crimson`.
Big live lists (ColumnView): don't replace every row on each refresh (`store.splice` of new
objects cost ~30 % of a core for ~300 processes); keep the row objects, update their data and emit
a per-row signal the bound cells listen to (see `Proc` in `sysgui.py`), then
`sorter.changed(DIFFERENT)`. GTK keeps the top visible row in view while rows move, so after a
re-sort `scroll_to(0, …)` if the list was at the top.
Check one without putting a window on the user's screen: run it under `gtk4-broadwayd :7` with
`GDK_BACKEND=broadway BROADWAY_DISPLAY=:7`, snapshot the window with `Gtk.WidgetPaintable` and save
the texture as PNG.

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
