---
name: workspace-groups
description: Add, move, rename or remove a workspace group (one workspace per kind of app, e.g. Agents on 1, Code on 2) in this dotfiles repo, keeping Hyprland, the waybar buttons and the README in step.
---

# Workspace groups

One source of truth: `config/hypr/workspaces.conf`, one line per group:

```
workspace | name | window class regex | launch command(s) ; … | new-window command (optional, - = none) | bar icon (optional)
1 | Code     | code          | code ~/dotfiles | code --new-window
8 | Discord  | discord       | discord
10 | Control | controlcenter | kitty --config … -e …/panel.sh …/controlcenter.py | -
```

Readers of that file:

- `config/hypr/modules/wsgroups.lua` — sends each class to its workspace, maximizes new windows
  once (not with `fullscreen_state`, which would block real fullscreen), makes workspaces 1–10
  persistent, binds Alt + 1…0 (window N here), Super + Ctrl + 1…0 (go/launch), Super + N.
- `local/bin/wsgroups` — the CLI, the waybar group label (`wsgroups bar`) and the terminal manager
  (`wsgroups tui`, also the Control Center card). The manager window is `local/lib/panels/wsgui.py`
  (GTK); `wsgroups` with no arguments opens it.
- `config/hypr/scripts/wsbar.py` — the waybar workspace buttons; each button's glyph is the 6th column.
- `config/hypr/modules/codepair.lua` — `PAIR_WS` (the scrolling layout for VS Code + kitty pairs) is
  the workspace whose classes include VS Code; `autostart.lua` launches the Control Center on
  `workspaceOfClass("sami.controlcenter")` (wsgroups.lua). Nothing else hard-codes a number.
- Settings → Workspaces (`page_workspaces` in `settingsgui.py`) edits, reorders (swap two, then
  `wsgroups tidy`, which also moves each VS Code kitty to its VS Code's workspace) and pins
  workspaces to screens (per PC: `settings.local.json` `ws_monitors` → `settings_local.lua`).

## Steps

1. Find the app's class: `hyprctl clients -j | jq -r '.[].class' | sort -u`.
2. Edit the line(s) in `workspaces.conf`. Workspace 10 is the `0` key.
3. Icon on the bar button: the 6th column (a Nerd Font glyph; empty shows the digit). It moves with
   its line, so a reorder needs nothing else.
4. Prefer Settings → Workspaces for all of this: it writes the file through `wsgroups`.
5. Update the tables in `README.md` ("Workspace groups", "Control Center").
6. Apply:

```bash
hyprctl reload && sleep 1 && hyprctl configerrors   # the bar buttons follow a reload by themselves
~/.local/bin/wsgroups tidy        # move already-open windows to their new workspaces
hyprctl clients -j | jq -r '.[] | "\(.workspace.id)\t\(.class)"' | sort -n
```

`tidy` has once left a window on the wrong workspace; check the list and run it again if needed.
Screenshot the left of the bar to confirm the icons: `grim -g "0,0 400x34" /tmp/ws.png`.
