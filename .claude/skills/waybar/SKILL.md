---
name: waybar
description: Add, move or restyle a waybar module in this dotfiles repo (config/waybar), including custom script-driven modules, and verify it visually. Use for any change to the top bar.
---

# Waybar

One full-width strip (no outer margins, 1px line along the bottom), square corners, Tokyo Night. Layout in `config/waybar/config.jsonc`:

```
left:   logo │ workspace buttons (custom/ws1…10) │ audio visualizer (custom/visualizer: cava, only
        while sound plays; styles bars / solid / mirror / dots / wave / off, Super + Alt + B), filling the gap
        to the right section; it may shrink (max-length), so it can never push it
right:  │ media (play/pause, only while playing/paused) │ group label + window title in one block
        (custom/window, max 22 chars in wintitle.py, hidden on an empty workspace) │
        codesync, the hub (one icon: Setups, System agent, keys, settings, bt, net, volume in its
        menu) │ stats (frame rate / Hz per screen, cpu mem temp battery) │
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
- Signals taken: 8 workspace buttons, 9 idle, 10 notifications, 11 codesync, 12 downloads, 13 mirror,
  14 lsend.
- Clicks that dispatch to Hyprland must use Lua syntax:
  `"on-click": "hyprctl dispatch 'hl.dsp.focus({ workspace = \"1\" })'"`.
  Waybar's built-in Hyprland click actions send legacy commands and silently fail.
- Scripts live in `config/hypr/scripts/` (a panel it opens lives in `local/lib/panels/`); `chmod +x` them.

## Known quirks

- The clock is `custom/clock` (`scripts/clock.py`), not waybar's `clock`: the visualizer has to
  know whether the long view (click) is on. Its hover calendar is drawn by bartips (below).
- **Tooltips are ours** (`bartips/bartips.c`, the `cffi/bartips` module: a C plugin in each waybar,
  compiled by `scripts/waybars.py` when the `.c` is newer than the gitignored `.so`). It stops GTK's
  tooltip and shows a layer-shell card (namespace `bartip`, blurred in `windowrules.lua`) built from the
  module's tooltip markup, so a module's `tooltip-format` / JSON `tooltip` is all you write. Shape it
  so the card reads well: `<b>Title</b>  <span alpha='55%'>Super + X</span>` is a title row with key caps;
  other lines are the body; a line that is all `<span alpha='55%'>…</span>` or starts with `Click:` /
  `Scroll:` goes under a rule as a hint. Styles: `.bartip-*` in `style.css`. A change to the `.c`:
  `systemctl --user restart waybar` rebuilds it; to see a card without hovering, include `bartips.c`
  in a small GTK3 program that calls `build_tooltip()` into a `GtkOffscreenWindow` under `broadwayd`.
- **Rich cards** (`bartips/cards.c`): a script whose tooltip is `bartip.card(blocks)` (`local/lib/hypr/bartip.py`)
  gets a card of blocks — `head` / `section`, `kv` rows, `meter`, `stack` (style `dimm`: a memory stick),
  `chip` (a GPU die with gauges), `bars` (a column per value), `spark` (a history), `table`, `text`, `rule`, `hints`. Colours are the `@define-color` names
  (`green`, `amber`, `alert`, `accent`…), so themes reach them. See `scripts/cpu.py`, `storage.py`, `memory.py`,
  `gpu.py`; render one off-screen by calling `build_rich()` in a small GTK3 program under `broadwayd`.
- **The hub** (`group/hub`, first module `custom/hub`): bartips moves the group's other modules off the
  bar into an offscreen window (waybar keeps updating them) and its card lists them; a row forwards its
  click / scroll to the module as a GDK event, so their `on-click` etc. stay in config.jsonc. To add a
  control there, add its module to `group/hub`; its tooltip's first line is the row's title, the next
  line its state.
- `mpris` `tooltip-format` is plain text; tags show up literally.
- GTK ellipsizes a `max-length` label before it moves the centre island, and an ellipsizing label
  in the left section can't cross the middle of the bar. Truncate in the script instead
  (`wintitle.py`, `MAX`).
- waybar runs as its packaged systemd user service (`autostart.lua`: `systemctl --user start
  waybar.service`, `Restart=on-failure`): a crash brings it back by itself. A drop-in
  (`waybar.service.conf`, linked by `setup/install.sh`) makes it run `scripts/waybars.py`: one waybar
  per screen (its own `-c $XDG_RUNTIME_DIR/waybar/<screen>.jsonc`, which includes `config.jsonc` and adds
  `output`), so SIGUSR1 to one pid hides one screen's bar (`modules/barhide.lua`). Signal every bar by
  name (`pkill -RTMIN+n -x waybar`) or with `waybarsig.send(n)`, which keeps all the pids. Restart it with
  `systemctl --user restart waybar`, never `pkill -x waybar` (a clean stop: systemd leaves it off)
  nor `uwsm app -- waybar` (a second bar outside the service).
- A live `pkill -SIGUSR2 waybar` can crash waybar, at once (bar geometry: `margin-*`, `height`,
  `layer`, `position`) or minutes later (it segfaulted in glibmm's dispatcher after reloads that
  restarted the custom scripts). Prefer `systemctl --user restart waybar` to apply a change.
- **The power button never moves.** The visualizer is the last module of the left section, with
  max-length: GTK shrinks it rather than push the right section. Don't fill the gap with several
  modules: a row of them has no such shrinking and pushes the right section off when it runs out of
  room.
- To fill the gap exactly (no `…`, no empty strip), `scripts/visualizer.py` measures it on the bar
  while the bars show: a one-pixel-high `grim` of the bar (`PROBE_Y` 2, above the bars, whose soft
  top edge at y 4 is near the divider colour) finds the divider after the workspace buttons and the
  right section's first divider (`#2F354D`, `DIVIDER`), every second and 0.3 s after a window event
  or a play-button change. So the right side may change width freely; keep a divider at both ends of
  the gap. When it can't measure (silent, grim missing) it falls back to sums over what's on the
  right (title, group count, play button, indicators, clock view) from `GAP_REF`.
- Its recounts run under one lock: Pango isn't thread-safe, and two threads measuring text at once
  aborted the script (`fc_thread_func: code should not be reached`), freezing the bars.
- Check for a push: the dividers' x must be the same with the bars showing as without (`grim` the
  bar, find `#2F354D`). Calibrate `GAP_REF` only from an un-pushed bar: measured gap minus the push.
- waybar runs it once per bar (a screen each); every copy writes its pid as a file name in
  `$XDG_RUNTIME_DIR/waybar-visualizer/`. Signal them by those pids (SIGUSR1: recount; `visualizer.py
  set / cycle` sends SIGUSR2 for a new style), never `pkill -f`: a name match also hits an editor that
  has the file open. What it sees: `$XDG_RUNTIME_DIR/waybar-visualizer.state`; the style:
  `~/.local/state/visualizer-style`.

## Apply and verify

```bash
systemctl --user restart waybar         # config + CSS + custom scripts, all fresh
sleep 2; grim -g "0,0 1366x34" /tmp/bar.png
```

Look at the screenshot (zoom: `magick /tmp/bar.png -crop 480x34+886+0 -scale 250% /tmp/z.png`).
For a JSONC syntax check: strip `//` comment lines and `json.loads` it in Python.
