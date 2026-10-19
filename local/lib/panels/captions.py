# captions.py — live captions for the video player (videogui.py): whisper.cpp hears the file's sound a little
# ahead of where it plays and gives back timed lines, so each shows as it's said.
# While captions are on, whisper-server (package whisper-cpp) keeps the model loaded; a thread asks it for one
# stretch at a time (ffmpeg pulls that stretch's sound out of the file), staying up to AHEAD seconds ahead of
# the playhead and idle past that. A seek outside what's done starts again there; what's done is kept for the
# file. The models (whisper's multilingual base; silero, which finds the speech) are downloaded the first time,
# to ~/.local/share/videos.
# What keeps the lines right: whisper only hears the speech (silero: music and quiet made it invent lines), in
# one language for the file — guessed per stretch it was often wrong (Chinese, Korean for English). That's the
# locale's at first; whisper's own guesses are added up over the stretches heard, and one that clearly wins
# takes over (what's done is heard again in it). A line whisper itself doubts is left out.
import bisect, ctypes, json, os, re, signal, socket, subprocess, threading, time, urllib.request, uuid

from gi.repository import GLib

HOME = os.path.expanduser("~")
MODEL_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or f"{HOME}/.local/share", "videos")
MODEL = os.path.join(MODEL_DIR, "ggml-base.bin")
VAD = os.path.join(MODEL_DIR, "ggml-silero-v5.1.2.bin")
DOWNLOADS = ((MODEL, "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin", "speech model"),
             (VAD, "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin", "voice finder"))
FIRST, CHUNK, AHEAD = 8, 30, 60   # seconds: a short first stretch, so the first line comes quickly
OVER = 3                           # seconds heard past a stretch, so a sentence across its end comes whole
HOLD = 1.0                         # seconds a line stays after it's said (until the next one)
LINE = 60                          # characters: a longer sentence comes as several timed lines
DOUBT = -1.0                       # a line whose words whisper rates below this (mean log-probability) is left out
SWITCH = 2.0                       # summed guesses another language needs (and twice the current's) to take over
TAG = re.compile(r"^[\[(][^\])]*[\])]$")   # only a sound tag: [BLANK_AUDIO], (music), [SPEAKING SPANISH]…
THREADS = max(1, (os.cpu_count() or 2) - 1)   # a core left for playing


def _die_with_parent():
    ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG: no server left behind by a crash


class Captions:
    """on_status(text, failed=False): something to tell (downloading, starting; failed: it went off);
    on_lines(): new lines arrived.
    Both are called on the main loop; everything else here is called from it."""

    def __init__(self, on_status, on_lines):
        self.on_status, self.on_lines = on_status, on_lines
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.running = False
        self.quit = threading.Event()   # this run's: off then on again at once starts a run of its own
        self.proc = None                # this run's whisper-server
        self.path = self.stream = None
        self.duration, self.pos, self.translate = 0, 0.0, False
        self.gen = 0          # bumped when the file or the language changes: a stretch in flight is then dropped
        self.lines = []       # (start, end, text), by start
        self.done = []        # the (start, end) spans heard so far, by start, apart
        self.home = (os.environ.get("LANG") or "en")[:2].lower()
        if not self.home.isalpha() or self.home == "c":
            self.home = "en"
        self.lang, self.votes = self.home, {}

    # ---- from the player ------------------------------------------------------------------------------
    def start(self):
        if self.running:
            return
        self.running = True
        self.quit = threading.Event()
        threading.Thread(target=self.run, args=(self.quit,), daemon=True).start()

    def stop(self):
        self.running = False
        self.quit.set()
        self.wake.set()
        if self.proc:   # also ends a request in flight
            self.proc.terminate()

    def set_file(self, path, stream, duration, translate, pos):
        """A file to caption (stream: its audio's index in the file, None for the first), playing from pos."""
        self.pos = pos   # not 0 until the next tick: a resumed file would hear its first seconds first
        with self.lock:
            self.gen += 1
            self.path, self.stream, self.duration, self.translate = path, stream, duration or 0, translate
            self.lines, self.done = [], []
            self.lang, self.votes = self.home, {}
        self.wake.set()

    def playhead(self, pos):
        self.pos = pos
        if self.running and self.next_stretch(pos):
            self.wake.set()

    def text_at(self, pos):
        with self.lock:
            i = bisect.bisect_right(self.lines, (pos, float("inf"), "")) - 1
            if i < 0:
                return ""
            start, end, text = self.lines[i]
            # never longer than it takes to say: whisper can stretch a line's end over a quiet the voice
            # finder took out (one stayed up for 30 s)
            until = min(end + HOLD, start + 2 + len(text) / 8)
            if i + 1 < len(self.lines):
                until = min(until, self.lines[i + 1][0])
            return text if pos < until else ""

    # ---- which stretch next ---------------------------------------------------------------------------
    def next_stretch(self, pos):
        """(start, seconds, over) to hear next — over: how far past it to listen — or None while far enough
        ahead."""
        with self.lock:
            if not self.path:
                return None
            here = next((s for s in self.done if s[0] - 0.5 <= pos < s[1]), None)
            start = here[1] if here else pos
            if start - pos >= AHEAD or (self.duration and start >= self.duration - 0.5):
                return None
            length = CHUNK if here else FIRST
            after = [s[0] for s in self.done if s[0] > start]
            limit = min([*after, *([self.duration] if self.duration else []), float("inf")])
            end = min(start + length, limit)
            return (start, end - start, max(min(OVER, limit - end), 0)) if end - start >= 0.5 else None

    def add(self, gen, start, length, over, lines, guesses):
        """Keep a stretch's lines: those that begin in it (one running past its end, whole). False if it's
        stale (another file, or the language changed)."""
        with self.lock:
            if gen != self.gen:
                return False
            lines = [ln for ln in lines if ln[0] < length]
            upto = min(max([length, *(b for _a, b, _t in lines)]), length + over)
            for a, b, text in lines:
                bisect.insort(self.lines, (start + a, start + min(b, upto), text))
            spans = sorted([*self.done, (start, start + upto)])
            self.done = [spans[0]]
            for s in spans[1:]:   # touching spans become one
                if s[0] <= self.done[-1][1] + 0.01:
                    self.done[-1] = (self.done[-1][0], max(self.done[-1][1], s[1]))
                else:
                    self.done.append(s)
            if lines:   # only stretches with speech have a say in the language
                for k, v in guesses.items():
                    self.votes[k] = self.votes.get(k, 0) + v
                best = max(self.votes, key=self.votes.get)
                if best != self.lang and self.votes[best] >= max(SWITCH, 2 * self.votes.get(self.lang, 0)):
                    self.lang, self.gen = best, self.gen + 1
                    self.lines, self.done = [], []   # heard again in it
            return True

    # ---- the thread -----------------------------------------------------------------------------------
    def status(self, text):
        GLib.idle_add(lambda: self.on_status(text) and False)

    def run(self, quit):
        proc = None
        try:
            self.fetch_model(quit)
            if not quit.is_set():
                proc, port = self.serve(quit)
            while not quit.is_set():
                self.wake.clear()
                with self.lock:
                    gen, path, stream, translate, lang = self.gen, self.path, self.stream, self.translate, self.lang
                todo = self.next_stretch(self.pos)
                if not todo:
                    self.wake.wait()
                    continue
                lines, guesses = self.hear(port, path, stream, todo[0], todo[1] + todo[2], lang, translate)
                if not quit.is_set() and self.add(gen, *todo, lines, guesses) and lines:
                    GLib.idle_add(lambda: self.on_lines() and False)
        except Exception as e:   # noqa: BLE001 — whatever went wrong is said, and captions go off
            if not quit.is_set():
                quit.set()
                said = f"live captions off: {e}"   # e is gone once the except block ends
                GLib.idle_add(lambda: self.on_status(said, failed=True) and False)
        finally:
            if proc:
                proc.terminate()
                proc.wait()

    def fetch_model(self, quit):
        for path, url, what in DOWNLOADS:
            if os.path.exists(path) or quit.is_set():
                continue
            os.makedirs(MODEL_DIR, exist_ok=True)
            part, shown = path + ".part", -1
            with urllib.request.urlopen(url, timeout=30) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                while not quit.is_set() and (block := r.read(1 << 20)):
                    f.write(block)
                    pct = f.tell() * 100 // total if total else 0
                    if pct // 5 != shown:   # every 5 %, not every block
                        shown = pct // 5
                        self.status(f"live captions: downloading the {what} ({pct} %)")
            if not quit.is_set():
                os.rename(part, path)
            else:
                os.unlink(part)

    def serve(self, quit):
        """Start whisper-server on a free local port and wait until the model is loaded: (process, port)."""
        self.status("live captions: starting…")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        try:
            proc = self.proc = subprocess.Popen(
                ["nice", "-n", "10", "whisper-server", "-m", MODEL, "-t", str(THREADS), "--vad", "-vm", VAD,
                 "--host", "127.0.0.1", "--port", str(port)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                preexec_fn=_die_with_parent)
        except OSError:
            raise RuntimeError("whisper-server isn't installed (package whisper-cpp)") from None
        for _ in range(300):
            if quit.is_set() or proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
                self.status("live captions on")
                return proc, port
            except OSError:
                time.sleep(0.1)
        proc.terminate()
        if quit.is_set():
            return None, None
        raise RuntimeError("whisper-server didn't start")

    def hear(self, port, path, stream, start, length, lang, translate):
        """The lines said in [start, start + length), as (from, to, text) within it, and whisper's guesses at
        the language ({code: probability})."""
        sound = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path,
             "-map", f"0:{stream}" if stream is not None else "0:a:0?", "-vn", "-ac", "1", "-ar", "16000",
             "-f", "wav", "pipe:1"], capture_output=True).stdout
        if len(sound) < 1000:   # no sound there (or none at all)
            return [], {}
        fields = {"response_format": "verbose_json", "temperature": "0", "language": lang,
                  "translate": "true" if translate else "false", "max_len": str(LINE), "split_on_word": "true"}
        cut = uuid.uuid4().hex
        body = b"".join(f'--{cut}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in fields.items())
        body += (f'--{cut}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n").encode() + sound + f"\r\n--{cut}--\r\n".encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/inference", body,
                                     {"Content-Type": f"multipart/form-data; boundary={cut}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                reply = json.load(r)
        except urllib.error.HTTPError:
            # whisper-server (1.9.5) fails a new server's first request when the voice finder hears no speech
            # in it ("basic_string: construction from null"); there's nothing to say there anyway
            return [], {}
        lines = []
        for seg in reply.get("segments", []):
            text = " ".join(seg.get("text", "").split())
            if text and not TAG.match(text) and seg.get("avg_logprob", 0) >= DOUBT:
                lines.append((float(seg["start"]), float(seg["end"]), text))
        return lines, reply.get("language_probabilities") or {}
