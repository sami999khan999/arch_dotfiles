#!/usr/bin/env bash
# The wallpaper, drawn by awww.   wallpaper.sh restore | next | <path> | blur <output> on|off
# Every picture also gets a blurred, darker copy (made once, cached): a screen whose workspace has
# windows shows that one, so the gaps around the windows are soft; an empty workspace shows the
# picture itself. modules/wallblur.lua says which screen gets which; awww fades between them without
# a restart (swaybg could only be killed and restarted, which flashed). Hyprland's own blur can't do
# it: it only reaches behind see-through windows, never the wallpaper.
dir="$HOME/.local/share/backgrounds/wallpapers"
state="${XDG_STATE_HOME:-$HOME/.local/state}/wallpaper"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/wallpaper"
blur_state="${XDG_RUNTIME_DIR:-/tmp}/wallblur"   # blur_state-<output>: on / off, what it shows now
mkdir -p "$(dirname "$state")" "$cache"

current=$(cat "$state" 2>/dev/null)
# fill (crop to the screen) or fit (whole picture, bars around it): the Settings panel's choice
mode=$(jq -r '.wallpaper.mode // "fill"' "$HOME/.config/hypr/settings.json" 2>/dev/null)
[[ "$mode" == fit ]] && resize=fit || resize=crop

daemon() {   # awww's daemon, started once; it keeps the pictures between calls
  awww query >/dev/null 2>&1 && return
  pkill -x swaybg   # the wallpaper before awww
  setsid uwsm app -- awww-daemon >/dev/null 2>&1 &
  for _ in $(seq 50); do awww query >/dev/null 2>&1 && return; sleep 0.1; done
}

blurred() {   # the picture's blurred copy (by name and date, so an edited picture gets a new one)
  local out="$cache/$(basename "${1%.*}")-$(stat -c %Y "$1")-blur.jpg"
  if [[ ! -f "$out" ]]; then
    # blurring a quarter-size copy is fast and as soft; darker, like Hyprland's blur behind windows
    magick "$1" -resize 25% -blur 0x8 -resize 400% -modulate 80 -quality 92 "$out.tmp.jpg" && mv "$out.tmp.jpg" "$out"
  fi
  echo "$out"
}

show() {   # show <output> on|off <transition>
  local pic="$current"
  [[ "$2" == on ]] && pic=$(blurred "$current")
  awww img "$pic" --outputs "$1" --resize "$resize" \
    --transition-type "$3" --transition-duration 0.3 --transition-fps 60 >/dev/null 2>&1
}

outputs() { hyprctl monitors -j | jq -r '.[].name'; }

case "${1:-restore}" in
  blur)
    [[ -n "$2" && -f "$current" ]] && command -v awww >/dev/null || exit 0
    echo "${3:-on}" > "$blur_state-$2"
    daemon
    show "$2" "${3:-on}" fade
    exit 0 ;;
  restore)
    [[ -f "$current" ]] || current="$dir/6-bloodborne.jpg" # default on a fresh machine
    [[ -f "$current" ]] || current=$(find -L "$dir" -maxdepth 1 -type f \( -name "*.jpg" -o -name "*.png" \) | sort | head -1) ;;
  next)
    mapfile -t walls < <(find -L "$dir" -maxdepth 1 -type f \( -name "*.jpg" -o -name "*.png" \) | sort)
    next="${walls[0]}"
    for i in "${!walls[@]}"; do
      [[ "${walls[$i]}" == "$current" ]] && next="${walls[$(( (i + 1) % ${#walls[@]} ))]}"
    done
    current="$next" ;;
  *) current="$1" ;;
esac

echo "$current" > "$state"
ln -sf "$current" "${state}-current" # hyprlock reads this link
if ! command -v awww >/dev/null; then   # not installed yet (setup/packages.txt): the plain picture
  pkill -x swaybg
  setsid uwsm app -- swaybg -i "$current" -m "$mode" >/dev/null 2>&1 &
  exit 0
fi
daemon
blurred "$current" >/dev/null   # ready before a window opens
for out in $(outputs); do
  show "$out" "$(cat "$blur_state-$out" 2>/dev/null || echo off)" "$([[ "$1" == restore ]] && echo none || echo fade)"
done
