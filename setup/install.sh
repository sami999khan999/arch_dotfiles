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
  # packages.txt carries the NVIDIA PC's driver; a PC without an NVIDIA card leaves those out
  pacman_list=$(grep -v '^#' "$REPO/setup/packages.txt")
  if ! lspci | grep -qi nvidia; then
    say "no NVIDIA card: skipping $(grep -i nvidia <<< "$pacman_list" | xargs)"
    pacman_list=$(grep -vi nvidia <<< "$pacman_list")
  fi
  if (( DRY )); then
    say "would install $(wc -l <<< "$pacman_list") pacman + $(wc -l < "$REPO/setup/packages-aur.txt") AUR packages"
    say "would copy system/ files into /etc"
  else
    sudo pacman -S --needed - <<< "$pacman_list"
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
  if [[ -L "$dst" && "$dst" -ef "$src" ]]; then   # -ef: the same file once resolved (no readlink processes)
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

head_ "waybar  ->  one bar per screen (a drop-in for its systemd service)"
link "$REPO/config/waybar/waybar.service.conf" "$HOME/.config/systemd/user/waybar.service.d/screens.conf"

head_ "tmux panes (dev servers, agents) swapped out last (systemd drop-ins)"
link "$REPO/setup/systemd/tmux-pane-memory.conf" "$HOME/.config/systemd/user/tmux-spawn-.scope.d/memory.conf"
for slice in app app-graphical; do
  link "$REPO/setup/systemd/app-memory.conf" "$HOME/.config/systemd/user/$slice.slice.d/memory.conf"
done
(( DRY )) || systemctl --user daemon-reload

head_ "cursors ->  ~/.icons"
link_children "$REPO/icons" "$HOME/.icons"

head_ "directories Noctalia regenerates"
if (( DRY )); then
  say "would create ~/.local/share/color-schemes, ~/.config/qt6ct/colors"
else
  mkdir -p "$HOME/.local/share/color-schemes" "$HOME/.config/qt6ct/colors"
  say "ok"
fi

# user timers, written by the tools themselves (idempotent, no questions): dotperf once a day, agentmux's
# usage at midnight (the daily budget), stacks (idle Docker stacks) every 15 min where Docker is installed;
# lsend, the LocalSend service (receives from the phone); downloads, the download manager (and its Chrome extension)
head_ "background checks (systemd user timers)"
if (( DRY )); then
  say "would enable dotperf.timer, agentmux-usage.timer$(command -v docker >/dev/null && echo ', stacks.timer'), lsend.service, downloads.service"
else
  "$REPO/local/bin/dotperf" enable >/dev/null
  "$REPO/local/bin/agentmux" usage enable >/dev/null
  "$REPO/local/bin/lsend" enable >/dev/null
  "$REPO/local/bin/downloads" enable >/dev/null
  if command -v docker >/dev/null; then "$REPO/local/bin/stacks" enable >/dev/null; fi
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

Once on a new PC, to make it the same as the others (MANUAL.md "Bootstrap a new machine"):
  pkexec ~/dotfiles/system/greeter/install.sh   login screen (greetd + noctalia-greeter)
  pkexec ~/dotfiles/system/boot-splash.sh       loading screen at boot and shutdown
  pkexec ~/dotfiles/system/swap-setup.sh        SSD swapfile after zram
  pkexec ~/dotfiles/system/root-setup.sh        Chrome policy, LocalSend through the firewall, dev servers at normal priority + kept in RAM, snapshots listable + 10 kept, Docker closed to the network + its on/off switch, the data drive where it's attached
  pkexec ~/dotfiles/system/dokploy-setup.sh     Dokploy (deploy dashboard on localhost:23300), off at boot
  codesync enable                               code backup (asks where the code is)
  sign in: VS Code Settings Sync, Chrome, claude / codex / opencode / agy
EOF
