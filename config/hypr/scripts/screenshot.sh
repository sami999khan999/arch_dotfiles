#!/usr/bin/env bash
# screenshot.sh region | output — saves to ~/Pictures/Screenshots, copies, offers satty to annotate
dir="$(xdg-user-dir PICTURES)/Screenshots"
mkdir -p "$dir"
file="$dir/screenshot-$(date +%Y-%m-%d_%H-%M-%S).png"

case "${1:-region}" in
  region)
    # freeze the screen first, like Windows' Snipping Tool: grim takes the whole screen now, and
    # hyprpicker -r shows a still copy of it on top of everything while you select. The shot is
    # then cut out of grim's picture, so nothing drawn on top while selecting (slurp's dimming,
    # border and size label, the freeze layer) can end up in it. Always unfrozen again, also on Esc.
    # (uncompressed: a PNG of both screens takes ~0.3 s, and slurp must be up before you drag)
    full=$(mktemp --suffix=.ppm) sel=$(mktemp)
    trap 'kill "$freeze" "$slurper" 2>/dev/null; rm -f "$full" "$sel"' EXIT
    grim -t ppm "$full" || exit 1
    hyprpicker -r -z -d >/dev/null 2>&1 &
    freeze=$!
    # the selection's border in the colour theme's accent (Tokyo Night's, recolored); no tint over
    # the screen (slurp's default is a white veil)
    accent=$(python3 -c "import sys,os;sys.path.insert(0,os.path.expanduser('~/.local/lib/theme'))
from themelib import recolor;print(recolor('#6b8fe0')[1:])" 2>/dev/null || echo 6b8fe0)
    sleep 0.15   # let the frozen copy appear before selecting
    slurp -d -b 00000000 -s 00000000 -c "${accent}ff" -w 1 >"$sel" &
    slurper=$!
    # Hyprland tells a newly shown layer where the pointer is in the wrong screen's coordinates
    # (with screens placed right-to-left: slurp then puts the pointer at the same spot on the other
    # screen), so a drag begun before the mouse moves started on the opposite screen. Once slurp is
    # up, put the cursor back where it is: that motion event carries the right position.
    read -r cx cy < <(hyprctl cursorpos | tr -d ,)
    for _ in {1..50}; do
      hyprctl layers -j | jq -e '[.. | objects | select(.namespace? == "selection")] | length > 0' >/dev/null && break
      sleep 0.02
    done
    hyprctl dispatch "hl.dsp.cursor.move({ x = $cx, y = $cy })" >/dev/null
    wait "$slurper" || exit 0
    geom=$(<"$sel")
    kill "$freeze" 2>/dev/null
    # slurp gives "X,Y WxH" in layout coordinates; grim's full picture starts at the layout's
    # top-left corner, at the highest screen scale
    read -r ox oy sc < <(hyprctl monitors -j | jq -r '"\(map(.x)|min) \(map(.y)|min) \(map(.scale)|max)"')
    read -r x y w h <<<"${geom//[,x]/ }"
    crop=$(awk -v x="$x" -v y="$y" -v w="$w" -v h="$h" -v ox="$ox" -v oy="$oy" -v s="$sc" \
      'BEGIN { printf "%dx%d+%d+%d", w*s+0.5, h*s+0.5, (x-ox)*s+0.5, (y-oy)*s+0.5 }')
    magick "$full" -crop "$crop" +repage "$file" ;;
  output) grim -o "$(hyprctl activeworkspace -j | jq -r .monitor)" "$file" ;;
esac

wl-copy < "$file"
action=$(notify-send -i "$file" -A edit=Edit "Screenshot copied & saved" "${file/#$HOME/\~}")
[[ "$action" == edit ]] && satty --filename "$file" --output-filename "$file" --copy-command wl-copy
