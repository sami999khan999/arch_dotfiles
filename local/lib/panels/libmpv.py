# libmpv.py — just enough of libmpv (ctypes, no python-mpv package) for the video player (videogui.py):
# a player, its commands and properties, its events on the GLib main loop, and its OpenGL renderer, which
# draws into a Gtk.GLArea. mpv runs its own threads; everything here is called from the main thread.
import ctypes, json
from ctypes import (CFUNCTYPE, POINTER, Structure, byref, c_char_p, c_double, c_int, c_int64, c_uint64,
                    c_void_p)

from gi.repository import GLib

_mpv = ctypes.CDLL("libmpv.so.2")
_libc = ctypes.CDLL("libc.so.6")

FMT_STRING, FMT_FLAG, FMT_INT64, FMT_DOUBLE = 1, 3, 4, 5
EV_NONE, EV_SHUTDOWN, EV_START_FILE, EV_END_FILE, EV_FILE_LOADED = 0, 1, 6, 7, 8
EV_VIDEO_RECONFIG, EV_PLAYBACK_RESTART, EV_PROPERTY_CHANGE = 17, 21, 22
END_EOF, END_ERROR = 0, 4


class _Event(Structure):
    _fields_ = [("event_id", c_int), ("error", c_int), ("reply_userdata", c_uint64), ("data", c_void_p)]


class _Property(Structure):
    _fields_ = [("name", c_char_p), ("format", c_int), ("data", c_void_p)]


class _EndFile(Structure):
    _fields_ = [("reason", c_int), ("error", c_int)]


class _RenderParam(Structure):
    _fields_ = [("type", c_int), ("data", c_void_p)]


_GetProc = CFUNCTYPE(c_void_p, c_void_p, c_char_p)


class _GLInit(Structure):
    _fields_ = [("get_proc_address", _GetProc), ("ctx", c_void_p)]


class _GLFbo(Structure):
    _fields_ = [("fbo", c_int), ("w", c_int), ("h", c_int), ("internal_format", c_int)]


_Wakeup = CFUNCTYPE(None, c_void_p)

for name, res, args in [
        ("mpv_create", c_void_p, []), ("mpv_initialize", c_int, [c_void_p]),
        ("mpv_terminate_destroy", None, [c_void_p]),
        ("mpv_set_option_string", c_int, [c_void_p, c_char_p, c_char_p]),
        ("mpv_command", c_int, [c_void_p, POINTER(c_char_p)]),
        ("mpv_command_async", c_int, [c_void_p, c_uint64, POINTER(c_char_p)]),
        ("mpv_set_property_string", c_int, [c_void_p, c_char_p, c_char_p]),
        ("mpv_set_property", c_int, [c_void_p, c_char_p, c_int, c_void_p]),
        ("mpv_get_property_string", c_void_p, [c_void_p, c_char_p]),
        ("mpv_free", None, [c_void_p]),
        ("mpv_observe_property", c_int, [c_void_p, c_uint64, c_char_p, c_int]),
        ("mpv_wait_event", POINTER(_Event), [c_void_p, c_double]),
        ("mpv_set_wakeup_callback", None, [c_void_p, _Wakeup, c_void_p]),
        ("mpv_error_string", c_char_p, [c_int]),
        ("mpv_render_context_create", c_int, [POINTER(c_void_p), c_void_p, POINTER(_RenderParam)]),
        ("mpv_render_context_set_update_callback", None, [c_void_p, _Wakeup, c_void_p]),
        ("mpv_render_context_update", c_uint64, [c_void_p]),
        ("mpv_render_context_render", c_int, [c_void_p, POINTER(_RenderParam)]),
        ("mpv_render_context_free", None, [c_void_p])]:
    fn = getattr(_mpv, name)
    fn.restype, fn.argtypes = res, args

# GL entry points for mpv's renderer: GTK draws with EGL here (Wayland), whose eglGetProcAddress also
# gives the core functions; libGL's for an X11 session
try:
    _egl = ctypes.CDLL("libEGL.so.1")
    _egl.eglGetProcAddress.restype, _egl.eglGetProcAddress.argtypes = c_void_p, [c_char_p]
except OSError:
    _egl = None
try:
    _glx = ctypes.CDLL("libGL.so.1")
    _glx.glXGetProcAddressARB.restype, _glx.glXGetProcAddressARB.argtypes = c_void_p, [c_char_p]
except OSError:
    _glx = None


@_GetProc
def _get_proc(_ctx, name):
    for lib, fn in ((_egl, "eglGetProcAddress"), (_glx, "glXGetProcAddressARB")):
        if lib is not None:
            addr = getattr(lib, fn)(name)
            if addr:
                return addr
    return None


_gl = ctypes.CDLL("libGL.so.1") if _glx is not None else ctypes.CDLL("libOpenGL.so.0")
_gl.glGetIntegerv.argtypes = [c_int, POINTER(c_int)]
GL_FRAMEBUFFER_BINDING = 0x8CA6


def bound_framebuffer():
    """The framebuffer GTK has bound for the GLArea's render signal (not 0: GTK draws offscreen)."""
    fbo = c_int(0)
    _gl.glGetIntegerv(GL_FRAMEBUFFER_BINDING, byref(fbo))
    return fbo.value


class MpvError(Exception):
    pass


def _check(code, what):
    if code < 0:
        raise MpvError(f"{what}: {_mpv.mpv_error_string(code).decode()}")
    return code


def _argv(args):
    arr = (c_char_p * (len(args) + 1))(*[str(a).encode() for a in args], None)
    return arr


class Player:
    """One mpv. options: mpv's own (as on its command line, without --). on_property(name, value) when an
    observed property changes, on_event(kind, data) for the other events; both on the main loop."""

    def __init__(self, options, on_property, on_event):
        # mpv refuses to start unless numbers are formatted the C way (GTK set the user's locale)
        _libc.setlocale(1, b"C")   # LC_NUMERIC
        self.h = _mpv.mpv_create()
        if not self.h:
            raise MpvError("mpv_create failed")
        for k, v in options.items():
            _check(_mpv.mpv_set_option_string(self.h, k.encode(), str(v).encode()), f"option {k}")
        _check(_mpv.mpv_initialize(self.h), "mpv_initialize")
        self.on_property, self.on_event = on_property, on_event
        self.observed, self.render_ctx, self.queued = [], None, False
        # called on one of mpv's threads: only hand over to the main loop there
        self._wakeup = _Wakeup(lambda _d: self._queue())
        _mpv.mpv_set_wakeup_callback(self.h, self._wakeup, None)

    def _queue(self):
        if not self.queued:
            self.queued = True
            GLib.idle_add(self._drain)

    def _drain(self):
        self.queued = False
        while self.h:
            ev = _mpv.mpv_wait_event(self.h, 0).contents
            kind = ev.event_id
            if kind == EV_NONE:
                break
            if kind == EV_PROPERTY_CHANGE:
                p = ctypes.cast(ev.data, POINTER(_Property)).contents
                self.on_property(p.name.decode(), self._value(p.format, p.data))
            elif kind == EV_END_FILE:
                end = ctypes.cast(ev.data, POINTER(_EndFile)).contents
                self.on_event(kind, (end.reason, _mpv.mpv_error_string(end.error).decode() if end.error else ""))
            elif kind == EV_SHUTDOWN:
                break
            else:
                self.on_event(kind, None)
        return False

    @staticmethod
    def _value(fmt, data):
        if not data:
            return None   # unavailable (no file, no duration yet)
        if fmt == FMT_DOUBLE:
            return ctypes.cast(data, POINTER(c_double)).contents.value
        if fmt == FMT_FLAG:
            return bool(ctypes.cast(data, POINTER(c_int)).contents.value)
        if fmt == FMT_INT64:
            return ctypes.cast(data, POINTER(c_int64)).contents.value
        if fmt == FMT_STRING:
            s = ctypes.cast(data, POINTER(c_char_p)).contents.value
            return s.decode(errors="replace") if s is not None else None
        return None

    def observe(self, name, fmt):
        self.observed.append(name)
        _check(_mpv.mpv_observe_property(self.h, len(self.observed), name.encode(), fmt), f"observe {name}")

    def command(self, *args):
        """Run a command and wait for it (seek, set, cycle…); an error raises MpvError."""
        return _check(_mpv.mpv_command(self.h, _argv(args)), args[0])

    def command_async(self, *args):
        """A command that may take a while (loadfile, screenshot): mpv does it in the background."""
        _mpv.mpv_command_async(self.h, 0, _argv(args))

    def set(self, name, value):
        if isinstance(value, bool):
            value = "yes" if value else "no"
        return _check(_mpv.mpv_set_property_string(self.h, name.encode(), str(value).encode()), f"set {name}")

    def get(self, name, default=None):
        """A property as text (lists and maps come as JSON: get_json), or default if it isn't there now."""
        p = _mpv.mpv_get_property_string(self.h, name.encode())
        if not p:
            return default
        try:
            return ctypes.string_at(p).decode(errors="replace")
        finally:
            _mpv.mpv_free(p)

    def get_json(self, name, default=None):
        text = self.get(name)
        try:
            return json.loads(text) if text is not None else default
        except ValueError:
            return default

    # -- drawing: call with the GLArea's context current --
    def render_init(self, on_update):
        """mpv's OpenGL renderer; on_update() (main loop) when there's a new frame to draw."""
        self._gl_init = _GLInit(_get_proc, None)
        params = (_RenderParam * 3)(_RenderParam(1, ctypes.cast(c_char_p(b"opengl"), c_void_p)),
                                    _RenderParam(2, ctypes.cast(byref(self._gl_init), c_void_p)),
                                    _RenderParam(0, None))
        ctx = c_void_p()
        _check(_mpv.mpv_render_context_create(byref(ctx), self.h, params), "render context")
        self.render_ctx = ctx

        def update():
            self.update_queued = False
            if self.render_ctx:
                _mpv.mpv_render_context_update(self.render_ctx)
                on_update()
            return False
        self.update_queued = False

        def wake(_d):   # mpv's thread
            if not self.update_queued:
                self.update_queued = True
                GLib.idle_add(update, priority=GLib.PRIORITY_HIGH_IDLE)
        self._update_cb = _Wakeup(wake)
        _mpv.mpv_render_context_set_update_callback(ctx, self._update_cb, None)

    def render(self, fbo, w, h):
        if not self.render_ctx:
            return
        self._fbo = _GLFbo(fbo, w, h, 0)
        self._flip = c_int(1)   # GL's rows go bottom-up
        params = (_RenderParam * 3)(_RenderParam(3, ctypes.cast(byref(self._fbo), c_void_p)),
                                    _RenderParam(4, ctypes.cast(byref(self._flip), c_void_p)),
                                    _RenderParam(0, None))
        _mpv.mpv_render_context_render(self.render_ctx, params)

    def render_free(self):
        if self.render_ctx:
            _mpv.mpv_render_context_free(self.render_ctx)
            self.render_ctx = None

    def destroy(self):
        """After render_free(): stops playback and frees mpv."""
        if self.h:
            h, self.h = self.h, None
            _mpv.mpv_terminate_destroy(h)
