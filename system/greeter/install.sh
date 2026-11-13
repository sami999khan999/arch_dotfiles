#!/usr/bin/env bash
# The login screen (greetd): greetd's config (greetd.toml -> /etc/greetd) and greetd as the display manager
# (instead of whatever a fresh install came with); our greeter, the lock screen's look (greeter.py), copied
# root-owned to /usr/local/lib/dotfiles/greeter with what it reads (the wallpaper, the theme, the animation,
# the clock format: it can't read your home); noctalia-greeter, its fallback and its compositor:
# greeter.toml and the desktop's current wallpaper into /var/lib/noctalia-greeter, owned by the greeter user,
# both in the active colour theme (config/themes/active); your picture for the login card (AccountsService).
# greetd's PAM also unlocks the keyring at login.
# Idempotent; run it again after changing the greeter, the wallpaper, the colour theme, the lock screen's
# animation, the clock format or ~/.face.
# Takes effect at the next login screen.
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

if ! cmp -s "$HERE/greetd.toml" /etc/greetd/config.toml; then
  [[ -f /etc/greetd/config.toml ]] && cp /etc/greetd/config.toml /etc/greetd/config.toml.bak
  install -Dm644 "$HERE/greetd.toml" /etc/greetd/config.toml
  echo "greetd:    /etc/greetd/config.toml (old one: config.toml.bak)"
fi
if [[ "$(systemctl is-enabled greetd.service 2>/dev/null)" != enabled ]]; then
  systemctl enable --force greetd.service   # --force: takes display-manager.service over from another one
  echo "greetd:    the display manager from the next boot"
fi

# greetd's PAM unlocks the keyring with the login password (else every login asks for it again);
# the keyring's password has to be the login password
PAM=/etc/pam.d/greetd
if ! grep -q pam_gnome_keyring "$PAM"; then
  cp "$PAM" "$PAM.bak"
  sed -i -e '/^auth[[:space:]].*system-local-login/a auth       optional     pam_gnome_keyring.so' \
         -e '/^session[[:space:]].*system-local-login/a session    optional     pam_gnome_keyring.so auto_start' "$PAM"
  echo "pam:       $PAM unlocks the keyring at login (old one: greetd.bak)"
fi

# our greeter: the lock screen's code and what it needs from the repo, laid out as in the repo so its paths work
# the same (themelib finds config/themes three folders up), root-owned (the greeter user only reads it)
APP=/usr/local/lib/dotfiles/greeter
rm -rf "$APP"
install -d "$APP/local/lib/lockscreen" "$APP/local/lib/theme" "$APP/config/themes"
install -m644 "$REPO"/local/lib/lockscreen/*.py "$APP/local/lib/lockscreen/"
install -m644 "$REPO/local/lib/theme/themelib.py" "$APP/local/lib/theme/"
for t in "$REPO"/config/themes/*/theme.json; do
  install -Dm644 "$t" "$APP/config/themes/$(basename "$(dirname "$t")")/theme.json"
done
install -m644 "$REPO/config/themes/active" "$APP/config/themes/active" 2>/dev/null || true
install -m755 "$HERE/greeter" "$APP/greeter"
install -m644 "$wall" "$APP/wallpaper"   # magick reads it whatever its type
h24=true
[[ "$(runuser -u "$user" -- gsettings get org.gnome.desktop.interface clock-format 2>/dev/null)" == "'12h'" ]] && h24=false
python3 - "$APP" "$user" "$APP/wallpaper" "$h24" "$REPO/config/hypr/settings.json" <<'PY'
import json, shlex, sys
app, user, wall, h24, settings = sys.argv[1:]
try:
    anim = json.load(open(settings)).get("lockscreen", {}).get("animation", "ocean")
except (OSError, ValueError):
    anim = "ocean"
desk = {}
for line in open("/usr/share/wayland-sessions/hyprland-uwsm.desktop"):
    k, _, v = line.strip().partition("=")
    desk.setdefault(k, v)
json.dump({"user": user, "session": shlex.split(desk["Exec"]), "desktop": desk.get("DesktopNames", "Hyprland"),
           "wallpaper": wall, "animation": anim, "h24": h24 == "true"}, open(app + "/greeter.json", "w"), indent=2)
PY
python3 -m compileall -q "$APP/local/lib"     # it can't write its own __pycache__: compiled once, here
echo "greeter:   $APP  (the lock screen's look; noctalia-greeter if it fails)"

# the files are written in Tokyo Night's colours, like every file in the repo: the active theme swaps them
recolor() {
  python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import themelib; sys.stdout.write(themelib.recolor(sys.stdin.read()))' \
    "$REPO/local/lib/theme"
}
theme="$(cat "$REPO/config/themes/active" 2>/dev/null || echo tokyo-night)"

install -d -o greeter -g greeter -m 0750 "$DEST"
recolor < "$HERE/greeter.toml" > "$DEST/greeter.toml"
chown greeter:greeter "$DEST/greeter.toml"
chmod 0640 "$DEST/greeter.toml"
echo "fallback:  $DEST/greeter.toml  (noctalia-greeter; colours: $theme)"

# blurred, under a light overlay of the theme's base colour and a vignette of it (the greeter can't blur by
# itself): calm at the edges, the login card in focus. Shrink → blur → enlarge: looks like a big blur,
# takes half a second instead of minutes.
base="$(recolor <<< "#1a1b26")"
magick "$wall" -resize 1920x1080^ -gravity center -extent 1920x1080 \
  -resize 12% -blur 0x3 -resize 1920x1080! -fill "$base" -colorize 25% \
  \( -size 1920x1080 radial-gradient:none-"${base}cc" \) -compose over -composite -quality 92 "$DEST/wallpaper.jpg"
chown greeter:greeter "$DEST/wallpaper.jpg"
chmod 0640 "$DEST/wallpaper.jpg"
echo "wallpaper: $DEST/wallpaper.jpg  (blurred, from ${wall/#$home/\~})"

# the login card's picture: ~/.face if you have one, else your initial in the theme's accent. Through
# AccountsService (it keeps a copy the greeter can read: it can't read your home)
uid="$(id -u "$user")"
face="$home/.face"
if [[ ! -f "$face" ]]; then
  face="$DEST/avatar.png"
  font="$(fc-match -f '%{file}' 'JetBrainsMono Nerd Font:bold')"
  initial="$(printf '%s' "$user" | cut -c1 | tr '[:lower:]' '[:upper:]')"
  magick -size 256x256 xc:"$(recolor <<< "#292e42")" -font "$font" -pointsize 140 -fill "$(recolor <<< "#6b8fe0")" \
    -gravity center -annotate +0+0 "$initial" "$face"
fi
busctl call org.freedesktop.Accounts "/org/freedesktop/Accounts/User$uid" org.freedesktop.Accounts.User \
  SetIconFile s "$face"
echo "picture:   ${face/#$home/\~}"
echo "Done: the new look shows at the next login screen."
