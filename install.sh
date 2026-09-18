#!/usr/bin/env bash
# Bootstrap this configuration onto a machine.
# Idempotent: safe to re-run. Existing real files are backed up, never deleted.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP="$HOME/.config-backup-$(date +%Y%m%d-%H%M%S)"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

say()  { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }

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

head_ "cursors ->  ~/.icons"
link_children "$REPO/icons" "$HOME/.icons"

head_ "directories Noctalia regenerates"
if (( DRY )); then
  say "would create ~/.local/share/color-schemes, ~/.config/qt6ct/colors"
else
  mkdir -p "$HOME/.local/share/color-schemes" "$HOME/.config/qt6ct/colors"
  say "ok"
fi

if [[ -d "$BACKUP" ]]; then
  printf '\nReplaced files were saved to: %s\n' "$BACKUP"
fi

cat <<'EOF'

Done. Remaining manual steps:
  1. Install packages:   sudo pacman -S --needed - < packages.txt
  2. System files need root (see system/):
       sudo cp system/disable_ondevice_ai.json /etc/opt/chrome/policies/managed/
  3. Log out and back in so Hyprland and Noctalia reload.
EOF
