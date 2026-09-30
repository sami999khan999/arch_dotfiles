# dotfiles

CachyOS + Hyprland desktop configuration, riced after [Omarchy](https://github.com/basecamp/omarchy)
(its classic v3 stack: waybar, walker, mako, swayosd, hyprlock, hypridle, swaybg) in Tokyo Night.

## Bootstrap a new machine

Install CachyOS with Hyprland, then:

```bash
git clone https://github.com/sami999khan999/arch_dotfiles.git ~/dotfiles
cd ~/dotfiles
./install.sh --dry-run --packages   # preview, changes nothing
./install.sh --packages             # packages + /etc files + symlinks
```

Then log out and back in. Re-run `./install.sh` (no flag) any time to relink.

`system/root-setup.sh` also mounts this PC's data drive — machine-specific, don't
run it elsewhere.

## Layout

| Path | Links to | Contains |
|---|---|---|
| `config/` | `~/.config/` | settings — hypr, noctalia, gtk, qt, terminals, fish |
| `local/share/` | `~/.local/share/` | assets — cursor theme, wallpapers, launcher overrides |
| `icons/` | `~/.icons/` | legacy cursor stub (`default/index.theme`) |
| `system/` | *manual, needs root* | `/etc` files — Chrome policy |
| `packages.txt` | — | explicit pacman packages |
| `packages-aur.txt` | — | AUR packages (install with `paru`) |

`install.sh` is idempotent and never deletes: anything real it finds in the way
is moved to `~/.config-backup-<timestamp>/` first.

## Settings vs assets

Two different things, two locations:

- **`config/`** names a theme  — e.g. `gtk-theme-name=adw-gtk3`
- **`local/share/`** holds the theme itself

A setting pointing at an absent asset fails silently. Most themes here come from
packages (`/usr/share/themes`, `/usr/share/icons`), so `packages.txt` matters as
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
| Hyprland borders | `config/hypr/config/colors.lua` |
| waybar, swayosd | `config/waybar/style.css`, `config/swayosd/style.css` |
| walker | `config/walker/themes/omarchy-default/style.css` |
| mako | `config/mako/config` |
| hyprlock | `config/hypr/hyprlock.conf` |
| sysmon | `config/hypr/scripts/sysmon.py` |
| kitty, alacritty, btop | `themes/tokyo-night.*` in each |
| GTK 3/4, Qt | `gtk-*/tokyo-night.css`, `qt6ct/colors/tokyo-night.conf` |
| KDE apps (Dolphin) | `config/kdeglobals` |

Wallpapers live in `local/share/backgrounds/wallpapers`; `Super+Ctrl+Space`
cycles them. Helper scripts (power menu, toggles, screenshots) are in
`config/hypr/scripts/`.

Noctalia is still installed but no longer autostarted.

## Development

`./install.sh --packages` installs everything below, then runs `setup-dev.sh`
for the toolchains that don't come from pacman.

| Tool | Installed by | Update with |
|---|---|---|
| docker (+ compose, buildx, lazydocker), go, uv, cmake, ninja, CLI tools | pacman (`packages.txt`) | `sudo pacman -Syu` |
| VS Code (Microsoft build) | AUR (`packages-aur.txt`) | `paru -Syu` |
| Node LTS (+ npm), pnpm, bun | mise (`config/mise/config.toml`) | `mise upgrade` |
| Rust stable, clippy, rustfmt, rust-analyzer | rustup | `rustup update` |
| Python 3 | system python; use `uv` for venvs and tools | `sudo pacman -Syu` |

- Git settings (identity, delta diffs, rebase on pull): `config/git/config`.
  The gh login helper stays in `~/.gitconfig`, which is per machine.
- VS Code: settings and keybindings are not in this repo; they come from VS Code Settings Sync
  (GitHub account). Extensions are listed in `vscode-extensions.txt`.
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
click opens the Code Sync panel (backed-up files/folders/size, what the ignore list skips, free space,
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
The backup drive must be mounted at boot on that PC (an `/etc/fstab` line, not in these dotfiles).
The program is `local/bin/codesync`.

## App hotkeys (AutoHotkey-style)

`config/hypr/apps.conf` maps keys to apps, one per line:

```
SUPER + B    | google-chrome     | google-chrome-stable
```

The key jumps to that app's window on any workspace, cycles through its windows
on repeat presses, and launches it if nothing is open. Find a window's class with
`hyprctl clients | grep class`, then `hyprctl reload`. Loaded by
`config/hypr/config/apps.lua`, run by `scripts/focus-or-launch.sh`.

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
| `Super + Ctrl + G`, or click it in waybar | open the manager: map open apps to workspaces, edit, launch |
| `Alt + 1…0` | switch to window N of the current workspace (in the order they were opened) |
| `Alt + Tab` / `Alt + Shift + Tab` | next / previous window of the current workspace, same order |
| `Super + Ctrl + 1…0` | go to workspace N; launch its programs if none are open |
| `Super + N` | open another window of the current workspace's app (a new Chrome window on Web…) |
| `wsgroups launch N\|all [-f]` | launch a group's programs |
| `wsgroups tidy` | move already-open windows to their workspaces |
| `wsgroups list` | show groups and numbered windows |

Several launch commands are separated with `;` and open in that order. Alt + 1…0 is
taken over everywhere, so Chrome tabs switch with `Ctrl + 1…8` instead.
Loaded by `config/hypr/config/wsgroups.lua`; the program is `local/bin/wsgroups`.

### Docker panel (workspace 9)

`scripts/dockerpanel.py`, in the same card style as the Control Center. Four tabs (`1`–`4`,
`Tab`, `←→` or a click), each a list on the left and the selected item's details on the right:

| Tab | List | Details |
|---|---|---|
| Containers | grouped by compose project: status dot, CPU bar, memory, first port | image, uptime, ports, CPU/memory, project folder, latest logs |
| Images | in use / unused: size, age | ID, which containers use it, layers |
| Volumes | grouped by compose project: size, which containers use it | full name, driver, mountpoint |
| Networks | created / built in: driver, subnet, container count | gateway, the containers on it and their IPs |

- Containers: `s` start/stop · `r` restart · `o` open the first port in the browser
- Compose projects work like Docker Desktop: each project's heading is a row you can select.
  Its details show the folder, totals and every container's state. There, `s` stops the project
  if anything in it runs (else starts all of it), `r` restarts it, `l` follows all its logs in
  one stream, and `Enter`/`Space` folds it. Only the containers that are changing say
  "stopping…"/"starting…".
- Logs are formatted by `scripts/dockerlogs.py`: local time, a coloured level badge (ERR / WRN /
  INF / DBG), the message, then `key=value` details dimmed; long lines wrap under the message and
  repeats collapse to `×N`. It reads JSON logs, logfmt, postgres / pgbouncer / redis / nginx
  prefixes and plain text. `l` opens them full screen in `less`, following new lines
  (`Ctrl+C` stops following, `F` resumes, `/` searches, `q` goes back).
- `e` exec: a prompt in the footer, pre-filled with the container's shell (bash, else ash / sh).
  Type any command instead, `↑↓` for history and presets (`env`, `ps aux`, `df -h`…), `Tab` to
  run it as root, `Enter` to run. A one-off command waits for Enter afterwards so you can read
  the output.
- Images: `u` pull a newer version. Images, volumes, networks: `d` twice removes it (docker
  refuses if something still uses it).
- `L` opens lazydocker for everything else (themed by `config/lazydocker/config.yml`).

### Control Center (workspace 10)

One window, five cards: workspace groups manager and system monitor on top; shortcuts, audio
and network below. `scripts/controlcenter.py` draws them all on one character grid, so the
gaps between cards are equal and every card has the same border, padding, header, section
titles and key-hint footer (`scripts/panelkit.py`). It starts at login in the background
(`wsgroups launch 10 --background` in `autostart.lua`); `Super + Ctrl + 0` reopens it.

- `Tab` / `Shift + Tab` or a click moves between cards (blue border = focused); every other
  key goes to that card.
- Audio: ←→ volume, m mute, Enter makes a device the default, w opens wiremix
  (themed by `config/wiremix/wiremix.toml`).
- Network: Enter opens nmtui.
- The cards also run on their own as popups: `sysmon.py`, `keys.py`, `netpanel.py`,
  `audiopanel.py`, `wsgroups`.
- Spacing: `config/kitty/controlcenter.conf` (padding) and `config/hypr/config/controlcenter.lua`
  (no window border, no outer gap on workspace 10).

## Shortcut list

`Super + K` (or the keyboard icon in waybar) opens a floating list of every
shortcut, read live from `binds.lua` and `apps.conf`. Type to search, Esc closes.
A bind's description comes from a built-in table in `scripts/keys.py`; to label
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
