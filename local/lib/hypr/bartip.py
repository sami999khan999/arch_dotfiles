"""bartip.py — make a bar module's rich card: the tooltip bartips draws as blocks (config/waybar/bartips/cards.c).

    from bartip import card, size, level
    tooltip = card([{"type": "head", "icon": "…", "title": "Memory"}, {"type": "meter", "value": 58}, …])

Used by the stats' scripts (config/hypr/scripts/storage.py, memory.py, gpu.py). The JSON's "<", ">" and "&"
are written as \\u003c… so GTK takes the whole thing as a plain tooltip (it's markup otherwise).
"""
import json


def card(blocks):
    """The tooltip text for these blocks (cards.c lists the kinds)."""
    text = json.dumps({"bartip": 1, "blocks": blocks}, ensure_ascii=False)
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def size(n, unit="B"):
    """Bytes as people read them: 512 B, 1.2 GB, 3.6 TB (powers of 1000, as drives are sold)."""
    for u in ("", "K", "M", "G", "T", "P"):
        if abs(n) < 1000 or u == "P":
            return f"{n:.0f} {u}{unit}" if u == "" or n >= 100 else f"{n:.1f} {u}{unit}"
        n /= 1000
    return ""


def gib(n):
    """Bytes in binary units, as memory is: 15.6 GiB."""
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or u == "TiB":
            return f"{n:.0f} {u}" if u == "B" or n >= 100 else f"{n:.1f} {u}"
        n /= 1024
    return ""


def level(v, warn=70, bad=90):
    """A value's colour: fine, getting there, too much."""
    return "green" if v < warn else "amber" if v < bad else "alert"


def temp_colour(c, warn=50, bad=60):
    return None if c is None else level(c, warn, bad)
