#!/usr/bin/env python3
# netpanel.py — network status panel (Tokyo Night) for the Control Center.
#   enter  manage connections (nmtui)     esc / q  quit
import re, subprocess, time

INTERVAL = 1.0
HISTORY = 40

from panelkit import (Panel, run as show, suspend, FG, DIM, ACCENT, TRACK, GREEN, RED, CYAN, MAGENTA, BOLD, RESET,
                      fit, frame, card, spread, header, section, hints)
SPARK = "▁▂▃▄▅▆▇█"


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def active_device():
    """(device, type, state, connection) of the main connected device."""
    rows = [l.split(":") for l in run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device").splitlines()]
    rows = [r for r in rows if len(r) >= 4 and r[1] not in ("loopback", "bridge", "tun")]
    for r in rows:
        if r[2] == "connected":
            return r[0], r[1], r[2], r[3]
    return rows[0] if rows else ("-", "-", "disconnected", "")


def details(dev):
    ip = re.search(r"inet (\S+)", run("ip", "-4", "-o", "addr", "show", "dev", dev))
    gw = re.search(r"default via (\S+)", run("ip", "route", "show", "default", "dev", dev))
    dns = [l.split(":", 1)[1] for l in run("nmcli", "-t", "-f", "IP4.DNS", "device", "show", dev).splitlines() if ":" in l]
    wifi = ""
    for l in run("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi").splitlines():
        if l.startswith("yes:"):
            _, ssid, sig = l.split(":", 2)
            wifi = f"{ssid}  {sig}%"
    return (ip.group(1) if ip else "-"), (gw.group(1) if gw else "-"), ", ".join(dns) or "-", wifi


def counters(dev):
    try:
        base = f"/sys/class/net/{dev}/statistics"
        return int(open(f"{base}/rx_bytes").read()), int(open(f"{base}/tx_bytes").read())
    except OSError:
        return 0, 0


def rate(b):
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1024 or unit == "GB/s":
            return f"{b:.0f} {unit}" if unit == "B/s" else f"{b:.1f} {unit}"
        b /= 1024


def spark(values, width):
    values = values[-width:]
    top = max(values + [1])
    return "".join(SPARK[min(int(v / top * (len(SPARK) - 1) + 0.5), len(SPARK) - 1)] if v else "▁" for v in values)


def total(b):
    return rate(b)[:-2]  # "2.0 GB/s" -> "2.0 GB"


def render(dev, typ, state, conn, info, down, up, totals):
    cols, rows, w, pad = frame(90)
    ip, gw, dns, wifi = info
    icon = "\U000f05a9" if typ == "wifi" else "\U000f0200"
    ok = state == "connected"
    color = GREEN if ok else RED
    body = [spread(f"{color}●{RESET}  {FG}{BOLD}{fit(conn or 'Not connected', w - 18)}{RESET}", f"{color}{state}{RESET}", w), ""]
    # traffic: current rate and a sparkline of the last HISTORY seconds
    sw = max(w - 15, 8)
    for arrow, c, hist in (("↓", CYAN, down), ("↑", MAGENTA, up)):
        body.append(f"{c}{arrow}{RESET} {FG}{rate(hist[-1]):>10}{RESET}  {c}{spark(hist, sw)}{RESET}")
    body += ["", section("Details", w)]
    kv = [("IP", ip), ("Gateway", gw), ("DNS", dns)] + ([("Wi-Fi", wifi)] if wifi else [])
    kv += [("Received", total(totals[0])), ("Sent", total(totals[1]))]
    body += [f"{DIM}{k:<10}{RESET}{FG}{fit(v, w - 10)}{RESET}" for k, v in kv]
    head = header(icon, "Network", f"{dev} · {typ}", w)
    foot = hints([("enter", "manage connections")], w)
    return card(head, body, foot, rows, pad)


class NetworkPanel(Panel):
    interval = INTERVAL

    def __init__(self):
        self.down, self.up = [0.0] * HISTORY, [0.0] * HISTORY
        self.refresh()
        self.prev, self.prev_t = counters(self.dev), time.time()

    def refresh(self):
        self.dev, self.typ, self.state, self.conn = active_device()
        self.info, self.last_info = details(self.dev), time.time()

    def tick(self, details=True):
        now, cur = time.time(), counters(self.dev)
        dt = max(now - self.prev_t, 0.001)
        self.down = (self.down + [max(cur[0] - self.prev[0], 0) / dt])[-HISTORY:]
        self.up = (self.up + [max(cur[1] - self.prev[1], 0) / dt])[-HISTORY:]
        self.prev, self.prev_t = cur, now
        if details and now - self.last_info > 10:  # connection details change rarely
            self.refresh()

    def draw(self, w, h):
        return render(self.dev, self.typ, self.state, self.conn, self.info, self.down, self.up, self.prev)

    def key(self, k):
        if k in ("q", "ESC"):
            return "quit"
        if k == "ENTER":
            suspend(["nmtui"])
            self.refresh()


if __name__ == "__main__":
    show(NetworkPanel())
