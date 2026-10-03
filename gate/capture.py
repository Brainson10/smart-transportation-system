"""
Background camera capture: one thread per source, newest frame only.

Replaces a module-level `cap = cv2.VideoCapture(0)` executed at IMPORT time,
which meant:
  * the hardcoded webcam index could not be pointed at a CCTV stream;
  * every /video_feed viewer ran its own generator against the same capture,
    stealing frames from the others;
  * `app.run(debug=True)` forked two processes that both grabbed the camera.

Nothing here opens a device at import. The stream is created on the first
get_stream() call, which happens inside the first /video_feed request -- the
Werkzeug reloader's parent process imports the module but never serves a
request, so only one process ever touches the camera. (The alternative is a
WERKZEUG_RUN_MAIN check, needed only if eager warm-up is ever wanted.)
"""

import atexit
import logging
import threading
import time

import cv2

import config

log = logging.getLogger(__name__)


class CameraStream:
    """Reads a capture source in a daemon thread, keeping only the latest frame."""

    def __init__(self, source=None, *, width=None, height=None, fps=None,
                 buffersize=None, loop_video=None, name="default"):
        self.name = name
        self.source = config.resolve_camera_source(source)
        self.width = config.CAMERA_WIDTH if width is None else width
        self.height = config.CAMERA_HEIGHT if height is None else height
        self.fps = config.CAMERA_FPS if fps is None else fps
        self.buffersize = config.CAMERA_BUFFERSIZE if buffersize is None else buffersize
        self.loop_video = config.CAMERA_LOOP_VIDEO if loop_video is None else loop_video

        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._last_frame_ts = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._opened = False
        self._frames = 0
        self._reconnects = 0
        self._last_error = None

    @property
    def is_file_source(self):
        return isinstance(self.source, str) and not self.source.lower().startswith(
            ("rtsp://", "http://", "https://", "rtmp://")
        )

    # ---------- lifecycle ----------
    def start(self):
        """Idempotent."""
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"capture-{self.name}", daemon=True
        )
        self._thread.start()
        log.info("capture[%s] started on source=%r", self.name, self.source)
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        with self._cond:
            self._cond.notify_all()

    # ---------- reading ----------
    def read(self, timeout=1.0):
        """Newest frame as a private copy, plus its sequence number."""
        with self._cond:
            if self._frame is None:
                self._cond.wait(timeout)
            if self._frame is None:
                return None, self._seq
            return self._frame.copy(), self._seq

    def read_new(self, last_seq, timeout=1.0):
        """
        Block until a frame newer than last_seq exists.

        This is what lets the detection worker skip everything it was too slow
        to see, instead of draining a backlog.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq <= last_seq and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, last_seq
                self._cond.wait(remaining)
            if self._frame is None:
                return None, self._seq
            return self._frame.copy(), self._seq

    def is_healthy(self):
        return self._opened and (time.monotonic() - self._last_frame_ts) < 5.0

    def stats(self):
        return {
            "name": self.name,
            "source": str(self.source),
            "opened": self._opened,
            "healthy": self.is_healthy(),
            "frames": self._frames,
            "reconnects": self._reconnects,
            "last_error": self._last_error,
            "age": round(time.monotonic() - self._last_frame_ts, 2) if self._last_frame_ts else None,
        }

    # ---------- internals ----------
    def _open(self):
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            cap.release()
            return None
        # BUFFERSIZE first: it is what keeps an RTSP feed near real-time.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self.buffersize)
            if not self.is_file_source:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                cap.set(cv2.CAP_PROP_FPS, self.fps)
        except Exception as exc:
            log.debug("capture[%s] property set failed: %s", self.name, exc)
        return cap

    def _publish(self, frame):
        with self._cond:
            self._frame = frame
            self._seq += 1
            self._last_frame_ts = time.monotonic()
            self._frames += 1
            self._cond.notify_all()

    def _run(self):
        cap = None
        backoff = config.CAMERA_RECONNECT_DELAY
        # File sources are paced to their native fps so a clip is not consumed
        # in two seconds; live devices block in read(), so the sleep is ~0.
        frame_interval = 1.0 / max(1, self.fps) if self.is_file_source else 0.0

        while not self._stop.is_set():
            if cap is None:
                cap = self._open()
                if cap is None:
                    self._opened = False
                    self._last_error = f"cannot open source {self.source!r}"
                    log.warning("capture[%s] %s; retrying in %.1fs",
                                self.name, self._last_error, backoff)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, config.CAMERA_RECONNECT_MAX_DELAY)
                    continue
                self._opened = True
                self._last_error = None
                backoff = config.CAMERA_RECONNECT_DELAY
                log.info("capture[%s] opened source=%r", self.name, self.source)

            started = time.monotonic()
            ok, frame = cap.read()

            if not ok or frame is None:
                if self.is_file_source and self.loop_video:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # loop the demo clip
                    continue
                self._opened = False
                self._last_error = "read failed"
                self._reconnects += 1
                log.warning("capture[%s] read failed; reconnecting (#%d)",
                            self.name, self._reconnects)
                cap.release()
                cap = None
                self._stop.wait(backoff)
                backoff = min(backoff * 2, config.CAMERA_RECONNECT_MAX_DELAY)
                continue

            self._publish(frame)

            if frame_interval:
                time.sleep(max(0.0, frame_interval - (time.monotonic() - started)))

        if cap is not None:
            cap.release()
        self._opened = False
        log.info("capture[%s] stopped", self.name)


# =================================================
# REGISTRY (lazy, one stream per name)
# =================================================
_STREAMS = {}
_LOCK = threading.Lock()


def get_stream(name="default", source=None):
    """
    One stream per SOURCE, not per caller.

    Keying by source means every gate in the simulation shares the single laptop
    webcam instead of fighting over the device, while a real deployment that
    gives each gate its own rtsp:// URL still gets one capture thread per camera.
    """
    key = str(config.resolve_camera_source(source))
    with _LOCK:
        stream = _STREAMS.get(key)
        if stream is None:
            stream = CameraStream(source=source, name=name)
            _STREAMS[key] = stream
        return stream.start()


def shutdown_all():
    with _LOCK:
        streams = list(_STREAMS.values())
        _STREAMS.clear()
    for stream in streams:
        stream.stop()


atexit.register(shutdown_all)
