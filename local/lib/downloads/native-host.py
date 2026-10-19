#!/usr/bin/env python3
# native-host.py — Chrome's native-messaging host for the download manager (sami.downloads; its manifest is
# written by `downloads enable`). The extension (chrome/) sends one message per download: {url, name, referrer,
# cookie, agent, size}; it's passed to the service and its answer goes back: {"ok": true} and Chrome cancels its
# own download, anything else and Chrome keeps it. Chrome starts this per message (sendNativeMessage).
import json, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from downloads import request


def main():
    size = sys.stdin.buffer.read(4)
    if len(size) < 4:
        return
    msg = json.loads(sys.stdin.buffer.read(struct.unpack("<I", size)[0]))
    msg.update(op="add", source="chrome")
    reply = request(msg) or {"ok": False, "error": "the download manager isn't running"}
    out = json.dumps(reply).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(out)) + out)
    sys.stdout.buffer.flush()


main()
