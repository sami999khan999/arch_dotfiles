# What every lock screen animation draws with (ocean.py, aurora.py): a grid of characters from RAMP, each
# cell's character a glyph from an A8 atlas and its colour one RGBA pixel, which the GPU scales up under
# the glyphs (locker.py). An animation is a class taking (width, height, font, rgb: hex -> (r, g, b) floats)
# with w, h (cells), px, py (the grid in px), ripple(kind, now) and frame(now) -> (mask bytes, colour bytes).
import numpy as np

import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

RAMP = " .·:-=+*#%@"   # dark → bright
ALPHA = 0.9         # the characters' opacity


def glyph_atlas(cw, ch, font):
    """RAMP drawn into cw x ch A8 cells: an array (glyph, row, column)."""
    import cairo
    desc = Pango.FontDescription.from_string(font)
    atlas = np.zeros((len(RAMP), ch, cw), np.uint8)
    for i, c in enumerate(RAMP):
        surf = cairo.ImageSurface(cairo.FORMAT_A8, cw, ch)
        cr = cairo.Context(surf)
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(desc)
        layout.set_text(c, -1)
        PangoCairo.show_layout(cr, layout)
        surf.flush()
        data = np.frombuffer(bytes(surf.get_data()), np.uint8).reshape(ch, surf.get_stride())
        atlas[i] = data[:, :cw]
    return atlas


def cell_size(font):
    import cairo
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_A8, 1, 1))
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    layout.set_text("@", -1)
    _ink, logical = layout.get_pixel_extents()
    return logical.width, logical.height


def gradient(stops, n=64):
    """n RGB colours (0..1) along stops [(position, (r, g, b))…]."""
    xs = np.linspace(0, 1, n)
    pos = [p for p, _ in stops]
    return np.stack([np.interp(xs, pos, [c[k] for _, c in stops]) for k in range(3)], axis=1)


def rgba(colours, alpha=ALPHA):
    """A gradient's colours as RGBA bytes with the characters' opacity."""
    a = np.full((len(colours), 1), alpha)
    return (np.concatenate([colours, a], axis=1) * 255).astype(np.uint8)


def ramp_index(v):
    """Brightness 0..1 -> a RAMP character's index."""
    return np.minimum((v * len(RAMP)).astype(np.intp), len(RAMP) - 1)
