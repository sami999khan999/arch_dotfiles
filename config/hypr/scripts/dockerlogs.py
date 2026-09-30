#!/usr/bin/env python3
# dockerlogs.py — makes container logs readable (Tokyo Night): local time, a coloured level badge,
# the message, then key=value details dimmed. Understands JSON logs, logfmt (key=value), the
# postgres / pgbouncer / redis / nginx prefixes, and plain text. In the panel, repeated lines
# collapse to one with ×N.
#
# dockerpanel.py uses it for its log pane, and runs it for the full-screen log view:
#   dockerlogs.py --follow CONTAINER [--width N]   follow the container's logs, formatted
#   docker logs --timestamps X 2>&1 | dockerlogs.py   format a stream
import datetime, json, re, subprocess, sys

from panelkit import rgb, FG, DIM, TRACK, ACCENT, RED, YELLOW, CYAN, MAGENTA, BOLD, RESET

SUB = rgb("#a9b1d6")
INDENT = 15  # "HH:MM:SS  INF  ": continuation rows line up under the message
BADGES = {"error": (RED, "ERR"), "warn": (YELLOW, "WRN"), "info": (ACCENT, "INF"),
          "debug": (DIM, "DBG"), "trace": (DIM, "TRC")}
ALIASES = {
    "err": "error", "error": "error", "fatal": "error", "panic": "error", "crit": "error", "critical": "error",
    "emerg": "error", "alert": "error", "severe": "error",
    "warn": "warn", "warning": "warn",
    "info": "info", "information": "info", "notice": "info", "log": "info", "statement": "info",
    "detail": "info", "hint": "info",
    "debug": "debug", "dbg": "debug", "verbose": "debug", "trace": "trace",
    # pino / bunyan numbers
    "10": "trace", "20": "debug", "30": "info", "40": "warn", "50": "error", "60": "error",
}
LEVEL_NAMES = "ERROR|ERR|FATAL|PANIC|CRIT(?:ICAL)?|WARN(?:ING)?|INFO(?:RMATION)?|NOTICE|DEBUG|TRACE|LOG|STATEMENT|DETAIL|HINT"

ANSI = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|.)")
DOCKER_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?Z ")
LEAD_TS = re.compile(r"^\[?(?:\d{4}[-/.]\d\d[-/.]\d\d|\d\d?[-/ ]\w{3}[-/ ]\d{4})[T ]?\d\d:\d\d:\d\d(?:[.,]\d+)?"
                     r"\s*(?:Z|UTC|GMT|[+-]\d\d:?\d\d)?\]?[\s:,-]*")
PID = re.compile(r"^\[\s*\d+\s*\]\s*")
REDIS = re.compile(r"^\d+:[XCSM] \d{1,2} \w{3} \d{4} [\d:.]+ ([.\-*#]) ")
REDIS_SIGNAL = re.compile(r"^\d+:signal-handler \(\d+\) ")
WORD = re.compile(r"^[\[<(]?(" + LEVEL_NAMES + r")[\]>)]?(?::|\s+-|\s)\s*", re.I)
BRACKETED = re.compile(r"[\[<](" + LEVEL_NAMES + r")[\]>]\s*", re.I)
LOGFMT = re.compile(r'([\w.\-/@]+)=("(?:[^"\\]|\\.)*"|\S*)')
URL = re.compile(r"https?://")
TIME_KEYS = {"ts", "time", "timestamp", "@timestamp", "t", "date", "datetime"}
LEVEL_KEYS = ("level", "lvl", "severity", "levelname", "log.level", "loglevel")
MSG_KEYS = ("msg", "message", "@message", "event")


def level_of(word):
    return ALIASES.get(str(word).strip().lower())


def unquote(v):
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    return v


class Record:
    """One log line: time (HH:MM:SS local), level (or None), message, [(key, value)]."""
    __slots__ = ("time", "level", "msg", "fields", "count")

    def __init__(self, time, level, msg, fields):
        self.time, self.level, self.msg, self.fields, self.count = time, level, msg, fields, 1

    def same(self, other):
        return (self.level, self.msg, self.fields) == (other.level, other.msg, other.fields)


def parse(raw):
    line = ANSI.sub("", raw).split("\r")[-1].expandtabs(4).rstrip()
    stamp = ""
    m = DOCKER_TS.match(line)
    if m:  # docker's own timestamp (--timestamps), in UTC: show it in local time
        t = datetime.datetime.fromisoformat(m.group(1)).replace(tzinfo=datetime.timezone.utc)
        stamp, line = t.astimezone().strftime("%H:%M:%S"), line[m.end():]
    body = line.strip()
    indent = line[:len(line) - len(line.lstrip())]

    if body.startswith("{") and body.endswith("}"):
        try:
            d = json.loads(body)
        except ValueError:
            d = None
        if isinstance(d, dict):
            level = next((level_of(d.pop(k)) for k in LEVEL_KEYS if k in d), None)
            msg = next((str(d.pop(k)) for k in MSG_KEYS if k in d), "")
            fields = [(k, v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))) for k, v in d.items() if k not in TIME_KEYS]
            return Record(stamp, level, msg, fields)

    pairs = LOGFMT.findall(body)
    if len(pairs) >= 2 and LOGFMT.match(body):
        level, msg, fields = None, "", []
        for k, v in pairs:
            v = unquote(v)
            if k in TIME_KEYS: continue
            if k in LEVEL_KEYS and level is None: level = level_of(v)
            elif k in MSG_KEYS and not msg: msg = v
            else: fields.append((k, v))
        return Record(stamp, level, msg, fields)

    # plain text: peel off the app's own timestamp, pid and level
    plain = body
    body = LEAD_TS.sub("", body, count=1)
    level = None
    m = REDIS.match(body)
    if m:
        level = {".": "debug", "-": "debug", "*": "info", "#": "warn"}[m.group(1)]
        body = body[m.end():]
    body = REDIS_SIGNAL.sub("", body)
    body = PID.sub("", body)
    if level is None:
        m = WORD.match(body)
        if m:
            level, body = level_of(m.group(1)), body[m.end():]
        else:
            m = BRACKETED.search(body[:60])
            if m:
                level, body = level_of(m.group(1)), (body[:m.start()] + body[m.end():]).strip()
    return Record(stamp, level, indent + body if body == plain else body, [])


def collapse(records):
    """Consecutive repeats (same level, message and details) become one record with a count."""
    out = []
    for r in records:
        if out and out[-1].same(r):
            out[-1].count += 1
            out[-1].time = r.time
        else:
            out.append(r)
    return out


# ---- drawing -----------------------------------------------------------------------------------
def prefix(r):
    color, badge = BADGES.get(r.level, (DIM, "   "))
    return f"{DIM}{r.time or ' ' * 8:<8}{RESET}  {color}{BOLD}{badge}{RESET}  "


def words(r):
    """The text after the prefix as [(gap, [(colour, text), …])]: each entry is one word or one
    key=value pair, kept together when wrapping; gap = spaces before it."""
    base = RED if r.level == "error" else YELLOW if r.level == "warn" else FG
    out = []
    for m in re.finditer(r"(\s*)(\S+)", r.msg):  # the first gap keeps a stack trace's indent
        word = m.group(2)
        out.append((len(m.group(1)), [(ACCENT if URL.match(word) else base, word)]))
    for k, v in r.fields:
        vcolor = RED if k in ("err", "error", "exception") else SUB
        out.append((2, [(DIM, k), (TRACK, "="), (vcolor, v if " " not in v else f'"{v}"')]))
    if r.count > 1:
        out.append((2, [(MAGENTA, f"×{r.count}")]))
    return out


def blank(r):
    return not r.msg.strip() and not r.fields


def rows(r, w):
    """The record as screen rows of width w: prefix, then the text word-wrapped under it."""
    avail = max(w - INDENT, 10)
    lines, cur, n = [], [], 0

    def flush():
        nonlocal cur, n
        lines.append("".join(cur) + RESET)
        cur, n = [], 0

    for i, (gap, pieces) in enumerate(words(r)):
        size = sum(len(t) for _, t in pieces)
        if n and n + gap + size > avail:
            flush()
        elif n or i == 0:
            gap = min(gap, avail // 2)
            cur.append(" " * gap)
            n += gap
        for color, text in pieces:  # a word longer than a row is cut across rows
            while text:
                if n >= avail:
                    flush()
                room = avail - n
                cur.append(color + text[:room])
                n += len(text[:room])
                text = text[room:]
    if cur or not lines:
        flush()
    return [prefix(r) + lines[0]] + [" " * INDENT + l for l in lines[1:]]


# ---- the full-screen view ----------------------------------------------------------------------
def stream(src, out, width):
    """Format a stream line by line, each written as soon as it arrives (so following works)."""
    for raw in src:
        r = parse(raw)
        if blank(r):
            continue
        out.write("\n".join(rows(r, width)) + "\n")
        out.flush()


def main(argv):
    width = int(argv[argv.index("--width") + 1]) if "--width" in argv else 160
    try:
        if "--follow" in argv:
            cid = argv[argv.index("--follow") + 1]
            p = subprocess.Popen(["docker", "logs", "--follow", "--timestamps", "--tail", "5000", cid],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            stream((l.decode(errors="replace") for l in p.stdout), sys.stdout, width)
        else:
            stream(sys.stdin, sys.stdout, width)
    except (BrokenPipeError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main(sys.argv[1:])
