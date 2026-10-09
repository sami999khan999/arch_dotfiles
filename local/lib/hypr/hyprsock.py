# hyprsock.py — ask Hyprland something over its control socket, as hyprctl does, without starting
# hyprctl (about 5 ms and a process each; the bar asked about 9 times per focus change).
# Used by the bar's scripts (wsbar.py, wintitle.py, wsgroups) and the panels (gtkkit.hypr_socket).
import json, os, socket


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
