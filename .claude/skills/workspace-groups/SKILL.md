---
name: workspace-groups
description: Add, move, rename or remove a workspace group (one workspace per kind of app, e.g. Code on 1, Web on 2) in this dotfiles repo, keeping Hyprland, the waybar buttons and the README in step.
---

# Workspace groups

One source of truth: `config/hypr/workspaces.conf`, one line per group:

```
workspace | name | window class regex | launch command(s) ; … | new-window command (optional, - = none)
1 | Code     | code          | code ~/dotfiles | code --new-window
8 | Discord  | discord       | discord
10 | Control | controlcenter | kitty --config … -e …/panel.sh …/controlcenter.py | -
```

Readers of that file:

- `config/hypr/config/wsgroups.lua` — sends each class to its workspace, maximizes new windows
  once (not with `fullscreen_state`, which would block real fullscreen), makes workspaces 1–10
  persistent, binds Alt + 1…0 (window N here), Super + Ctrl + 1…0 (go/launch), Super + N.
- `local/bin/wsgroups` — the CLI/manager UI and the waybar group label (`wsgroups bar`).
- `config/hypr/scripts/wsbar.py` — the waybar workspace buttons; tooltips show the group name.

## Steps

1. Find the app's class: `hyprctl clients -j | jq -r '.[].class' | sort -u`.
2. Edit the line(s) in `workspaces.conf`. Workspace 10 is the `0` key.
3. Icon on the bar button: `ICONS` in `config/hypr/scripts/wsbar.py` maps workspace number →
   Nerd Font glyph (unmapped workspaces show their digit). Move icons with the groups.
4. If the Control Center (class `controlcenter`) moves, also update: the `gaps_out = 0` rule in
   `config/hypr/config/workspaces.lua`, `wsgroups launch N --background` in `autostart.lua`, and the
   "(workspace N)" comments in `controlcenter.lua`, `scripts/controlcenter.py`,
   `kitty/controlcenter.conf`.
5. Update the tables in `README.md` ("Workspace groups", "Control Center").
6. Apply:

```bash
hyprctl reload && sleep 1 && hyprctl configerrors
pkill -SIGUSR2 waybar
~/.local/bin/wsgroups tidy        # move already-open windows to their new workspaces
hyprctl clients -j | jq -r '.[] | "\(.workspace.id)\t\(.class)"' | sort -n
```

`tidy` has once left a window on the wrong workspace; check the list and run it again if needed.
Screenshot the left of the bar to confirm the icons: `grim -g "0,0 400x34" /tmp/ws.png`.
