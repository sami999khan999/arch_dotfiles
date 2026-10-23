---
name: gtk-panel
description: Build or change a GTK panel / popup in this dotfiles repo (local/lib/panels/*gui.py on gtkkit.py — Setups, Settings, Workspaces, the pickers, any new window) so it looks and behaves exactly like the others, and check it off-screen. Use for any new panel or any change to how one looks.
---

# GTK panels (gtkkit)

Every window of this desktop's own (Setups, Settings, Workspaces, Power, the agentmux pickers…) is a
`gtkkit.View` in `local/lib/panels/<name>gui.py`, run as a popup. They must look like one family. Read a
neighbour before writing one (`wsgui.py` list + detail, `pairgui.py` settings, `powergui.py` tiles,
`sessionendgui.py` progress rows), and reuse gtkkit's pieces instead of building your own.

## Checklist (each of these has been got wrong before)

1. **Backdrop: leave it on.** `View.backdrop` is True by default: a blurred, dimmed full-screen backdrop
   behind the popup. Don't set `backdrop = False` "because windows open while it's up": only the
   session-end panel has none (you watch the apps closing). The pair popup and Setups both lost their blur
   by copying that line.
2. **A size rule.** Add one line to `config/hypr/modules/windowrules.lua` beside the others:
   `hl.window_rule({ match = { tag = "floating-window", class = "^(panels\\.<name>)$" }, size = { W, H } })`.
   The size given to `run()` is only a fallback; the rule is what makes it open the same every time. Then
   `hyprctl reload`, and check that `hyprctl configerrors` prints nothing.
3. **The app id is `panels.<name>`.** That makes it float, centred, with the popin animation, and toggled:
   running it again closes it. Binds and waybar `on-click` run the file directly (`panels .. "<name>gui.py"`),
   so `chmod +x` it.
4. **The module layout.** A header comment says what it is, how it's opened, and its keys. Then
   `class X(View)` with `icon` (the same Nerd Font glyph as its bar icon, written as a `\U000fXXXX` escape),
   `title` and `subtitle`. Then `make()` returning the view (the Control Center uses it), and
   `main()` → `run(make(), "panels.<name>", (W, H))`.
5. **Width.** The scrolled area holding the content must `set_hexpand(True)` when it sits in a horizontal
   box (list + detail). Otherwise a wrapped label (an empty state, a hint) gets the minimum width and
   breaks one word per line. Always look at the **empty state** too, not just the full one.
6. **Use the shared controls, not hand-made ones:**
   - a setting → `setting_row(title, sub, control)`;
   - a choice → `dropdown([(value, text)], current, on_change)`, not a row of toggle buttons;
   - on / off → `switch`;
   - numbers → `spin` / `slider`;
   - a section → `rule_heading("Name", right, spaced=True)` (`spaced` between blocks of a window);
   - buttons → `button(text, fn, classes)`:
     - `"primary"` for the one main action (Open, Restore, Done);
     - `"flat"` for the rest;
     - `"flat", "danger"` for destructive ones;
     - ask once with the `armed` class (red; the same again within 3 s does it; see `powergui.py`).
   - a left list → a `Gtk.ListBox` (SINGLE) in `box(True, 0, …, classes=("side",))`, 260–360 px wide;
     the detail beside it in `scrolled()`, with 18–20 px margins.
   - a dropdown repeating a heading's words says less: `1 · Agents`, not `Workspace 1 · Agents`.
7. **Colours.** Only Tokyo Night's, in the view's `css`; gtkkit recolours it for the other themes.
   - Text classes: `dim`, `sub`, `accent`, `amber`, `green`, `red`, `bold`.
   - Pango markup needs `recolor("#…")`, cairo needs `rgbf("#…")`.
   - Square corners, 1 px borders.
8. **Keys.**
   - `hints = [(key, what), …]` with `("Esc", "close")` last; call `self.host.show_hints()` after changing them.
   - gtkkit closes the popup on Esc *before* `view.key()` is called, unless a text field has the keyboard.
     So Esc can't mean "back" in a sub-view: give it a Done button.
   - Letter keys act only when `not self.typing()`.
   - Enter on a ListBox row also fires `row-activated`: make the action idempotent.
9. **Threads.** GTK is main-thread only. Slow work (git, opening apps, docker) goes in a
   `threading.Thread(daemon=True)`; the UI changes from it go through `GLib.idle_add(fn, …)`.
   Nothing polls while unseen: `interval = 0` for a popup updated by events or by its own actions.
10. **Status.** `self.say(text, "ok"|"bad", seconds)` for messages, `self.set_subtitle()` for progress.

## Check it without a window on the user's screen

Don't open the panel on the user's screen to look at it. Use the snapshot helper beside this file:

```bash
python3 .claude/skills/gtk-panel/snapshot.py <module> "<view expr>" out.png --size WxH \
    --setup 'm.somelib.save = lambda *a: None' --then 'view.selected = "x"; view.paint()'
```

- It draws the panel under `gtk4-broadwayd :7` and saves the window as PNG; then read the PNG.
- Use `--setup` to stub whatever saves, pulls, pushes or opens apps (it gets the module as `m`).
- Use `--then` to reach another state (it gets the view as `view`).
- Look at each state: empty, full, the longest text, a sub-view (picker), progress.
- `DOTFILES_THEME=crimson` in front checks another theme.

Broadway has no compositor, so **the blurred backdrop and the window's transparency don't show** in a
snapshot (the background looks flat grey). Check those by reading instead:
- the view has no `backdrop = False`;
- the size rule exists;
- the id starts with `panels.`.

`pkill gtk4-broadwayd` when done.

## Then

- A key goes in `config/hypr/modules/binds.lua` with a `-- description` (Super + K lists it). A bar icon
  follows the `waybar` skill.
- Describe the panel in MANUAL.md: the panels table, plus its own section if it has keys.
