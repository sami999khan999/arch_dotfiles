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

# blurred, under a dark Tokyo Night overlay (the greeter can't blur by itself), so the login card
# stands out. Shrink → blur → enlarge: looks like a big blur, takes half a second instead of minutes.
magick "$wall" -resize 1920x1080^ -gravity center -extent 1920x1080 \
  -resize 25% -blur 0x4 -resize 1920x1080! -fill "#1a1b26" -colorize 45% -quality 92 "$DEST/wallpaper.jpg"
chown greeter:greeter "$DEST/wallpaper.jpg"
chmod 0640 "$DEST/wallpaper.jpg"
echo "wallpaper: $DEST/wallpaper.jpg  (blurred, from ${wall/#$home/\~})"
echo "Done: the new look shows at the next login screen."
