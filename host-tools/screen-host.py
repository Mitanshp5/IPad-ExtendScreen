#!/usr/bin/env python3
"""ExtendScreen host: stream this Windows desktop to an iPad mini (iOS 9.3.5).

The host is deliberately small and dependency-light: Python's standard library
plus an ``ffmpeg`` executable.  It captures one monitor (or the whole virtual
desktop), scales it to a 1024x768 MJPEG stream, and serves it over the trusted
LAN.  A paired iPad can send mouse/keyboard events back.

Security model:
  * LAN-only listener by default (a specific interface address is detected).
  * Eight-digit one-time pairing code, valid for 30 minutes.
  * Per-device random bearer token stored as a SHA-256 hash on the host.
  * No TLS.  HTTP is intentional for trusted-Wi-Fi testing only.
  * Screen frames and tokens are never written to logs.

Usage:
  python host-tools/screen-host.py
  python host-tools/screen-host.py --monitor 2 --fps 10
  python host-tools/screen-host.py --test-pattern --no-input
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import math
import os
import plistlib
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlsplit

APP_NAME = "extendscreen"
APP_VERSION = "1.0.0"
BOUNDARY = b"HPScreenFrame"
MAX_JSON_BYTES = 8192
MAX_FRAME_BYTES = 4 * 1024 * 1024
PAIRING_MINUTES = 30
CABLE_DEVICE_PORT = 8765
USBMUX_WINDOWS = "127.0.0.1:27015"
USBMUX_UNIX = "/var/run/usbmuxd"
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
PAIRING_RE = re.compile(r"^[0-9]{8}$")
ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def enable_dpi_awareness() -> None:
    """Make monitor coordinates physical pixels on high-DPI Windows."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def detect_lan_ip() -> str:
    """Best-effort local IPv4 address without sending a packet."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET-1, no packet is sent
        address = sock.getsockname()[0]
    except OSError:
        address = "127.0.0.1"
    finally:
        sock.close()
    return address


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AttemptLimiter:
    """Simple per-source fixed-window limiter for pairing/input attempts."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._attempts: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            values = self._attempts.setdefault(key, deque())
            while values and now - values[0] > self.window:
                values.popleft()
            if len(values) >= self.limit:
                return False
            values.append(now)
            return True


# ---------------------------------------------------------------------------
# Monitor discovery
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class MonitorInfo:
    index: int
    region: Region
    primary: bool
    name: str = ""


if os.name == "nt":
    from ctypes import wintypes

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    _MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(_RECT), ctypes.c_ssize_t,
    )

    class _DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128),
            ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128),
            ("DeviceKey", wintypes.WCHAR * 128),
        ]


def _display_adapters() -> list[dict]:
    """List Windows display adapters, including a detached VDD output."""
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    user32.EnumDisplayDevicesW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32,
        ctypes.POINTER(_DISPLAY_DEVICEW), ctypes.c_uint32,
    ]
    user32.EnumDisplayDevicesW.restype = ctypes.c_int
    found: list[dict] = []
    index = 0
    while True:
        device = _DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(device)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        found.append({
            "name": str(device.DeviceName),
            "string": str(device.DeviceString),
            "flags": int(device.StateFlags),
            "id": str(device.DeviceID),
        })
        index += 1
    return found


def virtual_display_names() -> set[str]:
    names: set[str] = set()
    for adapter in _display_adapters():
        identity = (adapter["id"] + " " + adapter["string"]).lower()
        if "mttvdd" in identity or "virtual display driver" in identity:
            names.add(adapter["name"])
    return names


def virtual_display_state() -> tuple[bool, bool]:
    """Return (connected, active) for the virtual monitor."""
    connected = False
    active = False
    for adapter in _display_adapters():
        identity = (adapter["id"] + " " + adapter["string"]).lower()
        if "mttvdd" in identity or "virtual display driver" in identity:
            connected = True
            if adapter["flags"] & 1:
                active = True
    return connected, active


def other_active_secondaries() -> list[MonitorInfo]:
    virtual_names = virtual_display_names()
    return [monitor for monitor in enumerate_monitors()
            if not monitor.primary and monitor.name not in virtual_names]


class DisplayTopology:
    """Keep Windows single-screen until the iPad is actually streaming."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        if self.enabled:
            import atexit
            atexit.register(self.disable, "Process exit cleanup")

    def _switch(self, option: str) -> bool:
        if os.name == "nt":
            try:
                user32 = ctypes.windll.user32
                hdesk = user32.OpenDesktopW("default", 0, False, 0x01FF)
                if hdesk:
                    user32.SetThreadDesktop(hdesk)
                SDC_APPLY = 0x00000080
                SDC_TOPOLOGY_INTERNAL = 0x00000001
                SDC_TOPOLOGY_EXTEND = 0x00000004
                flag = SDC_TOPOLOGY_INTERNAL if option == "/internal" else (SDC_TOPOLOGY_EXTEND if option == "/extend" else None)
                if flag is not None:
                    res = user32.SetDisplayConfig(0, None, 0, None, SDC_APPLY | flag)
                    if res == 0:
                        return True
            except Exception:
                pass

        executable = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                  "System32", "DisplaySwitch.exe")
        try:
            subprocess.run([executable, option], check=False, timeout=12,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=(0x08000000 if os.name == "nt" else 0))
            return True
        except (OSError, subprocess.SubprocessError) as error:
            print(f"Display: could not change the Windows display layout ({error}).",
                  file=sys.stderr)
            return False

    def _wait_for(self, active: bool, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if virtual_display_state()[1] == active:
                return True
            time.sleep(0.2)
        return virtual_display_state()[1] == active

    def enable(self) -> bool:
        if not self.enabled:
            return False
        connected, active = virtual_display_state()
        if active:
            return True
        if not connected:
            print("Display: the virtual monitor is not connected; leaving the layout unchanged.",
                  file=sys.stderr)
            return False
        print("Display: iPad connected; extending the Windows desktop to the virtual monitor.",
              flush=True)
        if not self._switch("/extend"):
            return False
        if not self._wait_for(True, 8.0):
            print("Display: the virtual monitor did not become active.", file=sys.stderr)
            return False
        return True

    def disable(self, reason: str = "The iPad is not connected") -> bool:
        if not self.enabled:
            return False
        connected, active = virtual_display_state()
        if not connected or not active:
            return True
        if other_active_secondaries():
            print("Display: another external monitor is active; leaving the Windows layout unchanged.",
                  file=sys.stderr)
            return False
        print(f"Display: {reason}; returning the PC to single-screen mode.", flush=True)
        if not self._switch("/internal"):
            return False
        if not self._wait_for(False, 8.0):
            print("Display: the virtual monitor is still active.", file=sys.stderr)
            return False
        return True


def enumerate_monitors() -> list[MonitorInfo]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    found: list[MonitorInfo] = []

    def callback(handle, _hdc, _rect, _data):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            rect = info.rcMonitor
            found.append(MonitorInfo(
                index=0,
                region=Region(rect.left, rect.top,
                              rect.right - rect.left, rect.bottom - rect.top),
                primary=bool(info.dwFlags & 1),
                name=str(info.szDevice),
            ))
        return 1

    procedure = _MONITORENUMPROC(callback)
    user32.EnumDisplayMonitors(None, None, procedure, 0)
    # Enumeration order is not guaranteed.  Order left-to-right, top-to-bottom.
    found.sort(key=lambda item: (item.region.x, item.region.y))
    return [MonitorInfo(index=i + 1, region=m.region, primary=m.primary, name=m.name)
            for i, m in enumerate(found)]


def virtual_desktop_region() -> Region:
    if os.name == "nt":
        user32 = ctypes.windll.user32
        x = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        y = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if width > 0 and height > 0:
            return Region(x, y, width, height)
    return Region(0, 0, 1024, 768)


def parse_region(text: str) -> Region:
    parts = text.lower().replace(" ", "").split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("region must be x,y,width,height")
    try:
        x, y, width, height = (int(value) for value in parts)
    except ValueError as error:
        raise argparse.ArgumentTypeError("region values must be integers") from error
    if width < 64 or height < 64 or width > 16384 or height > 16384:
        raise argparse.ArgumentTypeError("region size is not supported")
    return Region(x, y, width, height)


# ---------------------------------------------------------------------------
# Configuration and pairing state
# ---------------------------------------------------------------------------

@dataclass
class Config:
    bind: str
    port: int
    monitor: str
    region: Optional[Region]
    width: int
    height: int
    fps: int
    quality: int
    no_input: bool
    allow_peers: tuple[str, ...]
    state_dir: Path
    test_pattern: bool
    capture: str = "auto"
    ddagrab_output: Optional[int] = None
    manage_display: bool = True


class PairingWindow:
    def __init__(self, minutes: int = PAIRING_MINUTES) -> None:
        self.code = f"{secrets.randbelow(10 ** 8):08d}"
        self.expires = time.time() + minutes * 60
        self.minutes = minutes

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires

    def matches(self, candidate: object) -> bool:
        return (isinstance(candidate, str) and PAIRING_RE.match(candidate) is not None
                and not self.expired and hmac.compare_digest(candidate, self.code))


class PairingStore:
    """Persist only hashes of device tokens; the raw token is shown once."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "state.json"
        self._lock = threading.Lock()
        self._devices: list[dict] = []
        self._hashes: set[str] = set()
        self._cable_token: Optional[str] = None
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            devices = payload.get("devices", [])
            if not isinstance(devices, list):
                raise ValueError("devices is not a list")
            for device in devices:
                digest = device.get("token_sha256") if isinstance(device, dict) else None
                if isinstance(digest, str) and re.match(r"^[0-9a-f]{64}$", digest):
                    self._devices.append({
                        "name": str(device.get("name", "iPad"))[:64],
                        "created": int(device.get("created", 0)),
                        "token_sha256": digest,
                        "last_seen": int(device.get("last_seen", 0)),
                    })
                    self._hashes.add(digest)
            cable_token = payload.get("cable_token")
            if isinstance(cable_token, str) and TOKEN_RE.match(cable_token):
                self._cable_token = cable_token
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            print(f"warning: ignoring invalid pairing state ({type(error).__name__}); "
                  "paired devices must pair again", file=sys.stderr)
            self._devices = []
            self._hashes = set()
            self._cable_token = None

    def _save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "devices": self._devices}
        if self._cable_token:
            payload["cable_token"] = self._cable_token
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    def add(self, name: str) -> str:
        token = secrets.token_hex(32)
        digest = sha256_hex(token)
        now = int(time.time())
        with self._lock:
            self._devices.append({
                "name": (name or "iPad")[:64],
                "created": now,
                "token_sha256": digest,
                "last_seen": now,
            })
            self._hashes.add(digest)
            self._save()
        return token

    def check(self, token: str) -> bool:
        if not isinstance(token, str) or TOKEN_RE.match(token) is None:
            return False
        return sha256_hex(token) in self._hashes

    def touch(self, token: str) -> None:
        digest = sha256_hex(token)
        with self._lock:
            for device in self._devices:
                if device["token_sha256"] == digest:
                    device["last_seen"] = int(time.time())
                    return

    def cable_token(self) -> str:
        with self._lock:
            if self._cable_token is None:
                self._cable_token = secrets.token_hex(32)
                self._save()
            return self._cable_token

    @property
    def device_count(self) -> int:
        return len(self._devices)


# ---------------------------------------------------------------------------
# MJPEG source
# ---------------------------------------------------------------------------

class JPEGFramer:
    """Split ffmpeg's back-to-back JPEG image2pipe output into frames."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        if chunk:
            self.buffer.extend(chunk)
        frames: list[bytes] = []
        while True:
            start = self.buffer.find(b"\xff\xd8")
            if start < 0:
                # Keep a possible first SOI byte for the next chunk.
                if self.buffer[-1:] == b"\xff":
                    self.buffer = bytearray(b"\xff")
                else:
                    self.buffer.clear()
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b"\xff\xd9", 2)
            if end < 0:
                if len(self.buffer) > MAX_FRAME_BYTES:
                    self.buffer.clear()
                break
            frames.append(bytes(self.buffer[:end + 2]))
            del self.buffer[:end + 2]
        return frames


class FrameSource:
    """Thread-safe latest-frame source used by one streaming connection."""

    def wait_frame(self, after_sequence: int, timeout: float) -> tuple[int, Optional[bytes], Optional[str]]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _publish(self, frame: bytes) -> None:
        with self._condition:
            self._sequence += 1
            self._latest = frame
            self._condition.notify_all()

    def _fail(self, message: str) -> None:
        with self._condition:
            self._error = message
            self._condition.notify_all()

    def _wait(self, after_sequence: int, timeout: float) -> tuple[int, Optional[bytes], Optional[str]]:
        deadline = time.monotonic() + timeout
        with self._condition:
            # ``-1`` is the sentinel for "wait for the first frame".  The
            # sequence starts at 0 with no frame published yet, so also wait
            # while ``_latest`` is None instead of returning immediately.
            while self._error is None and (
                    self._latest is None or self._sequence <= after_sequence):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return after_sequence, None, "timeout"
                self._condition.wait(remaining)
            if self._error is None and self._latest is not None and self._sequence > after_sequence:
                return self._sequence, self._latest, None
            return after_sequence, None, self._error or "stopped"


def ddagrab_output_for_region(region: Region) -> Optional[int]:
    """Return the Desktop Duplication output index for a monitor-sized region."""
    for monitor in enumerate_monitors():
        if monitor.region == region:
            return monitor.index - 1
    return None


class FFmpegFrameSource(FrameSource):
    def __init__(self, config: Config, region: Region, fps: int, capture: str = "gdigrab") -> None:
        self.config = config
        self.region = region
        self.fps = fps
        self.capture = capture
        self._condition = threading.Condition()
        self._sequence = 0
        self._latest: Optional[bytes] = None
        self._error: Optional[str] = None
        self._stop = False
        self._process = subprocess.Popen(
            self._command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            creationflags=(0x08000000 if os.name == "nt" else 0),  # CREATE_NO_WINDOW
        )
        self._stderr_tail: deque[str] = deque(maxlen=12)
        self._reader = threading.Thread(target=self._read_frames, name="extendscreen-frames", daemon=True)
        self._errors = threading.Thread(target=self._read_errors, name="extendscreen-ffmpeg-errors", daemon=True)
        self._reader.start()
        self._errors.start()

    def _command(self) -> list[str]:
        config = self.config
        region = self.region
        filters = (
            f"scale={config.width}:{config.height}:"
            f"force_original_aspect_ratio=decrease:flags=fast_bilinear,"
            f"pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
        common = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        output = ["-an", "-c:v", "mjpeg", "-q:v", str(config.quality), "-pix_fmt", "yuvj420p",
                  "-f", "image2pipe", "-"]
        if config.test_pattern:
            return [
                *common, "-re", "-f", "lavfi", "-i",
                f"testsrc2=size={region.width}x{region.height}:rate={self.fps}",
                "-vf", filters, *output,
            ]
        if self.capture == "ddagrab":
            output_index = config.ddagrab_output
            if output_index is None:
                output_index = ddagrab_output_for_region(region)
            if output_index is None:
                raise ValueError("ddagrab could not match the capture region to a display output")
            graph = (
                f"ddagrab=output_idx={output_index}:framerate={self.fps}:"
                f"video_size={region.width}x{region.height}:draw_mouse=1,"
                f"hwdownload,format=bgra,{filters}"
            )
            return [*common, "-filter_complex", graph, *output]
        return [
            *common,
            "-f", "gdigrab", "-framerate", str(self.fps), "-draw_mouse", "1",
            "-offset_x", str(region.x), "-offset_y", str(region.y),
            "-video_size", f"{region.width}x{region.height}", "-i", "desktop",
            "-vf", filters, *output,
        ]

    def _read_errors(self) -> None:
        assert self._process.stderr is not None
        for raw in iter(self._process.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self._stderr_tail.append(line[:300])

    def _read_frames(self) -> None:
        assert self._process.stdout is not None
        framer = JPEGFramer()
        try:
            while not self._stop:
                chunk = self._process.stdout.read(65536)
                if not chunk:
                    break
                for frame in framer.feed(chunk):
                    self._publish(frame)
        except (OSError, ValueError) as error:
            if not self._stop:
                self._fail(f"capture read failed: {type(error).__name__}")
        finally:
            if not self._stop:
                try:
                    code = self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    code = -1
                detail = "; ".join(self._stderr_tail)
                message = f"ffmpeg stopped (exit {code})"
                if detail:
                    message += f": {detail}"
                self._fail(message)

    def wait_frame(self, after_sequence: int, timeout: float) -> tuple[int, Optional[bytes], Optional[str]]:
        return self._wait(after_sequence, timeout)

    def close(self) -> None:
        self._stop = True
        process = self._process
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        for stream in (process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        self._reader.join(timeout=1)
        self._errors.join(timeout=1)


class FallbackFrameSource(FrameSource):
    """Start with ddagrab and fall back to gdigrab if Desktop Duplication fails."""

    def __init__(self, config: Config, region: Region, fps: int) -> None:
        self.config = config
        self.region = region
        self.fps = fps
        self._offset = 0
        self._fallback_started = False
        self._ddagrab: Optional[FFmpegFrameSource] = None
        self._active: Optional[FFmpegFrameSource] = None
        if config.capture in {"auto", "ddagrab"}:
            try:
                self._ddagrab = FFmpegFrameSource(config, region, fps, capture="ddagrab")
                self._active = self._ddagrab
            except (OSError, ValueError) as error:
                if config.capture == "ddagrab":
                    print(f"ddagrab unavailable ({error}); using gdigrab.", file=sys.stderr)
        if self._active is None:
            self._active = FFmpegFrameSource(config, region, fps, capture="gdigrab")

    def _start_fallback(self, after_sequence: int) -> None:
        if self._fallback_started or self._ddagrab is None:
            return
        self._fallback_started = True
        print("Desktop Duplication stalled or stopped; falling back to gdigrab.", file=sys.stderr)
        self._ddagrab.close()
        self._ddagrab = None
        self._offset = max(0, after_sequence)
        self._active = FFmpegFrameSource(self.config, self.region, self.fps, capture="gdigrab")

    def wait_frame(self, after_sequence: int, timeout: float) -> tuple[int, Optional[bytes], Optional[str]]:
        assert self._active is not None
        sequence, frame, error = self._active.wait_frame(after_sequence - self._offset, timeout)
        if (frame is None and self._ddagrab is not None and not self._fallback_started
                and (error != "timeout" or after_sequence < 0)):
            self._start_fallback(after_sequence)
            assert self._active is not None
            sequence, frame, error = self._active.wait_frame(-1, 10.0)
            if frame is not None:
                return self._offset + sequence, frame, None
            return after_sequence, None, error
        if frame is not None:
            return self._offset + sequence, frame, None
        return after_sequence, None, error

    def close(self) -> None:
        if self._active is not None:
            self._active.close()


def make_ffmpeg_source(config: Config, region: Region, fps: int) -> FrameSource:
    if config.test_pattern or config.capture == "gdigrab":
        return FFmpegFrameSource(config, region, fps, capture="gdigrab")
    return FallbackFrameSource(config, region, fps)


# ---------------------------------------------------------------------------
# Windows input forwarding
# ---------------------------------------------------------------------------

class NullInput:
    available = False

    def apply(self, event: dict) -> bool:
        return False


if os.name == "nt":
    _ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long), ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
            ("time", ctypes.c_uint32), ("dwExtraInfo", _ULONG_PTR),
        ]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
            ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_uint32), ("union", _INPUTUNION)]

    class WindowsInput:
        available = True
        MOUSEEVENTF_MOVE = 0x0001
        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        MOUSEEVENTF_RIGHTDOWN = 0x0008
        MOUSEEVENTF_RIGHTUP = 0x0010
        MOUSEEVENTF_WHEEL = 0x0800
        MOUSEEVENTF_ABSOLUTE = 0x8000
        MOUSEEVENTF_VIRTUALDESK = 0x4000
        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_UNICODE = 0x0004

        VK = {
            "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
            "backspace": 0x08, "delete": 0x2E, "space": 0x20,
            "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
            "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
            "insert": 0x2D, "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
            "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
            "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
        }

        def __init__(self, region: Region, output_width: int, output_height: int) -> None:
            self.user32 = ctypes.windll.user32
            self.region = region
            self.output_width = output_width
            self.output_height = output_height
            scale = min(output_width / region.width, output_height / region.height)
            self.draw_width = max(1, int(round(region.width * scale)))
            self.draw_height = max(1, int(round(region.height * scale)))
            self.pad_x = (output_width - self.draw_width) // 2
            self.pad_y = (output_height - self.draw_height) // 2
            self.scale = scale
            self._left_down = False

        def _screen_point(self, event: dict) -> Optional[tuple[int, int]]:
            x = event.get("x")
            y = event.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                return None
            if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x <= 1) or not (0 <= y <= 1):
                return None
            draw_x = clamp(int(round(x * self.output_width)) - self.pad_x, 0, self.draw_width - 1)
            draw_y = clamp(int(round(y * self.output_height)) - self.pad_y, 0, self.draw_height - 1)
            return (self.region.x + int(draw_x / self.scale),
                    self.region.y + int(draw_y / self.scale))

        def _absolute(self, point: tuple[int, int]) -> tuple[int, int]:
            vx = self.user32.GetSystemMetrics(76)
            vy = self.user32.GetSystemMetrics(77)
            vw = max(1, self.user32.GetSystemMetrics(78))
            vh = max(1, self.user32.GetSystemMetrics(79))
            x = clamp(point[0], vx, vx + vw - 1)
            y = clamp(point[1], vy, vy + vh - 1)
            return (int(round((x - vx) * 65535 / max(1, vw - 1))),
                    int(round((y - vy) * 65535 / max(1, vh - 1))))

        def _mouse(self, flags: int, point: Optional[tuple[int, int]] = None,
                   data: int = 0) -> None:
            mouse = _MOUSEINPUT()
            mouse.mouseData = ctypes.c_uint32(data & 0xFFFFFFFF)
            mouse.dwFlags = flags
            if point is not None:
                mouse.dx, mouse.dy = self._absolute(point)
                mouse.dwFlags |= self.MOUSEEVENTF_ABSOLUTE | self.MOUSEEVENTF_VIRTUALDESK
            item = _INPUT(type=0, union=_INPUTUNION(mi=mouse))
            self.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_INPUT))

        def _key(self, virtual_key: int, up: bool = False) -> None:
            keyboard = _KEYBDINPUT(wVk=virtual_key, dwFlags=self.KEYEVENTF_KEYUP if up else 0)
            item = _INPUT(type=1, union=_INPUTUNION(ki=keyboard))
            self.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_INPUT))

        def _unicode(self, unit: int, up: bool = False) -> None:
            flags = self.KEYEVENTF_UNICODE | (self.KEYEVENTF_KEYUP if up else 0)
            keyboard = _KEYBDINPUT(wScan=unit & 0xFFFF, dwFlags=flags)
            item = _INPUT(type=1, union=_INPUTUNION(ki=keyboard))
            self.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_INPUT))

        def _click(self, point: tuple[int, int], right: bool = False) -> None:
            self._mouse(self.MOUSEEVENTF_MOVE, point)
            down = self.MOUSEEVENTF_RIGHTDOWN if right else self.MOUSEEVENTF_LEFTDOWN
            up = self.MOUSEEVENTF_RIGHTUP if right else self.MOUSEEVENTF_LEFTUP
            self._mouse(down, point)
            time.sleep(0.018)
            self._mouse(up, point)

        def apply(self, event: dict) -> bool:
            if not isinstance(event, dict):
                return False
            kind = event.get("type")
            if kind in {"tap", "rclick", "down", "move", "up"}:
                point = self._screen_point(event)
                if point is None:
                    return False
                if kind == "tap":
                    self._click(point)
                elif kind == "rclick":
                    self._click(point, right=True)
                elif kind == "down":
                    self._mouse(self.MOUSEEVENTF_LEFTDOWN, point)
                    self._left_down = True
                elif kind == "move":
                    self._mouse(self.MOUSEEVENTF_MOVE, point)
                elif kind == "up":
                    self._mouse(self.MOUSEEVENTF_LEFTUP, point)
                    self._left_down = False
                return True
            if kind == "scroll":
                dy = event.get("dy")
                if not isinstance(dy, int) or abs(dy) > 2400:
                    return False
                self._mouse(self.MOUSEEVENTF_WHEEL, None, dy)
                return True
            if kind == "text":
                text = event.get("text")
                if not isinstance(text, str) or not text or len(text) > 512 or "\x00" in text:
                    return False
                for char in text:
                    if char == "\n":
                        self._key(0x0D)
                        self._key(0x0D, up=True)
                        continue
                    if char == "\t":
                        self._key(0x09)
                        self._key(0x09, up=True)
                        continue
                    encoded = char.encode("utf-16-le")
                    for index in range(0, len(encoded), 2):
                        unit = int.from_bytes(encoded[index:index + 2], "little")
                        self._unicode(unit)
                        self._unicode(unit, up=True)
                return True
            if kind == "key":
                name = event.get("name")
                mods = event.get("mods", [])
                if not isinstance(name, str) or not isinstance(mods, list):
                    return False
                name = name.lower()
                virtual_key = self.VK.get(name)
                if virtual_key is None and len(name) == 1 and name.isalnum():
                    virtual_key = ord(name.upper())
                if virtual_key is None:
                    return False
                mod_keys = []
                for mod in mods:
                    if mod == "ctrl":
                        mod_keys.append(0x11)
                    elif mod == "alt":
                        mod_keys.append(0x12)
                    elif mod == "shift":
                        mod_keys.append(0x10)
                    elif mod == "win":
                        mod_keys.append(0x5B)
                    else:
                        return False
                for mod in mod_keys:
                    self._key(mod)
                self._key(virtual_key)
                self._key(virtual_key, up=True)
                for mod in reversed(mod_keys):
                    self._key(mod, up=True)
                return True
            return False

else:
    class WindowsInput(NullInput):  # type: ignore[no-redef]
        def __init__(self, region: Region, output_width: int, output_height: int) -> None:
            self.available = False


def make_input_backend(config: Config, region: Region) -> object:
    return WindowsInput(region, config.width, config.height)


# ---------------------------------------------------------------------------
# USB cable transport (usbmuxd)
# ---------------------------------------------------------------------------

def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("peer closed the connection")
        data.extend(chunk)
    return bytes(data)


def send_json_line(sock: socket.socket, payload: dict) -> None:
    data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("cable message is too large")
    sock.sendall(data)


def recv_json_line(sock: socket.socket, limit: int = MAX_JSON_BYTES) -> dict:
    data = bytearray()
    while len(data) < limit:
        byte = sock.recv(1)
        if not byte:
            raise ConnectionError("peer closed the connection")
        if byte == b"\n":
            break
        if byte != b"\r":
            data.extend(byte)
    else:
        raise ValueError("cable message is too large")
    if not data:
        raise ValueError("empty cable message")
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("cable message is not a JSON object")
    return payload


def default_usbmux_address() -> str:
    return USBMUX_WINDOWS if os.name == "nt" else USBMUX_UNIX


def _usbmux_open(address: str) -> socket.socket:
    if address.startswith("/"):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(address)
        return sock
    host, _, port = address.rpartition(":")
    if not host:
        host, port = address, "27015"
    return socket.create_connection((host, int(port)), timeout=10)


def _usbmux_exchange(sock: socket.socket, tag: int, request: dict) -> dict:
    payload = plistlib.dumps(request, fmt=plistlib.FMT_XML)
    header = struct.pack("<IIII", len(payload) + 16, 1, 8, tag)
    sock.sendall(header + payload)
    raw_header = recv_exact(sock, 16)
    length, version, message_type, _reply_tag = struct.unpack("<IIII", raw_header)
    if version != 1 or message_type != 8 or not 16 <= length <= 1024 * 1024:
        raise RuntimeError("unexpected usbmuxd response")
    body = recv_exact(sock, length - 16)
    reply = plistlib.loads(body)
    if not isinstance(reply, dict):
        raise RuntimeError("invalid usbmuxd plist response")
    return reply


def usbmux_connect(address: str, device_port: int, udid: Optional[str] = None) -> socket.socket:
    """Tunnel to a TCP port on the iPad through the Apple usbmuxd service."""
    sock = _usbmux_open(address)
    try:
        listing = _usbmux_exchange(sock, 1, {
            "MessageType": "ListDevices",
            "ClientVersionString": "ExtendScreen 1.0",
            "ProgName": "screen-host",
            "kLibUSBMuxVersion": 3,
        })
        devices = listing.get("DeviceList")
        if not isinstance(devices, list) or not devices:
            raise RuntimeError("no iPad is visible over USB; connect the cable, unlock the iPad and trust this PC")
        selected = None
        for device in devices:
            if not isinstance(device, dict):
                continue
            properties = device.get("Properties", {}) if isinstance(device.get("Properties"), dict) else {}
            candidates = {str(properties.get("SerialNumber", "")),
                          str(properties.get("UDID", "")),
                          str(device.get("DeviceID", ""))}
            if not udid or udid in candidates:
                selected = device
                break
        if selected is None:
            raise RuntimeError(f"no connected iPad matched UDID {udid!r}")
        device_id = int(selected["DeviceID"])
        reply = _usbmux_exchange(sock, 2, {
            "MessageType": "Connect",
            "ClientVersionString": "ExtendScreen 1.0",
            "ProgName": "screen-host",
            "kLibUSBMuxVersion": 3,
            "DeviceID": device_id,
            "PortNumber": socket.htons(device_port),
        })
        if reply.get("MessageType") != "Result" or int(reply.get("Number", -1)) != 0:
            raise RuntimeError(f"the iPad refused the cable port (usbmuxd result {reply.get('Number')!r})")
        sock.settimeout(5)
        return sock
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise


def _cable_input_reader(sock: socket.socket, backend: object, stop: threading.Event) -> None:
    buffer = bytearray()
    while not stop.is_set():
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        except OSError as error:
            print(f"Cable input reader stopped: {error}", file=sys.stderr)
            break
        if not chunk:
            print("Cable input reader: the iPad closed its side of the cable (EOF).", file=sys.stderr)
            break
        buffer.extend(chunk)
        if len(buffer) > MAX_JSON_BYTES * 4:
            buffer.clear()
            continue
        while b"\n" in buffer:
            line, _, remainder = buffer.partition(b"\n")
            buffer = bytearray(remainder)
            if not line or len(line) > MAX_JSON_BYTES:
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(event, dict):
                try:
                    backend.apply(event)  # type: ignore[attr-defined]
                except Exception:
                    pass
    stop.set()


def run_cable(config: Config, args: argparse.Namespace,
              source_factory: Callable[[Config, Region, int], FrameSource] = make_ffmpeg_source,
              input_backend: Optional[object] = None) -> int:
    manage_display = (config.manage_display and config.monitor == "extend"
                      and not config.test_pattern)
    topology = DisplayTopology(manage_display)
    if manage_display:
        topology.disable("No iPad is connected")
    region: Optional[Region] = None
    backend = input_backend
    if not manage_display:
        region = resolve_region(config)
        if backend is None:
            backend = make_input_backend(config, region)
    input_available = bool(getattr(backend, "available", False)) and not config.no_input

    print(f"Extend Screen cable host {APP_VERSION}")
    print(f"  Device port: {args.cable_port} (the app listens on 127.0.0.1:{args.cable_port})")
    print("  Pairing    : none; USB cable mode has no password or code.")
    print(f"  Transport  : {'direct ' + args.cable_connect if args.cable_connect else 'usbmuxd ' + args.usbmux}")
    if region is not None:
        print(f"  Capture    : {region.width}x{region.height} at ({region.x},{region.y}) -> "
              f"{config.width}x{config.height} @ {config.fps} fps")
    else:
        print("  Capture    : extended desktop (enabled when the iPad connects)")
        print("  Display    : single-screen until the iPad is connected")
    print(f"  Input      : {'enabled' if not config.no_input else 'disabled'}")
    print("  Security   : USB/private; the screen stream is not sent over the network.")
    if config.test_pattern:
        print("  Mode       : synthetic test pattern (desktop is not captured)")

    sock: Optional[socket.socket] = None
    source: Optional[FrameSource] = None
    stop = threading.Event()
    reader: Optional[threading.Thread] = None
    try:
        if args.cable_connect:
            host, _, port = args.cable_connect.rpartition(":")
            if not host:
                host, port = args.cable_connect, "8765"
            print(f"Connecting to {host}:{port} ...")
            sock = socket.create_connection((host, int(port)), timeout=10)
        else:
            print(f"Connecting through usbmuxd at {args.usbmux} ...")
            deadline = time.monotonic() + 30.0
            while True:
                try:
                    sock = usbmux_connect(args.usbmux, args.cable_port, args.udid)
                    break
                except (OSError, RuntimeError) as error:
                    if time.monotonic() >= deadline:
                        raise
                    print(f"Waiting for the iPad cable listener: {error}", file=sys.stderr)
                    time.sleep(2.0)
        sock.settimeout(15)
        sock.sendall(b"ESP1\n")
        send_json_line(sock, {
            "type": "hello",
            "version": 1,
            "width": config.width,
            "height": config.height,
            "fps": config.fps,
            "input": not config.no_input,
        })
        reply = recv_json_line(sock)
        if reply.get("type") != "welcome":
            message = reply.get("message")
            print(f"iPad rejected the cable connection: {message or reply.get('type')!r}",
                  file=sys.stderr)
            return 1
        print("iPad connected over the cable.")
        if manage_display:
            if topology.enable():
                print("Display: extended desktop is ready.", flush=True)
            else:
                print("Display: continuing with the current Windows layout.", file=sys.stderr)
            region = resolve_region_when_ready(config, 8.0)
            if region is None:
                print("The extended desktop did not appear; check the virtual display driver.",
                      file=sys.stderr)
                return 1
            if backend is None:
                backend = make_input_backend(config, region)
            input_available = bool(getattr(backend, "available", False)) and not config.no_input
            print(f"  Capture    : {region.width}x{region.height} at ({region.x},{region.y}) -> "
                  f"{config.width}x{config.height} @ {config.fps} fps")
            print(f"  Input      : {'enabled' if input_available else 'disabled'}")
        assert region is not None
        print("Streaming; press Ctrl+C to stop.")
        sock.settimeout(5)
        source = source_factory(config, region, config.fps)
        sequence, first, error = source.wait_frame(-1, 10.0)
        if first is None:
            print(f"Screen capture failed: {error or 'no frame'}", file=sys.stderr)
            return 1
        reader_backend = backend if backend is not None else NullInput()
        reader = threading.Thread(target=_cable_input_reader, args=(sock, reader_backend, stop),
                                  name="extendscreen-cable-input", daemon=True)
        reader.start()
        last_sequence = sequence
        sock.sendall(struct.pack(">I", len(first)) + first)
        while not stop.is_set():
            sequence, frame, error = source.wait_frame(last_sequence, 5.0)
            if frame is None:
                if error and error != "timeout":
                    print(f"Cable stream stopped: frame capture error: {error}", file=sys.stderr)
                    break
                continue
            try:
                sock.sendall(struct.pack(">I", len(frame)) + frame)
            except OSError as send_err:
                print(f"Cable stream stopped: connection closed: {send_err}", file=sys.stderr)
                raise
            last_sequence = sequence
    except KeyboardInterrupt:
        print("\nStopping cable stream.")
    except (OSError, ConnectionError, ValueError, RuntimeError) as error:
        print(f"Cable connection failed: {error}", file=sys.stderr)
        if not args.cable_connect and isinstance(error, OSError):
            print("If usbmuxd is not running, install iTunes/Apple Mobile Device support "
                  "or libimobiledevice, or use --cable-connect after starting iproxy.",
                  file=sys.stderr)
        return 1
    finally:
        stop.set()
        if source is not None:
            source.close()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if reader is not None:
            reader.join(timeout=1)
        if topology.enabled:
            topology.disable("The cable stream ended")
    return 0


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class ScreenHost:
    def __init__(self, config: Config,
                 frame_source_factory: Callable[[Config, Region, int], FrameSource] = make_ffmpeg_source,
                 input_backend: Optional[object] = None) -> None:
        self.config = config
        self.pairing = PairingWindow()
        self.store = PairingStore(config.state_dir)
        self.frame_source_factory = frame_source_factory
        self.stream_slots = threading.BoundedSemaphore(2)
        self.pair_limiter = AttemptLimiter(8, 60)
        self.input_limiter = AttemptLimiter(180, 1.0)
        self.started = time.time()
        self.running = True
        self._stream_lock = threading.Lock()
        self._active_streams = 0
        self._disable_timer: Optional[threading.Timer] = None
        self.display_topology = DisplayTopology(
            config.manage_display and config.monitor == "extend"
            and not config.test_pattern and config.region is None)
        if self.display_topology.enabled:
            self.display_topology.disable("No iPad is streaming")
            self.region: Optional[Region] = config.region
            self.input_backend = input_backend
        else:
            self.region = resolve_region(config)
            self.input_backend = (input_backend if input_backend is not None
                                  else make_input_backend(config, self.region))

    @property
    def input_available(self) -> bool:
        if self.config.no_input:
            return False
        if self.input_backend is None:
            return True  # capability; the backend is created when a stream starts
        return bool(getattr(self.input_backend, "available", False))

    def begin_stream(self) -> None:
        with self._stream_lock:
            if self._disable_timer is not None:
                self._disable_timer.cancel()
                self._disable_timer = None
            if self.region is None:
                try:
                    if self.display_topology.enabled:
                        if not self.display_topology.enable():
                            print("Display: continuing with the current Windows layout.",
                                  file=sys.stderr)
                    self.region = resolve_region_when_ready(self.config, 8.0)
                    if self.region is None:
                        raise RuntimeError("the extended desktop did not appear; "
                                           "check the virtual display driver")
                    if self.input_backend is None:
                        self.input_backend = make_input_backend(self.config, self.region)
                except Exception:
                    if self._active_streams == 0 and self.display_topology.enabled:
                        self.display_topology.disable("No iPad is streaming")
                    raise
            elif self.input_backend is None:
                self.input_backend = make_input_backend(self.config, self.region)
            self._active_streams += 1

    def end_stream(self) -> None:
        with self._stream_lock:
            self._active_streams = max(0, self._active_streams - 1)
            if self._active_streams == 0 and self.display_topology.enabled:
                if self._disable_timer is not None:
                    self._disable_timer.cancel()
                def _do_disable():
                    with self._stream_lock:
                        if self._active_streams == 0 and self.display_topology.enabled:
                            self.display_topology.disable("The iPad stream ended")
                            self.region = None
                self._disable_timer = threading.Timer(15.0, _do_disable)
                self._disable_timer.daemon = True
                self._disable_timer.start()

    def shutdown_display(self) -> None:
        with self._stream_lock:
            if self._disable_timer is not None:
                self._disable_timer.cancel()
                self._disable_timer = None
            if self.display_topology.enabled:
                self.display_topology.disable("The host is stopping")
                self.region = None


def resolve_region(config: Config) -> Region:
    if config.region is not None:
        return config.region
    monitors = enumerate_monitors()
    if config.monitor == "all":
        return virtual_desktop_region()
    if not monitors:
        if config.test_pattern:
            return Region(0, 0, 1280, 720)
        return virtual_desktop_region()
    if config.monitor in {"primary", "0"}:
        for monitor in monitors:
            if monitor.primary:
                return monitor.region
        return monitors[0].region
    if config.monitor == "extend":
        for monitor in monitors:
            if not monitor.primary:
                return monitor.region
        raise SystemExit(
            "extend mode needs a second display, but only the primary monitor is active.\n"
            "Install a virtual display driver (for example VirtualDrivers.Virtual-Display-Driver), "
            "then switch Windows to Extend (Win+P) and run --list-monitors again."
        )
    try:
        index = int(config.monitor)
    except ValueError as error:
        raise SystemExit(f"unknown monitor {config.monitor!r}; use primary, extend, all or 1-{len(monitors)}") from error
    if not 1 <= index <= len(monitors):
        raise SystemExit(f"monitor {index} not found; available: 1-{len(monitors)}")
    return monitors[index - 1].region


def resolve_region_when_ready(config: Config, timeout: float = 8.0) -> Optional[Region]:
    """Wait for the virtual monitor to appear after enabling extend mode."""
    deadline = time.monotonic() + timeout
    last_error = ""
    while True:
        try:
            return resolve_region(config)
        except SystemExit as error:
            last_error = str(error)
        if time.monotonic() >= deadline:
            if last_error:
                print(last_error, file=sys.stderr)
            return None
        time.sleep(0.25)


class ScreenHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], host: ScreenHost) -> None:
        self.host = host
        super().__init__(address, ScreenRequestHandler)


class ScreenRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"ExtendScreen/{APP_VERSION}"
    sys_version = ""

    @property
    def host(self) -> ScreenHost:
        return self.server.host  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        # Paths contain no credentials (bearer token lives in a header).
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def _peer_allowed(self) -> bool:
        allowed = self.host.config.allow_peers
        if not allowed:
            return True
        return self.client_address[0] in allowed

    def _authorized(self) -> Optional[str]:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        token = header[7:].strip()
        if TOKEN_RE.match(token) is None or not self.host.store.check(token):
            return None
        return token

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_json(self) -> Optional[dict]:
        length_header = self.headers.get("Content-Length")
        content_type = self.headers.get("Content-Type", "")
        if not length_header or "application/json" not in content_type.lower():
            self._json(415, {"error": "expected application/json"})
            return None
        try:
            length = int(length_header)
        except ValueError:
            self._json(400, {"error": "invalid content length"})
            return None
        if length <= 0 or length > MAX_JSON_BYTES:
            self._json(413, {"error": "request body is too large"})
            return None
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "invalid JSON"})
            return None
        if not isinstance(payload, dict):
            self._json(400, {"error": "expected a JSON object"})
            return None
        return payload

    def do_GET(self) -> None:
        if not self._peer_allowed():
            self._json(403, {"error": "peer is not allowed"})
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self._json(200, {
                "app": APP_NAME,
                "status": "ok",
                "version": APP_VERSION,
                "width": self.host.config.width,
                "height": self.host.config.height,
                "fps": self.host.config.fps,
                "input": self.host.input_available,
                "paired": self.host.store.device_count,
            })
            return
        if path == "/stream":
            self._stream()
            return
        if path == "/raw":
            self._raw_stream()
            return
        if path in ("", "/", "/pair"):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            code = query.get("code", [""])[0]
            host_url = f"http://{self.host.config.bind}:{self.host.config.port}"
            app_url = f"extendscreen://pair?host={host_url}&code={code}"
            html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ExtendScreen</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; text-align: center; padding: 40px 20px; }}
.card {{ background: #1e293b; border-radius: 16px; padding: 24px; max-width: 360px; margin: 0 auto; box-shadow: 0 4px 20px rgba(0,0,0,0.5); }}
h1 {{ font-size: 20px; margin-bottom: 8px; color: #38bdf8; }}
p {{ font-size: 14px; color: #94a3b8; }}
.btn {{ display: inline-block; background: #0284c7; color: #fff; text-decoration: none; font-weight: bold; padding: 12px 24px; border-radius: 8px; margin-top: 16px; font-size: 16px; }}
.code {{ font-family: monospace; font-size: 24px; letter-spacing: 3px; color: #38bdf8; margin: 12px 0; font-weight: bold; }}
</style>
<script>
window.onload = function() {{
    window.location.href = "{app_url}";
}};
</script>
</head>
<body>
<div class="card">
<h1>ExtendScreen PC Host</h1>
<p>Host: {host_url}</p>
<div class="code">{code if code else "-- -- -- --"}</div>
<p>Tap below to pair with this iPad:</p>
<a class="btn" href="{app_url}">Open ExtendScreen App</a>
</div>
</body>
</html>"""
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._peer_allowed():
            self._json(403, {"error": "peer is not allowed"})
            return
        path = urlsplit(self.path).path
        if path == "/pair":
            self._pair()
            return
        if path == "/input":
            self._input()
            return
        self._json(404, {"error": "not found"})

    def _pair(self) -> None:
        peer = self.client_address[0]
        if not self.host.pair_limiter.allow(peer):
            self._json(429, {"error": "too many pairing attempts; wait a minute"})
            return
        payload = self._read_json()
        if payload is None:
            return
        code = payload.get("code")
        if self.host.pairing.expired:
            self._json(403, {"error": "the pairing window expired; restart the host for a new code"})
            return
        if not self.host.pairing.matches(code):
            self._json(403, {"error": "pairing code is not valid"})
            return
        name = payload.get("name", "iPad")
        if not isinstance(name, str):
            name = "iPad"
        token = self.host.store.add(name)
        print(f"Paired a new device ({name[:32]}).", file=sys.stderr)
        self._json(200, {
            "app": APP_NAME,
            "version": APP_VERSION,
            "token": token,
            "width": self.host.config.width,
            "height": self.host.config.height,
            "fps": self.host.config.fps,
            "input": self.host.input_available,
        })

    def _input(self) -> None:
        token = self._authorized()
        if token is None:
            self._json(401, {"error": "pair this iPad first"})
            return
        if not self.host.input_available:
            self._json(403, {"error": "input forwarding is disabled"})
            return
        if self.host.input_backend is None:
            self._json(503, {"error": "start a screen stream before sending input"})
            return
        if not self.host.input_limiter.allow(token):
            self._json(429, {"error": "too many input events"})
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            accepted = bool(self.host.input_backend.apply(payload))  # type: ignore[attr-defined]
        except Exception:
            accepted = False
        if not accepted:
            self._json(400, {"error": "input event was not valid"})
            return
        self.host.store.touch(token)
        self._empty(204)

    def _stream(self) -> None:
        token = self._authorized()
        if token is None:
            self._json(401, {"error": "pair this iPad first"})
            return
        if not self.host.stream_slots.acquire(blocking=False):
            self._json(503, {"error": "another screen is already streaming"})
            return
        source: Optional[FrameSource] = None
        headers_sent = False
        registered = False
        try:
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            # Prefer the host PC configured FPS (from the tray controller / CLI),
            # preventing legacy client queries (e.g. ?fps=12) from throttling the stream.
            fps = self.host.config.fps if self.host.config.fps >= 5 else 30
            query = parse_qs(urlsplit(self.path).query)
            if "fps" in query and not self.host.config.fps:
                try:
                    fps = clamp(int(query["fps"][0]), 5, 60)
                except (TypeError, ValueError):
                    pass
            try:
                self.host.begin_stream()
                registered = True
            except (RuntimeError, SystemExit, OSError) as error:
                self._json(503, {"error": f"screen capture failed: {error}"})
                return
            assert self.host.region is not None
            source = self.host.frame_source_factory(self.host.config, self.host.region, fps)
            sequence, first, error = source.wait_frame(-1, 8.0)
            if first is None:
                self._json(503, {"error": f"screen capture failed: {error or 'no frame'}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY.decode('ascii')}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            headers_sent = True
            self.close_connection = True
            self._write_frame(first, sequence)
            self.host.store.touch(token)
            last_sequence = sequence
            while self.host.running:
                sequence, frame, error = source.wait_frame(last_sequence, 5.0)
                if frame is None:
                    if error and error != "timeout":
                        break
                    continue
                self._write_frame(frame, sequence)
                last_sequence = sequence
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            if source is not None:
                source.close()
            self.host.stream_slots.release()
            if registered:
                self.host.end_stream()
            if not headers_sent:
                # A response was already sent or the client vanished.
                pass

    def _write_frame(self, frame: bytes, sequence: int) -> None:
        header = (
            b"--" + BOUNDARY + b"\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(frame)).encode("ascii") + b"\r\n"
            b"X-Sequence: " + str(sequence).encode("ascii") + b"\r\n\r\n"
        )
        self.wfile.write(header + frame + b"\r\n")
        self.wfile.flush()

    def _raw_stream(self) -> None:
        token = self._authorized()
        if token is None:
            self._json(401, {"error": "pair this iPad first"})
            return
        if not self.host.stream_slots.acquire(blocking=False):
            self._json(503, {"error": "another screen is already streaming"})
            return
        source: Optional[FrameSource] = None
        registered = False
        reader_thread: Optional[threading.Thread] = None
        stop_event = threading.Event()
        try:
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            fps = self.host.config.fps if self.host.config.fps >= 5 else 30
            try:
                self.host.begin_stream()
                registered = True
            except (RuntimeError, SystemExit, OSError) as error:
                self._json(503, {"error": f"screen capture failed: {error}"})
                return
            assert self.host.region is not None
            source = self.host.frame_source_factory(self.host.config, self.host.region, fps)
            sequence, first, error = source.wait_frame(-1, 8.0)
            if first is None:
                self._json(503, {"error": f"screen capture failed: {error or 'no frame'}"})
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("X-ExtendScreen-Proto", "raw-v1")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            # Binary header: [8 bytes magic 'EXTSCR01'][4 bytes width][4 bytes height]
            magic = b"EXTSCR01"
            header = magic + struct.pack(">II", self.host.config.width, self.host.config.height)
            self.wfile.write(header)
            self.wfile.flush()

            acked_seq = [0]
            sock = self.connection

            def _socket_reader():
                buf = bytearray()
                while not stop_event.is_set():
                    try:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        buf.extend(chunk)
                        while b"\n" in buf:
                            line, _, remainder = buf.partition(b"\n")
                            buf = bytearray(remainder)
                            line_str = line.decode("utf-8", "replace").strip()
                            if not line_str:
                                continue
                            if line_str.startswith("ACK "):
                                try:
                                    s = int(line_str[4:].strip())
                                    if s > acked_seq[0]:
                                        acked_seq[0] = s
                                except ValueError:
                                    pass
                            elif line_str.startswith("{"):
                                try:
                                    ev = json.loads(line_str)
                                    if isinstance(ev, dict) and self.host.input_backend:
                                        self.host.input_backend.apply(ev)
                                except Exception:
                                    pass
                    except (OSError, socket.error):
                        break
                stop_event.set()

            reader_thread = threading.Thread(target=_socket_reader, daemon=True, name="extendscreen-raw-reader")
            reader_thread.start()

            # First frame
            pkt = struct.pack(">II", sequence, len(first)) + first
            self.wfile.write(pkt)
            self.wfile.flush()
            last_sent_sequence = sequence
            last_send_time = time.monotonic()
            self.host.store.touch(token)

            while self.host.running and not stop_event.is_set():
                # ACK backpressure: wait if iPad hasn't acknowledged last sent frame yet
                if last_sent_sequence > acked_seq[0]:
                    time.sleep(0.002)
                    if time.monotonic() - last_send_time < 0.5:
                        continue
                    # Grace timeout: if iPad took >500ms, assume dropped packet and send next frame

                sequence, frame, error = source.wait_frame(last_sent_sequence, 2.0)
                if frame is None:
                    if error and error != "timeout":
                        break
                    continue

                pkt = struct.pack(">II", sequence, len(frame)) + frame
                self.wfile.write(pkt)
                self.wfile.flush()
                last_sent_sequence = sequence
                last_send_time = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            stop_event.set()
            if source is not None:
                source.close()
            self.host.stream_slots.release()
            if registered:
                self.host.end_stream()


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ExtendScreen host: stream this PC's screen to an iPad mini over trusted Wi-Fi.")
    parser.add_argument("--bind", default=None,
                        help="interface address to listen on (default: detected LAN IPv4)")
    parser.add_argument("--port", type=int, default=8644, help="TCP port (default: 8644)")
    parser.add_argument("--monitor", default="primary",
                        help="primary, extend, all, or monitor number from --list-monitors")
    parser.add_argument("--extend", action="store_true",
                        help="stream the first non-primary monitor (extended desktop)")
    parser.add_argument("--region", type=parse_region, default=None,
                        help="explicit capture region x,y,width,height")
    parser.add_argument("--width", type=int, default=1024, help="stream width (default: 1024)")
    parser.add_argument("--height", type=int, default=768, help="stream height (default: 768)")
    parser.add_argument("--fps", type=int, default=12, help="maximum frames per second (5-60)")
    parser.add_argument("--quality", type=int, default=5, help="MJPEG quality 2 (best) to 20 (smallest)")
    parser.add_argument("--no-input", action="store_true", help="disable mouse/keyboard forwarding")
    parser.add_argument("--allow-peer", action="append", default=[],
                        help="allow only this peer IP (repeatable)")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local" / "extendscreen",
                        help="private paired-device state directory")
    parser.add_argument("--test-pattern", action="store_true",
                        help="stream a synthetic test pattern instead of the desktop")
    parser.add_argument("--capture", choices=("auto", "gdigrab", "ddagrab"), default="auto",
                        help="desktop capture backend (default: auto, ddagrab with gdigrab fallback)")
    parser.add_argument("--ddagrab-output", type=int, default=None, metavar="N",
                        help="Desktop Duplication output index (default: match the capture monitor)")
    parser.add_argument("--no-manage-display", action="store_true",
                        help="do not switch Windows to single-screen mode while the iPad is disconnected")
    parser.add_argument("--list-monitors", action="store_true", help="print monitors and exit")
    parser.add_argument("--cable", action="store_true",
                        help="stream over USB: the app listens on 127.0.0.1:8765 and usbmuxd tunnels to it")
    parser.add_argument("--cable-port", type=int, default=CABLE_DEVICE_PORT,
                        help="iPad listener port for cable mode (default: 8765)")
    parser.add_argument("--cable-connect", default=None, metavar="HOST:PORT",
                        help="connect directly to an external tunnel/iproxy instead of usbmuxd")
    parser.add_argument("--usbmux", default=None,
                        help="usbmuxd address (default: 127.0.0.1:27015 or /var/run/usbmuxd)")
    parser.add_argument("--udid", default=None, help="select a connected iPad by UDID or serial")
    parser.add_argument("--parent-pid", type=int, default=None,
                        help="watchdog: terminate and reset display layout if this parent PID exits")
    return parser


def print_monitors() -> None:
    monitors = enumerate_monitors()
    if not monitors:
        print("No Windows monitors were found.")
        return
    for monitor in monitors:
        region = monitor.region
        label = " primary" if monitor.primary else ""
        print(f"{monitor.index}: {region.width}x{region.height} at "
              f"({region.x},{region.y}){label} {monitor.name}".rstrip())


def main(argv: Optional[list[str]] = None) -> int:
    enable_dpi_awareness()
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "parent_pid", None) and os.name == "nt":
        def _on_parent_exit():
            print("Display: parent controller exited; restoring single-screen mode.", flush=True)
            topology = DisplayTopology(True)
            topology.disable("Parent controller exited")
            os._exit(0)

        def _watch_parent():
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            hProc = kernel32.OpenProcess(SYNCHRONIZE, False, int(args.parent_pid))
            if not hProc:
                _on_parent_exit()
                return
            try:
                kernel32.WaitForSingleObject(hProc, 0xFFFFFFFF)
            finally:
                kernel32.CloseHandle(hProc)
                _on_parent_exit()

        threading.Thread(target=_watch_parent, daemon=True, name="ParentWatchdog").start()

    if args.list_monitors:
        print_monitors()
        return 0
    if not (5 <= args.fps <= 60):
        parser.error("--fps must be between 5 and 60")
    if not (320 <= args.width <= 2048 and 240 <= args.height <= 1536):
        parser.error("--width/--height are outside the supported range")
    if not (2 <= args.quality <= 20):
        parser.error("--quality must be between 2 and 20")
    if not (0 <= args.port <= 65535):
        parser.error("--port is invalid")
    if not (1 <= args.cable_port <= 65535):
        parser.error("--cable-port is invalid")
    if args.ddagrab_output is not None and args.ddagrab_output < 0:
        parser.error("--ddagrab-output must be zero or greater")
    if args.cable_connect:
        args.cable = True
    if args.extend:
        args.monitor = "extend"
    if args.cable:
        args.usbmux = args.usbmux or default_usbmux_address()
    if not args.test_pattern and os.name != "nt":
        parser.error("desktop capture and input forwarding require Windows; use --test-pattern for a stream test")
    if not args.test_pattern:
        try:
            subprocess.run(["ffmpeg", "-version"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.SubprocessError):
            print("ffmpeg was not found on PATH. Install it (for example: winget install Gyan.FFmpeg)",
                  file=sys.stderr)
            return 2

    bind = args.bind or ("127.0.0.1" if args.cable else detect_lan_ip())
    if not args.cable and bind in {"127.0.0.1", "::1"}:
        print("warning: binding to loopback only; pass --bind with this PC's LAN address "
              "so the iPad can connect", file=sys.stderr)
    config = Config(
        bind=bind, port=args.port, monitor=args.monitor, region=args.region,
        width=args.width, height=args.height, fps=args.fps, quality=args.quality,
        no_input=args.no_input, allow_peers=tuple(args.allow_peer),
        state_dir=args.state_dir, test_pattern=args.test_pattern,
        capture=args.capture, ddagrab_output=args.ddagrab_output,
        manage_display=not args.no_manage_display,
    )
    if args.cable:
        return run_cable(config, args)
    host = ScreenHost(config)
    server = ScreenHTTPServer((bind, args.port), host)
    display_host = bind
    if bind in {"0.0.0.0", "::"}:
        display_host = detect_lan_ip()
    print(f"ExtendScreen host {APP_VERSION}", flush=True)
    print(f"  URL       : http://{display_host}:{server.server_port}", flush=True)
    print(f"  Pair code : {host.pairing.code}   (valid {PAIRING_MINUTES} minutes)", flush=True)

    session_file = ROOT / "host-tools" / "session.json"
    try:
        session_data = {
            "mode": "wifi",
            "url": f"http://{display_host}:{server.server_port}",
            "pair_code": host.pairing.code,
            "expires": host.pairing.expires,
            "port": server.server_port,
            "host": display_host,
            "started_at": time.time(),
        }
        session_file.write_text(json.dumps(session_data, indent=2), encoding="utf-8")
    except Exception:
        pass
    if host.region is not None:
        print(f"  Capture   : {host.region.width}x{host.region.height} at "
              f"({host.region.x},{host.region.y}) -> {config.width}x{config.height} @ {config.fps} fps")
    else:
        print("  Capture   : extended desktop (enabled when the iPad streams)")
        print("  Display   : single-screen until the iPad is connected")
    print(f"  Input     : {'enabled' if host.input_available else 'disabled'}")
    print(f"  Paired    : {host.store.device_count} device(s)")
    print("  Security  : trusted LAN only; HTTP is unencrypted. Press Ctrl+C to stop.")
    if config.test_pattern:
        print("  Mode      : synthetic test pattern (desktop is not captured)")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping ExtendScreen host.")
    finally:
        host.running = False
        server.shutdown()
        server.server_close()
        host.shutdown_display()
        try:
            if session_file.exists():
                session_file.unlink()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
