#!/usr/bin/env python3
"""fps.py — waybar module: the frames this bar's screen drew in the last second, out of its refresh rate.

Hyprland has no frame counter to ask, so this counts frames itself through wlr-screencopy: a 1x1-pixel
copy with damage, which Hyprland answers once per frame it actually draws on that output (nothing on a
still screen, up to the refresh rate while something moves). A pixel per frame costs next to nothing,
but while it runs Hyprland counts a screencast and won't scan a fullscreen game out directly.
The refresh rate comes from the same connection (wl_output's current mode), so a mode change follows.

waybar runs one copy per bar; each counts its own screen (WAYBAR_OUTPUT_NAME). Pure Python: the
Wayland wire protocol is small enough to speak over the socket. Prints one JSON line per change; on a
still screen it sleeps until the next frame instead of waking every second.
"""
import json, os, select, socket, struct, sys, time

OUTPUT = os.environ.get("WAYBAR_OUTPUT_NAME") or (sys.argv[1] if len(sys.argv) > 1 else "")
ICON = "\U000f04c5"  # nf-md-speedometer

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(os.path.join(os.environ["XDG_RUNTIME_DIR"], os.environ.get("WAYLAND_DISPLAY", "wayland-0")))
last_id = 1   # wl_display


# ---- the wire: messages are [object][size << 16 | opcode][arguments], little-endian, 4-byte aligned
def new_id():
    global last_id
    last_id += 1
    return last_id


def u32(*vals):
    return struct.pack(f"<{len(vals)}I", *vals)


def i32(*vals):
    return struct.pack(f"<{len(vals)}i", *vals)


def string(text):
    b = text.encode() + b"\0"
    return u32(len(b)) + b + b"\0" * (-len(b) % 4)


def send(obj, opcode, args=b"", fds=()):
    msg = u32(obj, ((8 + len(args)) << 16) | opcode) + args
    socket.send_fds(sock, [msg], list(fds)) if fds else sock.sendall(msg)


inbox = b""


def receive():
    """The complete messages that arrived: (object, opcode, arguments)."""
    global inbox
    data = sock.recv(65536)
    if not data:
        sys.exit(0)   # Hyprland went away; waybar starts a new copy (restart-interval)
    inbox += data
    msgs = []
    while len(inbox) >= 8:
        obj, size_op = struct.unpack_from("<II", inbox)
        size = size_op >> 16
        if len(inbox) < size:
            break
        msgs.append((obj, size_op & 0xFFFF, inbox[8:size]))
        inbox = inbox[size:]
    for obj, opcode, args in msgs:
        if obj == 1 and opcode == 0:   # wl_display.error
            sys.exit(f"wayland error: {args!r}")
    return msgs


def read_string(args, off):
    n = struct.unpack_from("<I", args, off)[0]
    return args[off + 4:off + 3 + n].decode(), off + 4 + n + (-n % 4)


def roundtrip(handle):
    """Every event up to now goes through handle(); returns once the server has answered a sync."""
    done = new_id()
    send(1, 0, u32(done))   # wl_display.sync
    while True:
        for obj, opcode, args in receive():
            if obj == done:
                return
            handle(obj, opcode, args)


# ---- setup: the globals, this bar's output, a 1x1 shm buffer
registry = new_id()
send(1, 1, u32(registry))   # wl_display.get_registry
globals_ = {}   # interface -> [(name, version)]


def on_global(obj, opcode, args):
    if obj == registry and opcode == 0:
        name = struct.unpack_from("<I", args)[0]
        iface, off = read_string(args, 4)
        globals_.setdefault(iface, []).append((name, struct.unpack_from("<I", args, off)[0]))


roundtrip(on_global)


def bind(iface, version, name=None):
    obj = new_id()
    name = name if name is not None else globals_[iface][0][0]
    send(registry, 0, u32(name) + string(iface) + u32(version, obj))
    return obj


if "zwlr_screencopy_manager_v1" not in globals_:
    sys.exit("no wlr-screencopy")
outputs = {bind("wl_output", min(v, 4), n): {} for n, v in globals_["wl_output"]}
shm = bind("wl_shm", 1)
copier = bind("zwlr_screencopy_manager_v1", 3)


def on_output(obj, opcode, args):
    if obj not in outputs:
        return
    if opcode == 4:   # name
        outputs[obj]["name"] = read_string(args, 0)[0]
    elif opcode == 1:   # mode: flags, width, height, refresh (mHz); flag 1 = the current one
        flags, _, _, mhz = struct.unpack_from("<IiiI", args)
        if flags & 1:
            outputs[obj]["hz"] = round(mhz / 1000)


roundtrip(on_output)
output = next((o for o, v in outputs.items() if v.get("name") == OUTPUT), None)
if output is None:
    sys.exit(f"no output {OUTPUT!r}")

fd = os.memfd_create("fps")
os.ftruncate(fd, 64)   # one pixel needs 4 bytes; the format's stride may round it up
pool = new_id()
send(shm, 0, u32(pool) + i32(64), [fd])   # wl_shm.create_pool
os.close(fd)
buffer, buffer_fmt = None, None
frame = None


def capture():
    """Ask for this output's next drawn frame (one pixel of it)."""
    global frame
    frame = new_id()
    send(copier, 1, u32(frame) + i32(0) + u32(output) + i32(0, 0, 1, 1))   # capture_output_region


# ---- the loop: count the frames, print once a second while it changes
def emit(fps):
    hz = outputs[output].get("hz", 0)
    shown = f"{fps:>{len(str(hz))}}"   # fixed width: the bar doesn't shift as the count changes
    print(json.dumps({"text": f"{ICON} {shown}/{hz}",
                      "tooltip": f"{OUTPUT}: {fps} frames drawn in the last second\nRefresh rate {hz} Hz",
                      "class": "idle" if fps == 0 else "moving"}, ensure_ascii=False), flush=True)


capture()
frames, shown, start, fmt = 0, None, time.monotonic(), None
while True:
    # nothing drawn and 0 shown: wait for the next frame however long it takes, and count the
    # second from when it comes
    still = frames == 0 and shown == 0
    if select.select([sock], [], [], None if still else max(0.0, start + 1 - time.monotonic()))[0]:
        if still:
            start = time.monotonic()
        for obj, opcode, args in receive():
            if obj == output:
                on_output(obj, opcode, args)   # a new mode: its refresh rate
            elif obj != frame:
                continue
            elif opcode == 0:   # buffer: format, width, height, stride
                fmt = struct.unpack_from("<IIII", args)
            elif opcode == 6:   # buffer_done: copy it at the next frame drawn (with damage)
                if fmt != buffer_fmt:   # first time, or the mode changed the format
                    if buffer is not None:
                        send(buffer, 0)   # wl_buffer.destroy
                    buffer, buffer_fmt = new_id(), fmt
                    f, w, h, stride = fmt
                    send(pool, 0, u32(buffer) + i32(0, w, h, stride) + u32(f))   # create_buffer
                send(frame, 2, u32(buffer))   # copy_with_damage
            elif opcode in (2, 3):   # ready / failed
                frames += opcode == 2
                send(frame, 1)   # destroy
                if opcode == 3:
                    time.sleep(1)   # the output is off or changing mode: try again shortly
                capture()
    now = time.monotonic()
    if now - start >= 1:
        if frames != shown:
            emit(frames)
            shown = frames
        frames, start = 0, now
