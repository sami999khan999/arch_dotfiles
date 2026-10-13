# hyprsock.py — ask Hyprland something over its control socket, as hyprctl does, without starting
# hyprctl (about 5 ms and a process each; the bar asked about 9 times per focus change).
# Used by the bar's scripts (wsbar.py, wintitle.py, wsgroups) and the panels (gtkkit.hypr_socket).
# follow(): the event socket, for the bar's daemons.
import json, os, socket, time


def request(raw):
    """One request ("j/clients", "dispatch …"), its reply as text ("" if Hyprland isn't there)."""
    path = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/hypr/{os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')}/.socket.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.connect(path)
            sock.sendall(raw.encode())
            reply = b""
            while chunk := sock.recv(65536):
                reply += chunk
        return reply.decode(errors="replace")
    except OSError:
        return ""


def query(what):
    """hyprctl <what> -j, parsed: "clients", "activewindow"… None if there's no answer."""
    try:
        return json.loads(request(f"j/{what}") or "null")
    except json.JSONDecodeError:
        return None


def follow(on_events, on_connect=None):
    """Hyprland's event socket, for ever: on_connect() once connected (catch up on what was missed), then
    on_events([line, …]) (bytes, without "\n") for each read. Reconnects when Hyprland closes it, after
    a pause that doubles up to 30 s: a socket that accepts and drops at once (Hyprland stopping) must not
    make a busy loop."""
    path = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/hypr/{os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')}/.socket2.sock"
    pause = 1
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(path)
                if on_connect:
                    on_connect()
                buf = b""
                while data := sock.recv(4096):
                    pause = 1
                    buf += data
                    *events, buf = buf.split(b"\n")
                    on_events(events)
        except OSError:
            pass
        time.sleep(pause)
        pause = min(pause * 2, 30)
