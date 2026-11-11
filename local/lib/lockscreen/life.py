# Conway's Game of Life for the lock screen (locker.py draws it): a sparse field where a few patches of
# soup and gliders / light spaceships cross the screen, never more than LIVE of the cells alive.
#
# Cheap on purpose: each row of the grid is one Python int and a generation is ~40 bitwise operations a
# row (a bit-sliced neighbour count), ~2 ms for a 1080p screen; rgba() turns it into a 1-pixel-per-cell
# texture, which the GPU scales up and cross-fades.
import random

LIVE = 0.07    # at most this share of the cells alive: room for things to travel

# moving patterns as (x, y) cells, heading right / down; flipped at random when dropped in
SHIPS = (
    ((1, 0), (2, 1), (0, 2), (1, 2), (2, 2)),                                                    # glider
    ((1, 0), (4, 0), (0, 1), (0, 2), (4, 2), (0, 3), (1, 3), (2, 3), (3, 3)),                    # LWSS
)


class Life:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.mask = (1 << w) - 1
        self.rows = [0] * h
        self.gen = 0
        for _ in range(w * h // 1700):
            self.patch()
        for _ in range(w * h // 1500):
            self.ship()

    def soup(self, x, y, w, h, density):
        for j in range(y, y + h):
            bits = 0
            for i in range(w):
                if random.random() < density:
                    bits |= 1 << ((x + i) % self.w)
            self.rows[j % self.h] |= bits

    def patch(self):
        s = random.randint(10, 18)
        self.soup(random.randrange(self.w), random.randrange(self.h), s, s, 0.4)

    def ship(self):
        cells = random.choice(SHIPS)
        fx, fy, swap = random.random() < .5, random.random() < .5, random.random() < .5
        x0, y0 = random.randrange(self.w), random.randrange(self.h)
        for x, y in cells:
            if swap:
                x, y = y, x
            x, y = (-x if fx else x), (-y if fy else y)
            self.rows[(y0 + y) % self.h] |= 1 << ((x0 + x) % self.w)

    def step(self):
        w, mask, rows, h = self.w, self.mask, self.rows, self.h
        left = [((r << 1) | (r >> (w - 1))) & mask for r in rows]     # the screen wraps around
        right = [(r >> 1) | ((r & 1) << (w - 1)) for r in rows]
        new = []
        for y in range(h):
            a = b = c = 0          # the neighbour count, bit-sliced (8 wraps to 0: dead either way)
            u, d = y - 1, (y + 1) % h
            for x in (left[u], rows[u], right[u], left[y], right[y], left[d], rows[d], right[d]):
                carry = a & x
                a ^= x
                c ^= b & carry
                b ^= carry
            new.append(b & ~c & (a | rows[y]) & mask)   # 3 neighbours, or 2 and alive
        self.rows = new
        self.keep_alive()

    def keep_alive(self):
        """Every few generations something new comes in while there's room; now and then a block of the
        settled leftovers (still lifes, blinkers) is wiped, so the screen doesn't fill with debris."""
        self.gen += 1
        if self.gen % 6:
            return
        pop = sum(r.bit_count() for r in self.rows)
        room = pop < self.w * self.h * LIVE
        if room and random.random() < 0.5:
            self.ship()
        if room and random.random() < 0.3:
            self.patch()
        if not room or self.gen % 480 == 0:
            x, y, s = random.randrange(self.w), random.randrange(self.h), 40
            keep = ~(((1 << s) - 1) << x | ((1 << s) - 1) >> (self.w - x)) & self.mask
            for j in range(y, y + s):
                self.rows[j % self.h] &= keep

    def rgba(self, pixel):
        """The grid as R8G8B8A8 bytes, one pixel per cell: pixel (4 bytes) where alive, clear elsewhere."""
        table = getattr(self, "_table", None)
        if table is None or table[0] != pixel:
            clear = b"\0\0\0\0"
            table = self._table = (pixel, [b"".join(pixel if b >> i & 1 else clear for i in range(8))
                                           for b in range(256)])
        nb, width = (self.w + 7) // 8, self.w * 4
        return b"".join(b"".join(map(table[1].__getitem__, r.to_bytes(nb, "little")))[:width]
                        for r in self.rows)
