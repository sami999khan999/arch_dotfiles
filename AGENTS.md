# AGENTS.md — working on these dotfiles

CachyOS + Hyprland desktop config for one user (sami), shared between PCs through GitHub.
Riced after Omarchy (waybar, walker, mako, swayosd, hyprlock, hypridle, swaybg), Tokyo Night,
plus custom tools: workspace groups, a Control Center, and `codesync` (code backup SSD → HDD).
`README.md` is the user manual; this file is what an agent needs to change things safely.

Task-specific guides live in `.claude/skills/*/SKILL.md` (plain Markdown, usable by any agent):

| Skill | Use it when |
|---|---|
| `hyprland-config` | editing anything under `config/hypr/` (binds, rules, autostart, Lua gotchas) |
| `waybar` | adding or restyling a bar module |
| `workspace-groups` | adding, moving or renaming a workspace group |
| `tui-panel` | building or changing a panel / Control Center card (`panelkit.py`) |
| `codesync` | anything about the code backup service |

## How the repo is wired

- `config/*` is symlinked into `~/.config/*`, `local/bin/*` into `~/.local/bin/`, `local/share/*/*`
  into `~/.local/share/*/`, `local/lib/*` into `~/.local/lib/`, by `setup/install.sh` (idempotent; backs up whatever is in the way).
  Editing `~/.config/hypr/…` and `~/dotfiles/config/hypr/…` edits the same file.
- **Never replace a symlink with a regular file.** Write files in place (`open(path, "w")`), not by
  write-to-temp-then-rename onto the link. Check with `ls -l ~/.config`.
- A new top-level entry in `config/`, `local/bin/` or `local/lib/` needs `setup/install.sh` once to get linked.
  VS Code settings are not in this repo: they come from VS Code Settings Sync.
- `dotsync` (in `local/bin/`) = `git add -A`, commit, pull --rebase, push, relink, `hyprctl reload`.
- Generated or per-machine files are gitignored (see `.gitignore`): Noctalia colour files,
  `__pycache__/`, `config/codesync/machine.json`, `fish_variables`.

## Map

| Path | What |
|---|---|
| `config/hypr/hyprland.lua` | entry point: `require`s the modules in `config/hypr/modules/` |
| `config/hypr/modules/*.lua` | binds, window rules, workspaces, autostart, colours, animations… |
| `config/hypr/apps.conf` | app hotkeys (focus-or-launch), read by `modules/apps.lua` |
| `config/hypr/workspaces.conf` | workspace groups, read by `modules/wsgroups.lua` and `local/bin/wsgroups` |
| `config/hypr/scripts/` | shell helpers (tui.sh, panel.sh, toggles, screenshots…) + waybar modules (wsbar, wintitle) |
| `local/lib/panels/` | the panels: GTK windows `*gui.py` (in use; shared `gtkkit.py`, Control Center `ccgui.py`) and the older terminal versions (`panelkit.py`) |
| `config/waybar/` | `config.jsonc` + `style.css` |
| `config/codesync/` | backup ignore list, shared timing (`settings.json`), per-PC `machine.json` |
| `local/bin/` | `wsgroups`, `codesync`, `dotsync` |
| `setup/` | `install.sh` (links + `--packages`), `setup-dev.sh`, `packages*.txt`, `vscode-extensions.txt` |
| `system/` | root-only bits: Chrome policy, this PC's data-drive fstab line; `root-setup.sh` applies both (machine-specific) |

## Apply and verify every change

Don't report a change as done without seeing it work:

| Changed | Apply | Check |
|---|---|---|
| Hyprland config | `hyprctl reload` | `hyprctl configerrors` must print nothing |
| waybar | `pkill -SIGUSR2 waybar` (reloads config + CSS, restarts custom scripts) | screenshot |
| a waybar custom script | same | run the script by hand; its output is JSON |
| codesync | `systemctl --user restart codesync` | `codesync status`, `journalctl --user -u codesync` |
| a GTK panel | close and reopen it (Control Center / Docker: kill its PID, `wsgroups launch 10 --background`) | off-screen screenshot (see the `tui-panel` skill) — don't pop windows on the user's screen |

Screenshots: `grim -g "0,0 1366x34" out.png` (the bar; the screen is 1366×768), `grim out.png`
(whole screen). Crop/zoom with `magick in.png -crop WxH+X+Y -scale 300% out.png`, then look at it.

## Conventions

- **Colours** (Tokyo Night, hand-applied — nothing generates them): base `#1a1b26`,
  overlay `#292e42`, line/border `#3b4261`, muted `#565f89`, subtext `#a9b1d6`, text `#c0caf5`,
  accent `#6b8fe0` (darker than stock), alert `#f7768e`, green `#9ece6a`, amber `#e0af68`.
  Square corners, 1px borders in `#3b4261` at 70%.
- Font: JetBrainsMono Nerd Font. Icons are Nerd Font glyphs; write them as `\uXXXX` / `\U000fXXXX`
  escapes in Python so they survive editors.
- Match the surrounding code: short header comment saying what the file is and how it's used,
  comments that explain *why*, no dead code. Lua and Python modules follow the existing layout.
- A new keybind gets a trailing `-- description` comment: `keys.py` (Super + K) shows it.
- When behaviour changes, update the matching README section in the same commit.

## Hard-won gotchas

- **Hyprland uses the Lua config (0.56).** Legacy dispatch syntax is rejected:
  `hyprctl dispatch workspace 2` fails. Use `hyprctl dispatch 'hl.dsp.focus({ workspace = "2" })'`.
  Anything that sends legacy commands (e.g. waybar's built-in `hyprland/workspaces` click) silently
  does nothing — that's why the workspace buttons are custom modules (`scripts/wsbar.py`).
- API reference: `/usr/share/hypr/stubs/hl.meta.lua` (events, window fields, config keys).
- `fullscreen_state` in a window rule **pins** the state: apps can no longer go real fullscreen
  (YouTube stayed in the tile). Maximize once on open instead (`hl.on("window.open", …)` in
  `wsgroups.lua`).
- Window rules matched through a tag (`floating-window`) are applied after plain ones; a size
  override for a tagged window must also match the tag.
- The bar's clock is `custom/clock` (`scripts/clock.py`), not waybar's `clock`: the audio
  visualizer fills the gap to the pixel and must know the clock's view. Anything on the right that
  changes width must be fixed-width or followed by `scripts/visualizer.py` (see the `waybar` skill).
  `mpris` tooltips are plain text (markup shows raw).
- waybar/GTK prefers ellipsizing a label over moving the centre island, so the window title is
  truncated in `scripts/wintitle.py`, not with `max-length`.
- waybar signals in use: `RTMIN+8` workspace buttons, `+9` idle, `+10` notifications, `+11` codesync.
- `pkill -f <pattern>` also matches the shell running it and kills your own command. Use
  `pkill -f '[c]ontrolcenter\.py'`-style patterns, or better, signal by PID.
- The Bash tool's shell is zsh/fish-like: `--include=*.lua` globs fail and `$PIPESTATUS` is empty.
  Wrap scripting in `bash -c '…'`, or use Python.

## Safety

- `/dev/sda` is the Windows install: never mount it read-write, never modify it.
- `/mnt/data` is `/dev/sdc1`, NTFS (ntfs3), the HDD holding the code backup. Don't write there except
  through `codesync`; its `.code-trash/` holds recoverable old versions.
- Don't start `pkexec`/`sudo` prompts you then kill: three failed auths lock the account for
  10 minutes (faillock). Ask the user to run root commands themselves (`! sudo …`).
- Workspace 10 is the Control Center, started at login; restarting it is fine, closing its
  window just means relaunching it (`wsgroups launch 10 --background`).

## Commits

- Commit as the user (sami). **No `Co-Authored-By` or other AI attribution lines.**
- Small, focused commits with a subject like the existing history: `waybar: …`, `hypr: …`,
  `codesync: …`. Push only when asked.
