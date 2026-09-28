#!/usr/bin/env python3
# netpanel.py — network status panel (Tokyo Night) for the Control Center.
#   enter  manage connections (nmtui)     esc / q  quit
import os, re, select, shutil, subprocess, sys, termios, time, tty

INTERVAL = 1.0
HISTORY = 40

def rgb(h): return f"\033[38;2;{int(h[1:3],16)};{int(h[3:5],16)};{int(h[5:7],16)}m"
FG, DIM, ACCENT, TRACK = rgb("#c0caf5"), rgb("#565f89"), rgb("#6b8fe0"), rgb("#292e42")
GREEN, YELLOW, RED, CYAN, MAGENTA = rgb("#9ece6a"), rgb("#e0af68"), rgb("#f7768e"), rgb("#7dcfff"), rgb("#bb9af7")
BOLD, RESET = "\033[1m", "\033[0m"
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


def render(dev, typ, state, conn, info, down, up):
    cols, rows = shutil.get_terminal_size()
    w = min(cols - 4, 90)
    pad = " " * max((cols - w) // 2, 0)
    ip, gw, dns, wifi = info
    icon = "\U000f05a9" if typ == "wifi" else "\U000f0200"
    sub = f"{dev} · {typ}"
    out = [pad + f"{ACCENT}{icon}{RESET}  {FG}{BOLD}Network{RESET}" + " " * max(w - 11 - len(sub), 1) + f"{DIM}{sub}{RESET}",
           pad + f"{TRACK}{'━' * w}{RESET}", ""]
    ok = state == "connected"
    dot = f"{GREEN}●{RESET}" if ok else f"{RED}●{RESET}"
    out.append(pad + f"{dot}  {FG}{BOLD}{conn or 'Not connected'}{RESET}" + " " * max(w - 5 - len(conn or 'Not connected') - len(state), 1)
               + (f"{GREEN}" if ok else f"{RED}") + state + RESET)
    out.append("")
    gw_ = max(w - 22, 8)
    out.append(pad + f"   {CYAN}↓{RESET}  {FG}{rate(down[-1]):>10}{RESET}  {CYAN}{spark(down, gw_)}{RESET}")
    out.append(pad + f"   {MAGENTA}↑{RESET}  {FG}{rate(up[-1]):>10}{RESET}  {MAGENTA}{spark(up, gw_)}{RESET}")
    out.append("")
    rows_kv = [("IP", ip), ("Gateway", gw), ("DNS", dns)] + ([("Wi-Fi", wifi)] if wifi else [])
    for k, v in rows_kv:
        out.append(pad + f"   {DIM}{k:<9}{RESET}{FG}{v[:w - 12]}{RESET}")
    out += [""] * max(rows - len(out) - 1, 0)
    out.append(pad + f"{DIM}enter  manage connections{RESET}")
    sys.stdout.write("\033[H\033[2J" + "\n".join(out[:rows]))
    sys.stdout.flush()


def enter_screen():
    sys.stdout.write("\033[?1049h\033[?25l\033[?7l")
    sys.stdout.flush()


def leave_screen():
    sys.stdout.write("\033[?7h\033[?25h\033[?1049l")
    sys.stdout.flush()


def main():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    enter_screen()
    down, up = [0.0] * HISTORY, [0.0] * HISTORY
    dev, typ, state, conn = active_device()
    info, last_info = details(dev), time.time()
    prev, prev_t = counters(dev), time.time()
    try:
        while True:
            render(dev, typ, state, conn, info, down, up)
            r, _, _ = select.select([sys.stdin], [], [], INTERVAL)
            if r:
                k = os.read(fd, 8)
                if k in (b"q", b"\x1b"):
                    break
                if k in (b"\r", b"\n"):
                    leave_screen()
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                    subprocess.run(["nmtui"])
                    tty.setcbreak(fd)
                    enter_screen()
                    dev, typ, state, conn = active_device()
                    info = details(dev)
                continue
            now, cur = time.time(), counters(dev)
            dt = max(now - prev_t, 0.001)
            down = (down + [max(cur[0] - prev[0], 0) / dt])[-HISTORY:]
            up = (up + [max(cur[1] - prev[1], 0) / dt])[-HISTORY:]
            prev, prev_t = cur, now
            if now - last_info > 10:  # connection details change rarely
                dev, typ, state, conn = active_device()
                info, last_info = details(dev), now
    except KeyboardInterrupt:
        pass
    finally:
        leave_screen()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
