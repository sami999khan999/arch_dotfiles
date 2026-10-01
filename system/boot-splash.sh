#!/usr/bin/env bash
# Boot and shutdown show the Plymouth loading screen (CachyOS boot animation) instead of scrolling
# text. Plymouth is already in the initramfs and `splash` on the kernel command line; what printed
# over it was the kernel and systemd status lines. This adds to GRUB's kernel command line:
#   quiet                       no kernel / systemd status messages
#   rd.udev.log_level=3         no early udev messages
#   vt.global_cursor_default=0  no blinking text cursor
# then rebuilds grub.cfg. Idempotent. Takes effect at the next boot.
# Run with: pkexec /home/sami/dotfiles/system/boot-splash.sh
set -euo pipefail

GRUB=/etc/default/grub
WANT=(quiet rd.udev.log_level=3 vt.global_cursor_default=0)

line=$(grep -E "^GRUB_CMDLINE_LINUX_DEFAULT=" "$GRUB")
value=${line#*=}
value=${value#[\'\"]}
value=${value%[\'\"]}
added=()
for opt in "${WANT[@]}"; do
  [[ " $value " == *" $opt "* ]] || { value="$value $opt"; added+=("$opt"); }
done

if (( ${#added[@]} )); then
  cp "$GRUB" "$GRUB.bak"
  sed -i "s|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT='${value# }'|" "$GRUB"
  echo "added to the kernel command line: ${added[*]}   (old file: $GRUB.bak)"
else
  echo "kernel command line already has: ${WANT[*]}"
fi
echo "now: $(grep -E '^GRUB_CMDLINE_LINUX_DEFAULT=' "$GRUB")"

grub-mkconfig -o /boot/grub/grub.cfg
echo "Done: the loading screen shows from the next boot (and at shutdown)."
