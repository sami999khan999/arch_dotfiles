#!/usr/bin/env bash
# screenshot.sh region | output — saves to ~/Pictures/Screenshots, copies, offers satty to annotate
dir="$(xdg-user-dir PICTURES)/Screenshots"
mkdir -p "$dir"
file="$dir/screenshot-$(date +%Y-%m-%d_%H-%M-%S).png"

case "${1:-region}" in
  region)
    # freeze the screen first, like Windows' Snipping Tool: hyprpicker -r shows a still copy of
    # the screen on top of everything, so you select (and grim captures) that moment, not a live
    # screen that keeps changing under the cursor. Always unfrozen again, also on Esc.
    hyprpicker -r -z >/dev/null 2>&1 &
    freeze=$!
    trap 'kill "$freeze" 2>/dev/null' EXIT
    sleep 0.15   # let the frozen copy appear before selecting
    geom=$(slurp -d) || exit 0
    grim -g "$geom" "$file"
    kill "$freeze" 2>/dev/null ;;
  output) grim -o "$(hyprctl activeworkspace -j | jq -r .monitor)" "$file" ;;
esac

wl-copy < "$file"
action=$(notify-send -i "$file" -A edit=Edit "Screenshot copied & saved" "${file/#$HOME/\~}")
[[ "$action" == edit ]] && satty --filename "$file" --output-filename "$file" --copy-command wl-copy
