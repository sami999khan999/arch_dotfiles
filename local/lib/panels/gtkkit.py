# gtkkit.py — the shared look and plumbing of the GUI panels (GTK 4, Tokyo Night).
#
# A panel is a View (build() returns its content, refresh() runs every `interval` s while it's
# shown). run(view, app_id) puts it in its own window; controlcenter.py shows several in one.
# Popups (app id "panels.<name>"): running one again while it's open closes it (GTK's single-
# instance D-Bus app: the second launch just activates the first), so one bind toggles it; Esc
# closes it too; Hyprland floats, sizes and centres every "panels.*" window (modules/windowrules.lua).
# Style: the Control Center cards' look everywhere (icon + title, blue underline, blue rule headings,
# amber keys / classes, green-amber-red levels, key hints) on translucent #16161e.
import gc, os
# GTK's default Vulkan renderer runs on Mesa's hasvk here (Haswell iGPU), which drew flickering white
# dots over re-filled text (the shortcut search). The GL renderer draws it cleanly. Must be set
# before GTK starts; an explicit GSK_RENDERER in the environment still wins.
os.environ.setdefault("GSK_RENDERER", "gl")

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango

# The colour theme (local/bin/theme): everything here is written in Tokyo Night's colours and goes
# through recolor() (CSS, markup, cairo colours via rgbf), which does nothing while Tokyo Night is active.
import sys
sys.path.insert(0, os.path.expanduser("~/.local/lib/theme"))
try:
    from themelib import recolor, rgbf
except ImportError:   # a PC where setup/install.sh hasn't linked it yet: Tokyo Night as written
    def recolor(text, tid=None):
        return text

    def rgbf(colour):
        h = colour.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

CSS = """
/* Tokens. bg: VS Code's Tokyo Night #16161e, 85 % opaque like kitty, so Hyprland blurs the wallpaper
   behind. text #c0caf5 · secondary #a9b1d6 · muted #565f89 · hairline alpha(#c0caf5, .07) ·
   accent #6b8fe0 · alert #f7768e. One typeface; hierarchy comes from size, weight and spacing,
   never from boxes or icons. Section labels are small spaced capitals; key numbers are large and light. */
window.panel { background: alpha(#16161e, .85); color: #c0caf5; font-family: "JetBrainsMono Nerd Font"; font-size: 10pt; }
window.panel * { border-radius: 0; }
window.backdrop { background: transparent; }   /* behind a popup: Backdrop draws it */

/* every window has the Control Center card's chrome: icon + bold title, details on the right,
   an underline that's blue under the title, key hints on the last line */
.head { padding: 14px 20px 0 20px; }
.head .title { font-weight: 700; }
.head-icon { color: #6b8fe0; }
.head-line-lead { min-height: 2px; background: #6b8fe0; }
.head-line { min-height: 2px; background: #292e42; }
.foot { padding: 8px 20px 10px 20px; color: #565f89; min-height: 16px; border-top: 1px solid alpha(#3b4261, .7); }
.foot.bad { color: #f7768e; }

.dim { color: #565f89; }
.sub { color: #a9b1d6; }
.accent { color: #6b8fe0; }
.bold { font-weight: 700; }
.red { color: #f7768e; }
.amber, .yellow { color: #e0af68; }
.green { color: #9ece6a; }
.cyan { color: #7dcfff; }
.magenta { color: #bb9af7; }
/* a card section heading: blue title, a thin rule to the right */
.rule-title { color: #6b8fe0; font-weight: 700; }
.rule { min-height: 1px; background: alpha(#3b4261, .9); }
.heading { font-weight: 700; font-size: 13pt; }
/* section headings: blue and bold, as on the Control Center cards */
.section { color: #6b8fe0; font-weight: 700; margin: 18px 0 6px 0; }
.section.flush { margin: 0; }   /* a label that starts its block: no gap above or below */
.rule-head.spaced { margin: 18px 0 6px 0; }
/* the numbers that matter: a size up */
.readout { font-size: 15pt; font-weight: 700; }
.readout-small { font-size: 12pt; font-weight: 700; }

.side { border-right: 1px solid alpha(#c0caf5, .07); }
.hairline { border-top: 1px solid alpha(#c0caf5, .07); }
list { background: transparent; color: #c0caf5; }
list > row { padding: 4px 12px; }
list > row:hover { background: alpha(#292e42, .45); }
list > row:selected { background: #292e42; color: #c0caf5; }
list > row:focus { outline: none; }

entry { background: alpha(#000000, .22); color: #c0caf5; border: none; border-bottom: 1px solid alpha(#c0caf5, .12);
        padding: 5px 10px; min-height: 22px; box-shadow: none; caret-color: #6b8fe0; }
entry:focus-within { border-bottom-color: #6b8fe0; background: alpha(#000000, .32); outline: none; }
entry placeholder, entry > text > placeholder { color: #565f89; }

/* quiet tinted text buttons; blue only for the one main action */
button { background: alpha(#c0caf5, .06); color: #c0caf5; border: none; padding: 5px 14px;
         box-shadow: none; text-shadow: none; min-height: 22px; }
button:hover { background: alpha(#c0caf5, .11); }
button:active { background: alpha(#c0caf5, .16); }
button:focus-visible { outline: 1px solid #6b8fe0; outline-offset: -1px; }
button.primary { background: #6b8fe0; color: #16161e; font-weight: 700; }
button.primary:hover { background: #7f9fe8; }
button.danger { color: #f7768e; }
button.armed { background: #f7768e; color: #16161e; font-weight: 700; }
button.flat { background: transparent; color: #a9b1d6; padding: 3px 10px; }
button.flat:hover { background: alpha(#c0caf5, .08); color: #c0caf5; }
button.flat.danger, button.flat.danger:hover { color: #f7768e; }   /* a flat destructive action stays red */
button:disabled { background: alpha(#c0caf5, .03); color: #565f89; }

menubutton arrow { -gtk-icon-source: none; min-width: 0; min-height: 0; margin: 0; }
/* text tabs (Docker, System): the current one bright and underlined in blue */
.tabs { border-bottom: 1px solid alpha(#c0caf5, .07); padding: 0 14px; }
.tab { background: transparent; border-bottom: 2px solid transparent; color: #565f89; padding: 8px 12px; font-weight: 700; }
.tab:hover { background: transparent; color: #a9b1d6; }
.tab.on { color: #c0caf5; border-bottom-color: #6b8fe0; }

/* settings controls (Settings panel, any panel): switch, dropdown, number box, slider */
switch { background: #292e42; border: none; min-width: 36px; min-height: 18px; padding: 0; box-shadow: none; }
switch:checked { background: #6b8fe0; }
switch slider { background: #c0caf5; min-width: 14px; min-height: 14px; margin: 2px; border: none; box-shadow: none; }
switch:checked slider { background: #16161e; }
switch image { -gtk-icon-source: none; }
/* a check box (System's "Show all", Docker's "as root"): square, the theme's colours, not Adwaita's white */
checkbutton check { background: #1a1b26; border: 1px solid alpha(#3b4261, .7); border-radius: 0; min-width: 14px;
                    min-height: 14px; margin-right: 6px; box-shadow: none; -gtk-icon-source: none; }
checkbutton check:checked { background: #6b8fe0; border-color: #6b8fe0; color: #16161e;
                            -gtk-icon-source: -gtk-icontheme("object-select-symbolic"); }
dropdown > button { padding: 4px 10px; min-width: 120px; }
dropdown arrow, spinbutton button { color: #565f89; }
dropdown popover listview { background: transparent; }
dropdown popover listview > row { padding: 4px 10px; }
dropdown popover listview > row:selected { background: #292e42; }
spinbutton { background: alpha(#000000, .22); color: #c0caf5; border: none; border-bottom: 1px solid alpha(#c0caf5, .12); box-shadow: none; }
spinbutton:focus-within { border-bottom-color: #6b8fe0; }
spinbutton > text { padding: 4px 8px; min-width: 40px; }
spinbutton button { background: transparent; border: none; padding: 2px 8px; }
spinbutton button:hover { background: alpha(#c0caf5, .08); color: #c0caf5; }
scale trough { background: #292e42; border: none; min-height: 4px; padding: 0; }
scale highlight { background: #6b8fe0; border: none; min-height: 4px; margin: 0; }
scale slider { background: #c0caf5; border: none; min-width: 12px; min-height: 12px; margin: -5px; box-shadow: none; }
scale:disabled highlight { background: #565f89; }
.setting { padding: 7px 0; border-bottom: 1px solid alpha(#c0caf5, .04); }
.setting-title { color: #c0caf5; }
.setting-sub { color: #565f89; font-size: 9pt; }

popover > contents { background: #16161e; color: #c0caf5; border: 1px solid alpha(#c0caf5, .1); padding: 6px; }
/* scrollbars: a thin bar, no track, no arrow buttons */
scrollbar, scrollbar trough { background: transparent; border: none; box-shadow: none; }
scrollbar button { min-width: 0; min-height: 0; padding: 0; -gtk-icon-source: none; border: none; }
scrollbar slider { background: alpha(#565f89, .45); border: none; min-width: 3px; min-height: 3px; margin: 0 2px; }
scrollbar slider:hover, scrollbar.dragging slider { background: #565f89; }
"""


def hint_markup(pairs):
    """Key hints as markup: keys bright, what they do dim, " · " between."""
    esc = GLib.markup_escape_text
    return recolor("<span foreground='#3b4261'> · </span>".join(
        f"<span foreground='#c0caf5'>{esc(k)}</span> <span foreground='#565f89'>{esc(v)}</span>" for k, v in pairs))


def level(pct):
    """The colour class for a percentage: green, amber from 60, red from 85."""
    return "green" if pct < 60 else "yellow" if pct < 85 else "red"


def rule_heading(title, right="", spaced=False):
    """A section heading: blue title, a thin rule, dim text at the end. spaced: with the gap above
    and below a heading needs between blocks of a window (a card's own spacing does without)."""
    line = Gtk.Box(hexpand=True, valign=Gtk.Align.CENTER, css_classes=["rule"])
    head = box(False, 8, label(title, "rule-title"), line, classes=("rule-head", "spaced") if spaced else ("rule-head",))
    if right:
        head.append(label(right, "dim"))
    return head


def label(text="", *classes, xalign=0.0, ellipsize=False, wrap=False, markup=False):
    w = Gtk.Label(xalign=xalign)
    w.set_markup(text) if markup else w.set_text(text)
    for c in classes:
        w.add_css_class(c)
    if ellipsize:
        w.set_ellipsize(Pango.EllipsizeMode.END)
        w.set_hexpand(True)
    if wrap:
        w.set_wrap(True)
    return w


def button(text, on_click, *classes, tooltip=None):
    b = Gtk.Button(label=text)
    for c in classes:
        b.add_css_class(c)
    if tooltip:
        b.set_tooltip_text(tooltip)
    b.connect("clicked", lambda *_: on_click())
    return b


def box(vertical=False, spacing=0, *children, classes=()):
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                spacing=spacing)
    for c in classes:
        b.add_css_class(c)
    for child in children:
        b.append(child)
    return b


def setting_row(title, sub, *controls):
    """A settings line: title with a dim explanation under it, the control(s) at the right."""
    text = box(True, 1, label(title, "setting-title"), classes=())
    if sub:
        text.append(label(sub, "setting-sub", wrap=True))
    text.set_hexpand(True)
    row = box(False, 14, text, classes=("setting",))
    for c in controls:
        c.set_valign(Gtk.Align.CENTER)
        row.append(c)
    return row


def debounced(fn, ms=250):
    """fn(value), called once the value has stopped changing for ms (sliders, number boxes)."""
    pending = {"id": 0}

    def call(value):
        if pending["id"]:
            GLib.source_remove(pending["id"])

        def fire():
            pending["id"] = 0
            fn(value)
            return False
        pending["id"] = GLib.timeout_add(ms, fire)
    return call


def switch(active, on_change):
    """A Gtk.Switch; on_change(bool) when the user flips it (not when set from code via .quiet())."""
    sw = Gtk.Switch(active=bool(active))
    sw.quiet = False

    def flipped(w, _p):
        if not w.quiet:
            on_change(w.get_active())
    sw.connect("notify::active", flipped)
    return sw


def dropdown(options, current, on_change, search=False):
    """options: [(value, text)]. on_change(value) when the user picks one."""
    values = [v for v, _ in options]
    dd = Gtk.DropDown.new_from_strings([t for _, t in options])
    if current in values:
        dd.set_selected(values.index(current))
    if search:   # long lists (time zones, keyboard layouts): type to filter
        dd.set_enable_search(True)
        dd.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        dd.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)

    def picked(w, _p):
        i = w.get_selected()
        if 0 <= i < len(values):
            on_change(values[i])
    dd.connect("notify::selected", picked)
    return dd


def spin(lo, hi, step, value, on_change, digits=0):
    """A number box; on_change(number) shortly after the user stops changing it."""
    sp = Gtk.SpinButton.new_with_range(lo, hi, step)
    sp.set_digits(digits)
    sp.set_value(value if value is not None else lo)
    later = debounced(on_change, 400)
    sp.connect("value-changed", lambda w: later(round(w.get_value(), digits) if digits else int(w.get_value())))
    return sp


def slider(lo, hi, step, value, on_change, fmt="{:.2f}", width=200):
    """A slider with its value at the right; on_change(number) once it stops moving."""
    sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
    sc.set_draw_value(False)
    sc.set_size_request(width, -1)
    sc.set_value(value if value is not None else lo)
    shown = label(fmt.format(sc.get_value()), "sub", xalign=1.0)
    shown.set_width_chars(len(fmt.format(hi)) + 1)
    later = debounced(on_change)

    def moved(w):
        shown.set_text(fmt.format(w.get_value()))
        later(round(w.get_value(), 2))
    sc.connect("value-changed", moved)
    return box(False, 8, sc, shown)


def clear(container):
    while (child := container.get_first_child()) is not None:
        container.remove(child)


def scrolled(child, vexpand=True):
    s = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=vexpand)
    s.set_child(child)
    return s


def install_css(extra=""):
    provider = Gtk.CssProvider()
    provider.load_from_string(recolor(CSS + extra))
    # above USER priority: ~/.config/gtk-4.0/gtk.css (the Noctalia/GTK theme) mustn't restyle panels
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                              Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)


class View:
    """A panel's content. The same view runs on its own (run()) or as a section of the Control
    Center; either way its host gives it a header, a status line and a refresh timer."""
    title, subtitle = "Panel", ""
    interval = 2.0           # seconds between refresh() calls while shown; 0 = never
    hints = []               # [(key, what)] shown on the status line when there's no message
    css = ""                 # extra CSS this view needs (added to CSS above)
    header = True            # False: no title row (the Control Center's tiles have their own)
    compact = False          # set by the Control Center before build(): a simplified tile
    popup = ""               # command for the full panel (Edit / Open from its Control Center card)
    icon = ""                # the Control Center card's header glyph
    footer = True            # False: the window shows no hint line (the Control Center's cards have their own)
    backdrop = True          # False: no blurred / dimmed backdrop behind the popup (the pair popup: you watch the pair)

    @property
    def tile_info(self):
        """Dim text at the right of the card's header."""
        return self.subtitle

    @property
    def tile_footer(self):
        """The card's last line: its key hints (Pango markup)."""
        return hint_markup([h for h in self.hints if h[0] != "Esc"])

    def __init__(self):
        self.host = None

    # -- override --
    def build(self):
        """The content between the header and the status line. Called once."""
        return Gtk.Box()

    def header_extra(self):
        """Widgets at the right end of the header (e.g. a global action button)."""
        return []

    def refresh(self):
        pass

    def key(self, keyval, state):
        """A key the focused widget didn't use. Return True if handled."""
        return False

    # -- provided by the host --
    def say(self, msg, kind="ok", seconds=4):
        """A message on the status line; kind "bad" shows it in red. It clears itself after seconds."""
        self.host.say(msg, kind, seconds)

    def typing(self):
        """True while a text field has the keyboard (so letter shortcuts stay off)."""
        return self.host.typing()

    def set_subtitle(self, text):
        """Change the line next to the title (only in the panel's own window; tiles have none)."""
        if self.host and self.host.view is self:
            self.host.subtitle_label.set_text(text)

    def close(self):
        """Done with the panel (after "go to workspace", focusing a window…): closes a popup,
        does nothing inside the Control Center."""
        self.host.close_view()

    @property
    def window(self):
        return self.host.win


HYPR_SETTINGS = os.path.expanduser("~/.config/hypr/settings.json")


def backdrop_setting():
    """What goes behind a popup, from System Settings → Appearance → Panels: (on, blur px, darken 0-1)."""
    import json
    try:
        p = json.load(open(HYPR_SETTINGS)).get("panels", {})
    except (OSError, ValueError):
        p = {}
    on = p.get("backdrop", True) not in (False, "off")
    return on, max(0, int(p.get("blur", 12))), max(0.0, min(float(p.get("darken", 0.25)), 0.9))


def pad_edges(pb, p):
    """The picture with p pixels more on every side, repeating its edge pixels: blurred, the edges
    then stay as they are instead of fading out."""
    from gi.repository import GdkPixbuf
    if p <= 0:
        return pb
    w, h, near = pb.get_width(), pb.get_height(), GdkPixbuf.InterpType.NEAREST
    out = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, pb.get_has_alpha(), 8, w + 2 * p, h + 2 * p)
    pb.copy_area(0, 0, w, h, out, p, p)
    for sx, sy, sw, sh, dx, dy, dw, dh in (
            (0, 0, 1, h, 0, p, p, h), (w - 1, 0, 1, h, w + p, p, p, h),            # left, right
            (0, 0, w, 1, p, 0, w, p), (0, h - 1, w, 1, p, h + p, w, p),            # top, bottom
            (0, 0, 1, 1, 0, 0, p, p), (w - 1, 0, 1, 1, w + p, 0, p, p),            # corners
            (0, h - 1, 1, 1, 0, h + p, p, p), (w - 1, h - 1, 1, 1, w + p, h + p, p, p)):
        pb.new_subpixbuf(sx, sy, sw, sh).scale_simple(dw, dh, near).copy_area(0, 0, dw, dh, out, dx, dy)
    return out


def screen_below_bar(blur):
    """A screenshot of the focused monitor without the bar (grim), ready to blur: (texture, the area
    it covers in the window (x, y, w, h), where to draw the texture (a little larger: its edges are
    repeated outward so the blur doesn't fade them), the GTK blur radius still to apply, the
    monitor's (width, height)), or Nones.
    A big blur is mostly done here, by shrinking with an averaging filter (GdkPixbuf's BILINEAR
    integrates over the area) and stretching back up: cheap, and GTK's GL blur misdraws big radii
    (40 px showed the screen zoomed in). The picture is drawn 1:1 over the screen, so the windows
    keep their places and gaps under the blur."""
    import json, math, subprocess
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    try:
        mons = json.loads(subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True).stdout)
        m = next(m for m in mons if m["focused"])
        left, top, right, bottom = m["reserved"]
        scale = m["scale"]
        w, h = round(m["width"] / scale) - left - right, round(m["height"] / scale) - top - bottom
        png = subprocess.run(["grim", "-l", "0", "-g", f"{m['x'] + left},{m['y'] + top} {w}x{h}", "-"],
                             capture_output=True, timeout=2).stdout
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(png)
        loader.close()
        shot = loader.get_pixbuf()
        shrink = max(1.0, blur / 4)   # what's left for GTK: about a quarter, at most 10 px
        gtk_blur = min(blur, 10) if shrink > 1 else blur
        if shrink > 1:
            shot = shot.scale_simple(max(1, round(shot.get_width() / shrink)),
                                     max(1, round(shot.get_height() / shrink)), GdkPixbuf.InterpType.BILINEAR)
        kx, ky = w / shot.get_width(), h / shot.get_height()   # screen px per picture px
        p = math.ceil(gtk_blur * 2 / min(kx, ky)) if gtk_blur else 0
        shot = pad_edges(shot, p)
        draw = (left - p * kx, top - p * ky, w + 2 * p * kx, h + 2 * p * ky)
        return (Gdk.Texture.new_for_pixbuf(shot), (left, top, w, h), draw, gtk_blur,
                (left + w + right, top + h + bottom))
    except Exception:
        return None, None, None, 0, None


def hypr_socket(request):
    """One request on Hyprland's control socket (what hyprctl sends: "j/clients", "dispatch …"),
    its reply as text ("" if Hyprland isn't there)."""
    import socket
    path = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/hypr/{os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')}/.socket.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.connect(path)
            sock.sendall(request.encode())
            reply = b""
            while chunk := sock.recv(65536):
                reply += chunk
        return reply.decode(errors="replace")
    except OSError:
        return ""


class Backdrop(Gtk.Widget):
    """The screen as it was when the popup opened, blurred (Hyprland has one blur strength for
    everything, so this one's its own: screen_below_bar) and darkened. The bar's strip stays clear:
    the live bar is above it."""

    def __init__(self, blur, darken):
        super().__init__(hexpand=True, vexpand=True)
        self.texture, self.rect, self.draw, self.blur, self.screen = screen_below_bar(blur)
        self.darken = darken

    def do_snapshot(self, snap):
        x, y, w, h = self.rect or (0, 0, self.get_width(), self.get_height())
        area = Graphene.Rect().init(x, y, w, h)
        snap.push_clip(area)
        if self.texture:
            if self.blur:
                snap.push_blur(self.blur)
            snap.append_scaled_texture(self.texture, Gsk.ScalingFilter.LINEAR, Graphene.Rect().init(*self.draw))
            if self.blur:
                snap.pop()
        dark = Gdk.RGBA()
        dark.parse(recolor(f"rgba(22, 22, 30, {self.darken if self.texture else max(self.darken, 0.3)})"))
        snap.append_color(dark, area)
        snap.pop()


class PanelApp(Gtk.Application):
    """A window hosting one view. toggle=True (popups): running the panel again while it's open
    closes it, and Esc closes it. toggle=False (the Docker panel and Control Center, which are
    their workspace's app): running it again just brings it up."""

    def __init__(self, view, app_id, size=(900, 560), toggle=True):
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.view, self.size, self.toggle = view, size, toggle
        view.host = self
        self.win = None
        self.status_timer = 0

    def do_activate(self):
        if self.win:
            self.win.close() if self.toggle else self.win.present()
            return
        install_css(self.view.css)
        self.backdrop = self.make_backdrop() if self.toggle and self.view.backdrop else None
        self.win = Gtk.ApplicationWindow(application=self, title=self.view.title, decorated=False)
        self.win.add_css_class("panel")
        self.win.set_default_size(*self.size)

        lead = box(False, 10, *([label(self.view.icon, "head-icon")] if self.view.icon else []),
                   label(self.view.title, "title"))
        self.subtitle_label = label(self.view.subtitle, "dim", xalign=1.0, ellipsize=True)
        extra = self.view.header_extra()
        close = button("Close", self.win.close, "flat", tooltip="Esc")
        # the header's buttons never take the focus: GTK gives a new window's focus to its first focusable
        # widget, and Enter would then press Close (or Tidy, Wiremix…) instead of reaching the panel
        # (Network's "Manage connections", the theme picker). They're clicked, or have their own key.
        for b in [close] + extra:
            if isinstance(b, Gtk.Button):
                b.set_focusable(False)
        top = box(False, 12, lead, self.subtitle_label, *extra, *([close] if self.toggle else []))
        under_lead = Gtk.Box(css_classes=["head-line-lead"])
        group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)   # blue exactly under icon + title
        group.add_widget(lead)
        group.add_widget(under_lead)
        underline = box(False, 0, under_lead, Gtk.Box(hexpand=True, css_classes=["head-line"]))
        underline.set_margin_top(8)
        head = box(True, 0, top, underline, classes=("head",))

        self.status = label("", "foot")
        self.status.set_visible(self.view.footer)
        body = self.view.build()
        body.set_vexpand(True)
        self.show_hints()
        self.win.set_child(box(True, 0, *([head] if self.view.header else []), body, self.status))

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.win.add_controller(keys)
        if self.view.interval:
            GLib.timeout_add(int(self.view.interval * 1000), self._tick)
        if self.backdrop:   # the popup goes up once the backdrop is there, so it's the one on top
            self.win.connect("close-request", lambda *_: self.backdrop.destroy() or False)
            self.backdrop.connect("map", lambda *_: GLib.idle_add(lambda: self.win.present() or False))
            self.win.connect("map", lambda *_: GLib.timeout_add(30, self.pin_over_backdrop))
            self.backdrop.present()
        else:
            self.win.present()

    def make_backdrop(self):
        """A window over the whole screen, under the popup, showing it blurred (Backdrop). Titled
        "panels-backdrop": modules/windowrules.lua sizes it. A click on it closes the popup."""
        on, blur, darken = backdrop_setting()
        if not on:
            return None
        win = Gtk.ApplicationWindow(application=self, title="panels-backdrop", decorated=False)
        win.add_css_class("backdrop")
        backdrop = Backdrop(blur, darken)
        if backdrop.screen:   # full size from its first frame: Hyprland stretches a smaller first
            win.set_default_size(*backdrop.screen)   # frame to the rule's size (a zoomed-in corner)
        win.set_child(backdrop)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.close_from_backdrop())
        win.add_controller(click)
        keys = Gtk.EventControllerKey()   # focused for a moment by that click: Esc there closes too
        keys.connect("key-pressed", self._on_key)
        win.add_controller(keys)
        return win

    def popup_address(self):
        import json
        try:
            return next(c["address"] for c in json.loads(hypr_socket("j/clients") or "[]")
                        if c["pid"] == os.getpid() and c["title"] != "panels-backdrop" and c["floating"])
        except (ValueError, StopIteration):
            return None

    def pin_over_backdrop(self):
        """Pin the popup once Hyprland has it: a click on the backdrop focuses and raises the backdrop,
        and a pinned window stays above it (no frame of the popup hidden, as the click closes it). Pinned
        also means it would follow a workspace switch without its backdrop, so that closes it
        (watch_workspace)."""
        me = self.popup_address()
        if me:
            hypr_socket(f'dispatch hl.dsp.window.pin({{ window = "address:{me}" }})')
            self.watch_workspace()
            return False
        self.pin_tries = getattr(self, "pin_tries", 0) + 1
        return self.pin_tries < 30   # Hyprland lists a new window within a few tries of 30 ms

    def watch_workspace(self):
        import socket, threading
        path = f"{os.environ.get('XDG_RUNTIME_DIR', '/tmp')}/hypr/{os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')}/.socket2.sock"

        def watch():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.connect(path)
                    buf = b""
                    while chunk := sock.recv(4096):
                        buf += chunk
                        *events, buf = buf.split(b"\n")
                        if any(e.startswith(b"workspace>>") for e in events):
                            GLib.idle_add(lambda: self.win.close() or False)
                            return
            except OSError:
                pass
        threading.Thread(target=watch, daemon=True).start()

    def close_from_backdrop(self):
        """A click on the backdrop. The popup is pinned (pin_over_backdrop), so it stays above the
        backdrop that click raised; both close, the popup fading out over the backdrop as with Esc."""
        self.win.close()

    def _tick(self):
        if not self.win:
            return False
        self.view.refresh()
        return True

    def _on_key(self, _ctrl, keyval, _code, state):
        if keyval == Gdk.KEY_Escape and self.toggle and not self.typing():
            self.win.close()
            return True
        return self.view.key(keyval, state)

    def close_view(self):
        if self.toggle:
            self.win.close()

    def say(self, msg, kind="ok", seconds=4):
        self.status.set_visible(True)
        self.status.set_text(msg)
        self.status.remove_css_class("bad")
        if kind == "bad":
            self.status.add_css_class("bad")
        if self.status_timer:
            GLib.source_remove(self.status_timer)

        def clear_msg():
            self.status_timer = 0
            self.show_hints()
            return False
        self.status_timer = GLib.timeout_add(seconds * 1000, clear_msg)

    def show_hints(self):
        self.status.remove_css_class("bad")
        self.status.set_visible(self.view.footer)
        self.status.set_markup(hint_markup(self.view.hints))

    def typing(self):
        w = self.win.get_focus() if self.win else None
        while w is not None:
            if isinstance(w, Gtk.Editable):
                return True
            w = w.get_parent()
        return False


def run(view, app_id, size=(900, 560), toggle=True):
    # Python's cyclic garbage collector runs on whichever thread happens to allocate, and the panels do
    # their slow work (git, docker, nvidia-smi) in threads: a closed popup's widgets (a cycle through
    # their own signal handlers) were once collected on a git thread, and GTK, which is main-thread
    # only, crashed (the Projects panel, SIGSEGV in gtk_list_box_remove_all). So the collector runs
    # only here, on the main loop, every few seconds; plain reference counting still frees the rest.
    gc.disable()
    GLib.timeout_add_seconds(5, lambda: gc.collect() is not None)
    PanelApp(view, app_id, size, toggle).run([])
