#!/usr/bin/env bash
# Steps that require root. Run with: pkexec /home/sami/dotfiles/system/root-setup.sh
set -euo pipefail

echo "== 1. Chrome: block on-device AI model re-download"
mkdir -p /etc/opt/chrome/policies/managed
cat > /etc/opt/chrome/policies/managed/disable_ondevice_ai.json <<'JSON'
{
  "GenAILocalFoundationalModelSettings": 1
}
JSON
chmod 644 /etc/opt/chrome/policies/managed/disable_ondevice_ai.json
echo "   wrote /etc/opt/chrome/policies/managed/disable_ondevice_ai.json"

echo "== 2. Mount the data HDD (NTFS, by UUID) at /mnt/data"
mkdir -p /mnt/data
if mountpoint -q /mnt/data; then
  echo "   already mounted"
else
  # by UUID, never /dev/sdX: disk names can change between boots, and the wrong one could be Windows
  mount -t ntfs3 -o uid=1000,gid=1000,umask=022,windows_names,noatime UUID=6422C6A022C67718 /mnt/data
  echo "   mounted"
fi
findmnt /mnt/data || true

echo
echo "Done. /dev/sda was not touched."
