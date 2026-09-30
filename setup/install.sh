#!/usr/bin/env bash
# Bootstrap this configuration onto a machine.
# Idempotent: safe to re-run. Existing real files are backed up, never deleted.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # the repo root; this script lives in setup/
BACKUP="$HOME/.config-backup-$(date +%Y%m%d-%H%M%S)"
DRY=0 PKGS=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)  DRY=1 ;;
    --packages) PKGS=1 ;;   # also install packages + root-owned system files
    *) echo "usage: $0 [--dry-run] [--packages]" >&2; exit 1 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }

if (( PKGS )); then
  head_ "packages"
  if (( DRY )); then
    say "would install $(wc -l < "$REPO/setup/packages.txt") pacman + $(wc -l < "$REPO/setup/packages-aur.txt") AUR packages"
    say "would copy system/ files into /etc"
  else
    sudo pacman -S --needed - < "$REPO/setup/packages.txt"
    paru -S --needed - < "$REPO/setup/packages-aur.txt"
    sudo install -Dm644 "$REPO/system/disable_ondevice_ai.json" \
      /etc/opt/chrome/policies/managed/disable_ondevice_ai.json
    fc-cache -f >/dev/null
  fi
fi

# link <source-in-repo> <destination-in-home>
link() {
  local src="$1" dst="$2"
  [[ -e "$src" ]] || return 0

  # already correct
  if [[ -L "$dst" && "$(readlink -f "$dst")" == "$(readlink -f "$src")" ]]; then
    say "ok       ${dst/#$HOME/\~}"
    return 0
  fi
  if (( DRY )); then
    say "would link ${dst/#$HOME/\~}"
    return 0
  fi

  mkdir -p "$(dirname "$dst")"
  # move anything real out of the way first
  if [[ -e "$dst" || -L "$dst" ]]; then
    mkdir -p "$BACKUP/$(dirname "${dst#$HOME/}")"
    mv "$dst" "$BACKUP/${dst#$HOME/}"
    say "backed up ${dst/#$HOME/\~}"
  fi
  ln -s "$src" "$dst"
  say "linked   ${dst/#$HOME/\~}"
}

# link every child of a repo dir into a target dir
link_children() {
  local srcdir="$1" dstdir="$2"
  [[ -d "$srcdir" ]] || return 0
  local entry
  for entry in "$srcdir"/*; do
    [[ -e "$entry" ]] || continue
    link "$entry" "$dstdir/$(basename "$entry")"
  done
}

head_ "config  ->  ~/.config"
link_children "$REPO/config" "$HOME/.config"

head_ "assets  ->  ~/.local/share"
# icons/ themes/ fonts/ are SHARED namespaces - packages install into them too.
# Link the individual themes inside, never the directory itself.
for group in "$REPO"/local/share/*; do
  [[ -d "$group" ]] || continue
  link_children "$group" "$HOME/.local/share/$(basename "$group")"
done

head_ "scripts ->  ~/.local/bin"
link_children "$REPO/local/bin" "$HOME/.local/bin"

head_ "panels  ->  ~/.local/lib"
link_children "$REPO/local/lib" "$HOME/.local/lib"

head_ "cursors ->  ~/.icons"
link_children "$REPO/icons" "$HOME/.icons"

head_ "directories Noctalia regenerates"
if (( DRY )); then
  say "would create ~/.local/share/color-schemes, ~/.config/qt6ct/colors"
else
  mkdir -p "$HOME/.local/share/color-schemes" "$HOME/.config/qt6ct/colors"
  say "ok"
fi

if (( PKGS )); then
  if (( DRY )); then
    head_ "dev toolchains"
    say "would run setup-dev.sh (rust stable, node LTS/pnpm/bun, docker, VS Code extensions)"
  else
    "$REPO/setup/setup-dev.sh"
  fi
fi

if [[ -d "$BACKUP" ]]; then
  printf '\nReplaced files were saved to: %s\n' "$BACKUP"
fi

cat <<'EOF'

Done. Log out and back in so Hyprland reloads.
(Without --packages, install them with: ~/dotfiles/setup/install.sh --packages)
EOF
