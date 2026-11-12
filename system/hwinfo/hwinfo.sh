#!/usr/bin/env bash
# hwinfo.sh — what only root can read about the hardware, saved where the bar can read it: each drive's
# SMART report (health, wear, hours, temperature) and the memory sticks (dmidecode: slot, size, type, speed).
# Run by hwinfo.timer (at boot, then every 30 min; system/root-setup.sh installs it) into /run/hwinfo,
# read by config/hypr/scripts/storage.py and memory.py. Only reads: smartctl -a and dmidecode change nothing.
set -u
out=/run/hwinfo
mkdir -p "$out"
chmod 755 "$out"
for dev in $(lsblk -dno NAME,TYPE | awk '$2 == "disk" && $1 !~ /^(zram|loop)/ { print $1 }'); do
  smartctl -j -a "/dev/$dev" > "$out/.smart-$dev.json" 2>/dev/null
  mv -f "$out/.smart-$dev.json" "$out/smart-$dev.json"
done
dmidecode -t memory > "$out/.memory.txt" 2>/dev/null && mv -f "$out/.memory.txt" "$out/memory.txt"
chmod 644 "$out"/*
