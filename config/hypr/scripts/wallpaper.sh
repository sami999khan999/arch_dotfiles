#!/usr/bin/env bash
# Wallpaper cycling with swaybg.   wallpaper.sh restore | next | <path>
dir="$HOME/.local/share/backgrounds/wallpapers"
state="${XDG_STATE_HOME:-$HOME/.local/state}/wallpaper"
mkdir -p "$(dirname "$state")"

mapfile -t walls < <(find -L "$dir" -maxdepth 1 -type f \( -name "*.jpg" -o -name "*.png" \) | sort)
current=$(cat "$state" 2>/dev/null)

case "${1:-restore}" in
  restore)
    [[ -f "$current" ]] || current="$dir/6-bloodborne.jpg" # default on a fresh machine
    [[ -f "$current" ]] || current="${walls[0]}" ;;
  next)
    next="${walls[0]}"
    for i in "${!walls[@]}"; do
      [[ "${walls[$i]}" == "$current" ]] && next="${walls[$(( (i + 1) % ${#walls[@]} ))]}"
    done
    current="$next" ;;
  *) current="$1" ;;
esac

echo "$current" > "$state"
ln -sf "$current" "${state}-current" # hyprlock reads this link
pkill -x swaybg
setsid uwsm app -- swaybg -i "$current" -m fill >/dev/null 2>&1 &
