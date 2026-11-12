# The lock screen's aurora (locker.py draws it), in shaded characters: curtains of northern lights folding
# and drifting over a mountain range, their rays shimmering, green at the foot to violet at the top, with
# stars between them and the whole sky mirrored, wavering, in a lake at the bottom. Keys typed make the
# lights flare and ripple the lake; a wrong password turns them red for a moment.
#
# NumPy, every cell at once (~9000 at 1366x768): the curtains are a few sines along x and an exponential
# up the sky, the lake is the picture above it gathered upside down with a shift per cell. Drawn like the
# ocean: a glyph mask at full size and an RGBA pixel per cell (cells.py).
import math

import numpy as np

from cells import ALPHA, cell_size, glyph_atlas, gradient, ramp_index

LAKE = 0.80         # the shore, as a part of the screen's height
MIRROR = 3.0        # a row of the lake mirrors this many above the shore: it reaches the lights
# (foot as a part of the height, height px, phase, speed): two curtains, the far one higher and fainter
CURTAINS = ((.26, 170, 0.0, 1.0, .8), (.39, 120, 2.1, 1.3, 1.1))
STARS = 1 / 55      # of the sky's cells
STAR_GLYPHS = (1, 2, 6, 7)          # . · + * : a star's character by its brightness
LIGHTS = ((0, "#73daca"), (.3, "#9ece6a"), (.65, "#7aa2f7"), (1, "#bb9af7"))   # foot → top
SKY, ALERT = "#3b4261", "#f7768e"
FAR, DEEP, NEAR, RIDGE = "#444b6a", "#32344a", "#292e42", "#a9b1d6"   # the mountains, lit up to RIDGE
FLARE = (.45, .9)   # a key: the lights this much brighter, fading over seconds
RED = 2.0           # s a wrong password keeps them red
RIPPLE = {"key": (1.0, 3.0), "fail": (2.2, 4.0)}    # kind: (strength, seconds)
RIPPLE_SPEED, RIPPLE_LENGTH = 9.0, 2.2              # cells/s, cells


class Aurora:
    def __init__(self, width, height, font, rgb):
        """width x height px; rgb turns a Tokyo Night hex into the theme's (r, g, b) floats."""
        self.cw, self.ch = cell_size(font)
        cw, ch = self.cw, self.ch
        self.w, self.h = width // cw + 1, height // ch + 1
        self.px, self.py = self.w * cw, self.h * ch
        self.height = height
        self.atlas = glyph_atlas(cw, ch, font)
        self.lights = gradient([(p, rgb(c)) for p, c in LIGHTS])
        self.sky, self.alert, self.far, self.deep, self.near, self.ridge = (
            np.array(rgb(c)) for c in (SKY, ALERT, FAR, DEEP, NEAR, RIDGE))
        rng = np.random.default_rng()

        self.lake = int(height * LAKE / ch)             # the first row of the lake
        x = (np.arange(self.w) + .5) * cw
        y = (np.arange(self.lake) + .5) * ch
        self.x, self.Y = x, y[:, None]
        # two ranges: far peaks (textured, lit by the lights above), near hills (a dark silhouette)
        p = rng.uniform(0, 2 * math.pi, 6)
        far = height * (.66 - .13 * (1 - np.abs(np.sin(x * .0042 + p[0]))) ** 2
                        - .05 * (1 - np.abs(np.sin(x * .011 + p[1]))) ** 2 - .008 * np.sin(x * .05 + p[2]))
        near = height * (.71 - .035 * np.sin(x * .0062 + p[3]) - .015 * np.sin(x * .019 + p[4])
                         - .006 * np.sin(x * .07 + p[5]))
        near = np.minimum(near, (self.lake - .5) * ch)
        self.sky_cells = self.Y < far
        self.far_cells = (self.Y >= far) & (self.Y < near)
        self.below = np.maximum(self.Y - far, 0)        # px under the ridge: the slopes darken downwards
        self.peak = np.maximum(far - 40, 0)             # where the light shining on each peak is read
        self.base = .02 + .05 * np.clip(self.Y / (height * .6), 0, 1)   # the sky, a little lighter low down

        self.stars = np.nonzero(self.sky_cells & (rng.random(self.sky_cells.shape) < STARS))
        n = len(self.stars[0])
        self.star = rng.uniform(.3, 1, n) ** 2
        self.twinkle = (rng.uniform(.6, 2.2, n), rng.uniform(0, 2 * math.pi, n))
        self.star_glyphs = np.array(STAR_GLYPHS)

        # the lake: each row mirrors one above the shore, and wavers more the nearer it is
        rows = np.arange(self.lake, self.h)
        self.src = np.clip(np.rint(self.lake - 1 - (rows - self.lake) * MIRROR).astype(np.intp), 0, self.lake - 1)
        self.near_ness = ((rows - self.lake + 1) / max(self.h - self.lake, 1))[:, None]
        self.lr, self.lc = np.meshgrid(rows, np.arange(self.w), indexing="ij")
        self.at = (self.w / 2, self.lake + .55 * (self.h - self.lake))     # where ripples start (cells)

        self.events = []    # (when, kind): flares, ripples, red
        self.glyph = np.zeros((self.h, self.w), np.intp)

    def ripple(self, kind, now):
        self.events.append((now, kind))

    def curtains(self, x, y, t, boost):
        """(brightness, height in the curtain 0..1) of the lights at px x (row) and y (column)."""
        total = np.zeros(np.broadcast(x, y).shape)
        up = np.zeros_like(total)
        for foot, tall, ph, speed, strength in CURTAINS:
            s = t * speed
            base = self.height * (foot + .05 * np.sin(x * .0035 + s * .05 + ph)
                                     + .025 * np.sin(x * .009 - s * .09 + 2 * ph))
            fold = (.5 + .5 * np.sin(x * .006 - s * .12 + ph)) ** 1.5
            rays = .3 + .7 * (.5 + .5 * np.sin(x * .11 + 3 * np.sin(x * .017 + s * .25 + ph))) ** 3
            shimmer = .8 + .2 * np.sin(x * .05 + s * 1.3 + ph)
            above = base - y
            shape = np.where(above >= 0, np.exp(-np.maximum(above, 0) / tall), np.exp(-(above / 10) ** 2))
            i = strength * fold * rays * shimmer * shape * boost
            total += i
            up += i * np.clip(above / (tall * 2), 0, 1)
        return total, up / np.maximum(total, 1e-6)

    def frame(self, now):
        """(glyph mask bytes: A8, px x py; colour bytes: RGBA, w x h) at now seconds."""
        self.events = [(t0, k) for t0, k in self.events if now - t0 < max(RIPPLE[k][1], RED)]
        boost, red = 1.0, 0.0
        for t0, k in self.events:
            age = now - t0
            boost += FLARE[0] * math.exp(-age / FLARE[1]) * (1.6 if k == "fail" else 1)
            if k == "fail" and age < RED:
                red = max(red, (1 - age / RED) ** 1.5)

        # the sky and the mountains above the shore
        light, up = self.curtains(self.x, self.Y, now, boost)
        colour = self.lights[np.minimum((up * 63).astype(np.intp), 63)]
        glow = np.clip(light * 3, 0, 1)[..., None]
        rgb = self.sky + (colour - self.sky) * glow
        if red:
            rgb += (self.alert - rgb) * red * glow
        bright = np.clip(self.base + light * 1.6, 0, 1)
        lit, _ = self.curtains(self.x, self.peak, now, boost)     # the lights on each peak
        lit = np.broadcast_to(np.clip(lit, 0, 1), bright.shape)
        far = self.far_cells
        # solid ranges: dense characters in dark colours, the far one lit from above near its ridge
        bright = np.where(far, .5, bright)
        down = np.clip(self.below / 60, 0, 1)[..., None]
        slope = self.far + (self.deep - self.far) * down
        rgb = np.where(far[..., None], slope + (self.ridge - slope) * (.5 * lit[..., None] * (1 - down)), rgb)
        land = ~self.sky_cells & ~far
        bright = np.where(land, .4, bright)
        rgb = np.where(land[..., None], self.near, rgb)

        glyph = self.glyph
        top = self.lake
        glyph[:top] = ramp_index(bright)
        freq, phase = self.twinkle
        stars = self.star * (.55 + .45 * np.sin(now * freq + phase)) * np.clip(1 - light[self.stars] * 4, 0, 1)
        on = stars > .05
        shown = tuple(a[on] for a in self.stars)
        glyph[shown] = self.star_glyphs[np.minimum((stars[on] * 4).astype(np.intp), 3)]

        # the lake: the picture above, upside down, each cell shifted by the water's waver and the ripples
        shift = np.sin(self.lr * 1.3 + now * 1.2) * (.4 + 1.2 * self.near_ness) \
            + .5 * np.sin(self.lc * .21 + self.lr * .7 - now * .8)
        ring = np.zeros(shift.shape)
        tint = np.zeros(shift.shape)
        for t0, k in self.events:
            strength, life = RIPPLE[k]
            age = now - t0
            if age >= life:
                continue
            d = np.hypot(self.lc - self.at[0], (self.lr - self.at[1]) * 3.2)   # flattened: seen at a slant
            env = np.exp(-((d - RIPPLE_SPEED * age) / RIPPLE_LENGTH) ** 2) * (1 - age / life) ** 2 * strength
            ring += env * np.cos((d - RIPPLE_SPEED * age) * 2 * math.pi / RIPPLE_LENGTH)
            if k == "fail":
                tint = np.maximum(tint, env)
        cols = np.clip(np.rint(self.lc + shift + 1.5 * ring).astype(np.intp), 0, self.w - 1)
        rows = self.src[:, None]
        water = np.clip(bright[rows, cols] * .55 + .03 + .18 * np.abs(ring), 0, 1)
        glyph[top:] = ramp_index(water)
        lake_rgb = rgb[rows, cols] * .8
        lake_rgb += (self.alert - lake_rgb) * np.clip(tint * .6, 0, 1)[..., None]

        out = np.empty((self.h, self.w, 4), np.uint8)
        out[:top, :, :3] = np.clip(rgb * 255, 0, 255)
        out[top:, :, :3] = np.clip(lake_rgb * 255, 0, 255)
        out[..., 3] = int(ALPHA * 255)
        mask = self.atlas[glyph].transpose(0, 2, 1, 3).reshape(self.py, self.px)
        return mask.tobytes(), out.tobytes()
