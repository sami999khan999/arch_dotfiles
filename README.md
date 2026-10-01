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

`system/root-setup.sh` (run once with `pkexec`) writes the Chrome policy and adds this PC's
data drive (`system/fstab-data`) to `/etc/fstab` — machine-specific, don't run it elsewhere.

## Layout

| Path | Links to | Contains |
|---|---|---|
| `config/` | `~/.config/` | settings — hypr, noctalia, gtk, qt, terminals, fish |
| `local/bin/` | `~/.local/bin/` | commands — `wsgroups`, `codesync`, `dotsync` |
| `local/lib/panels/` | `~/.local/lib/panels/` | the panels — GTK windows (`*gui.py`, shared look in `gtkkit.py`) and their older terminal versions (`panelkit.py`) |
| `local/share/` | `~/.local/share/` | assets — cursor theme, wallpapers, launcher overrides |
| `icons/` | `~/.icons/` | legacy cursor stub (`default/index.theme`) |
| `system/` | *manual, needs root* | `/etc` files — Chrome policy, this PC's data-drive fstab line |
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

Wallpapers live in `local/share/backgrounds/wallpapers`; `Super+Ctrl+Space`
cycles them. Helper scripts (power menu, toggles, screenshots) are in
`config/hypr/scripts/`.

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
  as `terminal.external.linuxExec`), also as its own app.
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
| 1 | Code | VS Code windows |
| 2 | Web | Chrome windows |
| 3 | Terminal | kitty |
| 7 | Files | Dolphin |
| 8 | Discord | Discord |
| 9 | Docker | the Docker panel (below) |
| 10 (key 0) | Control | the Control Center (below) |

```
1 | Code     | code            | code ~/dotfiles
2 | Web      | google-chrome   | google-chrome-stable
```

Windows of that class always open on that workspace, maximized (waybar and gaps stay).
A fullscreen video stays fullscreen when you switch away with `Alt + 1…0` or `Alt + Tab`,
and is fullscreen again when you come back (a mouse click on another window exits it).
The waybar module on the right shows the current group and window, e.g. `Code 2/3`;
hover for the numbered window list.

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

| Panel | Opens with | Does |
|---|---|---|
| Workspaces (`wsgui.py`) | `Super + Ctrl + G`, waybar groups icon | above |
| System (`sysgui.py`) | `Super + Ctrl + T`, waybar CPU / memory | tabs (`1`–`3`): **Overview** (CPU, memory, GPU — a 90 s graph and the details: temperature, clocks, swap, video memory, power…), **Processes** (every process; sort by a column, `/` search, End process / Kill on a second click), **Storage** (each drive: SSD / HDD, read / write now, each partition's usage) |
| Audio (`audiogui.py`) | `Super + Ctrl + A`, waybar volume | outputs, inputs, what's playing: volume, mute, make default; the Wiremix button opens wiremix |
| Network (`netgui.py`) | `Super + Ctrl + W`, waybar network | connection, traffic graph, addresses; "Manage connections" opens nmtui |
| Shortcuts (`keysgui.py`) | `Super + K`, waybar keyboard icon | every shortcut, live from the config; type to search |
| Code Sync (`syncgui.py`) | waybar sync icon | see Code backup above |

The older terminal versions (`sysmon.py`, `keys.py`, `audiopanel.py`, `netpanel.py`,
`syncpanel.py`, `dockerpanel.py`, `controlcenter.py`, `wsgroups tui`) still work if run directly.

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
shortcut, read live from `binds.lua` and `apps.conf`. Type to search, Esc clears / closes.
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
