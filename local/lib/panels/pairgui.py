#!/usr/bin/env python3
# pairgui.py — the layout of a VS Code + kitty pair on the Code workspace (Tokyo Night GTK popup).
# Super + Alt + P on a VS Code window or its kitty opens it (codepair.lua, openPairPanel passes the
# VS Code window's address); running it again closes it. Changes apply at once, through
# applyPairLayout / fullPair in codepair.lua (hyprctl eval): the kitty's width, its side, either
# window full width, this pair or every pair, and whether new pairs open like this (written to
# ~/.config/hypr/codepair.conf).
#
#   ← →  kitty width ∓5 %   l / r  kitty left / right   b / k / c  side by side / kitty full / VS Code full
#   a  every pair   Enter / Esc  close
import json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gtkkit import Gdk, Gtk, View, box, button, label, rule_heading, run, setting_row, switch

CONF = os.path.expanduser("~/.config/hypr/codepair.conf")
PRESETS = (20, 30, 40, 45, 50, 60, 70, 80)
DEFAULT = (0.45, "left")
CODE_MIN_PX = 640   # as in codepair.lua: VS Code's title bar doesn't draw narrower
MIN_SHARE = 0.10


def hypr(*args):
    out = subprocess.run(["hyprctl", *args], capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def read_conf():
    share, side = DEFAULT
    try:
        for line in open(CONF):
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k == "share":
                share = float(v)
            elif k == "side" and v in ("left", "right"):
                side = v
    except (OSError, ValueError):
        pass
    return share, side


def write_conf(share, side):
    with open(CONF, "w") as f:   # in place: the hypr folder is a dotfiles symlink
        f.write("# VS Code + kitty pairs: how new pairs open. Written by the pair popup (Super + Alt + P).\n"
                f"share = {share:.3f}\nside = {side}\n")


class Pair(View):
    title, subtitle = "Pair layout", "VS Code + kitty"
    interval = 0
    backdrop = False   # the pair beside it is what you're adjusting
    hints = [("← →", "width"), ("l / r", "side"), ("b / k / c", "both / kitty / VS Code full"), ("Esc", "close")]
    css = """
    .pair-preview { margin: 6px 0 14px 0; }
    .preset { padding: 4px 10px; min-width: 0; }
    """

    def __init__(self, address):
        super().__init__()
        self.address = address
        self.code = self.term = None
        self.share, self.side = read_conf()
        self.most = 1 - MIN_SHARE
        self.total = 0
        self.full = "none"   # "term" / "code" when one of them fills the screen
        self.all = False
        self.remember = True
        self.load()

    def load(self):
        """The pair as it is now: the kitty's share of the pair, and its side."""
        wins = hypr("clients", "-j") or []
        self.code = next((w for w in wins if w["address"] == self.address), None)
        self.term = next((w for w in wins if w["class"] == f"code-term-{self.address}"), None)
        if self.code and self.term:
            mon = next((m for m in hypr("monitors", "-j") or [] if m["id"] == self.code["monitor"]), None)
            self.total = (mon["width"] - 16) if mon else 1350   # the outer gaps, the gap between, borders
            t, c = self.term["size"][0], self.code["size"][0]
            wide = mon["width"] - 20 if mon else 1340          # a column this wide is "full width"
            self.full = "term" if t >= wide else ("code" if c >= wide else "none")
            if self.full == "none":
                self.share = t / (t + c)
                self.side = "left" if self.term["at"][0] < self.code["at"][0] else "right"
            self.most = min(1 - MIN_SHARE, 1 - CODE_MIN_PX / self.total)
            folder = self.code["title"].rsplit(" - Visual Studio Code", 1)[0].split(" - ")[-1].lstrip("●").strip()
            self.subtitle = folder or self.subtitle

    # ---- the window ----
    def build(self):
        page = box(True, 0)
        for edge in ("start", "end", "top", "bottom"):
            getattr(page, f"set_margin_{edge}")(18)
        if not (self.code and self.term):
            page.append(label("This VS Code window has no kitty beside it.", "dim"))
            return page

        self.preview = Gtk.DrawingArea(content_height=46, hexpand=True)
        self.preview.add_css_class("pair-preview")
        self.preview.set_draw_func(self.draw_preview)
        page.append(self.preview)

        page.append(rule_heading("Kitty width"))
        self.presets = {}
        row = box(False, 6)
        for p in PRESETS:
            b = button(f"{p} %", lambda p=p: self.set_share(p / 100), "preset")
            b.set_sensitive(p / 100 <= self.most + 1e-6)
            self.presets[p] = b
            row.append(b)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, MIN_SHARE * 100, self.most * 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.set_value(self.share * 100)
        self.scale_handler = self.scale.connect("value-changed", lambda w: self.set_share(w.get_value() / 100, slider=True))
        self.value = label("", "sub", xalign=1.0)
        self.value.set_width_chars(6)
        page.append(setting_row("Presets", "VS Code gets the rest", row))
        self.code_note = label("", "setting-sub", wrap=True)
        page.append(setting_row("Exact", "", box(False, 8, self.scale, self.value)))
        page.append(self.code_note)

        page.append(rule_heading("Show", spaced=True))
        self.show_btn = {
            "none": button("Side by side", lambda: self.set_full("none")),
            "term": button("Kitty full", lambda: self.set_full("term")),
            "code": button("VS Code full", lambda: self.set_full("code")),
        }
        page.append(setting_row("Full width", "Either one takes the whole width, the other waits beside it; "
                                "side by side goes back to the split", *self.show_btn.values()))

        page.append(rule_heading("Kitty side", spaced=True))
        self.side_left = button("Left", lambda: self.set_side("left"))
        self.side_right = button("Right", lambda: self.set_side("right"))
        page.append(setting_row("Kitty on the", "Which side of VS Code the terminal sits",
                                box(False, 6, self.side_left, self.side_right)))

        page.append(rule_heading("Apply", spaced=True))
        self.all_switch = switch(self.all, self.set_all)
        page.append(setting_row("Every pair", "All VS Code windows on the Code workspace, not only this one",
                                self.all_switch))
        page.append(setting_row("New pairs open like this", "Saved in ~/.config/hypr/codepair.conf",
                                switch(self.remember, self.set_remember)))
        page.append(box(False, 8, button("Reset to 45 % · left", self.reset), classes=()))
        self.show()
        return page

    def draw_preview(self, _area, cr, w, h):
        """The pair to scale: the kitty block and the VS Code block, the kitty on its side."""
        gap = 6
        share = {"term": 1.0, "code": 0.0}.get(self.full, self.share)
        kw = round((w - gap) * share)
        blocks = [(kw, (0.42, 0.56, 0.88), "kitty"), (w - gap - kw, (0.16, 0.18, 0.26), "VS Code")]
        if self.side == "right":
            blocks.reverse()
        x = 0
        for bw, rgb, name in blocks:
            cr.set_source_rgb(*rgb)
            cr.rectangle(x, 0, bw, h)
            cr.fill()
            cr.set_source_rgb(0.75, 0.79, 0.96) if name == "VS Code" else cr.set_source_rgb(0.09, 0.09, 0.12)
            cr.select_font_face("JetBrainsMono Nerd Font")
            cr.set_font_size(12)
            if bw < 60:
                x += bw + gap
                continue
            text = f"{name}  {round(share * 100 if name == 'kitty' else 100 - share * 100)} %"
            ext = cr.text_extents(text)
            cr.move_to(x + (bw - ext.width) / 2, h / 2 + ext.height / 2)
            cr.show_text(text)
            x += bw + gap

    def show(self):
        """Controls and preview to match self.share / self.side / self.full."""
        self.value.set_text(f"{round(self.share * 100)} %")
        code_px = round((1 - self.share) * self.total)
        self.code_note.set_text(f"VS Code {code_px} px · it can't draw narrower than {CODE_MIN_PX} px "
                                "(its title bar); for more room, use Kitty full")
        for k, b in self.show_btn.items():
            (b.add_css_class if self.full == k else b.remove_css_class)("primary")
        for p, b in self.presets.items():
            (b.add_css_class if round(self.share * 100) == p else b.remove_css_class)("primary")
        for b, s in ((self.side_left, "left"), (self.side_right, "right")):
            (b.add_css_class if self.side == s else b.remove_css_class)("primary")
        self.preview.queue_draw()

    # ---- changing it ----
    def apply(self):
        all_ = "true" if self.all else "false"
        remember = "true" if self.remember else "false"
        subprocess.run(["hyprctl", "eval", f'applyPairLayout("{self.address}", {self.share:.4f}, '
                                           f'"{self.side}", {all_}, {remember})'], capture_output=True)
        if self.remember:
            write_conf(self.share, self.side)

    def set_full(self, which):
        self.full = which
        self.show()
        if which == "none":   # back side by side: the split as set here
            self.apply()
        else:
            subprocess.run(["hyprctl", "eval", f'fullPair("{self.address}", "{which}")'], capture_output=True)

    def set_share(self, share, slider=False):
        self.full = "none"   # a split is side by side
        self.share = max(MIN_SHARE, min(self.most, share))
        if not slider:   # keep the slider in step without it calling back
            self.scale.handler_block(self.scale_handler)
            self.scale.set_value(self.share * 100)
            self.scale.handler_unblock(self.scale_handler)
        self.show()
        self.apply()

    def set_side(self, side):
        if side != self.side or self.full != "none":
            self.side, self.full = side, "none"
            self.show()
            self.apply()

    def set_all(self, on):
        self.all = on
        if on:
            self.apply()

    def set_remember(self, on):
        self.remember = on
        if on:
            write_conf(self.share, self.side)

    def reset(self):
        self.side = DEFAULT[1]
        self.set_share(DEFAULT[0])

    def key(self, keyval, state):
        if not (self.code and self.term):
            return False
        if keyval == Gdk.KEY_Left:
            self.set_share(self.share - 0.05)
        elif keyval == Gdk.KEY_Right:
            self.set_share(self.share + 0.05)
        elif keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.set_side("left")
        elif keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self.set_side("right")
        elif keyval in (Gdk.KEY_b, Gdk.KEY_B):
            self.set_full("none")
        elif keyval in (Gdk.KEY_k, Gdk.KEY_K):
            self.set_full("term")
        elif keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.set_full("code")
        elif keyval in (Gdk.KEY_a, Gdk.KEY_A):
            self.all_switch.set_active(not self.all_switch.get_active())
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Escape):
            self.close()
        else:
            return False
        return True


def make(address=""):
    return Pair(address)


def main():
    run(make(sys.argv[1] if len(sys.argv) > 1 else ""), "panels.pair", (680, 670))


if __name__ == "__main__":
    main()
