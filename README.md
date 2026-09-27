# dotfiles

CachyOS + Hyprland desktop configuration, riced after [Omarchy](https://github.com/basecamp/omarchy)
(its classic v3 stack: waybar, walker, mako, swayosd, hyprlock, hypridle, swaybg) in Kanagawa Dragon.

## Bootstrap a new machine

Install CachyOS with Hyprland, then:

```bash
git clone <this-repo> ~/dotfiles
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

## Theme: Kanagawa Dragon, hand-applied

[Kanagawa Dragon](https://github.com/rebelot/kanagawa.nvim) — picked to match the
Bloodborne wallpaper. Terminal colors are the upstream ports; everything else
is written directly into each app. Nothing generates them:

| Role | Color |
|---|---|
| background / surface | `#181616` / `#1D1C19` / `#282727` |
| text / dim | `#c5c9c5` / `#737c73`, highlight text `#C8C093` |
| window & popup borders | `#393836` focused, `#1D1C19` unfocused |
| selection / highlight bg | `#43242B` (winter red) |
| accent as text | `#c4746e` (dragon red) |
| secondary | `#b6927b` orange, `#87a987` green, `#c4b28a` yellow |

| App | File |
|---|---|
| Hyprland borders | `config/hypr/config/colors.lua` |
| waybar, swayosd | `config/waybar/style.css`, `config/swayosd/style.css` |
| walker | `config/walker/themes/omarchy-default/style.css` |
| mako | `config/mako/config` |
| hyprlock | `config/hypr/hyprlock.conf` |
| sysmon | `config/hypr/scripts/sysmon.py` |
| kitty, alacritty, btop | `themes/kanagawa-dragon.*` in each |
| GTK 3/4, Qt | `gtk-*/kanagawa-dragon.css`, `qt6ct/colors/kanagawa-dragon.conf` |
| KDE apps (Dolphin) | `config/kdeglobals` |

Wallpapers live in `local/share/backgrounds/wallpapers`; `Super+Ctrl+Space`
cycles them. Helper scripts (power menu, toggles, screenshots) are in
`config/hypr/scripts/`.

Noctalia is still installed but no longer autostarted.

## App hotkeys (AutoHotkey-style)

`config/hypr/apps.conf` maps keys to apps, one per line:

```
SUPER + B    | google-chrome     | google-chrome-stable
```

The key jumps to that app's window on any workspace, cycles through its windows
on repeat presses, and launches it if nothing is open. Find a window's class with
`hyprctl clients | grep class`, then `hyprctl reload`. Loaded by
`config/hypr/config/apps.lua`, run by `scripts/focus-or-launch.sh`.

## Launcher and default apps

- `local/share/applications/*.desktop` hide apps from Walker (`NoDisplay=true`).
  Delete one to bring that app back.
- `config/mimeapps.list` sets default apps (imv for images, mpv for video).

## Editing

The files in `~/.config/*` are symlinks to this repo, so editing either path edits
the same file. Commit here afterwards.

Never replace a symlink with a regular file — some editors "save" by delete-and-
recreate, which silently detaches the file from the repo. Check with `ls -l ~/.config`.
