# dotfiles

CachyOS + Hyprland desktop configuration, riced after [Omarchy](https://github.com/basecamp/omarchy)
(its classic v3 stack: waybar, walker, mako, swayosd, hyprlock, hypridle, swaybg) in Tokyo Night.

## Bootstrap a new machine

Install CachyOS with Hyprland, then:

```bash
git clone https://github.com/sami999khan999/arch_dotfiles.git ~/dotfiles
cd ~/dotfiles
setup/install.sh --dry-run --packages   # preview, changes nothing
setup/install.sh --packages             # packages + /etc files + symlinks
```

Then log out and back in. Re-run `setup/install.sh` (no flag) any time to relink.

That gives the same packages (`setup/packages*.txt` hold everything installed by name on the
first PC), the same config, toolchains, coding agents and their agentmux hooks. The rest needs
root or a choice, once per PC (`install.sh` prints the list at the end):

```bash
pkexec ~/dotfiles/system/greeter/install.sh   # login screen: greetd + noctalia-greeter
pkexec ~/dotfiles/system/boot-splash.sh       # loading screen instead of boot text
pkexec ~/dotfiles/system/swap-setup.sh        # SSD swapfile after zram (the root btrfs)
pkexec ~/dotfiles/system/root-setup.sh        # Chrome policy; the data drive, where it's attached
codesync enable                               # code backup (asks where the code is)
```

Then sign in: VS Code (Settings Sync brings its settings), Chrome, and the coding agents.
What isn't in git follows the repo by itself: at every login `settingslib.py restore` puts the
theme, cursor, fonts and clock format in gsettings back in line with `config/gtk-3.0/settings.ini`
and `config/hypr/settings.json` (also how a theme change made on one PC reaches the other).
Per-PC on purpose: monitor modes (`settings.local.json`), `config/codesync/machine.json`, time
zone, power profile, the GPU's own services.

## Layout

| Path | Links to | Contains |
|---|---|---|
| `config/` | `~/.config/` | settings — hypr, noctalia, gtk, qt, terminals, fish |
| `local/bin/` | `~/.local/bin/` | commands — `wsgroups`, `codesync`, `dotsync` |
| `local/lib/panels/` | `~/.local/lib/panels/` | the panels — GTK windows (`*gui.py`, shared look in `gtkkit.py`) and their older terminal versions (`panelkit.py`) |
| `local/share/` | `~/.local/share/` | assets — cursor theme, wallpapers, launcher overrides |
| `icons/` | `~/.icons/` | legacy cursor stub (`default/index.theme`) |
| `system/` | *manual, needs root* | `/etc` files — Chrome policy, this PC's data-drive fstab line |
| `system/greeter/` | *manual, needs root* | login screen (noctalia-greeter): Tokyo Night, the desktop's wallpaper blurred and darkened — `pkexec ~/dotfiles/system/greeter/install.sh` |
| `system/swap-setup.sh` | *manual, needs root* | 16 GB swapfile on the SSD (subvolume `@swap` at `/swap`, priority 10, after zram) so systemd-oomd doesn't kill VS Code when RAM fills — `pkexec ~/dotfiles/system/swap-setup.sh` |
| `system/boot-splash.sh` | *manual, needs root* | boot and shutdown show the loading screen (Plymouth) instead of text: adds `quiet …` to GRUB's kernel command line — `pkexec ~/dotfiles/system/boot-splash.sh` |
| `setup/` | — | `install.sh`, `setup-dev.sh`, package lists (`packages.txt`, `packages-aur.txt`), `vscode-extensions.txt` |

`setup/install.sh` is idempotent and never deletes: anything real it finds in the way
is moved to `~/.config-backup-<timestamp>/` first.

## Settings vs assets

Two different things, two locations:

- **`config/`** names a theme  — e.g. `gtk-theme-name=adw-gtk3`
- **`local/share/`** holds the theme itself

A setting pointing at an absent asset fails silently. Most themes here come from
packages (`/usr/share/themes`, `/usr/share/icons`), so `setup/packages.txt` matters as
much as the files. The exception is **Bibata-Modern-Ice**, which was hand-placed
and belongs to no package — it is committed here because nothing else restores it.

## Theme: Tokyo Night, hand-applied

Colors come from Omarchy's `themes/tokyo-night/colors.toml` and are written
directly into each app. Nothing generates them:

| Role | Color |
|---|---|
| background / surface | `#1a1b26` / `#24283b` / `#292e42` |
| text / dim | `#a9b1d6`, bright `#c0caf5` / `#565f89` |
| accent | `#6b8fe0` blue (a bit darker than stock Tokyo Night), `#bb9af7` magenta |
| window & popup borders | `#3b4261` at 70%, 1px — same as the waybar bottom line |

| App | File |
|---|---|
| Hyprland borders | `config/hypr/modules/colors.lua` |
| waybar, swayosd | `config/waybar/style.css`, `config/swayosd/style.css` |
| walker | `config/walker/themes/omarchy-default/style.css` |
| mako | `config/mako/config` |
| hyprlock | `config/hypr/hyprlock.conf` |
| panels (GTK) | `local/lib/panels/gtkkit.py` (`CSS`) |
| kitty, alacritty, btop | `themes/tokyo-night.*` in each |
| GTK 3/4, Qt | `gtk-*/tokyo-night.css`, `qt6ct/colors/tokyo-night.conf` |
| KDE apps (Dolphin) | `config/kdeglobals` |
| terminal greeting (a spinning bagel + the system summary; any key skips it) | `local/bin/greet` (data from fastfetch), `config/fastfetch/config.jsonc` for plain `fastfetch` |

Wallpapers live in `local/share/backgrounds/wallpapers`; `Super+Ctrl+Space`
cycles them. Helper scripts (power menu, toggles, screenshots) are in
`config/hypr/scripts/`.

The bar runs as waybar's own systemd user service (`autostart.lua` starts `waybar.service`): if
it crashes it comes back by itself within a second. Restart it with `systemctl --user restart waybar`.

Noctalia is still installed but no longer autostarted.

## Development

`setup/install.sh --packages` installs everything below, then runs `setup/setup-dev.sh`
for the toolchains that don't come from pacman.

| Tool | Installed by | Update with |
|---|---|---|
| docker (+ compose, buildx, lazydocker), go, uv, cmake, ninja, CLI tools | pacman (`setup/packages.txt`) | `sudo pacman -Syu` |
| VS Code (Microsoft build) | AUR (`setup/packages-aur.txt`) | `paru -Syu` |
| Node LTS (+ npm), pnpm, bun | mise (`config/mise/config.toml`) | `mise upgrade` |
| Rust stable, clippy, rustfmt, rust-analyzer | rustup | `rustup update` |
| Python 3 | system python; use `uv` for venvs and tools | `sudo pacman -Syu` |

- Git settings (identity, delta diffs, rebase on pull): `config/git/config`.
  The gh login helper stays in `~/.gitconfig`, which is per machine.
- VS Code: settings and keybindings are not in this repo; they come from VS Code Settings Sync
  (GitHub account). Extensions are listed in `setup/vscode-extensions.txt`.
- VS Code terminals don't take the editor down when memory runs out: the built-in terminal starts
  each shell with `systemd-run --user --scope` (VS Code setting `terminal.integrated.profiles.linux`;
  shell integration is then loaded from `~/.zshrc`), so systemd-oomd kills that terminal, not
  VS Code. `Ctrl+Shift+C` opens kitty in the project folder instead (`local/bin/kitty-here`, set
  as `terminal.external.linuxExec`), also as its own app. Memory rarely runs out at all: a 16 GB
  SSD swapfile (`system/swap-setup.sh`) takes what overflows zram, so oomd's 90 % swap line is far away.
- Per-project versions: put a `mise.toml` in the project (`mise use node@22`).
- `Super + Shift + C` jumps to or opens VS Code.

## Syncing between PCs

```bash
dotsync                  # save local changes, pull the other PC's, push, relink
dotsync "what changed"   # same, with your own commit message
```

Run it after changing something, and before starting on the other PC.
`dotsync` lives in `local/bin/` and is linked into `~/.local/bin`.

## Code backup (codesync)

Code lives on the SSD in `~/code` (fast); `codesync` copies every change to the HDD at
`/mnt/data/code` a few seconds after you save, so a crash loses at most those seconds.

- Runs in the background as a systemd user service, starts at login (`codesync enable`).
- Mirror: the HDD matches the SSD. Anything the sync deletes or overwrites on the HDD is moved to
  `/mnt/data/.code-trash/<date>/` first and kept 30 days.
- Ignore list: `config/codesync/ignore` (`codesync ignore` opens it) — node_modules, .next, dist,
  .venv, caches… A project can add its own `.syncignore`. Ignored files are never copied and are left
  untouched on the HDD.
- Safety: nothing runs if the HDD isn't mounted, and an empty `~/code` is never mirrored.
- **Empty code folder, full backup** (new PC, wiped SSD, wrong folder picked): syncing holds, the
  icon turns amber and a notification says so. In the panel press `r` (or run `codesync restore`) to
  copy the backup into the code folder — nothing is deleted — and syncing resumes by itself.

**Waybar:** the sync icon next to the logo shows the state: grey (up to date), white (changes waiting),
blue (syncing or restoring), amber (paused, or restore needed), red (problem). Hover for a summary;
click opens the Code Sync panel (a window, `panels/syncgui.py`: backed-up files/folders/size, what the ignore list skips, free space,
old versions, per-folder breakdown, today's activity, recent changed files, the two folders and the
timing settings: ↑↓ pick, ←→ change a setting). Right-click syncs now, middle-click pauses/resumes.
Timing is stored in `config/codesync/settings.json`.

**Changing the folders:** in the panel, pick *Code folder* (where you work) or *Backup* (where copies
go) and press Enter (or click it). A folder dialog opens; after you choose, the panel shows what the
switch would do to the backup (+ new, ~ changed, − moved to old versions) and applies it on `y`.

| Command | Does |
|---|---|
| `codesync` | status: last sync, errors, paths |
| `codesync pause` / `resume` | hold syncing (changes still noticed, synced on resume) |
| `codesync now` | sync immediately |
| `codesync log` | follow what it's doing |
| `codesync restore` | copy the backup into the code folder (new machine, emptied SSD); never deletes. Alias: `pull` |
| `codesync enable` / `disable` | start / stop the background service |

**On another PC** the folders can be anywhere: `codesync enable` first asks where the code is and
where the backup goes (on the other drive), and saves that to `config/codesync/machine.json`, which is
gitignored, so every PC keeps its own paths while the ignore list and timing stay shared.
`codesync setup` (or the panel) changes them later. If that PC's backup already holds code, run
`codesync restore` first to copy it to the fast drive. Until codesync is enabled on a PC, its waybar icon stays hidden.
The backup drive must be mounted at boot on that PC: an `/etc/fstab` line (this PC's is `system/fstab-data`).
The program is `local/bin/codesync`.

## App hotkeys (AutoHotkey-style)

`config/hypr/apps.conf` maps keys to apps, one per line:

```
SUPER + B    | google-chrome     | google-chrome-stable
```

The key jumps to that app's window on any workspace, cycles through its windows
on repeat presses, and launches it if nothing is open. Find a window's class with
`hyprctl clients | grep class`, then `hyprctl reload`. Loaded by
`config/hypr/modules/apps.lua`, run by `scripts/focus-or-launch.sh`.

## Workspace groups

`config/hypr/workspaces.conf` gives each kind of app its own workspace:

| Workspace | Group | Apps |
|---|---|---|
| 1 | Code | VS Code windows, each with its kitty (below) |
| 2 | Web | Chrome windows |
| 3 | Terminal | kitty |
| 4 | Agents | agentmux (Super + A) |
| 7 | Files | Dolphin |
| 8 | Discord | Discord |
| 9 | Docker | the Docker panel (below) |
| 10 (key 0) | Control | the Control Center (below) |

```
1 | Code     | code            | code ~/dotfiles
2 | Web      | google-chrome   | google-chrome-stable
```

**Settings → Workspaces** (Super + I) edits all of it: what opens on each workspace (name, bar icon,
window class, launch command, Super + N; or "Use an open app"), the order (↑ ↓ swap two
workspaces: their open windows, bar icons and screens go along, and the VS Code pairs' scrolling
layout and the Control Center follow their apps), and, with more than one screen, which screen
each workspace lives on (saved per PC in `settings.local.json`, since screens differ). The bar
icon is the 6th column of `workspaces.conf`.

Windows of that class always open on that workspace, maximized (waybar and gaps stay).
A fullscreen video stays fullscreen when you switch away with `Alt + 1…0` or `Alt + Tab`,
and is fullscreen again when you come back (a mouse click on another window exits it).
The waybar module on the right shows the current group and window, e.g. `Code 2/3`;
hover for the numbered window list. A popup panel doesn't count: while one is open, the count
and the title beside it stay on the window it opened over.

**Code workspace: VS Code + kitty pairs.** Every VS Code window (Super + N, `code`, File > New
Window…) opens with its own kitty beside it: kitty 45 % on the left, VS Code 55 %, together the
whole screen. kitty starts in that window's project folder (found from the window title under
`~/code`; an empty window gets `~/code`) and runs as its own app, so a dev server that runs out of
memory takes down that kitty, not VS Code. The workspace uses Hyprland's scrolling layout: pairs
sit side by side and `Alt + N` / `Alt + Tab` slide to VS Code window N with its kitty (kittys
aren't counted). Opening another project in a VS Code window takes its kitty along: it `cd`s
there if it's idle, or opens a new tab there if something is running. Closing a VS Code window
closes its kitty. `Super + F` (fullscreen) or `Super + Alt + F` (maximized, bar stays) makes
the focused VS Code or kitty fill the screen; press it again and the pair is back side by side.
`Super + Alt + C` / `Super + Alt + T` do the same for the VS Code / the kitty on screen, whichever
has the focus. **Pair layout:** `Super + Alt + P` on a VS Code window or its kitty opens a popup
(`panels/pairgui.py`): the kitty's width (presets 20–80 % or a slider), either one full width
(Kitty full / VS Code full / Side by side), the kitty's side (left / right), this pair or every
pair, and whether new pairs open like this (saved in `config/hypr/codepair.conf`). VS Code can't
go below ~640 px (its title bar doesn't draw narrower; squeezed, it gets cropped). It applies as you
change it; keys: `← →` width,
`l` / `r` side, `b` / `k` / `c` side by side / kitty full / VS Code full, `a` every pair, Esc closes. Lost a kitty? `kitty-pair <address>`
(address from `hyprctl clients`) opens it again. Code: `modules/codepair.lua`, `local/bin/kitty-pair`.

| Key / command | Does |
|---|---|
| `Super + Ctrl + G`, or click it in waybar | open (or close) the manager window: map open apps to workspaces, edit, launch, tidy |
| `Alt + 1…0` | switch to window N of the current workspace (in the order they were opened) |
| `Alt + Tab` / `Alt + Shift + Tab` | next / previous window of the current workspace, same order |
| `Super + Ctrl + 1…0` | go to workspace N; launch its programs if none are open |
| `Super + N` | open another window of the current workspace's app (a new Chrome window on Web…) |
| `wsgroups launch N\|all [-f]` | launch a group's programs |
| `wsgroups tidy` | move already-open windows to their workspaces |
| `wsgroups list` | show groups and numbered windows |

The manager is a real window (GTK, `local/lib/panels/wsgui.py`): the ten workspaces on the left,
the selected one's settings, actions and open windows on the right. Edit a field and press Enter
(or Save); "Use an open app" puts a running app on the workspace; Remove asks for a second click;
click an open window to jump to it. Keys when no field is focused: `1`–`0` select, `l` launch,
`g` go, `t` tidy, `Delete` remove, `Esc` close. `wsgroups tui` is the old terminal version.

Several launch commands are separated with `;` and open in that order. Alt + 1…0 is
taken over everywhere, so Chrome tabs switch with `Ctrl + 1…8` instead.
Loaded by `config/hypr/modules/wsgroups.lua`; the program is `local/bin/wsgroups`.

### Panels

Every panel is a GTK window (`local/lib/panels/*gui.py`) in the look of the Control Center cards:
icon and title with details on the right, a blue underline, blue section headings, amber keys and
window classes, green / amber / red levels, key hints at the bottom. The popups float in the middle of the screen; running one again (its
key or waybar click) closes it, and so does `Esc`. The hints at the bottom list each panel's keys.
While a popup is open the screen behind it is blurred and darkened, the bar excepted (a full-screen
window under it showing a screenshot taken as it opened, blurred by GTK: Hyprland's blur is one
strength for everything; `Backdrop` in `gtkkit.py`, `panels-backdrop` in `modules/windowrules.lua`);
a click there closes the popup, and so does switching workspace (the popup is pinned above its
backdrop, so that click can't cover it). Settings → Appearance → Panels: on / off, blur (0–40 px)
and darkness.
The pair popup (`Super + Alt + P`) has none: you watch the pair while you change it.

| Panel | Opens with | Does |
|---|---|---|
| Workspaces (`wsgui.py`) | `Super + Ctrl + G`, waybar groups icon | above |
| System (`sysgui.py`) | `Super + Ctrl + T`, waybar CPU / memory | tabs (`1`–`3`): **Overview** (CPU, memory, GPU — a 90 s graph and the details: temperature, clocks, swap, video memory, power…), **Processes** (every process; sort by a column, `/` search, End process / Kill on a second click), **Storage** (each drive: SSD / HDD, read / write now, each partition's usage) |
| Audio (`audiogui.py`) | `Super + Ctrl + A`, waybar volume | outputs, inputs, what's playing: volume, mute, make default; the Wiremix button opens wiremix |
| Network (`netgui.py`) | `Super + Ctrl + W`, waybar network | connection, traffic graph, addresses; "Manage connections" opens nmtui |
| Shortcuts (`keysgui.py`) | `Super + K`, waybar keyboard icon | every shortcut, live from the config; type to fuzzy-search |
| Code Sync (`syncgui.py`) | waybar sync icon | see Code backup above |
| Settings (`settingsgui.py`) | `Super + I`, waybar cog icon, "Settings" in Walker | see Settings panel below |

### Settings panel

`Super + I` (or the cog in waybar, or "Settings" in Walker) opens one window for every setting: a sidebar of sections
(type anywhere to fuzzy-search sections and the settings in them) and a page per section. Every
control applies at once; the bottom line says where it was saved.

| Group | Sections |
|---|---|
| Look & feel | Appearance (gaps, borders, corners, opacity, blur, shadow, what's behind an open panel: on / off, how blurry, how dark) · Animations (on/off, speed, workspace slide) · Wallpaper (thumbnails, fill / fit) · Theme & fonts (dark mode, icons, cursor, fonts; nwg-look / qt6ct for more) |
| Input & display | Keyboard (layouts, switch key, repeat, Num Lock) · Mouse (speed, acceleration, natural scroll, focus follows mouse, zoom) · Key mapping (any key or mouse button, side buttons included, to Copy / Paste / media / a shortcut / a command) · Display (mode and scale, with a 15 s "keep?" that reverts; VRR) · Night light (warmth, schedule) |
| Power & bar | Idle & lock (lock / screen-off timeouts, lock before sleep, stay awake) · Power profile · Notifications (corner, timeout, Do not disturb, hidden apps) · Top bar (position, height, stats) |
| System | Default apps (terminal, browser…, and what opens links / files) · Date & time (time zone, NTP, 24-hour clock) · More settings (opens the other panels) · About this PC |

Where values go (`local/lib/panels/settingslib.py` does the reading and writing; `settingsgui.py`
is only the layout):

- **Hyprland options** → `config/hypr/settings.json` → generated `modules/settings.lua`, required
  last in `hyprland.lua`, so a value set in the panel wins over the hand-written modules. "Back to the
  config's values" on Appearance forgets them. Display modes are per PC: `settings.local.json` →
  `modules/settings_local.lua` (both gitignored).
- **Everything else** in its own file, by line edits of the known keys: `hypridle.conf`,
  `mako/config`, `waybar/config.jsonc`, `variables.lua` (+ `uwsm/env`, `kdeglobals`),
  `mimeapps.list`, `gtk-3.0/settings.ini`, `xsettingsd.conf`, `qt6ct.conf`, plus gsettings.
- Key mapping: press a key or mouse button in the box (it shows its name, and what already uses it),
  pick an action, Add. Saved as `remaps` in settings.json; `modules/settings.lua` gets an `hl.bind`
  per mapping (Hyprland reloads). Copy / Paste send Ctrl+C / Ctrl+V to the focused window; in a
  terminal (kitty, agentmux, the VS Code kittys) Copy takes the selection to the clipboard instead
  (`wl-paste --primary | wl-copy`: Ctrl+C would interrupt) and Paste is Ctrl+Shift+V. A mapped key
  or button does only that, everywhere. Super + K lists the mappings.
- Night light: `toggle.sh nightlight [on|off|auto]` reads the warmth and schedule from
  settings.json; the schedule is two systemd user timers (`settings-nightlight-on/off.timer`), and
  `auto` at login. The bar clock's 24-hour switch and the wallpaper's fill / fit are in
  settings.json too.

The older terminal versions (`sysmon.py`, `keys.py`, `audiopanel.py`, `netpanel.py`,
`syncpanel.py`, `dockerpanel.py`, `controlcenter.py`, `wsgroups tui`) still work if run directly.

### Agents: agentmux

`Super + A` (or workspace 4's button in waybar) opens **agentmux**, a tmux workspace for coding agents, by
project: **Projects** | **Agents** (that project's threads) | the thread | **terminals** (regular
shells, `+ Terminal`; `× Terminal` or `Ctrl+Alt+W` closes the focused one, the last one closes the column). Drag the borders (or `Ctrl+Alt+←→`) to resize; widths are remembered.

```
┌Projects─┬Agents──────┬─ the thread ────────────────┬─ terminals ─┐
│▸cloud_t │◐ 1 diagram │                            │ $ pnpm dev  │
│ inkwell │✳ 2 docs    │                            ├─────────────┤
│+ open   │+ new thread│                            │ $ git st…   │
└─────────┴────────────┴────────────────────────────┴─────────────┘
 agentmux │ + Terminal Ctrl+Alt+T  × Terminal Ctrl+Alt+W  + Thread Ctrl+Alt+N  Open project Ctrl+Alt+O  ? keys   cloud_track · 1
```

- **Synced with the VS Code kittys.** Every thread is a session on a separate tmux server
  (`tmux -L agents`). The kitty beside a VS Code window starts in one (kitty-pair), and agentmux's
  middle pane attaches to the same one: one agent, two live views, nothing restarted or copied. A
  new thread made in agentmux also opens as a tab in that project's VS Code kitty. The agent
  redraws at the size of the view you last typed in.
- **`Super + N`** on its workspace (4) starts a new thread (`Ctrl+Alt+N`), opens agentmux if it
  isn't open, or asks for a project first if none is open.
- **The home screen** (the middle pane with no thread shown) has a prompt box: `↵` starts a thread
  in the agent picked with `Tab`, your text as its first message. Above it are the project's threads,
  or with none yet, every agent (uninstalled ones greyed out): click one (or `↑↓` `↵`) to start it.
- **New thread** asks which agent: Claude Code, opencode, agy, Codex (the installed ones; the others
  greyed out). Any number of threads, of any agent, per project; each is its own session. This
  picker and the project picker are GTK popups like every other panel (blurred backdrop; Esc or a
  click outside closes them).
- **The Projects list** shows what you opened, what has a thread and what's open in VS Code, in a
  fixed order: each project keeps the place it first got (a new one goes last), so nothing
  moves as threads and VS Code windows come and go (`order` in `state.json`).
- **Picking a project** (`Ctrl+Alt+O`, or `+ Open project`) is a folder browser. The first time (agentmux
  opens it by itself) it asks for your projects folder, the **root**, and remembers it
  (`~/.local/state/agentmux/state.json`); after that it opens there. `→` or a click goes into a folder,
  `←` goes up (also above the root, to open something anywhere), `↵` opens the selected folder (or
  the shown one: "Open this folder"), `^R` makes the shown folder the root. Typing searches folders
  up to 3 levels below (fuzzy, like the shortcut list); a query starting with `/` or `~` is a path.
  Git repos show a branch icon, open projects a `●`.
- **Opening a project** with no live thread continues the latest session of every agent used there,
  each in its own thread (`claude --continue`, `opencode --continue`, `codex resume --last`; agy
  can't continue, it starts fresh). agentmux remembers per project which agents you used; closing a
  thread yourself (`x`, `×`, `Ctrl+Alt+X`) forgets its agent there, closing the project doesn't. A
  project with nothing remembered (a new one) starts nothing: the home screen asks which agent, with
  the keyboard on its prompt. `+ thread` starts fresh. Skipped for an
  agent whose conversation already runs straight in the project's VS Code kitty.
- **Panels close and reopen:** `Ctrl+Alt+1` / `Ctrl+Alt+2` / `Ctrl+Alt+3` (Projects / Threads / Terminals), or the `×` in a
  panel's header; a closed one shows as `▸ projects Ctrl+Alt+1` in the status bar (click it) and stays closed
  until reopened. Closing Terminals only hides the column: its shells keep running.
- **States and notifications, for every agent.** Each thread shows *needs you* (a permission prompt,
  a question, a plan to approve), *error*, *working*, *done* (finished, not looked at yet), *waiting*
  or *agent exited*; a desktop notification comes when it needs you (stays until you act), finishes,
  fails or exits, once, and not for the thread you're looking at; clicking it opens the thread.
  Where it comes from: the agents' own hooks (`agentmux hooks` installs them into
  `~/.claude/settings.json`, `~/.codex/hooks.json` + `codex_hooks` in `~/.codex/config.toml`,
  `~/.gemini/config/hooks.json`; `setup/setup-dev.sh` runs it), and the screen, read by tmux in the
  sidebars' subscription: "esc to interrupt" (agy: "esc to cancel") = working, a dialog's footer
  ("Esc to cancel", Codex "Press a number to choose", opencode "Permission required", agy "↑/↓
  Navigate") = needs you. opencode 2.x has no hooks: screen only. **Subagents count as working**: a
  thread stays *working* ("· 2 agents") while its subagents or background agents run, even after its
  own turn ended, and notifies *done* when the last one finishes (SubagentStart / SubagentStop hooks,
  Claude's `◯ <agent> <task>` rows on screen, agy's `fullyIdle`).
- **Terminals are per project:** the column shows only in the projects where you opened it (`Ctrl+Alt+T`);
  switching to another project hides it (its shells keep running) and brings it back when you return.
- **Agents started before this** (straight in a kitty, not in tmux) show as `⚠ title`; Enter on
  one (or `a`) **adopts** it: `/exit`, then the same conversation resumes in tmux in that same kitty
  (`claude --resume <its session>`, found by its title). Only when you choose; it asks first.
- Keys (no prefix; Ctrl+Alt so nothing clashes with Hyprland or Claude Code), all **remappable** in
  Settings: `Ctrl+Alt+P` / `Ctrl+Alt+A` / `Ctrl+Alt+M` / `Ctrl+Alt+E` focus Projects / Threads / the
  thread / the terminals (a closed one is brought back), `Ctrl+Alt+F` the next part (left to right,
  wrapping), `Ctrl+Alt+N` new thread, `Ctrl+Alt+O` open project, `Ctrl+Alt+T` terminal,
  `Ctrl+Alt+W` close terminal, `Ctrl+Alt+X` close, `Ctrl+Alt+1/2/3` panels, `Ctrl+Alt+←→`
  resize, `Ctrl+Alt+S` settings, `Ctrl+Alt+R` reload (configs read again, every pane restarted; agents
  and shells keep running), `Ctrl+Alt+?` this list. In the sidebars: `↑↓`, Enter or a click, the
  wheel; long lists scroll with the selection (`↑ more` / `↓ more`); Agents: `n` new, `a` adopt, `r` rescan.
- **Settings** (`Ctrl+Alt+S`, or System Settings → More settings → agentmux): a window laid out
  like System Settings (search, sections: Shortcuts, Notifications, Projects). Click a shortcut and
  press the new keys (Esc cancels); a key already in use is refused, and so are Super (Hyprland's) and
  Ctrl + a letter without Alt (the shell's and the agents'). `Default` / `Reset all` put them back.
  Switches for the notifications (needs you / finished / error / agent quit) and for resuming a
  project's agents when it opens. Saved in `config/agentmux/settings.json` (only once something
  changes; synced with the dotfiles) and applied at once: the keys, the status bar, `Ctrl+Alt+?` and
  the Super + K list follow.
- **Closing:** a project or thread shows its `×` when selected or under the pointer; a click on it
  asks right under that row without opening it (also `x` / `Delete` in a sidebar). The question says
  what ends; `y Close` (or `y` / `↵`) does it, `n Cancel` (`n`, `Esc`, a click elsewhere) doesn't.
  `Ctrl+Alt+X` (remappable in Settings) asks the same for the selected row of the focused sidebar,
  or else for the shown thread. Closing a thread ends its agent. Closing a project ends its threads
  and terminals and takes it off the list, except a thread open in a VS Code kitty (ending it would
  close that kitty): it stays, and the project stays listed while VS Code has it open.
- Light by design: the sidebars sleep until tmux reports a change (a control-mode subscription,
  checked by tmux once a second); nothing polls the agents. A thread nobody watches that is just an
  idle shell is closed when its last viewer leaves; running agents and terminals stay.
- Copy and paste work as in any terminal, in agentmux and in the VS Code kitty (both are tmux): the
  wheel scrolls the history; a drag (or double / triple click) selects; `Ctrl+C` copies the selection
  to the clipboard (nothing selected: interrupt, as usual); `Ctrl+V` pastes; typing or `Esc` drops the
  selection. A selection also goes to the primary selection (middle-click). Shift + drag is kitty's
  own selection (`Ctrl+Shift+C` / `V`).

Code: `local/bin/agentmux`, `local/lib/agentmux/` (`lib.py`, `sidebar.py`, `home.py`), `local/lib/panels/agentmuxgui.py` (settings), `local/lib/panels/agentpickgui.py` (the two pickers),
`config/agentmux/{agents,app}.conf`, `local/bin/kitty-pair`. Needs `tmux` (in `setup/packages.txt`).

### Docker panel (workspace 9)

`panels/dockergui.py`, workspace 9's app (a tiled window, class `sami.docker`). Tabs for
Containers, Images, Volumes and Networks (`1`–`4`, `Tab`, `←→` or a click); the list on the left,
the selected item's details, actions and logs on the right.

- Containers are grouped by compose project. A project is a row too: Stop all / Start all,
  Restart all, Logs (all its containers in one stream), and Fold (`Enter`) to hide its containers.
- Container: `s` start/stop · `r` restart · `l` logs · `e` exec · `o` open the first port.
  Logs are formatted by `panels/dockerlogs.py` (local time, level, message, details dimmed);
  `l` follows them in `less` in a floating terminal. Exec offers the container's shell, presets
  and "as root".
- Images: `u` pull. Images, volumes, networks: `d` twice removes it (docker refuses if in use).
- `L` (or the lazydocker button) opens lazydocker in a floating terminal.

### Control Center (workspace 10)

`panels/ccgui.py`, workspace 10's app (class `sami.controlcenter`): one GTK window, five cards,
all visible at once, in the look of the original terminal version (`controlcenter.py`) —
Workspaces and System on top; Shortcuts, Audio and Network below. Every card has an icon and
title with details on the right, a blue-then-grey underline, and its own key hints at the bottom.

| Card | Shows | Keys |
|---|---|---|
| Workspaces | the ten workspaces: name, window class, a dot per open window; the selected one's launch command | `↑↓` / `1`–`0` select, `g` / Enter go, `l` launch, `t` tidy; `a` `e` `d` open the full manager |
| System | CPU, memory, disk, GPU (bar in green / amber / red), the biggest apps; load in the footer | — |
| Shortcuts | search and every shortcut | type to search, `esc` clear |
| Audio | outputs, inputs, what's playing; ● marks the default | `←→` volume, `m` mute, Enter default, `w` wiremix |
| Network | the connection, down / up with the last minute as bars, the details | Enter manage connections |

Click a card (or Tab into it) to focus it — blue border; keys go to it. It starts at login in the
background (`wsgroups launch 10 --background` in `autostart.lua`); `Super + Ctrl + 0` goes there.

## Shortcut list

`Super + K` (or the keyboard icon in waybar) opens a window listing every
shortcut, read live from `binds.lua`, `apps.conf` and `workspaces.conf`. Type to search, Esc clears / closes.
The search is fuzzy (plain letter matching, no index): a word matches as a substring, as an
abbreviation (`nwin` → "next window", `kpaw` → "keep screen awake") or with one typo (`fulscren`);
the best matches come first, with the matched letters highlighted. `win` / `mod` also find Super.
A bind's description comes from a built-in table in `panels/keys.py`; to label
a new bind yourself, end its line with a comment:

```lua
bind("SUPER + M", launch .. "mpv") -- open mpv
```

## Launcher and default apps

- `local/share/applications/*.desktop` hide apps from Walker (`NoDisplay=true`).
  Delete one to bring that app back.
- `config/mimeapps.list` sets default apps (imv for images, mpv for video).

## Editing

The files in `~/.config/*` are symlinks to this repo, so editing either path edits
the same file. Commit here afterwards.

Never replace a symlink with a regular file — some editors "save" by delete-and-
recreate, which silently detaches the file from the repo. Check with `ls -l ~/.config`.
