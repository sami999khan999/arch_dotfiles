# dotfiles

CachyOS + Hyprland desktop configuration, riced after [Omarchy](https://github.com/basecamp/omarchy)
(its classic v3 stack: waybar, walker, mako, swayosd, hyprlock, hypridle, swaybg) in a Bloodborne theme.

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

## Theme: Bloodborne, hand-applied

Colors are taken from the wallpaper (`6-bloodborne.jpg`) and written directly
into each app. Nothing generates them:

| Role | Color |
|---|---|
| background / surface | `#0f0c0c` / `#1c1515` / `#2a1f1e` |
| text / dim | `#d8cbb8` (parchment) / `#7a6a5c` |
| accent — borders, highlights | `#7d1a14` (oxblood) |
| accent — as text | `#a3302a` |
| secondary | `#9a4a38` (ember), `#7d8b65` (moss), `#c9a25e` (brass) |

| App | File |
|---|---|
| Hyprland borders | `config/hypr/config/colors.lua` |
| waybar, swayosd | `config/waybar/style.css`, `config/swayosd/style.css` |
| walker | `config/walker/themes/omarchy-default/style.css` |
| mako | `config/mako/config` |
| hyprlock | `config/hypr/hyprlock.conf` |
| sysmon | `config/hypr/scripts/sysmon.py` |
| kitty, alacritty, btop | `themes/bloodborne.*` in each |
| GTK 3/4, Qt | `gtk-*/bloodborne.css`, `qt6ct/colors/bloodborne.conf` |
| KDE apps (Dolphin) | `config/kdeglobals` |

Wallpapers live in `local/share/backgrounds/wallpapers`; `Super+Ctrl+Space`
cycles them. Helper scripts (power menu, toggles, screenshots) are in
`config/hypr/scripts/`.

Noctalia is still installed but no longer autostarted.

## Launcher and default apps

- `local/share/applications/*.desktop` hide apps from Walker (`NoDisplay=true`).
  Delete one to bring that app back.
- `config/mimeapps.list` sets default apps (imv for images, mpv for video).

## Editing

The files in `~/.config/*` are symlinks to this repo, so editing either path edits
the same file. Commit here afterwards.

Never replace a symlink with a regular file — some editors "save" by delete-and-
recreate, which silently detaches the file from the repo. Check with `ls -l ~/.config`.
