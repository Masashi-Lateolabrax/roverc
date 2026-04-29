"""Camera registry + HTTP JPEG fetcher.

`CameraRegistry` is a thread-safe map of role -> CameraInfo. Entries are fed
in by `RoverCClient` from the periodic `{"cam": ...}` packets pushed by the
StickC, which probes each camera on the I2C bus and reports its IP / port /
status. The PC client therefore has no UDP-broadcast discovery path: the
StickC is the single source of truth for which cameras belong to this rover.

`CameraStream` polls `http://<ip>:<port><path>` for JPEG frames and exposes
the latest as bytes.

`set_camera_params` issues a `GET /control?fs=...&q=...` to the camera so the
sensor framesize / JPEG quality can be tuned at runtime.
"""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

# esp32-camera framesize_t enum values that we expose. Order matches the C
# header so the integer is the wire value used by /control?fs=.
FRAMESIZE_CHOICES: tuple[tuple[str, int, tuple[int, int]], ...] = (
    ("96x96",  0,  (96, 96)),
    ("QQVGA",  1,  (160, 120)),
    ("QCIF",   2,  (176, 144)),
    ("HQVGA",  3,  (240, 176)),
    ("240x240",4,  (240, 240)),
    ("QVGA",   5,  (320, 240)),
    ("CIF",    6,  (400, 296)),
    ("HVGA",   7,  (480, 320)),
    ("VGA",    8,  (640, 480)),
    ("SVGA",   9,  (800, 600)),
    ("XGA",   10, (1024, 768)),
    ("HD",    11, (1280, 720)),
    ("SXGA",  12, (1280, 1024)),
    ("UXGA",  13, (1600, 1200)),
)


@dataclass(frozen=True)
class CameraInfo:
    role: str
    ip: str
    http_port: int
    jpg_path: str
    camera_ok: bool
    last_seen_monotonic: float
    vbat_mv: int | None  # battery voltage at the camera, mV; None if unknown


class CameraRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_role: dict[str, CameraInfo] = {}

    def update(
        self,
        role: str,
        ip: str,
        http_port: int,
        camera_ok: bool,
        jpg_path: str = "/stream",
        vbat_mv: int | None = None,
    ) -> None:
        info = CameraInfo(
            role=role,
            ip=ip,
            http_port=http_port,
            jpg_path=jpg_path,
            camera_ok=camera_ok,
            last_seen_monotonic=time.monotonic(),
            vbat_mv=vbat_mv,
        )
        with self._lock:
            self._by_role[role] = info

    def clear(self, role: str) -> None:
        with self._lock:
            self._by_role.pop(role, None)

    def latest(self, role: str) -> CameraInfo | None:
        with self._lock:
            return self._by_role.get(role)

    def all_roles(self) -> dict[str, CameraInfo]:
        with self._lock:
            return dict(self._by_role)


def set_camera_params(
    info: CameraInfo,
    framesize: int | None = None,
    quality: int | None = None,
    timeout_s: float = 1.5,
) -> tuple[bool, str]:
    """Issue GET /control?fs=...&q=... to the camera. Returns (ok, body)."""
    if framesize is None and quality is None:
        return True, "noop"
    params: dict[str, str] = {}
    if framesize is not None:
        params["fs"] = str(int(framesize))
    if quality is not None:
        params["q"] = str(int(quality))
    url = (
        f"http://{info.ip}:{info.http_port}/control?"
        + urllib.parse.urlencode(params)
    )
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            body = r.read().decode("utf-8", errors="replace").strip()
        return True, body
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, str(exc)


class CameraStream:
    def __init__(self, info: CameraInfo, timeout_s: float = 5.0) -> None:
        self.info = info
        self.timeout_s = timeout_s
        self._url = f"http://{info.ip}:{info.http_port}{info.jpg_path}"
        self._lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._frame_count = 0
        self._error_count = 0
        self._last_fetch_monotonic: float | None = None
        self._last_error: str | None = None
        self._stop = threading.Event()
        # Active urllib response while inside _stream_once; stop() closes it so
        # a blocked read1() returns immediately instead of waiting for the next
        # MJPEG chunk.
        self._response: object | None = None
        self._thread = threading.Thread(
            target=self._run, name=f"CameraStream<{info.role}>", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        r = self._response
        if r is not None:
            try:
                r.close()  # type: ignore[attr-defined]
            except Exception:
                pass

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout=timeout)

    def latest(self) -> tuple[bytes | None, int, float | None, str | None, int]:
        with self._lock:
            return (
                self._latest_jpeg,
                self._frame_count,
                self._last_fetch_monotonic,
                self._last_error,
                self._error_count,
            )

    def _run(self) -> None:
        # The camera /stream endpoint returns multipart/x-mixed-replace with
        # boundary=frame. We open a single TCP connection and parse JPEG
        # parts continuously, reconnecting on any error.
        while not self._stop.is_set():
            try:
                self._stream_once()
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                with self._lock:
                    self._error_count += 1
                    self._last_error = str(exc)
                time.sleep(0.2)

    def _stream_once(self) -> None:
        with urllib.request.urlopen(self._url, timeout=self.timeout_s) as r:
            self._response = r
            try:
                self._stream_inner(r)
            finally:
                self._response = None

    def _stream_inner(self, r) -> None:
        ct = r.headers.get("Content-Type", "") or ""
        boundary_token = "frame"
        if "boundary=" in ct:
            boundary_token = ct.split("boundary=", 1)[1].split(";", 1)[0].strip()
        boundary = ("--" + boundary_token).encode("ascii")
        buf = bytearray()
        CHUNK = 32768

        def _read_into() -> bool:
            if self._stop.is_set():
                return False
            # read1() does a single underlying recv; read() can coalesce
            # multiple recvs into one return which serialises the parser
            # at the HTTPResponse buffer boundary instead of per-packet.
            chunk = r.read1(CHUNK)
            if not chunk:
                return False
            buf.extend(chunk)
            return True

        while not self._stop.is_set():
            bidx = buf.find(boundary)
            if bidx < 0:
                if not _read_into():
                    return
                continue
            # Drop everything up to and past the boundary.
            del buf[: bidx + len(boundary)]
            # Optional CRLF immediately after boundary.
            if buf[:2] == b"\r\n":
                del buf[:2]
            # Headers terminate at CRLF CRLF.
            hend = buf.find(b"\r\n\r\n")
            while hend < 0:
                if not _read_into():
                    return
                hend = buf.find(b"\r\n\r\n")
            headers_blob = bytes(buf[:hend]).decode("latin1", errors="replace")
            del buf[: hend + 4]
            clen = -1
            for line in headers_blob.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    try:
                        clen = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        clen = -1
                    break
            if clen <= 0:
                continue
            while len(buf) < clen:
                if not _read_into():
                    return
            jpeg = bytes(buf[:clen])
            del buf[:clen]
            with self._lock:
                self._latest_jpeg = jpeg
                self._frame_count += 1
                self._last_fetch_monotonic = time.monotonic()
                self._last_error = None
