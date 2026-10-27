#!/usr/bin/env python3
# native-host.py — Chrome's native-messaging host for the download manager (sami.downloads; its manifest is
# written by `downloads enable`). The extension (chrome/) sends one message per download: {url, name, referrer,
# cookie, agent, size}; it's passed to the service and its answer goes back: {"ok": true} and Chrome cancels its
# own download, anything else and Chrome keeps it. Chrome starts this per message (sendNativeMessage).
# {op: "tabs", windows: [...]} is the open tabs instead (each window's, sent when they change): written to
# TABS for Setups (local/lib/panels/sessionlib.py), not passed to the service. A window Setups is opening again
# (left in REGROUP, by its tabs) is looked for in it, and the reply tells the extension its tab groups and
# pinned tabs: {"regroup": [{window, pinned, groups}]}.
import json, os, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from downloads import request

TABS = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "setups", "chrome-tabs.json")
REGROUP = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "setups", "regroup.json")


def host(url):
    return url.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")


def regroup(windows):
    """The windows Setups is waiting for (REGROUP), found among the reported ones: the same number of tabs, at
    least half of them on the same site in the same place (a page may have redirected since). Those found are
    taken off the list; one not found within 2 minutes is dropped."""
    try:
        pending = json.load(open(REGROUP))
    except (OSError, ValueError):
        return []
    found, left, taken = [], [], set()
    for p in pending:
        if time.time() - p.get("at", 0) > 120:
            continue
        want = [host(u) for u in p["urls"]]
        best, score = None, 0
        for w in windows:
            tabs = [host(t.get("url") or "") for t in w.get("tabs", [])]
            same = sum(a == b for a, b in zip(tabs, want))
            # not one that has groups already (yours); of two alike, the newer (Chrome's ids only grow)
            if w.get("id") not in taken and len(tabs) == len(want) and same * 2 >= len(want) \
                    and not w.get("groups") and (same, w["id"]) > (score, best["id"] if best else -1):
                best, score = w, same
        if best:
            taken.add(best["id"])
            found.append({"window": best["id"], "pinned": p.get("pinned", []), "groups": p.get("groups", [])})
        else:
            left.append(p)
    if len(left) != len(pending):
        with open(REGROUP, "w") as f:
            json.dump(left, f)
    return found


def main():
    size = sys.stdin.buffer.read(4)
    if len(size) < 4:
        return
    msg = json.loads(sys.stdin.buffer.read(struct.unpack("<I", size)[0]))
    if msg.get("op") == "tabs":
        os.makedirs(os.path.dirname(TABS), exist_ok=True)
        with open(TABS, "w") as f:
            json.dump({"at": time.time(), "windows": msg.get("windows", [])}, f)
        reply = {"ok": True, "regroup": regroup(msg.get("windows", []))}
    else:
        msg.update(op="add", source="chrome")
        reply = request(msg) or {"ok": False, "error": "the download manager isn't running"}
    out = json.dumps(reply).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(out)) + out)
    sys.stdout.buffer.flush()


main()
