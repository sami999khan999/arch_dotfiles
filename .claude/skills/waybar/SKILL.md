---
name: waybar
description: Add, move or restyle a waybar module in this dotfiles repo (config/waybar), including custom script-driven modules, and verify it visually. Use for any change to the top bar.
---

# Waybar

One full-width strip (no outer margins, 1px line along the bottom), square corners, Tokyo Night. Layout in `config/waybar/config.jsonc`:

```
left:   logo │ workspace buttons (custom/ws1…10) │
centre: audio visualizer (custom/visualizer: cava, only while sound plays), filling the gap
        between the sides; it may shrink (max-length), so it can never push them
right:  │ media (play/pause, only while playing/paused) │ group label + window title in one block
        (custom/window, max 30 chars in wintitle.py, hidden on an empty workspace) │
        codesync, keys, tray, bt, net, volume │ stats (cpu mem temp battery) │
        clock (custom/clock: scripts/clock.py) + notification bell (custom/notifications:
        scripts/notifications.py; always shown) + idle indicator │ power (far right)
```

## Adding a module

1. Add its name to `modules-left/center/right` and its block to `config.jsonc`.
2. In `style.css` (above the colour theme's `@import`, which stays last), add its `#id` to the shared chip rule (padding/margin/transition) and the
   shared `:hover` rule near the top, then any specific colours below. Use the `@define-color`
   tokens (`@muted`, `@subtext`, `@text`, `@accent`, `@alert`, `@overlay`, `@line`), not hex.
3. Section dividers are `border-left/right: 1px solid alpha(@line, 0.7)` on the first/last module
   of a section (see `#stats`, `#custom-wsgroups`, `#custom-clock`, `#mpris`).

## Custom (script) modules

- `"return-type": "json"` and print one JSON line per update:
  `{"text": …, "class": …, "tooltip": …}`. Empty `text` hides the module.
- Event-driven: the script stays running and prints on each change (see `scripts/wintitle.py`,
  which follows Hyprland's `.socket2.sock`). Poll-driven: `"interval": N`. Push-driven:
  `"interval": "once"` + `"signal": N`, refreshed with `pkill -RTMIN+N -x waybar`.
- Signals taken: 8 workspace buttons, 9 idle, 10 notifications, 11 codesync.
- Clicks that dispatch to Hyprland must use Lua syntax:
  `"on-click": "hyprctl dispatch 'hl.dsp.focus({ workspace = \"1\" })'"`.
  Waybar's built-in Hyprland click actions send legacy commands and silently fail.
- Scripts live in `config/hypr/scripts/` (a panel it opens lives in `local/lib/panels/`); `chmod +x` them.

## Known quirks

- The clock is `custom/clock` (`scripts/clock.py`), not waybar's `clock`: the visualizer has to
  know whether the long view (click) is on. Its tooltip calendar is built there too.
- `mpris` `tooltip-format` is plain text; tags show up literally.
- GTK ellipsizes a `max-length` label before it moves the centre island, and an ellipsizing label
  in the left section can't cross the middle of the bar. Truncate in the script instead
  (`wintitle.py`, `MAX`).
- waybar runs as its packaged systemd user service (`autostart.lua`: `systemctl --user start
  waybar.service`, `Restart=on-failure`): a crash brings it back by itself. Restart it with
  `systemctl --user restart waybar`, never `pkill -x waybar` (a clean stop: systemd leaves it off)
  nor `uwsm app -- waybar` (a second bar outside the service).
- A live `pkill -SIGUSR2 waybar` can crash waybar, at once (bar geometry: `margin-*`, `height`,
  `layer`, `position`) or minutes later (it segfaulted in glibmm's dispatcher after reloads that
  restarted the custom scripts). Prefer `systemctl --user restart waybar` to apply a change.
- **The power button never moves.** The visualizer is the centre module with max-length: GTK
  places a centre module in the space the sides leave (off-centre if needed) and shrinks it rather
  than push a side. Don't move it into a side section: there a shrinkable label stops at the bar's
  middle, and an unshrinkable one pushes the right section off when the bar runs out of room.
- To fill that space exactly (no `…`, no empty strip), `scripts/visualizer.py` measures the gap on
  the bar while the bars show: a one-pixel-high `grim` of the bar finds the divider after the
  workspace buttons and the right section's first divider (`#2F354D`, `DIVIDER`), every second and
  0.3 s after a window event. So the right side may change width freely; keep a divider at both
  ends of the gap. When it can't measure (silent, grim missing) it falls back to sums over what's
  on the right (title, group count, play button, indicators, clock view) from `GAP_REF`.
- Its recounts run under one lock: Pango isn't thread-safe, and two threads measuring text at once
  aborted the script (`fc_thread_func: code should not be reached`), freezing the bars.
- Check for a push: the power button's divider must be at x 1335 (`grim` the bar). Calibrate the
  visualizer's `GAP_REF` only from an un-pushed bar: measured gap minus the push.
- Signal the visualizer by its pid (`$XDG_RUNTIME_DIR/waybar-visualizer.pid`), never `pkill -f`:
  a name match also hits an editor that has the file open.

## Apply and verify

```bash
systemctl --user restart waybar         # config + CSS + custom scripts, all fresh
sleep 2; grim -g "0,0 1366x34" /tmp/bar.png
```

Look at the screenshot (zoom: `magick /tmp/bar.png -crop 480x34+886+0 -scale 250% /tmp/z.png`).
For a JSONC syntax check: strip `//` comment lines and `json.loads` it in Python.
