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
| accent | `#7aa2f7` blue, `#bb9af7` magenta |
| window & popup borders | `#414868` focused, `#292e42` unfocused |

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
- VS Code: only `User/settings.json` and `keybindings.json` are linked, since VS Code
  keeps caches in `~/.config/Code`. Extensions are listed in `vscode-extensions.txt`.
- Per-project versions: put a `mise.toml` in the project (`mise use node@22`).
- `Super + Shift + C` jumps to or opens VS Code.

## Syncing between PCs

```bash
dotsync                  # save local changes, pull the other PC's, push, relink
dotsync "what changed"   # same, with your own commit message
```

Run it after changing something, and before starting on the other PC.
`dotsync` lives in `local/bin/` and is linked into `~/.local/bin`.

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
| 4 | Docker | lazydocker |
| 5 | Files | Dolphin |
| 6 | Control | the Control Center (below) |

```
1 | Code     | code            | code ~/dotfiles
2 | Web      | google-chrome   | google-chrome-stable
```

Windows of that class always open on that workspace, maximized (waybar and gaps stay).
The waybar module on the right shows the current group and window, e.g. `Code 2/3`;
hover for the numbered window list.

| Key / command | Does |
|---|---|
| `Super + Ctrl + G`, or click it in waybar | open the manager: map open apps to workspaces, edit, launch |
| `Alt + 1…0` | switch to window N of the current workspace (in the order they were opened) |
| `Super + Ctrl + 1…0` | go to workspace N; launch its programs if none are open |
| `wsgroups launch N\|all [-f]` | launch a group's programs |
| `wsgroups tidy` | move already-open windows to their workspaces |
| `wsgroups list` | show groups and numbered windows |

Several launch commands are separated with `;` and open in that order. Alt + 1…0 is
taken over everywhere, so Chrome tabs switch with `Ctrl + 1…8` instead.
Loaded by `config/hypr/config/wsgroups.lua`; the program is `local/bin/wsgroups`.

### Control Center (workspace 6)

One kitty window (splits layout) with the waybar tools as panes: workspace groups manager,
audio (wiremix), shortcuts, network (nmtui) and system monitor. It starts at login in the
background (`wsgroups launch 6 --background` in `autostart.lua`); `Super + Ctrl + 6` reopens it
if closed. Panes are defined in `config/kitty/controlcenter.session`; each runs through
`scripts/panel.sh`, which restarts a tool when you quit it. Drag a border (or `ctrl+shift+r`)
to resize: each border only affects the two panes next to it.

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
