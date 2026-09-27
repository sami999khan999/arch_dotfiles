#!/usr/bin/env bash
# screenshot.sh region | output — saves to ~/Pictures/Screenshots, copies, offers satty to annotate
dir="$(xdg-user-dir PICTURES)/Screenshots"
mkdir -p "$dir"
file="$dir/screenshot-$(date +%Y-%m-%d_%H-%M-%S).png"

case "${1:-region}" in
  region) geom=$(slurp -d) || exit 0; grim -g "$geom" "$file" ;;
  output) grim -o "$(hyprctl activeworkspace -j | jq -r .monitor)" "$file" ;;
esac

wl-copy < "$file"
action=$(notify-send -i "$file" -A edit=Edit "Screenshot copied & saved" "${file/#$HOME/\~}")
[[ "$action" == edit ]] && satty --filename "$file" --output-filename "$file" --copy-command wl-copy
