# dotfiles

CachyOS + Hyprland + Noctalia desktop configuration.

## Bootstrap a new machine

```bash
git clone <this-repo> ~/dotfiles
cd ~/dotfiles
./install.sh --dry-run    # preview, changes nothing
./install.sh              # symlink everything into place

sudo pacman -S --needed - < packages.txt
```

Then log out and back in.

## Layout

| Path | Links to | Contains |
|---|---|---|
| `config/` | `~/.config/` | settings — hypr, noctalia, gtk, qt, terminals, fish |
| `local/share/` | `~/.local/share/` | assets — the Bibata cursor theme |
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

## Colors are generated, not configured

Noctalia derives a Material 3 palette from the current wallpaper and writes it into:

```
config/gtk-4.0/noctalia.css
config/qt6ct/colors/noctalia.conf
config/kitty|alacritty|btop theme fragments
~/.local/share/color-schemes/noctalia.colors
```

Those are **output**. Editing them does nothing lasting — they are overwritten on
the next wallpaper change, and are gitignored.

To change colors, edit `config/noctalia/config.toml`:

```toml
[theme]
mode = "dark"                    # or "light"
source = "wallpaper"             # or a fixed palette
wallpaper_scheme = "m3-tonal-spot"
```

Structure that Noctalia does *not* touch — icon sets, cursors, the adw-gtk3 widget
style, Hyprland borders and gaps — is configured normally and is tracked here.

## Editing

The files in `~/.config/*` are symlinks to this repo, so editing either path edits
the same file. Commit here afterwards.

Never replace a symlink with a regular file — some editors "save" by delete-and-
recreate, which silently detaches the file from the repo. Check with `ls -l ~/.config`.
