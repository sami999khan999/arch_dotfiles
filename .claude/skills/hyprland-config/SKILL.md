---
name: hyprland-config
description: Edit the Hyprland Lua config in this dotfiles repo — keybinds, window rules, workspace rules, autostart, look and feel — and verify it loads. Use for any change under config/hypr/ (except workspace groups and panels, which have their own skills).
---

# Hyprland config (Lua, Hyprland 0.56)

`config/hypr/hyprland.lua` only `require`s modules from `config/hypr/config/`. Put a change in the
module it belongs to:

| Module | Holds |
|---|---|
| `variables.lua` | `TERMINAL`, `BROWSER`, `FILE_MANAGER`, `MONITOR1`… (globals used everywhere) |
| `binds.lua` | keybindings (local helper `bind(keys, action, opts)`) |
| `apps.lua` + `../apps.conf` | app hotkeys: edit `apps.conf`, not the Lua |
| `wsgroups.lua` + `../workspaces.conf` | workspace groups → use the `workspace-groups` skill |
| `windowrules.lua` | window and layer rules |
| `workspaces.lua` | extra workspace rules (1–10 come from `wsgroups.lua`) |
| `autostart.lua` | `hl.on("hyprland.start", …)` launches |
| `colors.lua`, `decorations.lua`, `animations.lua`, `inputs.lua`, `misc.lua`, `monitors.lua` | as named |

API reference: `/usr/share/hypr/stubs/hl.meta.lua` (event names, `HL.Window` fields, config keys).

## Keybinds

```lua
bind("SUPER + CTRL + M", launch .. "mpv")            -- open mpv
bind("SUPER + J", hl.dsp.layout("togglesplit"))
```

- Digits are bound by keycode (AZERTY-safe): `digitCode(n)` → `code:10`…`code:19`.
- End the line with `-- description`; `scripts/keys.py` (Super + K) shows it.
- Before adding, search for the key in `binds.lua`, `apps.conf` and `wsgroups.lua` (Alt+digits,
  Super+Ctrl+digits and Super+N are taken there). Both actions fire on a duplicate.
- Launch GUI apps through `launch` (`uwsm app -- `). TUIs in a floating terminal: `tui("cmd")`.

## Dispatching from scripts

Legacy syntax is rejected. Always Lua:

```bash
hyprctl dispatch 'hl.dsp.focus({ workspace = "3" })'
hyprctl dispatch "hl.dsp.window.fullscreen_state({ internal = 1, client = 0, window = \"address:$a\" })"
```

Query with `hyprctl clients -j`, `activewindow -j`, `workspaces -j`, `workspacerules -j` + `jq`.

## Window rules — traps

- `fullscreen_state` in a rule pins the state for the window's lifetime: the app can't go real
  fullscreen afterwards. For "maximize when it opens" use an event:
  `hl.on("window.open", function(w) … hl.dispatch(hl.dsp.window.fullscreen_state({…})) end)`.
- Rules that match through a tag (e.g. `tag = "floating-window"`, set by an earlier rule) apply
  later than plain ones. To override such a window's size, match `tag = "floating-window"` too.
- TUI popups from `scripts/tui.sh` have class `TUI.float` and title = the command's basename
  (`wiremix`, `syncpanel.py`), which is what size rules match on.

## Apply and verify

```bash
hyprctl reload && sleep 1 && hyprctl configerrors   # must print nothing
```

Then check the effect (`hyprctl clients -j | jq …`, or a `grim` screenshot). Workspace rules merge
per workspace; confirm with `hyprctl workspacerules -j`.
