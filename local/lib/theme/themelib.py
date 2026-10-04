#!/usr/bin/env python3
# themelib.py — colour themes: what every program that draws with the desktop's colours asks.
#
# A theme is config/themes/<id>/theme.json: a name and one colour for each role Tokyo Night uses
# (config/themes/tokyo-night/theme.json is the reference: every file in this repo is written in its
# colours). The active theme's id is config/themes/active. recolor(text) swaps each Tokyo Night colour
# in text for the active theme's colour of the same role, so code only ever writes Tokyo Night hex:
#
#     css = recolor(CSS)          # a GTK panel's stylesheet
#     r, g, b = rgbf("#6b8fe0")   # a colour for cairo
#
# With Tokyo Night active, recolor() returns its text untouched (no map, no work): the original look
# is the same bytes it was before themes existed. The files other programs read (kitty, waybar, mako…)
# are made from the same map by `theme apply` into config/themes/current/ (local/bin/theme).
import json, os, re

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", ".."))
THEMES = os.path.join(REPO, "config", "themes")
CURRENT = os.path.join(THEMES, "current")   # generated, gitignored
BASE = "tokyo-night"                        # the reference palette, and the fallback


def ids():
    """Every theme's id, the reference first."""
    found = sorted(d for d in os.listdir(THEMES) if os.path.isfile(os.path.join(THEMES, d, "theme.json")))
    return [BASE] + [d for d in found if d != BASE] if BASE in found else found


def load(tid):
    with open(os.path.join(THEMES, tid, "theme.json")) as f:
        data = json.load(f)
    data["id"] = tid
    return data


def active():
    """The active theme's id (config/themes/active); the reference if that's missing or unknown.
    DOTFILES_THEME=<id> in the environment wins (to try a theme in one program: previews, tests)."""
    forced = os.environ.get("DOTFILES_THEME")
    if forced in ids():
        return forced
    try:
        with open(os.path.join(THEMES, "active")) as f:
            tid = f.read().strip()
    except OSError:
        return BASE
    return tid if tid in ids() else BASE


def mapping(tid):
    """{Tokyo Night hex: this theme's hex} (6 digits, lower case, no #), for the colours that differ."""
    if tid == BASE:
        return {}
    try:
        base, new = load(BASE)["colors"], load(tid)["colors"]
    except (OSError, ValueError, KeyError):
        return {}
    return {base[r].lower(): new[r].lower() for r in base if r in new and base[r].lower() != new[r].lower()}


# #rrggbb or #rrggbbaa · rgb(rrggbb) / rgba(rrggbbaa) (Hyprland) · rgb(r, g, b) with decimals (hyprlock, CSS)
_HEX = re.compile(r"(?<![0-9A-Za-z_])#([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?(?![0-9A-Za-z_])")
_FN = re.compile(r"(rgba?\()([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?(\))")
_DEC = re.compile(r"(rgba?\(\s*)(\d{1,3})(\s*,\s*)(\d{1,3})(\s*,\s*)(\d{1,3})")

_cache = {}
_active = None


def refresh():
    """Read the active theme again (a process lives on across a switch only when it asks to)."""
    global _active
    _active = None
    _cache.clear()


def _map(tid):
    if tid not in _cache:
        _cache[tid] = mapping(tid)
    return _cache[tid]


def _same_case(old, new):
    return new.upper() if old.isupper() else new


def recolor(text, tid=None):
    """text with Tokyo Night's colours swapped for theme tid's (the active one by default)."""
    global _active
    if tid is None:
        if _active is None:   # once per process: `theme apply` restarts what's long-running
            _active = active()
        tid = _active
    table = _map(tid)
    if not table:
        return text

    def hexa(m):
        new = table.get(m[1].lower())
        return m[0] if new is None else "#" + _same_case(m[1], new) + (m[2] or "")

    def fn(m):
        new = table.get(m[2].lower())
        return m[0] if new is None else m[1] + _same_case(m[2], new) + (m[3] or "") + m[4]

    def dec(m):
        new = table.get("%02x%02x%02x" % tuple(min(int(m[i]), 255) for i in (2, 4, 6)))
        if new is None:
            return m[0]
        r, g, b = (int(new[i:i + 2], 16) for i in (0, 2, 4))
        return f"{m[1]}{r}{m[3]}{g}{m[5]}{b}"

    return _DEC.sub(dec, _FN.sub(fn, _HEX.sub(hexa, text)))


def rgbf(colour):
    """A "#rrggbb" colour (Tokyo Night's) as the active theme's (r, g, b) floats, for cairo."""
    h = recolor(colour).lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
