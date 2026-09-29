---
name: waybar
description: Add, move or restyle a waybar module in this dotfiles repo (config/waybar), including custom script-driven modules, and verify it visually. Use for any change to the top bar.
---

# Waybar

Three floating islands, square corners, Tokyo Night. Layout in `config/waybar/config.jsonc`:

```
left:   logo │ workspace buttons (custom/ws1…10) │ window title (custom/window)
centre: media (play/pause icon) │ clock
right:  group label │ codesync, keys, tray, bt, net, volume │ stats (cpu mem temp battery) │ power
```

## Adding a module

1. Add its name to `modules-left/center/right` and its block to `config.jsonc`.
2. In `style.css`, add its `#id` to the shared chip rule (padding/margin/transition) and the
   shared `:hover` rule near the top, then any specific colours below. Use the `@define-color`
   tokens (`@muted`, `@subtext`, `@text`, `@accent`, `@alert`, `@overlay`, `@line`), not hex.
3. Section dividers are `border-left/right: 1px solid alpha(@line, 0.7)` on the first/last module
   of a section (see `#stats`, `#custom-wsgroups`, `#mpris`).

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
- Scripts live in `config/hypr/scripts/`; `chmod +x` them.

## Known quirks

- `clock`: one `{}` field only. Markup goes inside it: `"<span color='#565f89'>{:L%a %d %b</span>  <b>%H:%M</b>}"`.
- `mpris` `tooltip-format` is plain text; tags show up literally.
- GTK ellipsizes a `max-length` label before it moves the centre island. To let text push the
  centre island, truncate in the script instead (`wintitle.py`, `MAX`).
- `.modules-center { margin: 0 6px }` keeps the islands from touching (two 1px borders read as one
  thick one). Keep it.
- The screen is 1366px wide; long content in the left island squeezes everything.

## Apply and verify

```bash
pkill -SIGUSR2 waybar                   # reloads config + CSS, restarts custom scripts
sleep 1.5; grim -g "0,0 1366x34" /tmp/bar.png
```

Look at the screenshot (zoom: `magick /tmp/bar.png -crop 480x34+886+0 -scale 250% /tmp/z.png`).
For a JSONC syntax check: strip `//` comment lines and `json.loads` it in Python.
