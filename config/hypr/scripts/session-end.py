#!/usr/bin/env python3
"""session-end.py poweroff|reboot|logout — close every app the way you would, then shut down, restart or log out.

Called by the power menu (local/lib/panels/powergui.py). Going straight to `systemctl poweroff` leaves
systemd to SIGTERM / SIGKILL whatever is open: Chrome comes back with "didn't shut down correctly",
an editor's unsaved work is at the mercy of a signal. Here every window is closed first, as Super + W
would; an app with several windows (Chrome, VS Code) is told to quit instead (SIGTERM, which Chromium
and Electron treat as File → Quit), so it keeps all of them for next time rather than only the last one.
The rest (services, tmux, Docker) is systemd's, and it stops them gracefully.

If a window is still there after WAIT seconds it's likely asking something (save changes?): nothing
happens, that window is focused and a notification says why. Close it and pick the action again.
"""
import os, signal, subprocess, sys, time

sys.path.insert(0, os.path.expanduser("~/.local/lib/hypr"))
from hyprsock import query, request

ACTIONS = {"poweroff": ("Shutdown", ["systemctl", "poweroff"]),
           "reboot": ("Restart", ["systemctl", "reboot"]),
           "logout": ("Log out", ["uwsm", "stop"])}
WAIT = 15   # seconds the apps get to close (Chrome and VS Code take 1–3)


def windows():
    return [c for c in query("clients") or [] if c.get("mapped", True) and c.get("pid", -1) > 0]


def notify(title, body, urgency="normal"):
    subprocess.run(["notify-send", "-a", "Power", "-u", urgency, "-h", "string:x-canonical-private-synchronous:session-end",
                    title, body], stderr=subprocess.DEVNULL)


def close_all(wins):
    by_pid = {}
    for w in wins:
        by_pid.setdefault(w["pid"], []).append(w)
    for pid, own in by_pid.items():
        if len(own) > 1:
            try:
                os.kill(pid, signal.SIGTERM)
                continue
            except OSError:
                pass
        for w in own:
            request(f'dispatch hl.dsp.window.close({{ window = "address:{w["address"]}" }})')


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ACTIONS:
        sys.exit(f"usage: {os.path.basename(sys.argv[0])} {'|'.join(ACTIONS)}")
    name, cmd = ACTIONS[sys.argv[1]]
    wins = windows()
    if wins:
        apps = sorted({w["class"] or w["title"] for w in wins})
        notify(f"{name}…", f"Closing {len(apps)} app{'s' * (len(apps) != 1)}: {', '.join(apps)}")
        close_all(wins)
        deadline = time.monotonic() + WAIT
        while (left := windows()) and time.monotonic() < deadline:
            time.sleep(0.25)
        if left:
            request(f'dispatch hl.dsp.focus({{ window = "address:{left[0]["address"]}" }})')
            stuck = sorted({w["class"] or w["title"] for w in left})
            notify(f"{name} stopped", f"Still open: {', '.join(stuck)} (it may be asking to save). "
                   f"Close it, then {name.lower()} again.", "critical")
            sys.exit(1)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
