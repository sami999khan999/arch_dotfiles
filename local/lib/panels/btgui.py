#!/usr/bin/env python3
# btgui.py — the Bluetooth panel as a GTK window (Tokyo Night): your devices and the ones nearby.
# Running it again closes it.
#
#   My devices   paired ones: Connect / Disconnect, battery, Forget (a second click confirms)
#   Nearby       found by the scan that runs while the panel is open; Pair = pair, trust, connect
#   keys         ↑↓ select   Enter connect / disconnect / pair   f forget   p power   s scan
#
# Talks to BlueZ directly over D-Bus (org.bluez, Gio): no bluetoothctl to parse. While open it is
# also the pairing agent, so devices that show a code (phones, keyboards) can pair: the code is on
# the status line; only the device being paired from here is accepted. BlueZ stops the scan and
# drops the agent by itself when the panel's process exits.
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, Gio, GLib, Gtk, View, box, button, clear, label, rule_heading, run, scrolled, switch

BUS, ROOT = "org.bluez", "/"
ADAPTER, DEVICE, BATTERY = "org.bluez.Adapter1", "org.bluez.Device1", "org.bluez.Battery1"
PROPS, OBJECTS = "org.freedesktop.DBus.Properties", "org.freedesktop.DBus.ObjectManager"
AGENT_PATH = "/sami/panels/btagent"
AGENT_XML = """<node><interface name="org.bluez.Agent1">
  <method name="Release"/><method name="Cancel"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
  <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
</interface></node>"""

CSS = """
.bt-row { padding: 6px 0; }
.bt-icon { color: #6b8fe0; font-size: 14pt; min-width: 28px; }
.bt-empty { color: #565f89; padding: 10px 0; }
list > row.bt-plain, list > row.bt-plain:hover { background: transparent; padding-left: 0; padding-right: 0; }
"""

# BlueZ's Icon property (freedesktop icon names) -> a Nerd Font glyph
ICONS = {"audio-headset": "\U000f02cb", "audio-headphones": "\U000f02cb", "audio-card": "\U000f04c3",
         "input-keyboard": "\U000f030c", "input-mouse": "\U000f037d", "input-gaming": "\U000f0297",
         "input-tablet": "\U000f04f7", "phone": "\U000f011c", "computer": "\U000f0322",
         "camera-photo": "\U000f0100", "printer": "\U000f042a", "video-display": "\U000f0379"}
ICON_OTHER = "\U000f00af"   # the Bluetooth rune

# what BlueZ's errors mean for the person clicking
ERRORS = [("page-timeout", "not answering: is it on and close by?"),
          ("Host is down", "not answering: is it on and close by?"),
          ("AuthenticationFailed", "pairing refused: put it in pairing mode and try again"),
          ("AuthenticationCanceled", "pairing cancelled on the device"),
          ("AuthenticationRejected", "pairing refused: put it in pairing mode and try again"),
          ("ConnectionAttemptFailed", "couldn't reach it: is it connected to another phone or PC?"),
          ("profile-unavailable", "no audio / input service for it: is PipeWire running?"),
          ("AlreadyConnected", "already connected"), ("InProgress", "busy, one moment"),
          ("Blocked", "Bluetooth is blocked (rfkill)"), ("NotReady", "the adapter is off"),
          ("Timeout", "it took too long: put it in pairing mode and try again")]


def friendly(err):
    text = err.message if hasattr(err, "message") else str(err)
    return next((say for key, say in ERRORS if key in text), text.split(": ")[-1])


class Bluetooth(View):
    title = "Bluetooth"
    icon = ICON_OTHER
    interval = 2.0   # BlueZ's signals do the work; this only re-reads in case one was missed
    hints = [("↑↓", "select"), ("Enter", "connect / pair"), ("f", "forget"), ("p", "power"), ("s", "scan"),
             ("Esc", "close")]
    css = CSS

    def __init__(self):
        super().__init__()
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.adapter, self.adapter_props = None, {}
        self.devices = {}       # path -> Device1 props (+ "Battery")
        self.busy = {}          # path -> "Connecting…" / "Pairing…" while a call runs
        self.armed = None       # path whose Forget was clicked once
        self.pairing = None     # path being paired from here: the only one the agent accepts
        self.scan_wanted = True
        self.shape = None
        self.rows = {}          # path -> widgets
        self.pending = 0

    # ---- BlueZ ------------------------------------------------------------------------------------
    def call(self, path, iface, method, args=None, reply=None, done=None, timeout=-1):
        """An async BlueZ call; done(error or None) on the main loop."""
        def finished(bus, res):
            try:
                bus.call_finish(res)
                err = None
            except GLib.Error as e:
                err = e
            if done:
                done(err)
        self.bus.call(BUS, path, iface, method, args, reply, Gio.DBusCallFlags.NONE, timeout, None, finished)

    def set_prop(self, path, iface, name, value, done=None):
        self.call(path, PROPS, "Set", GLib.Variant("(ssv)", (iface, name, value)), done=done)

    def load(self):
        try:
            objs = self.bus.call_sync(BUS, ROOT, OBJECTS, "GetManagedObjects", None, None,
                                      Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        except GLib.Error:
            objs = {}
        self.adapter = next((p for p, ifs in sorted(objs.items()) if ADAPTER in ifs), None)
        self.adapter_props = objs.get(self.adapter, {}).get(ADAPTER, {})
        self.devices = {}
        for path, ifs in objs.items():
            if DEVICE in ifs and ifs[DEVICE].get("Adapter") == self.adapter:
                d = dict(ifs[DEVICE])
                d["Battery"] = ifs.get(BATTERY, {}).get("Percentage")
                self.devices[path] = d

    def changed(self, *_):
        """A BlueZ signal: re-read once the burst is over (a scan sends many)."""
        if not self.pending:
            self.pending = GLib.timeout_add(150, self.on_changed)

    def on_changed(self):
        self.pending = 0
        self.refresh()
        return False

    def watch(self):
        for iface, member in ((OBJECTS, "InterfacesAdded"), (OBJECTS, "InterfacesRemoved"), (PROPS, "PropertiesChanged")):
            self.bus.signal_subscribe(BUS, iface, member, None, None, Gio.DBusSignalFlags.NONE, self.changed)

    def scan(self, on):
        if not self.adapter or not self.adapter_props.get("Powered"):
            return
        if on and not self.adapter_props.get("Discovering"):
            # BR/EDR + LE, and devices that only advertise (no name yet) still show their address
            self.call(self.adapter, ADAPTER, "SetDiscoveryFilter",
                      GLib.Variant("(a{sv})", ({"Transport": GLib.Variant("s", "auto")},)),
                      done=lambda _e: self.call(self.adapter, ADAPTER, "StartDiscovery"))
        elif not on and self.adapter_props.get("Discovering"):
            self.call(self.adapter, ADAPTER, "StopDiscovery")

    # ---- the pairing agent -----------------------------------------------------------------------
    def register_agent(self):
        node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        try:
            self.bus.register_object_with_closures2(AGENT_PATH, node.interfaces[0], self.agent_call, None, None)
        except GLib.Error:
            return
        manager = "org.bluez.AgentManager1"
        self.call("/org/bluez", manager, "RegisterAgent", GLib.Variant("(os)", (AGENT_PATH, "KeyboardDisplay")),
                  done=lambda e: e or self.call("/org/bluez", manager, "RequestDefaultAgent",
                                                GLib.Variant("(o)", (AGENT_PATH,))))

    def agent_call(self, _bus, _sender, _path, _iface, method, params, inv):
        args = params.unpack()
        dev = args[0] if args else None
        name = self.name(dev) if dev else ""
        ours = dev is not None and dev == self.pairing
        reject = lambda: inv.return_dbus_error("org.bluez.Error.Rejected", "not paired from the panel")
        if method in ("Release", "Cancel"):
            inv.return_value(None)
        elif not ours:   # something else asking to pair with this PC: no
            reject()
        elif method == "RequestPinCode":            # old headsets and car kits: their fixed PIN
            inv.return_value(GLib.Variant("(s)", ("0000",)))
        elif method == "DisplayPinCode":
            self.say(f"Type {args[1]} on {name}, then Enter", seconds=30)
            inv.return_value(None)
        elif method == "DisplayPasskey":
            self.say(f"Type {args[1]:06d} on {name}, then Enter", seconds=30)
            inv.return_value(None)
        elif method == "RequestConfirmation":       # phones: the same number shows on both
            self.say(f"Pairing {name}: confirm {args[1]:06d} on it", seconds=30)
            inv.return_value(None)
        elif method in ("RequestAuthorization", "AuthorizeService"):
            inv.return_value(None)
        else:   # RequestPasskey: a code shown on the device to type here; nothing does this anymore
            reject()

    # ---- actions --------------------------------------------------------------------------------
    def name(self, path):
        d = self.devices.get(path, {})
        return d.get("Alias") or d.get("Name") or d.get("Address", "device")

    def act(self, path):
        """Enter / the row's main button: disconnect, connect, or pair a new one."""
        d = self.devices.get(path)
        if not d or path in self.busy:
            return
        if d.get("Connected"):
            self.run_call(path, "Disconnecting…", "Disconnect", f"{self.name(path)} disconnected")
        elif d.get("Paired"):
            self.run_call(path, "Connecting…", "Connect", f"{self.name(path)} connected")
        else:
            self.pair(path)

    def run_call(self, path, doing, method, ok_msg, then=None):
        self.busy[path] = doing
        self.update()

        def done(err):
            self.busy.pop(path, None)
            if err:
                self.say(f"{self.name(path)}: {friendly(err)}", "bad")
            elif then:
                then()
            else:
                self.say(ok_msg)
            self.refresh()
        self.call(path, DEVICE, method, done=done, timeout=60000)

    def pair(self, path):
        """Pair, trust (so it can reconnect by itself), connect. The scan pauses meanwhile: pairing
        while discovering fails on many adapters."""
        self.pairing = path
        self.scan(False)
        self.say(f"Pairing {self.name(path)}… (put it in pairing mode if it isn't)", seconds=60)

        def paired():
            self.pairing = None
            self.set_prop(path, DEVICE, "Trusted", GLib.Variant("b", True))
            self.run_call(path, "Connecting…", "Connect", f"{self.name(path)} paired and connected")

        def done(err):
            self.busy.pop(path, None)
            if err:
                self.pairing = None
                self.say(f"{self.name(path)}: {friendly(err)}", "bad")
                self.scan(self.scan_wanted)
                self.refresh()
            else:
                paired()
        self.busy[path] = "Pairing…"
        self.update()
        self.call(path, DEVICE, "Pair", done=done, timeout=90000)

    def forget(self, path):
        if self.armed != path:   # a second click (or f) confirms
            self.armed = path
            self.say(f"Forget {self.name(path)}? Again to confirm")
            self.update()
            return
        self.armed = None
        name = self.name(path)
        self.call(self.adapter, ADAPTER, "RemoveDevice", GLib.Variant("(o)", (path,)),
                  done=lambda e: self.say(f"{name}: {friendly(e)}", "bad") if e else self.say(f"{name} forgotten"))

    def set_power(self, on):
        if not self.adapter:
            return

        def done(err):
            if err and "Blocked" in str(err) and on:   # soft-blocked (rfkill): unblock, then try again
                subprocess.run(["rfkill", "unblock", "bluetooth"], capture_output=True)
                GLib.timeout_add(800, lambda: self.set_prop(self.adapter, ADAPTER, "Powered", GLib.Variant("b", True),
                                                            done=lambda e: e and self.say(friendly(e), "bad")) and False)
            elif err:
                self.say(friendly(err), "bad")
            elif on:
                GLib.timeout_add(500, lambda: self.scan(self.scan_wanted) and False)
        self.set_prop(self.adapter, ADAPTER, "Powered", GLib.Variant("b", on), done=done)

    def toggle_scan(self):
        self.scan_wanted = not self.scan_wanted
        self.scan(self.scan_wanted)
        self.say("Scanning for devices" if self.scan_wanted else "Scan stopped")

    # ---- drawing --------------------------------------------------------------------------------
    def header_extra(self):
        self.power = switch(False, self.set_power)
        self.power.set_focusable(False)   # Enter belongs to the list (see gtkkit's header)
        self.power.set_valign(Gtk.Align.CENTER)
        self.power.set_tooltip_text("Bluetooth on / off (p)")
        return [self.power]

    def build(self):
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.set_activate_on_single_click(False)
        self.list.connect("row-activated", lambda _l, row: getattr(row, "path", None) and self.act(row.path))
        wrap = box(True, 0, self.list)
        for edge in ("start", "end"):
            getattr(wrap, f"set_margin_{edge}")(12)
        wrap.set_margin_top(8)
        wrap.set_margin_bottom(12)
        self.load()
        if self.adapter:
            self.register_agent()
            self.watch()
            self.scan(True)
        self.refresh()
        return scrolled(wrap)

    def refresh(self):
        self.load()
        if self.scan_wanted and self.adapter_props.get("Powered") and not self.pairing:
            self.scan(True)   # BlueZ can end a scan on its own (power cycle, a pairing)
        self.update()

    def groups(self):
        """(my devices, nearby with a name, how many nameless) — nearby nearest first."""
        mine = sorted((p for p, d in self.devices.items() if d.get("Paired")),
                      key=lambda p: (not self.devices[p].get("Connected"), self.name(p).lower()))
        near = [p for p, d in self.devices.items() if not d.get("Paired") and (d.get("Name") or p in self.busy)]
        near.sort(key=lambda p: -self.devices[p].get("RSSI", -999))
        nameless = sum(1 for d in self.devices.values() if not d.get("Paired") and not d.get("Name"))
        return mine, near, nameless

    def update(self):
        a = self.adapter_props
        powered = bool(a.get("Powered"))
        if hasattr(self, "power"):
            self.power.quiet = True
            self.power.set_active(powered)
            self.power.set_sensitive(bool(self.adapter))
            self.power.quiet = False
        if self.adapter:
            scanning = "scanning" if a.get("Discovering") else "not scanning"
            self.set_subtitle(f"{a.get('Alias', '')} · {scanning}" if powered else "off")
        else:
            self.set_subtitle("no adapter")

        mine, near, nameless = self.groups()
        shape = (bool(self.adapter), powered, tuple(mine), tuple(near), nameless, bool(a.get("Discovering")))
        if shape != self.shape:
            self.shape = shape
            self.rebuild(mine, near, nameless, powered)
        for path, w in self.rows.items():
            self.fill(path, w)

    def rebuild(self, mine, near, nameless, powered):
        sel = self.list.get_selected_row()
        sel = getattr(sel, "path", None)
        clear(self.list)
        self.rows = {}

        def heading(text, right=""):
            row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["bt-plain"])
            row.set_child(rule_heading(text, right, spaced=True))
            self.list.append(row)

        def note(text):
            row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["bt-plain"])
            row.set_child(label(text, "bt-empty", wrap=True))
            self.list.append(row)

        if not self.adapter:
            heading("No adapter")
            note("BlueZ sees no Bluetooth adapter. Check `rfkill list` and `systemctl status bluetooth`; "
                 "a USB dongle may need replugging.")
            return
        if not powered:
            heading("Bluetooth is off")
            note("Turn it on with the switch above, or press p.")
            return
        heading("My devices", f"{len(mine)} paired")
        if not mine:
            note("Nothing paired yet. Put your device in pairing mode; it shows up under Nearby.")
        for p in mine:
            self.add_row(p)
        scanning = self.adapter_props.get("Discovering")
        heading("Nearby", (f"+{nameless} without a name · " if nameless else "") + ("scanning…" if scanning else "s to scan"))
        if not near:
            note("Looking…" if scanning else "Nothing found. Press s to scan.")
        for p in near:
            self.add_row(p)
        for row in self.rows.values():
            if row["row"].path == sel:
                self.list.select_row(row["row"])
                break
        else:
            first = next(iter(self.rows.values()), None)
            if first:
                self.list.select_row(first["row"])

    def add_row(self, path):
        d = self.devices[path]
        icon = label(ICONS.get(d.get("Icon", ""), ICON_OTHER), "bt-icon", xalign=0.5)
        name = label("", "bold", ellipsize=True)
        detail = label("", "dim", xalign=1.0)
        main = button("", lambda: self.act(path))
        forget = button("Forget", lambda: self.forget(path), "flat", tooltip="Unpair (f)")
        for b in (main, forget):
            b.set_focusable(False)   # keys go to the list: Enter acts on the selected row
            b.set_valign(Gtk.Align.CENTER)
        line = box(False, 12, icon, name, detail, main, forget, classes=("bt-row",))
        row = Gtk.ListBoxRow()
        row.path = path
        row.set_child(line)
        self.list.append(row)
        self.rows[path] = {"row": row, "name": name, "detail": detail, "main": main, "forget": forget}

    def fill(self, path, w):
        d = self.devices.get(path)
        if not d:
            return
        w["name"].set_text(self.name(path))
        parts = []
        if path in self.busy:
            parts.append(self.busy[path])
        elif d.get("Connected"):
            parts.append("connected")
        elif d.get("Paired"):
            parts.append("not connected")
        if d.get("Battery") is not None:
            parts.append(f"\U000f0079 {d['Battery']}%")
        if not d.get("Paired") and d.get("RSSI") is not None:
            parts.append(self.signal(d["RSSI"]))
        w["detail"].set_text(" · ".join(parts))
        for c in ("green", "amber"):
            w["detail"].remove_css_class(c)
        if path in self.busy:
            w["detail"].add_css_class("amber")
        elif d.get("Connected"):
            w["detail"].add_css_class("green")

        main = w["main"]
        main.set_label("Disconnect" if d.get("Connected") else "Connect" if d.get("Paired") else "Pair")
        main.set_sensitive(path not in self.busy)
        (main.remove_css_class if d.get("Connected") else main.add_css_class)("primary")
        f = w["forget"]
        f.set_visible(bool(d.get("Paired")))
        f.set_label("Sure?" if self.armed == path else "Forget")
        (f.add_css_class if self.armed == path else f.remove_css_class)("armed")

    @staticmethod
    def signal(rssi):
        """RSSI in words: close by is around -50 dBm, the edge of range around -90."""
        return "close by" if rssi > -65 else "in range" if rssi > -80 else "far"

    def key(self, keyval, state):
        if self.typing():
            return False
        row = self.list.get_selected_row()
        path = getattr(row, "path", None)
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and path:
            self.act(path)
        elif keyval == Gdk.KEY_f and path and self.devices.get(path, {}).get("Paired"):
            self.forget(path)
        elif keyval == Gdk.KEY_p:
            self.set_power(not self.adapter_props.get("Powered"))
        elif keyval == Gdk.KEY_s:
            self.toggle_scan()
        else:
            if keyval not in (Gdk.KEY_Up, Gdk.KEY_Down) and self.armed:   # any other key disarms Forget
                self.armed = None
                self.update()
            return False
        return True


def make():
    return Bluetooth()


def main():
    run(make(), "panels.bluetooth", (760, 560))


if __name__ == "__main__":
    main()
