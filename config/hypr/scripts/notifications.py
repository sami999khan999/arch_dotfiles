#!/usr/bin/env python3
# notifications.py — the notification log, the bell beside the clock (waybar custom/notifications) and
# do not disturb. The panel (Super + Ctrl + N, or a click on the bell) is local/lib/panels/notifgui.py.
#
#   notifications.py log <id>     mako's on-notify (config/mako/config): keep that notification in the log
#   notifications.py bar          the bell for waybar (signal 10): unread or not, silenced or not
#   notifications.py seen         everything is read (the panel opened)
#   notifications.py dismiss <n>  take one out of the log (and off the screen if it's still there)
#   notifications.py clear        empty the log, dismiss what's on screen
#   notifications.py dnd [on|off|toggle]   do not disturb (mako's do-not-disturb mode)
#
# mako keeps no times and only a few expired ones, so the log is ours: ~/.local/state/notifications.json,
# the last LIMIT, newest last. Nothing polls: mako runs `log` per notification, which signals waybar.
import fcntl, json, os, subprocess, sys, time

LOG = os.path.expanduser("~/.local/state/notifications.json")
LIMIT = 200
DND_APP = "Do not disturb"   # the app name of its own confirmations (dnd), left out of the log
BELL, UNREAD, SILENCED = "\U000f009a", "\U000f116b", "\U000f009b"   # bell, bell with a dot, bell crossed out


def load():
    try:
        with open(LOG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"items": [], "seen": 0}


def change(fn):
    """Run fn(log) under a lock (mako can log two at once) and save what it leaves."""
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load()
        fn(data)
        data["items"] = data["items"][-LIMIT:]
        tmp = f"{LOG}.{os.getpid()}"
        with open(tmp, "w") as f:   # a plain file in ~/.local/state, not a dotfiles link
            json.dump(data, f)
        os.replace(tmp, LOG)
    signal_bar()


def signal_bar():
    subprocess.run(["pkill", "-RTMIN+10", "-x", "waybar"], capture_output=True)


def mako(*args):
    r = subprocess.run(["makoctl", *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def on_screen():
    """The ids mako still shows."""
    try:
        return {n["id"] for n in json.loads(mako("list", "-j") or "[]")}
    except (ValueError, KeyError, TypeError):
        return set()


def silenced():
    return "do-not-disturb" in mako("mode").split()


def log(nid):
    try:
        n = next(n for n in json.loads(mako("list", "-j") or "[]") if str(n["id"]) == str(nid))
    except (ValueError, StopIteration, KeyError, TypeError):
        return
    if n.get("app_name") == DND_APP:   # do not disturb's own "on / silenced": not worth keeping
        return
    item = {"id": n["id"], "app": n.get("app_name") or "", "summary": n.get("summary") or "",
            "body": n.get("body") or "", "urgency": n.get("urgency") or "normal", "time": time.time(),
            "actions": list((n.get("actions") or {}).keys())}
    # mako's ids restart with mako: the log's own key is the time it came, unique enough
    change(lambda d: d["items"].append(item))


def unread(data=None):
    data = data or load()
    return sum(1 for i in data["items"] if i["time"] > data.get("seen", 0))


def bar():
    data = load()
    n = unread(data)
    if silenced():
        text, cls, tip = SILENCED, "silenced", "Do not disturb: notifications are silenced"
    elif n:
        text, cls, tip = UNREAD, "unread", f"{n} new notification{'s' * (n != 1)}"
    else:
        text, cls, tip = BELL, "", "No new notifications"
    tip += "\nClick: notifications · Right-click: do not disturb"
    print(json.dumps({"text": text, "class": cls, "tooltip": tip}, ensure_ascii=False))


def seen():
    change(lambda d: d.update(seen=time.time()))


def dismiss(key):
    """key: the item's time (the panel's handle on it)."""
    def drop(d):
        for i in [i for i in d["items"] if str(i["time"]) == str(key)]:
            if i["id"] in on_screen():
                mako("dismiss", "-n", str(i["id"]))
            d["items"].remove(i)
    change(drop)


def clear():
    mako("dismiss", "--all")
    change(lambda d: d.update(items=[], seen=time.time()))


def dnd(how="toggle"):
    on = {"on": True, "off": False}.get(how, not silenced())
    if on:
        subprocess.run(["notify-send", "-a", DND_APP, "-u", "low", "\U000f009b  Notifications silenced"])
        mako("mode", "-a", "do-not-disturb")
    else:
        mako("mode", "-r", "do-not-disturb")
        subprocess.run(["notify-send", "-a", DND_APP, "-u", "low", "\U000f009a  Notifications on"])
    signal_bar()


def main(argv):
    cmd, args = (argv[1] if len(argv) > 1 else "bar"), argv[2:]
    {"log": lambda: log(args[0]), "bar": bar, "seen": seen, "dismiss": lambda: dismiss(args[0]),
     "clear": clear, "dnd": lambda: dnd(args[0] if args else "toggle")}.get(cmd, bar)()


if __name__ == "__main__":
    main(sys.argv)
