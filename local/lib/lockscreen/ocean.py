# The lock screen's night ocean (locker.py draws it), in shaded characters: rolling waves in perspective
# under a moon crossing the sky (it rises, arcs over and sets, its path of light on the water following it),
# lit like a 3D scene — each character's density is the light reflected by the water at
# that point (the moon's glint, the sky at a grazing angle, the haze on the horizon) — with a few stars
# twinkling. Keys typed send ripples across the water, a wrong password a red one.
#
# NumPy, every cell at once: a frame is ~2 ms at 1366x768 (~9000 cells): five waves and their slopes, the
# light, then two byte strings — the characters as an A8 mask at full size (glyphs taken from an atlas),
# and one RGBA pixel per cell for the colour, which the GPU scales up under the mask.
import math

import numpy as np

from cells import RAMP, cell_size, glyph_atlas, gradient, ramp_index, rgba

HORIZON = 0.43      # of the screen's height
FOCAL = 900         # px: the camera's focal length
EYE = 2.0           # m above the water
CALM = 0.55         # time runs this fast for the waves: a slow, quiet sea
# (amplitude m, wavelength m, heading: 0 rolls towards the camera, radians)
WAVES = ((.3, 23, 1.05), (.22, 13, -1.2), (.13, 7.1, .75), (.08, 4.1, -1.45), (.05, 2.3, 1.4),
         (.03, 1.3, -.9))
MOON_R = 78         # px: the moon's radius
MOON_ARC = (.06, .94, .12)  # its path: from x to x (parts of the screen), this high at the top
MOON_ORBIT = 480    # s from moonrise to moonrise: slow, but seen to move
MOON_START = .7     # where it is on its path when the screen locks (0 rising, .5 at the top, 1 set)
MOON_STEP = .5      # s between its moves: a few px each, and the sky redrawn only then
SUN = (-.55, -.25, .8)      # where the light on the moon comes from: a waning gibbous
STARS = 1 / 70      # of the sky's cells
STAR_GLYPHS = (1, 2, 6, 7)          # . · + * : a star's character by its brightness
SEA = ((0, "#24283b"), (.3, "#3b4261"), (.55, "#6b8fe0"), (.8, "#7dcfff"), (1, "#c0caf5"))   # dark → bright
SKY = ((0, "#3b4261"), (.5, "#a9b1d6"), (1, "#cfc9c2"))
ALERT = "#f7768e"   # a wrong password's ripple
RIPPLE = {"key": (.10, 3.5), "fail": (.22, 4.5)}    # kind: (height m, seconds)
RIPPLE_AT = 0.84    # where ripples start: this far down the screen, in the middle
RIPPLE_SPEED, RIPPLE_LENGTH = 2.6, 1.3              # m/s, m


class Ocean:
    def __init__(self, width, height, font, rgb):
        """width x height px; rgb turns a Tokyo Night hex into the theme's (r, g, b) floats."""
        self.cw, self.ch = cell_size(font)
        cw, ch = self.cw, self.ch
        self.w, self.h = width // cw + 1, height // ch + 1
        self.px, self.py = self.w * cw, self.h * ch
        self.width, self.height = width, height
        self.atlas = glyph_atlas(cw, ch, font)
        self.sea = gradient([(p, rgb(c)) for p, c in SEA])
        self.sky = gradient([(p, rgb(c)) for p, c in SKY])
        self.alert = np.array(rgb(ALERT))
        rng = np.random.default_rng()

        # every cell's centre, px from the horizon's middle (y down)
        self.cx, self.hy = cx, hy = width / 2, height * HORIZON
        x = (np.arange(self.w) + .5) * cw - cx
        y = (np.arange(self.h) + .5) * ch - hy
        X, Y = np.meshgrid(x, y)
        self.water = Y > 2
        self.top = top = int(np.argmax(self.water[:, 0]))      # the first row of water: the sky is above it
        self.skyX, self.skyY = X[:top], Y[:top]
        # the water's point under each cell: depth z ahead, x across (m)
        dy = np.where(self.water, Y, 1)
        Z = EYE * FOCAL / dy
        sea = slice(top, None)
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
        # the eye from each point of the water (the waves' height is small beside the distances)
        v = np.stack([-self.X, np.full_like(self.X, EYE), -self.Z])
        self.view = (v / np.linalg.norm(v, axis=0)).astype(np.float32)
        self.haze = (np.clip(1 - dy[sea] / (height * .035), 0, 1) ** 2).astype(np.float32)   # the horizon

        # the sky: stars (those the moon passes in front of are hidden while it does)
        self.stars = np.nonzero(rng.random(self.skyX.shape) < STARS)
        n = len(self.stars[0])
        self.star = rng.uniform(.3, 1, n) ** 2
        self.twinkle = (rng.uniform(.6, 2.2, n), rng.uniform(0, 2 * math.pi, n))
        self.star_glyphs = np.array(STAR_GLYPHS)
        self.sun = np.array(SUN) / np.linalg.norm(SUN)
        self.glyph = np.zeros((self.h, self.w), np.intp)
        self.sea_rgba, self.sky_rgba = rgba(self.sea), rgba(self.sky)
        self.colour = np.zeros((self.h, self.w, 4), np.uint8)
        self.mask = np.zeros((self.h, self.w, ch, cw), np.uint8)
        self.moved = -MOON_STEP
        self.ripples = []
        self.at = np.array([0.0, EYE * FOCAL / (height * (RIPPLE_AT - HORIZON))])   # x, z (m)

    def moon(self, now):
        """Moves the moon to where it is at now: the sky redrawn (the moon, a lit sphere, and its glow), the
        light it sends the water and how bright that is (0 once it has set), the stars it hides."""
        phase = (MOON_START + now / MOON_ORBIT) % 1
        x0, x1, high = MOON_ARC
        low = self.hy + MOON_R          # just under the horizon: rises and sets behind the sea
        mx = (x0 + (x1 - x0) * phase) * self.width - self.cx
        my = low - (low - high * self.height) * math.sin(math.pi * phase) - self.hy
        dx, dy = self.skyX - mx, self.skyY - my
        r = np.hypot(dx, dy) / MOON_R
        nz = np.sqrt(np.clip(1 - r * r, 0, 1))
        s = self.sun
        lit = np.clip(dx / MOON_R * s[0] + dy / MOON_R * s[1] + nz * s[2], 0, 1)
        maria = .82 + .18 * np.sin(dx * .09 + 1.3) * np.sin(dy * .11 + .4) * np.sin((dx + dy) * .05)
        disc = np.where(r <= 1, .25 + .75 * lit * maria, 0)
        glow = np.where((r > 1) & (r < 1.9), .13 * (1 - (r - 1) / .9) ** 2, 0)
        el, az = math.atan2(-my, FOCAL), math.atan2(mx, FOCAL)
        self.light = li = np.array([math.sin(az) * math.cos(max(el, .005)), math.sin(max(el, .005)),
                                    math.cos(az) * math.cos(max(el, .005))], np.float32)
        half = li[:, None, None] + self.view
        self.half = (half / np.linalg.norm(half, axis=0)).astype(np.float32)
        self.moonlight = min(max((el + .01) / .05, 0), 1)     # fades as it sinks into the sea
        up = np.clip(disc + glow, 0, 1)
        top = self.top
        self.glyph[:top] = ramp_index(up)
        self.mask[:top] = self.atlas[self.glyph[:top]]
        self.colour[:top] = self.sky_rgba[np.minimum((up * 63).astype(np.intp), 63)]
        seen = r[self.stars] > 1.15
        self.shown = tuple(a[seen] for a in self.stars)
        self.seen = seen

    def ripple(self, kind, now):
        self.ripples.append((now, kind))

    def frame(self, now):
        """(glyph mask bytes: A8, px x py; colour bytes: RGBA, w x h) at now seconds."""
        if now - self.moved >= MOON_STEP:
            self.moved = now
            self.moon(now)
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
        sea = .15 + (1.8 * tilt + 1.4 * glint) * self.moonlight + .3 * sky
        sea = np.clip(sea + (.2 - sea) * self.haze, 0, 1)

        top = self.top
        glyph = self.glyph
        glyph[top:] = ramp_index(sea)
        freq, phase = self.twinkle
        stars = self.star * (.55 + .45 * np.sin(now * freq + phase))
        glyph[self.shown] = self.star_glyphs[np.minimum((stars[self.seen] * 4).astype(np.intp), 3)]
        self.mask[top:] = self.atlas[glyph[top:]]
        self.mask[self.shown] = self.atlas[glyph[self.shown]]

        colour = self.colour
        colour[top:] = self.sea_rgba[np.minimum((sea * 63).astype(np.intp), 63)]
        if red is not None:
            rgb = colour[top:, :, :3].astype(np.float32)
            rgb += (self.alert * 255 - rgb) * np.clip(red * 1.4, 0, 1)[..., None]
            colour[top:, :, :3] = rgb.astype(np.uint8)
        mask = self.mask.transpose(0, 2, 1, 3).reshape(self.py, self.px)
        return mask.tobytes(), colour.tobytes()
