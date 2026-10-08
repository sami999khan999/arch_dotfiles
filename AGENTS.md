# AGENTS.md — working on these dotfiles

CachyOS + Hyprland desktop config for one user (sami), shared between PCs through GitHub.
Riced after Omarchy (waybar, walker, mako, swayosd, hyprlock, hypridle; awww for the wallpaper), Tokyo Night,
plus custom tools: workspace groups, a Control Center, and `codesync` (code backup SSD → HDD).
`MANUAL.md` is the user manual (`README.md` is GitHub's front page: screenshots, an overview, links
into the manual); this file is what an agent needs to change things safely.

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
| `config/hypr/workspaces.conf` | workspace groups (app, launch, bar icon), read by `modules/wsgroups.lua`, `local/bin/wsgroups`, `scripts/wsbar.py`, `codepair.lua`; edited and reordered in Settings → Workspaces, which also pins workspaces to screens per PC |
| `config/hypr/scripts/` | shell helpers (tui.sh, panel.sh, toggles, screenshots…) + waybar modules (wsbar, wintitle) |
| `local/lib/panels/` | the panels: GTK windows `*gui.py` (in use; shared `gtkkit.py`, Control Center `ccgui.py`) and the older terminal versions (`panelkit.py`) |
| `local/lib/panels/settings*.py` | Settings panel (Super + I): `settingslib.py` reads / writes / applies every setting, `settingsgui.py` is the window. Key mapping (keys / mouse buttons → an action) is `remaps` in `settings.json` → `hl.bind`s in `settings.lua`. `settingslib.py restore` (run at login) puts gsettings back in line with the repo files. Hyprland values: `config/hypr/settings.json` → generated `modules/settings.lua` (required last; don't hand-edit). Per-PC: `settings.local.json` (gitignored) |
| `config/waybar/` | `config.jsonc` + `style.css`; waybar runs as its packaged systemd user service (restarts itself after a crash) |
| `config/themes/`, `local/bin/theme`, `local/lib/theme/themelib.py`, `local/lib/panels/themegui.py` | colour themes: a palette per theme, `active`, the generated `current/` (gitignored); `theme apply <id>` makes the files and reloads; the picker is Super + Shift + T |
| `local/bin/agentmux`, `local/lib/agentmux/`, `config/agentmux/`, `local/lib/panels/agentpickgui.py` | agentmux (Super + A): threads are sessions on `tmux -L agents` (kitty-pair attaches the VS Code kitty to one), the workspace is `tmux -L agentmux`. Sidebars follow a control-mode subscription — never add polling of the agents. The New thread / Open project pickers are GTK popups (`agentpickgui.py`) |
| `local/bin/projects`, `local/lib/panels/projectsgui.py` | the project list: every repo in `~/code` and its remotes, in `~/code/.projects/projects.json` (a **private** repo, never in this one); `projects clone` gets them all back; the panel is Super + Ctrl + P |
| `local/lib/panels/sysagent*.py` | the System agent (Super + Ctrl + S): an AI chat about this PC, strictly read-only — opencode in a sandbox with no files, whose only tools are the panel's read-only MCP gate, which reads in a second sandbox (read-only filesystem, no network, no sockets, own PIDs, secrets covered). Never give it a tool that writes |
| `config/codesync/` | backup ignore list, shared timing (`settings.json`), per-PC `machine.json` |
| `local/bin/` | `wsgroups`, `codesync`, `dotsync` |
| `setup/` | `install.sh` (links + `--packages`), `setup-dev.sh`, `packages*.txt`, `vscode-extensions.txt` |
| `system/` | root-only bits, run once per PC with `pkexec` (`install.sh` lists them): `root-setup.sh` (Chrome policy; this PC's data-drive fstab line, only where that drive is attached), `swap-setup.sh` (SSD swapfile after zram, on the root btrfs), `boot-splash.sh` (quiet boot), `greeter/install.sh` (greetd + noctalia-greeter, `greetd.toml`) |

## Apply and verify every change

Don't report a change as done without seeing it work:

| Changed | Apply | Check |
|---|---|---|
| Hyprland config | `hyprctl reload` | `hyprctl configerrors` must print nothing |
| waybar | `systemctl --user restart waybar` (a live `SIGUSR2` reload can crash it) | screenshot |
| a waybar custom script | same | run the script by hand; its output is JSON |
| agentmux sidebars / home | `agentmux reload` (Ctrl+Alt+R: restarts the views, the agents keep running) | `tmux -L agentmux capture-pane -p -t %0`, `~/.cache/agentmux/errors.log` |
| a package, a service, state outside `config/` | add it to `setup/` (packages lists, `install.sh`, `setup-dev.sh`) or `system/` | `setup/install.sh --dry-run` |
| codesync | `systemctl --user restart codesync` | `codesync status`, `journalctl --user -u codesync` |
| anything with colours | `theme apply tokyo-night` (or `theme restore`) | Tokyo Night must look exactly as before: `theme apply crimson`, screenshot, `theme apply tokyo-night` |
| a GTK panel | close and reopen it (Control Center / Docker: kill its PID, `wsgroups launch 10 --background`) | off-screen screenshot (see the `tui-panel` skill) — don't pop windows on the user's screen |

Screenshots: `grim -g "0,0 1366x34" out.png` (the bar; the screen is 1366×768), `grim out.png`
(whole screen). Crop/zoom with `magick in.png -crop WxH+X+Y -scale 300% out.png`, then look at it.

## Conventions

- **Colours**: write Tokyo Night's, by hand, as always — base `#1a1b26`,
  overlay `#292e42`, line/border `#3b4261`, muted `#565f89`, subtext `#a9b1d6`, text `#c0caf5`,
  accent `#6b8fe0` (darker than stock), alert `#f7768e`, green `#9ece6a`, amber `#e0af68`.
  Square corners, 1px borders in `#3b4261` at 70%.
  Other colour themes (Crimson…, `Super + Shift + T`) are made from those: `config/themes/<id>/theme.json`
  maps each Tokyo Night colour (role) to its own, and `theme apply` / `themelib.recolor` swap them (MANUAL.md
  "Colour themes"). So a new colour must be one of the 32 in `config/themes/tokyo-night/theme.json` (or be
  added there and to every theme), Python that draws must pass its colours through `recolor` / `rgbf` (gtkkit
  does it for `css`; markup, cairo and ANSI need it explicitly), and an app's own colours stay ahead of the
  line that reads `config/themes/current/` (keep those `@import` / `include` / `source` lines last).
- Font: JetBrainsMono Nerd Font. Icons are Nerd Font glyphs; write them as `\uXXXX` / `\U000fXXXX`
  escapes in Python so they survive editors.
- Match the surrounding code: short header comment saying what the file is and how it's used,
  comments that explain *why*, no dead code. Lua and Python modules follow the existing layout.
- A new keybind gets a trailing `-- description` comment: `keys.py` (Super + K) shows it.
- When behaviour changes, update the matching MANUAL.md section in the same commit (and README.md if
  its overview, keys or screenshots no longer match).

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
- The audio visualizer fills the bar's gap to the pixel by measuring it (a one-row `grim` finds the
  dividers either side) while it shows; keep a divider at both ends of that gap. Its threads share
  one lock: Pango isn't thread-safe (two at once aborted the script). See the `waybar` skill.
- `mpris` tooltips are plain text (markup shows raw).
- waybar/GTK prefers ellipsizing a label over moving the centre island, so the window title is
  truncated in `scripts/wintitle.py`, not with `max-length`.
- waybar signals in use: `RTMIN+8` workspace buttons, `+9` idle, `+10` notifications, `+11` codesync.
- `pkill -f <pattern>` also matches the shell running it and kills your own command. Use
  `pkill -f '[c]ontrolcenter\.py'`-style patterns, or better, signal by PID.
- agentmux's sidebars are terminal UIs in tmux panes (`local/lib/agentmux/term.py`), sharpened to look
  like the GTK panels by its kitty (`config/kitty/agentmux.conf`: 10pt, `cell_height`, 1px box lines).
  A gap finer than a line is a block character (`▀` …), which kitty draws to the pixel.
- Keys sent to an app (Key mapping's Copy / Paste…): `hl.dsp.send_shortcut({ mods = "CTRL", key = "v" })`.
  In a terminal Ctrl+C interrupts: Copy there takes the primary selection to the clipboard instead.
- The Bash tool's shell is zsh/fish-like: `--include=*.lua` globs fail and `$PIPESTATUS` is empty.
  Wrap scripting in `bash -c '…'`, or use Python.

## Every PC the same

A fresh CachyOS + `setup/install.sh --packages` + the `system/` steps must give this exact desktop.
So: a package you install goes into `setup/packages.txt` (or `packages-aur.txt`); a tool from an
installer goes into `setup-dev.sh`; a user service, a gsettings value or anything else outside the
repo needs a step that recreates it (`install.sh`, `settingslib.py restore`, or `system/` for root).
Per-PC on purpose: monitor modes (`settings.local.json`), `codesync/machine.json`, time zone, power
profile, GPU services.

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
