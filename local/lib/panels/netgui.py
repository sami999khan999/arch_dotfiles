#!/usr/bin/env python3
# netgui.py — the network panel as a GTK window (Tokyo Night): the connection, live traffic and
# its details. Running it again closes it. Also a section of the Control Center.
#
#   Enter / "Manage connections"   nmtui in a floating terminal (Wi-Fi, VPN, wired settings)
#
# The readings are netpanel.py's (nmcli, ip, /sys/class/net); this only draws them.
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, Gtk, View, box, button, label, rgbf, rule_heading, run
import netpanel

CSS = ".red { color: #f7768e; }"
ACCENT, TRACK = rgbf("#6b8fe0"), rgbf("#292e42")
CYAN, MAGENTA = rgbf("#7dcfff"), rgbf("#bb9af7")   # the card: down, up
TUI = os.path.expanduser("~/.config/hypr/scripts/tui.sh")


def graph(values, height=34, colour=ACCENT):
    """A bar chart of the last HISTORY seconds of one direction, scaled to its own peak."""
    area = Gtk.DrawingArea(hexpand=True, content_height=height, valign=Gtk.Align.END)

    def draw(_area, cr, w, h):
        cr.set_source_rgb(*TRACK)
        cr.rectangle(0, h - 1, w, 1)
        cr.fill()
        vals = values()
        top = max(vals + [1])
        step = w / len(vals)
        cr.set_source_rgb(*colour)
        for i, v in enumerate(vals):
            bar = max(v / top * (h - 2), 1 if v else 0)
            cr.rectangle(i * step, h - 1 - bar, max(step - 2, 1), bar)
        cr.fill()
    area.set_draw_func(draw)
    return area


class Network(View):
    title = "Network"
    interval = netpanel.INTERVAL
    hints = [("Enter", "manage connections"), ("Esc", "close")]
    css = CSS

    def __init__(self):
        super().__init__()
        self.net = netpanel.NetworkPanel()   # keeps the traffic history between readings

    @property
    def icon(self):
        return "\U000f05a9" if self.net.typ == "wifi" else "\U000f0200"   # as the terminal card

    @property
    def tile_info(self):
        return f"{self.net.dev} · {self.net.typ}"

    def build_card(self):
        """The Control Center card, as the terminal one: the connection, down / up with their last
        minute as bars (cyan, purple), and the details."""
        self.hints = [("enter", "manage connections")]
        self.dot, self.conn = label("\u25cf"), label("", "bold", ellipsize=True)
        self.state = label("", xalign=1.0)
        traffic = Gtk.Grid(column_spacing=12, row_spacing=4)
        traffic.set_margin_top(10)
        self.rates = {}
        for i, (arrow, attr, colour, cls) in enumerate((("\u2193", "down", CYAN, "cyan"), ("\u2191", "up", MAGENTA, "magenta"))):
            rate = label("", xalign=1.0)
            rate.set_width_chars(10)
            g = graph(lambda a=attr: getattr(self.net, a), 16, colour)
            self.rates[attr], self.rates[attr + "_graph"] = rate, g
            for c, w in enumerate((label(arrow, cls), rate, g)):
                traffic.attach(w, c, i, 1, 1)
        self.details = Gtk.Grid(column_spacing=12, row_spacing=2)
        details = box(True, 4, rule_heading("Details"), self.details)
        details.set_margin_top(12)
        page = box(True, 0, box(False, 10, self.dot, self.conn, self.state), traffic, details)
        self.update()
        return page

    def build(self):
        if self.compact:
            return self.build_card()
        page = box(True, 0)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(24)

        self.dot = label("\u25cf")
        self.conn = label("", "heading", ellipsize=True)
        self.state = label("", xalign=1.0)
        page.append(box(False, 12, self.dot, self.conn, self.state))
        self.device = label("", "dim")
        page.append(self.device)

        # traffic: each direction's rate as a readout, its last minute as bars beside it
        traffic = Gtk.Grid(column_spacing=24, row_spacing=14)
        traffic.set_margin_top(18)
        self.rates = {}
        for i, (name, attr, colour, cls) in enumerate((("\u2193 Down", "down", CYAN, "cyan"),
                                                        ("\u2191 Up", "up", MAGENTA, "magenta"))):
            eyebrow = label(name, cls, "bold")
            rate = label("", "readout")
            left = box(True, 0, eyebrow, rate)
            left.set_size_request(170, -1)
            self.rates[attr] = rate
            g = graph(lambda a=attr: getattr(self.net, a), 46, colour)
            self.rates[attr + "_graph"] = g
            traffic.attach(left, 0, i, 1, 1)
            traffic.attach(g, 1, i, 1, 1)
        page.append(traffic)

        self.details = Gtk.Grid(column_spacing=16, row_spacing=6)
        page.append(rule_heading("Details", spaced=True))
        page.append(self.details)
        spacer = label("")
        spacer.set_vexpand(True)
        page.append(spacer)
        page.append(box(False, 0, button("Manage connections", self.manage, tooltip="nmtui: Wi-Fi, VPN, wired (Enter)")))
        self.update()
        return page

    def refresh(self):
        self.net.tick()
        self.update()

    def update(self):
        n = self.net
        ip, gw, dns, wifi = n.info
        self.conn.set_text(n.conn or "Not connected")
        ok = n.state == "connected"
        self.state.set_text(n.state)
        for w in (self.dot, self.state):   # green when connected, red when not
            w.remove_css_class("green" if not ok else "red")
            w.add_css_class("green" if ok else "red")
        if not self.compact:
            self.device.set_text(f"{n.dev} · {n.typ}")
        self.rates["down"].set_text(netpanel.rate(n.down[-1]))
        self.rates["up"].set_text(netpanel.rate(n.up[-1]))
        self.rates["down_graph"].queue_draw()
        self.rates["up_graph"].queue_draw()

        kv = [("IP", ip), ("Gateway", gw), ("DNS", dns)] + ([("Wi-Fi", wifi)] if wifi else [])
        kv += [("Received", netpanel.total(n.prev[0])), ("Sent", netpanel.total(n.prev[1]))]
        while (child := self.details.get_first_child()) is not None:
            self.details.remove(child)
        for i, (k, v) in enumerate(kv):
            key = label(k, "dim")
            key.set_size_request(80 if self.compact else 90, -1)
            self.details.attach(key, 0, i, 1, 1)
            self.details.attach(label(v, ellipsize=True), 1, i, 1, 1)

    def manage(self):
        subprocess.Popen([TUI, "nmtui"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # the popup goes: it's pinned above its backdrop, so nmtui would open underneath both
        # (in the Control Center close() does nothing)
        self.close()

    def key(self, keyval, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not self.typing():
            self.manage()
            return True
        return False


def make():
    """The view, for run() here and for the Control Center."""
    return Network()


def main():
    run(make(), "panels.network", (680, 590))


if __name__ == "__main__":
    main()
