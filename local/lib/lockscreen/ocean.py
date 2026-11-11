# The lock screen's night ocean (locker.py draws it), in shaded characters: rolling waves in perspective
# under a moon, lit like a 3D scene — each character's density is the light reflected by the water at
# that point (the moon's glint, the sky at a grazing angle, the haze on the horizon) — with a few stars
# twinkling. Keys typed send ripples across the water, a wrong password a red one.
#
# NumPy, every cell at once: a frame is ~2 ms at 1366x768 (~9000 cells): five waves and their slopes, the
# light, then two byte strings — the characters as an A8 mask at full size (glyphs taken from an atlas),
# and one RGBA pixel per cell for the colour, which the GPU scales up under the mask.
import math

import numpy as np

import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

RAMP = " .·:-=+*#%@"   # dark → bright
HORIZON = 0.43      # of the screen's height
FOCAL = 900         # px: the camera's focal length
EYE = 2.0           # m above the water
CALM = 0.55         # time runs this fast for the waves: a slow, quiet sea
# (amplitude m, wavelength m, heading: 0 rolls towards the camera, radians)
WAVES = ((.3, 23, 1.05), (.22, 13, -1.2), (.13, 7.1, .75), (.08, 4.1, -1.45), (.05, 2.3, 1.4),
         (.03, 1.3, -.9))
MOON = (0.75, 0.16, 78)     # x, y as parts of the screen; radius px
SUN = (-.55, -.25, .8)      # where the light on the moon comes from: a waning gibbous
STARS = 1 / 70      # of the sky's cells
STAR_GLYPHS = (1, 2, 6, 7)          # . · + * : a star's character by its brightness
RIPPLE = {"key": (.10, 3.5), "fail": (.22, 4.5)}    # kind: (height m, seconds)
RIPPLE_AT = 0.84    # where ripples start: this far down the screen, in the middle
RIPPLE_SPEED, RIPPLE_LENGTH = 2.6, 1.3              # m/s, m


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


class Ocean:
    def __init__(self, width, height, font, sea, sky, alert, alpha):
        """width x height px; sea and sky gradients as [(position, (r, g, b) floats)…], dark to bright;
        alert the wrong password's tint; alpha the characters' opacity."""
        self.cw, self.ch = cell_size(font)
        cw, ch = self.cw, self.ch
        self.w, self.h = width // cw + 1, height // ch + 1
        self.px, self.py = self.w * cw, self.h * ch
        self.atlas = glyph_atlas(cw, ch, font)
        self.sea, self.sky = gradient(sea), gradient(sky)
        self.alert, self.alpha = np.array(alert), alpha
        rng = np.random.default_rng()

        # every cell's centre, px from the horizon's middle (y down)
        cx, hy = width / 2, height * HORIZON
        x = (np.arange(self.w) + .5) * cw - cx
        y = (np.arange(self.h) + .5) * ch - hy
        X, Y = np.meshgrid(x, y)
        self.water = Y > 2
        self.top = int(np.argmax(self.water[:, 0]))      # the first row of water: the sky is above it
        # the water's point under each cell: depth z ahead, x across (m)
        dy = np.where(self.water, Y, 1)
        Z = EYE * FOCAL / dy
        sea = slice(self.top, None)
        self.X, self.Z = (X * Z / FOCAL)[sea].astype(np.float32), Z[sea].astype(np.float32)
        # a cell covers this much water: a wave much shorter is noise there, so it fades out with distance
        foot = np.maximum(self.Z * cw / FOCAL, self.Z * self.Z * ch / (EYE * FOCAL))
        self.waves = []     # (its slope's x and z parts, where it is (radians) at each cell, speed)
        for amp, length, heading in WAVES:
            k = 2 * math.pi / length
            kx, kz = k * math.sin(heading), -k * math.cos(heading)
            att = amp * np.exp(-(foot * k / 3.5) ** 2)
            self.waves.append(((kx * att).astype(np.float32), (kz * att).astype(np.float32),
                               (kx * self.X + kz * self.Z + rng.uniform(0, 2 * math.pi)).astype(np.float32),
                               math.sqrt(9.8 * k) * CALM))
        # the moonlight: towards the moon from the water
        mx, my, self.moon_r = MOON[0] * width - cx, MOON[1] * height - hy, MOON[2]
        el, az = math.atan2(-my, FOCAL), math.atan2(mx, FOCAL)
        self.light = np.array([math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el)],
                              np.float32)
        # the eye from each point of the water (the waves' height is small beside the distances)
        v = np.stack([-self.X, np.full_like(self.X, EYE), -self.Z])
        self.view = (v / np.linalg.norm(v, axis=0)).astype(np.float32)
        half = self.light[:, None, None] + self.view
        self.half = (half / np.linalg.norm(half, axis=0)).astype(np.float32)
        self.haze = (np.clip(1 - dy[sea] / (height * .035), 0, 1) ** 2).astype(np.float32)   # the horizon

        # the sky: the moon (a lit sphere, its glow), stars
        dx, dyy = X - mx, (Y - my)
        r = np.hypot(dx, dyy) / self.moon_r
        nz = np.sqrt(np.clip(1 - r * r, 0, 1))
        sun = np.array(SUN) / np.linalg.norm(SUN)
        lit = np.clip((dx / self.moon_r * sun[0] + dyy / self.moon_r * sun[1] + nz * sun[2]), 0, 1)
        maria = .82 + .18 * np.sin(dx * .09 + 1.3) * np.sin(dyy * .11 + .4) * np.sin((dx + dyy) * .05)
        self.moon = np.where(r <= 1, .25 + .75 * lit * maria, 0)
        self.glow = np.where((r > 1) & (r < 1.9), .13 * (1 - (r - 1) / .9) ** 2, 0)
        sky = ~self.water & (r > 1.15)
        self.stars = np.nonzero(sky & (rng.random(X.shape) < STARS))
        n = len(self.stars[0])
        self.star = rng.uniform(.3, 1, n) ** 2
        self.twinkle = (rng.uniform(.6, 2.2, n), rng.uniform(0, 2 * math.pi, n))
        self.star_glyphs = np.array(STAR_GLYPHS)
        # the sky, drawn once: only the stars change
        up = np.clip(self.moon + self.glow, 0, 1)[:self.top]
        self.glyph = np.zeros((self.h, self.w), np.intp)
        self.glyph[:self.top] = np.minimum((up * len(RAMP)).astype(np.intp), len(RAMP) - 1)
        self.sea_rgba = self.rgba(self.sea)
        self.colour = np.zeros((self.h, self.w, 4), np.uint8)
        self.colour[:self.top] = self.rgba(self.sky)[np.minimum((up * 63).astype(np.intp), 63)]
        self.mask = np.zeros((self.h, self.w, ch, cw), np.uint8)    # by cell: kept, the sea redrawn
        self.mask[:self.top] = self.atlas[self.glyph[:self.top]]
        self.ripples = []
        self.at = np.array([0.0, EYE * FOCAL / (height * (RIPPLE_AT - HORIZON))])   # x, z (m)

    def rgba(self, colours):
        """A gradient's colours as RGBA bytes with the characters' opacity."""
        a = np.full((len(colours), 1), self.alpha)
        return (np.concatenate([colours, a], axis=1) * 255).astype(np.uint8)

    def ripple(self, kind, now):
        self.ripples.append((now, kind))

    def frame(self, now):
        """(glyph mask bytes: A8, px x py; colour bytes: RGBA, w x h) at now seconds."""
        hx = np.zeros_like(self.X)
        hz = np.zeros_like(self.X)
        for sx, sz, at, omega in self.waves:
            c = np.cos(at - np.float32(omega * now))
            hx += sx * c
            hz += sz * c
        red = None
        self.ripples = [(t0, k) for t0, k in self.ripples if now - t0 < RIPPLE[k][1]]
        for t0, kind in self.ripples:
            height, life = RIPPLE[kind]
            age = now - t0
            ax, az = self.X - self.at[0], self.Z - self.at[1]
            d = np.hypot(ax, az) + 1e-6
            front = d - RIPPLE_SPEED * age
            env = np.exp(-(front / RIPPLE_LENGTH) ** 2) * (1 - age / life) ** 2
            slope = height * env * 2 * math.pi / RIPPLE_LENGTH * np.cos(front * 2 * math.pi / RIPPLE_LENGTH)
            hx += slope * ax / d
            hz += slope * az / d
            if kind == "fail":
                red = env if red is None else np.maximum(red, env)
        # the surface's normal (-hx, 1, -hz), normalised, and the light it sends to the eye
        norm = 1 / np.sqrt(hx * hx + hz * hz + 1)
        v, hf, li = self.view, self.half, self.light
        ndv = np.clip((v[1] - hx * v[0] - hz * v[2]) * norm, 0, 1)
        glint = np.clip((hf[1] - hx * hf[0] - hz * hf[2]) * norm, 0, 1) ** 350
        sky = .02 + .98 * (1 - ndv) ** 5                        # Fresnel: the sky's reflection
        # the slopes turned to the moon light up, those turned away go dark (against flat water)
        tilt = (li[1] - hx * li[0] - hz * li[2]) * norm - li[1]
        sea = .15 + 1.8 * tilt + .3 * sky + 1.4 * glint
        sea = np.clip(sea + (.2 - sea) * self.haze, 0, 1)

        top = self.top
        glyph = self.glyph
        glyph[top:] = np.minimum((sea * len(RAMP)).astype(np.intp), len(RAMP) - 1)
        freq, phase = self.twinkle
        stars = self.star * (.55 + .45 * np.sin(now * freq + phase))
        glyph[self.stars] = self.star_glyphs[np.minimum((stars * 4).astype(np.intp), 3)]
        self.mask[top:] = self.atlas[glyph[top:]]
        self.mask[self.stars] = self.atlas[glyph[self.stars]]

        colour = self.colour
        colour[top:] = self.sea_rgba[np.minimum((sea * 63).astype(np.intp), 63)]
        if red is not None:
            rgb = colour[top:, :, :3].astype(np.float32)
            rgb += (self.alert * 255 - rgb) * np.clip(red * 1.4, 0, 1)[..., None]
            colour[top:, :, :3] = rgb.astype(np.uint8)
        mask = self.mask.transpose(0, 2, 1, 3).reshape(self.py, self.px)
        return mask.tobytes(), colour.tobytes()
