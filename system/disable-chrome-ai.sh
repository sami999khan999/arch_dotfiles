#!/bin/bash
set -e
mkdir -p /etc/opt/chrome/policies/managed
cat > /etc/opt/chrome/policies/managed/disable_ondevice_ai.json <<'JSON'
{
  "GenAILocalFoundationalModelSettings": 1
}
JSON
chmod 644 /etc/opt/chrome/policies/managed/disable_ondevice_ai.json
echo "OK: policy written"
cat /etc/opt/chrome/policies/managed/disable_ondevice_ai.json
