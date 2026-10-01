#!/usr/bin/env bash
# The login screen (noctalia-greeter under greetd): installs greeter.toml and the desktop's current
# wallpaper into /var/lib/noctalia-greeter, owned by the greeter user. Idempotent; run it again after
# changing greeter.toml or the wallpaper. Takes effect at the next login screen.
# Run with: pkexec /home/sami/dotfiles/system/greeter/install.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DEST=/var/lib/noctalia-greeter

# whose wallpaper: the user who ran pkexec / sudo
user="${SUDO_USER:-$(id -nu "${PKEXEC_UID:-0}")}"
home="$(getent passwd "$user" | cut -d: -f6)"
wall="$(cat "$home/.local/state/wallpaper" 2>/dev/null || true)"   # set by scripts/wallpaper.sh
[[ -f "$wall" ]] || wall="$REPO/local/share/backgrounds/wallpapers/6-bloodborne.jpg"

install -d -o greeter -g greeter -m 0750 "$DEST"
install -o greeter -g greeter -m 0640 "$HERE/greeter.toml" "$DEST/greeter.toml"
echo "config:    $DEST/greeter.toml"

# greeter.toml points at wallpaper.jpg: convert anything else
if [[ "${wall,,}" == *.jpg || "${wall,,}" == *.jpeg ]]; then
  install -o greeter -g greeter -m 0640 "$wall" "$DEST/wallpaper.jpg"
else
  magick "$wall" "$DEST/wallpaper.jpg"
  chown greeter:greeter "$DEST/wallpaper.jpg"
  chmod 0640 "$DEST/wallpaper.jpg"
fi
echo "wallpaper: $DEST/wallpaper.jpg  (from ${wall/#$home/\~})"
echo "Done: the new look shows at the next login screen."
