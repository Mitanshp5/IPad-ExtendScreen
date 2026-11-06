#!/usr/bin/env python3
"""ExtendScreen System Tray Host Controller.

Provides a persistent system tray icon with an on/off toggle window:
- Left-click on the tray icon reveals the control window positioned above the system tray.
- Large ON/OFF toggle switch to start or kill all services.
- In OFF mode: kills all screen-host.py and ffmpeg processes, and reverts display to internal.
- In ON mode: launches the ExtendScreen host service (USB Cable or Wi-Fi).
- Right-click shows a context menu with quick toggle (Turn ON / Turn OFF) and exit.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from typing import Optional

from PIL import Image, ImageTk, ImageDraw, ImageFilter
import pystray

def render_qr_image(data: str, size: int = 260) -> Optional[Image.Image]:
    """Generates a clean black & white QR code PIL image."""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        return img.resize((size, size), Image.Resampling.NEAREST)
    except Exception:
        pass

    try:
        import cv2
        import numpy as np
        enc = cv2.QRCodeEncoder.create()
        mat = enc.encode(data)
        border = 2
        padded = np.pad(mat, pad_width=border, mode='constant', constant_values=255)
        img = Image.fromarray(padded).convert('RGB')
        return img.resize((size, size), Image.Resampling.NEAREST)
    except Exception:
        pass

    return None


class LargeQRWindow(tk.Toplevel):
    """Large, high-contrast modal QR code window for easy scanning from a distance."""

    def __init__(self, master, url: str, code: str):
        super().__init__(master)
        self.title("Scan to Pair iPad")
        self.configure(bg="#0B132B")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        qr_payload = f"{url}/?code={code}"
        qr_img = render_qr_image(qr_payload, size=350)
        self.qr_photo = ImageTk.PhotoImage(qr_img) if qr_img else None

        card = tk.Frame(self, bg="#0B132B", padx=24, pady=20)
        card.pack(fill="both", expand=True)

        header = tk.Frame(card, bg="#0B132B")
        header.pack(fill="x", pady=(0, 10))

        tk.Label(
            header,
            text="📶 SCAN TO PAIR IPAD",
            font=("Segoe UI", 11, "bold"),
            fg="#38BDF8",
            bg="#0B132B",
        ).pack(side="left")

        btn_close = tk.Label(
            header,
            text="✕",
            font=("Segoe UI", 12),
            fg="#94A3B8",
            bg="#0B132B",
            cursor="hand2",
        )
        btn_close.pack(side="right")
        btn_close.bind("<Button-1>", lambda e: self.destroy())

        qr_border = tk.Frame(card, bg="#FFFFFF", padx=8, pady=8)
        qr_border.pack(pady=6)

        if self.qr_photo:
            lbl = tk.Label(qr_border, image=self.qr_photo, bg="#FFFFFF", cursor="hand2")
            lbl.pack()
            lbl.bind("<Button-1>", lambda e: self.destroy())

        c_str = str(code)
        formatted = f"{c_str[:4]}  {c_str[4:]}" if len(c_str) == 8 else c_str
        tk.Label(
            card,
            text=f"Pairing Code: {formatted}",
            font=("Consolas", 15, "bold"),
            fg="#38BDF8",
            bg="#0B132B",
        ).pack(pady=(10, 2))

        tk.Label(
            card,
            text=f"Host: {url}",
            font=("Segoe UI", 9),
            fg="#94A3B8",
            bg="#0B132B",
        ).pack(pady=(0, 6))

        tk.Label(
            card,
            text="Point iPad camera at this screen • Click or press Esc to close",
            font=("Segoe UI", 8),
            fg="#64748B",
            bg="#0B132B",
        ).pack()

        self.bind("<Escape>", lambda e: self.destroy())

        self.update_idletasks()
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.focus_force()


# Ensure all threads bind to the interactive 'Default' desktop (Windows)
if os.name == "nt":
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    def ensure_default_desktop():
        try:
            hdesk = user32.OpenDesktopW("default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

    ensure_default_desktop()
    _orig_bootstrap = threading.Thread._bootstrap
    def _patched_bootstrap(self):
        ensure_default_desktop()
        return _orig_bootstrap(self)
    threading.Thread._bootstrap = _patched_bootstrap

    # DPI Awareness
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
else:
    user32 = None
    kernel32 = None
    def ensure_default_desktop():
        pass

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
HOST_SCRIPT = ROOT_DIR / "host-tools" / "screen-host.py"
ICON_PNG = ROOT_DIR / "screen" / "Resources" / "Icon.png"
TRAY_OFF_PNG = ROOT_DIR / "host-tools" / "tray_off.png"
TRAY_ON_PNG = ROOT_DIR / "host-tools" / "tray_on.png"
LOG_FILE = ROOT_DIR / "host-tools" / "tray.log"

SPI_GETWORKAREA = 0x0030
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# =====================================================================
# Generate High-Contrast Transparent Tray Icons
# =====================================================================

def ensure_tray_icons():
    """Generates bold, high-contrast, transparent tray icons."""
    if TRAY_OFF_PNG.exists() and TRAY_ON_PNG.exists():
        return

    def _draw_icon(is_active=False, size=64):
        scale = 4
        w = size * scale
        h = size * scale
        glyph = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(glyph)

        gw = 224
        gh = 100
        gx0 = (w - gw) // 2
        gy0 = (h - gh) // 2

        def pt(rx, ry):
            return (gx0 + (rx / 68.0) * gw, gy0 + (ry / 28.0) * gh)

        stroke = 16
        half_s = stroke / 2.0
        main_color = (14, 165, 233, 255) if is_active else (255, 255, 255, 255)

        # Base
        bx0, by0 = pt(0.0, 25.5)
        bx1, by1 = pt(31.5, 27.5)
        draw.rounded_rectangle([bx0, by0, bx1, by1], radius=(by1 - by0) / 2.0, fill=main_color)

        # Left screen
        lx0, ly0 = pt(2.0, 5.5)
        lx1, ly1 = pt(30.0, 22.5)
        lr = 18
        draw.rounded_rectangle([lx0, ly0, lx1, ly1], radius=lr, outline=main_color, width=stroke)

        cx_l, cy_l = pt(30.0, 22.5)
        rad_l = (12.5 / 68.0) * gw
        arc_box_l = [cx_l - rad_l, cy_l - rad_l, cx_l + rad_l, cy_l + rad_l]
        mask_l = Image.new("L", (w, h), 0)
        draw_ml = ImageDraw.Draw(mask_l)
        draw_ml.rounded_rectangle([lx0 + half_s, ly0 + half_s, lx1 - half_s, ly1 - half_s], radius=max(1, lr - half_s), fill=255)

        arc_layer_l = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw_al = ImageDraw.Draw(arc_layer_l)
        draw_al.arc(arc_box_l, start=180, end=270, fill=main_color, width=stroke)
        glyph.paste(arc_layer_l, (0, 0), mask_l)

        # Right screen
        rx0, ry0 = pt(36.0, 0.5)
        rx1, ry1 = pt(68.0, 22.5)
        rr = 20
        draw.rounded_rectangle([rx0, ry0, rx1, ry1], radius=rr, outline=main_color, width=stroke)

        cx_r, cy_r = pt(36.0, 22.5)
        rad_r = (16.0 / 68.0) * gw
        arc_box_r = [cx_r - rad_r, cy_r - rad_r, cx_r + rad_r, cy_r + rad_r]
        mask_r = Image.new("L", (w, h), 0)
        draw_mr = ImageDraw.Draw(mask_r)
        draw_mr.rounded_rectangle([rx0 + half_s, ry0 + half_s, rx1 - half_s, ry1 - half_s], radius=max(1, rr - half_s), fill=255)

        arc_layer_r = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw_ar = ImageDraw.Draw(arc_layer_r)
        draw_ar.arc(arc_box_r, start=270, end=360, fill=main_color, width=stroke)
        glyph.paste(arc_layer_r, (0, 0), mask_r)

        if is_active:
            dot_r = 18
            dot_x = rx1 + 10
            dot_y = ry0 - 10
            draw.ellipse([dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r], fill=(34, 197, 94, 255), outline=(255, 255, 255, 255), width=4)

        # Subtle dark halo for maximum visibility against light backgrounds
        shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        alpha = glyph.split()[3]
        shadow_mask = alpha.filter(ImageFilter.GaussianBlur(radius=3))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rectangle([0, 0, w, h], fill=(0, 0, 0, 160))

        final_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        final_img.paste(shadow, (0, 0), shadow_mask)
        final_img.paste(glyph, (0, 0), glyph)
        return final_img.resize((size, size), Image.Resampling.LANCZOS)

    ico_off = _draw_icon(is_active=False)
    ico_on = _draw_icon(is_active=True)
    ico_off.save(TRAY_OFF_PNG)
    ico_on.save(TRAY_ON_PNG)


# =====================================================================
# Display Switching & Job Management Helpers
# =====================================================================

SESSION_FILE = ROOT_DIR / "host-tools" / "session.json"


def detect_lan_ip() -> str:
    """Best-effort local IPv4 address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def set_display_internal() -> bool:
    """Instantly switches Windows display configuration to single-screen (internal)."""
    if os.name != "nt":
        return True
    try:
        if user32:
            ensure_default_desktop()
            SDC_APPLY = 0x00000080
            SDC_TOPOLOGY_INTERNAL = 0x00000001
            res = user32.SetDisplayConfig(0, None, 0, None, SDC_APPLY | SDC_TOPOLOGY_INTERNAL)
            if res == 0:
                return True
    except Exception:
        pass

    try:
        display_switch = os.path.join(
            os.environ.get("WINDIR", r"C:\Windows"), "System32", "DisplaySwitch.exe"
        )
        subprocess.run([display_switch, "/internal"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


def setup_process_job():
    """Create a Windows Job Object configured to kill child processes if parent dies."""
    if os.name != "nt" or not kernel32:
        return None
    try:
        hJob = kernel32.CreateJobObjectW(None, None)
        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', wintypes.LARGE_INTEGER),
                ('PerJobUserTimeLimit', wintypes.LARGE_INTEGER),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ('ReadOperationCount', ctypes.c_uint64),
                ('WriteOperationCount', ctypes.c_uint64),
                ('OtherOperationCount', ctypes.c_uint64),
                ('ReadTransferCount', ctypes.c_uint64),
                ('WriteTransferCount', ctypes.c_uint64),
                ('OtherTransferCount', ctypes.c_uint64),
            ]
        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('BasicInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ('IoCounters', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryLimit', ctypes.c_size_t),
                ('PeakJobMemoryLimit', ctypes.c_size_t),
            ]
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicInformation.LimitFlags = 0x00002000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(hJob, 9, ctypes.byref(info), ctypes.sizeof(info))
        return hJob
    except Exception:
        return None


# Global exit handlers to guarantee display restoration on any exit
import atexit
import signal

def _emergency_cleanup():
    set_display_internal()

atexit.register(_emergency_cleanup)

if os.name == "nt":
    try:
        PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        def _console_ctrl_handler(dwCtrlType):
            _emergency_cleanup()
            return False
        _global_ctrl_handler = PHANDLER_ROUTINE(_console_ctrl_handler)
        if kernel32:
            kernel32.SetConsoleCtrlHandler(_global_ctrl_handler, True)
    except Exception:
        pass

for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)):
    if sig is not None:
        try:
            signal.signal(sig, lambda s, f: (_emergency_cleanup(), sys.exit(0)))
        except Exception:
            pass


# =====================================================================
# Service Controller (Starts, Kills, and Monitors screen-host.py)
# =====================================================================

class ExtendServiceManager:
    """Manages the background ExtendScreen host processes and display switching."""

    def __init__(self, on_status_change=None, on_pairing_update=None) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.mode = "cable"  # "cable" or "wifi"
        self.fps = 45
        self.pair_code: Optional[str] = None
        self.url: Optional[str] = None
        self.on_status_change = on_status_change
        self.on_pairing_update = on_pairing_update
        self.monitor_thread: Optional[threading.Thread] = None
        self.stop_requested = False
        self.job = setup_process_job()
        self._load_cached_session()

    def _load_cached_session(self):
        if SESSION_FILE.exists():
            try:
                import json
                data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                if data.get("pair_code"):
                    self.pair_code = str(data["pair_code"])
                if data.get("url"):
                    self.url = str(data["url"])
            except Exception:
                pass

    def is_running(self) -> bool:
        if self.proc is not None:
            if self.proc.poll() is None:
                return True
            self.proc = None
        return False

    def start(self, mode: str = "cable", fps: int = 45) -> bool:
        self.stop_requested = False
        self.mode = mode
        self.fps = fps
        self.pair_code = None
        self.url = None

        if mode == "wifi" and self.on_pairing_update:
            self.on_pairing_update(None, None)

        # Kill any prior orphan processes before starting
        self.kill_all(reset_display=False)

        cmd = [sys.executable, "-u", str(HOST_SCRIPT), "--parent-pid", str(os.getpid())]
        if mode == "cable":
            q = "6" if fps >= 60 else "5"
            cmd.extend(["--cable", "--extend", "--fps", str(fps), "--quality", q])
        else:
            cmd.extend(["--extend", "--fps", str(fps), "--quality", "7"])

        try:
            with open(LOG_FILE, "a", encoding="utf-8") as log_out:
                log_out.write(f"\n--- ExtendScreen Started ({mode} @ {fps} fps) at {time.ctime()} ---\n")
                log_out.flush()

            self.proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
                text=True,
                bufsize=1,
            )

            if self.job and hasattr(self.proc, "_handle") and self.proc._handle:
                try:
                    kernel32.AssignProcessToJobObject(self.job, int(self.proc._handle))
                except Exception:
                    pass

            self.monitor_thread = threading.Thread(target=self._monitor_process, daemon=True)
            self.monitor_thread.start()

            # Fast session watcher for instant pairing code display
            if mode == "wifi":
                def _check_session():
                    for _ in range(25):
                        if self.pair_code or not self.is_running():
                            break
                        if SESSION_FILE.exists():
                            try:
                                import json
                                data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                                if data.get("pair_code"):
                                    self.pair_code = str(data["pair_code"])
                                    self.url = str(data.get("url") or f"http://{detect_lan_ip()}:8644")
                                    if self.on_pairing_update:
                                        self.on_pairing_update(self.pair_code, self.url)
                                    break
                            except Exception:
                                pass
                        time.sleep(0.12)
                threading.Thread(target=_check_session, daemon=True, name="FastSessionCheck").start()

            if self.on_status_change:
                self.on_status_change(True)
            return True
        except Exception as e:
            with open(LOG_FILE, "a", encoding="utf-8") as log_out:
                log_out.write(f"Failed to start host: {e}\n")
            if self.on_status_change:
                self.on_status_change(False)
            return False

    def _monitor_process(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as log_out:
                for line in self.proc.stdout:
                    log_out.write(line)
                    log_out.flush()

                    # Real-time extraction of pairing code and URL
                    updated = False
                    if "URL       :" in line:
                        self.url = line.split("URL       :", 1)[1].strip()
                        updated = True
                    elif "Pair code :" in line:
                        code_part = line.split("Pair code :", 1)[1].strip()
                        self.pair_code = code_part.split()[0].strip()
                        updated = True

                    # Also check session file fallback
                    if not self.pair_code and SESSION_FILE.exists():
                        try:
                            import json
                            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                            if data.get("pair_code"):
                                self.pair_code = str(data["pair_code"])
                                updated = True
                            if data.get("url"):
                                self.url = str(data["url"])
                                updated = True
                        except Exception:
                            pass

                    if updated and self.on_pairing_update:
                        self.on_pairing_update(self.pair_code, self.url)
        except Exception:
            pass
        finally:
            if not self.stop_requested:
                self.proc = None
                self.pair_code = None
                self.url = None
                if self.on_status_change:
                    self.on_status_change(False)
                if self.on_pairing_update:
                    self.on_pairing_update(None, None)

    def kill_all(self, reset_display: bool = True) -> None:
        """Kill all host processes (screen-host.py, ffmpeg) and reset display."""
        self.stop_requested = True
        self.pair_code = None
        self.url = None

        try:
            if SESSION_FILE.exists():
                SESSION_FILE.unlink()
        except Exception:
            pass

        if self.on_pairing_update:
            try:
                self.on_pairing_update(None, None)
            except Exception:
                pass

        # 1. Kill tracked child process tree
        if self.proc is not None:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                        capture_output=True,
                        creationflags=CREATE_NO_WINDOW,
                    )
                else:
                    self.proc.terminate()
                    self.proc.wait(timeout=1.5)
            except Exception:
                try:
                    if self.proc:
                        self.proc.kill()
                except Exception:
                    pass
            self.proc = None

        # 2. Fast kill any orphaned ffmpeg / screen-host processes
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/IM", "ffmpeg.exe"],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
            else:
                subprocess.run(["pkill", "-f", "screen-host.py"], capture_output=True)
        except Exception:
            pass

        # 3. Terminate any other screen-host.py python processes (Windows)
        if os.name == "nt":
            try:
                ps_cmd = (
                    'Get-CimInstance Win32_Process | '
                    'Where-Object { $_.CommandLine -like "*screen-host.py*" } | '
                    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }'
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
            except Exception:
                pass

        # 4. Revert Windows display layout to single-screen (internal)
        if reset_display:
            set_display_internal()

        with open(LOG_FILE, "a", encoding="utf-8") as log_out:
            log_out.write(f"--- All processes killed at {time.ctime()} ---\n")

        if self.on_status_change:
            self.on_status_change(False)
        if self.on_pairing_update:
            self.on_pairing_update(None, None)


# =====================================================================
# Modern Smooth Toggle Switch Widget
# =====================================================================

class SmoothToggle(tk.Canvas):
    """An animated iOS/Windows 11 style Toggle Switch."""

    def __init__(self, parent, width=54, height=28, command=None, bg="#202026"):
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            cursor="hand2",
        )
        self.width = width
        self.height = height
        self.command = command
        self.is_on = False
        self.current_x = 3.0
        self.target_x = 3.0
        self.animating = False

        self.bind("<Button-1>", self.on_click)
        self.draw()

    def set_state(self, is_on: bool, trigger_command: bool = False):
        if self.is_on == is_on:
            return
        self.is_on = is_on
        pad = 3.0
        kd = self.height - pad * 2
        self.target_x = (self.width - kd - pad) if self.is_on else pad
        self._animate()
        if trigger_command and self.command:
            # Defer callback so toggle animation starts immediately without delay
            self.after(0, lambda: self.command(self.is_on))

    def on_click(self, event=None):
        new_state = not self.is_on
        self.set_state(new_state, trigger_command=True)

    def _animate(self):
        if not self.animating:
            self.animating = True
            self._step()

    def _step(self):
        diff = self.target_x - self.current_x
        if abs(diff) < 0.8:
            self.current_x = self.target_x
            self.animating = False
            self.draw()
        else:
            self.current_x += diff * 0.35
            self.draw()
            self.after(16, self._step)

    def draw(self):
        self.delete("all")
        pad = 3.0
        kd = self.height - pad * 2
        r = self.height / 2.0

        t = (self.current_x - pad) / max(1.0, (self.width - kd - pad * 2))
        t = max(0.0, min(1.0, t))

        r_c = int(59 * (1 - t) + 14 * t)
        g_c = int(59 * (1 - t) + 165 * t)
        b_c = int(68 * (1 - t) + 233 * t)
        track_color = f"#{r_c:02x}{g_c:02x}{b_c:02x}"

        self.create_oval(0, 0, self.height, self.height, fill=track_color, outline="")
        self.create_oval(self.width - self.height, 0, self.width, self.height, fill=track_color, outline="")
        self.create_rectangle(r, 0, self.width - r, self.height, fill=track_color, outline="")

        kx = self.current_x
        self.create_oval(kx, pad, kx + kd, pad + kd, fill="#FFFFFF", outline="")


# =====================================================================
# Control Window (Positioned nicely above the system tray)
# =====================================================================

class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class ControlWindow(tk.Toplevel):
    """Modern dark control card window positioned above the system tray."""

    def __init__(self, master, manager: ExtendServiceManager, on_exit=None):
        super().__init__(master)
        self.manager = manager
        self.on_exit = on_exit

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#121218")
        self.resizable(False, False)
        self.width = 410
        self.height = 465
        self.opened_at = 0.0
        self._drag_x = 0
        self._drag_y = 0

        self.fps_var = tk.StringVar(value="45")
        self.fps_buttons = {}
        self.mode_tiles = {}
        self.qr_photo: Optional[ImageTk.PhotoImage] = None

        self.withdraw()

        self.icon_photo: Optional[ImageTk.PhotoImage] = None
        self._init_ui()

        self.bind("<Escape>", lambda e: self.hide())

    def _init_ui(self):
        # 1px border frame around the card
        border_frame = tk.Frame(self, bg="#333342", padx=1, pady=1)
        border_frame.pack(fill="both", expand=True)

        inner = tk.Frame(border_frame, bg="#181822", padx=18, pady=16)
        inner.pack(fill="both", expand=True)

        # Header (draggable)
        header = tk.Frame(inner, bg="#181822", cursor="fleur")
        header.pack(fill="x", pady=(0, 12))
        header.bind("<Button-1>", self._start_drag)
        header.bind("<B1-Motion>", self._on_drag)

        title_box = tk.Frame(header, bg="#181822")
        title_box.pack(side="left")
        title_box.bind("<Button-1>", self._start_drag)
        title_box.bind("<B1-Motion>", self._on_drag)

        if ICON_PNG.exists():
            try:
                im = Image.open(ICON_PNG).resize((22, 22), Image.Resampling.LANCZOS)
                self.icon_photo = ImageTk.PhotoImage(im)
                lbl_icon = tk.Label(title_box, image=self.icon_photo, bg="#181822")
                lbl_icon.pack(side="left", padx=(0, 8))
                lbl_icon.bind("<Button-1>", self._start_drag)
                lbl_icon.bind("<B1-Motion>", self._on_drag)
            except Exception:
                pass

        text_box = tk.Frame(title_box, bg="#181822")
        text_box.pack(side="left")
        text_box.bind("<Button-1>", self._start_drag)
        text_box.bind("<B1-Motion>", self._on_drag)

        lbl_title = tk.Label(
            text_box,
            text="ExtendScreen",
            font=("Segoe UI", 11, "bold"),
            fg="#FFFFFF",
            bg="#181822",
        )
        lbl_title.pack(anchor="w")
        lbl_title.bind("<Button-1>", self._start_drag)
        lbl_title.bind("<B1-Motion>", self._on_drag)

        lbl_tag = tk.Label(
            text_box,
            text="iPad Display Host Controller",
            font=("Segoe UI", 7),
            fg="#8E8EA0",
            bg="#181822",
        )
        lbl_tag.pack(anchor="w")
        lbl_tag.bind("<Button-1>", self._start_drag)
        lbl_tag.bind("<B1-Motion>", self._on_drag)

        btn_min = tk.Label(
            header,
            text="✕",
            font=("Segoe UI", 11),
            fg="#9CA3AF",
            bg="#181822",
            cursor="hand2",
            padx=4,
            pady=2,
        )
        btn_min.pack(side="right")
        btn_min.bind("<Button-1>", lambda e: self.hide())
        btn_min.bind("<Enter>", lambda e: btn_min.configure(fg="#FFFFFF"))
        btn_min.bind("<Leave>", lambda e: btn_min.configure(fg="#9CA3AF"))

        # Main Toggle Card
        self.card = tk.Frame(inner, bg="#22222E", padx=14, pady=12, highlightthickness=1, highlightbackground="#363648")
        self.card.pack(fill="x", pady=(0, 12))

        self.card_left = tk.Frame(self.card, bg="#22222E")
        self.card_left.pack(side="left", fill="both", expand=True)

        self.lbl_serv = tk.Label(self.card_left, text="SCREEN SHARING", font=("Segoe UI", 8, "bold"), fg="#9CA3AF", bg="#22222E")
        self.lbl_serv.pack(anchor="w")

        self.lbl_status = tk.Label(self.card_left, text="OFF", font=("Segoe UI", 16, "bold"), fg="#9CA3AF", bg="#22222E")
        self.lbl_status.pack(anchor="w", pady=(1, 1))

        self.lbl_substatus = tk.Label(
            self.card_left,
            text="All services stopped",
            font=("Segoe UI", 8),
            fg="#6B7280",
            bg="#22222E",
        )
        self.lbl_substatus.pack(anchor="w")

        self.toggle = SmoothToggle(self.card, width=54, height=28, command=self._on_toggle, bg="#22222E")
        self.toggle.pack(side="right", padx=(8, 0))

        # Connection Mode Box with High-Contrast Interactive Tiles
        mode_box = tk.LabelFrame(
            inner,
            text=" Connection Mode ",
            font=("Segoe UI", 8, "bold"),
            fg="#9CA3AF",
            bg="#181822",
            padx=10,
            pady=8,
            highlightthickness=1,
            highlightbackground="#2C2C3C",
        )
        mode_box.pack(fill="x", pady=(0, 10))

        self.mode_var = tk.StringVar(value="cable")

        mode_data = [
            ("cable", "⚡ USB Cable (Recommended)", "Direct USB tethering with ultra-low latency"),
            ("wifi", "📶 Wi-Fi Network Streaming", "Wireless streaming over local Wi-Fi network"),
        ]

        for m_val, m_title, m_desc in mode_data:
            tile = tk.Frame(
                mode_box,
                bg="#1E1E2A",
                padx=10,
                pady=7,
                highlightthickness=1,
                highlightbackground="#2E2E3E",
                cursor="hand2",
            )
            tile.pack(fill="x", pady=2)

            canvas_radio = tk.Canvas(
                tile,
                width=18,
                height=18,
                bg="#1E1E2A",
                highlightthickness=0,
                cursor="hand2",
            )
            canvas_radio.pack(side="left", padx=(0, 10))

            text_frame = tk.Frame(tile, bg="#1E1E2A", cursor="hand2")
            text_frame.pack(side="left", fill="both", expand=True)

            lbl_m_title = tk.Label(
                text_frame,
                text=m_title,
                font=("Segoe UI", 8, "bold"),
                fg="#CBD5E1",
                bg="#1E1E2A",
                anchor="w",
                cursor="hand2",
            )
            lbl_m_title.pack(fill="x")

            lbl_m_desc = tk.Label(
                text_frame,
                text=m_desc,
                font=("Segoe UI", 7),
                fg="#6B7280",
                bg="#1E1E2A",
                anchor="w",
                cursor="hand2",
            )
            lbl_m_desc.pack(fill="x")

            self.mode_tiles[m_val] = (tile, canvas_radio, text_frame, lbl_m_title, lbl_m_desc)

            for w in (tile, canvas_radio, text_frame, lbl_m_title, lbl_m_desc):
                w.bind("<Button-1>", lambda e, val=m_val: self._select_mode(val))

            def _bind_hover(t=tile, v=m_val):
                def _enter(e):
                    if self.mode_var.get() != v:
                        t.configure(highlightbackground="#475569", bg="#252535")
                def _leave(e):
                    if self.mode_var.get() != v:
                        t.configure(highlightbackground="#2E2E3E", bg="#1E1E2A")
                return _enter, _leave

            _ent, _lev = _bind_hover()
            for w in (tile, canvas_radio, text_frame, lbl_m_title, lbl_m_desc):
                w.bind("<Enter>", _ent)
                w.bind("<Leave>", _lev)

        # Wi-Fi Pairing Info Card (Shown when Wi-Fi mode is active)
        self.pair_box = tk.Frame(
            inner,
            bg="#162536",
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground="#0284C7",
        )

        pair_header = tk.Frame(self.pair_box, bg="#162536")
        pair_header.pack(fill="x", pady=(0, 6))

        tk.Label(
            pair_header,
            text="📶 WI-FI PAIRING QR CODE",
            font=("Segoe UI", 8, "bold"),
            fg="#38BDF8",
            bg="#162536",
        ).pack(side="left")

        self.lbl_pair_status = tk.Label(
            pair_header,
            text="Turn ON to pair",
            font=("Segoe UI", 7, "bold"),
            fg="#94A3B8",
            bg="#162536",
        )
        self.lbl_pair_status.pack(side="right")

        # QR Code Display Container
        self.qr_card = tk.Frame(
            self.pair_box,
            bg="#0B132B",
            padx=8,
            pady=8,
            highlightthickness=1,
            highlightbackground="#1E293B",
        )
        self.qr_card.pack(fill="x", pady=(0, 6))

        self.lbl_qr_image = tk.Label(self.qr_card, bg="#FFFFFF", cursor="hand2")
        self.lbl_qr_image.bind("<Button-1>", lambda e: self._open_large_qr())

        self.lbl_qr_placeholder = tk.Label(
            self.qr_card,
            text="Switch ON to display\npairing QR code",
            font=("Segoe UI", 9, "bold"),
            fg="#64748B",
            bg="#0B132B",
            pady=36,
        )
        self.lbl_qr_placeholder.pack(fill="x")

        # Sub-hint row with "Click to enlarge"
        hint_row = tk.Frame(self.pair_box, bg="#162536")
        hint_row.pack(fill="x", pady=(0, 4))

        self.lbl_qr_hint = tk.Label(
            hint_row,
            text="📷 Scan with iPad camera",
            font=("Segoe UI", 7, "bold"),
            fg="#38BDF8",
            bg="#162536",
        )
        self.lbl_qr_hint.pack(side="left")

        self.btn_enlarge_qr = tk.Label(
            hint_row,
            text="🔍 Click to enlarge QR",
            font=("Segoe UI", 7, "underline"),
            fg="#38BDF8",
            bg="#162536",
            cursor="hand2",
        )
        self.btn_enlarge_qr.pack(side="right")
        self.btn_enlarge_qr.bind("<Button-1>", lambda e: self._open_large_qr())

        # Fallback text details row
        fallback_row = tk.Frame(self.pair_box, bg="#162536")
        fallback_row.pack(fill="x", pady=(2, 0))

        self.lbl_pair_code = tk.Label(
            fallback_row,
            text="Code: -- --  -- --",
            font=("Consolas", 8, "bold"),
            fg="#94A3B8",
            bg="#162536",
        )
        self.lbl_pair_code.pack(side="left")

        self.btn_copy_code = tk.Label(
            fallback_row,
            text="Copy",
            font=("Segoe UI", 7, "underline"),
            fg="#38BDF8",
            bg="#162536",
            cursor="hand2",
            padx=4,
        )
        self.btn_copy_code.pack(side="left", padx=(2, 8))
        self.btn_copy_code.bind("<Button-1>", lambda e: self._copy_code())

        self.lbl_pair_url = tk.Label(
            fallback_row,
            text=f"Host: http://{detect_lan_ip()}:8644",
            font=("Segoe UI", 7),
            fg="#94A3B8",
            bg="#162536",
        )
        self.lbl_pair_url.pack(side="left")

        self.btn_copy_url = tk.Label(
            fallback_row,
            text="Copy",
            font=("Segoe UI", 7, "underline"),
            fg="#38BDF8",
            bg="#162536",
            cursor="hand2",
            padx=4,
        )
        self.btn_copy_url.pack(side="left", padx=(2, 0))
        self.btn_copy_url.bind("<Button-1>", lambda e: self._copy_url())

        # Framerate (FPS) Control Row
        self.fps_box = tk.Frame(inner, bg="#1F1F2C", padx=12, pady=8, highlightthickness=1, highlightbackground="#2C2C3C")
        self.fps_box.pack(fill="x", pady=(0, 10))

        lbl_fps = tk.Label(self.fps_box, text="Framerate:", font=("Segoe UI", 8, "bold"), fg="#D1D5DB", bg="#1F1F2C")
        lbl_fps.pack(side="left", padx=(0, 8))

        self.fps_btn_container = tk.Frame(self.fps_box, bg="#282838", padx=2, pady=2, highlightthickness=1, highlightbackground="#3A3A4A")
        self.fps_btn_container.pack(side="left")

        self.fps_buttons = {}
        for val in ["30", "45", "60"]:
            b = tk.Label(self.fps_btn_container, text=f"{val}", font=("Segoe UI", 8, "bold"), width=3, padx=4, pady=2, cursor="hand2")
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda e, v=val: self._select_fps(v))
            self.fps_buttons[val] = b

        self.entry_fps = tk.Entry(
            self.fps_box,
            textvariable=self.fps_var,
            font=("Segoe UI", 8, "bold"),
            width=3,
            bg="#282838",
            fg="#38BDF8",
            justify="center",
            bd=0,
            highlightthickness=1,
            highlightbackground="#3A3A4A",
            insertbackground="#38BDF8",
        )
        self.entry_fps.pack(side="left", padx=(8, 0))
        self.entry_fps.bind("<Return>", lambda e: self._on_custom_fps())
        self.entry_fps.bind("<FocusOut>", lambda e: self._on_custom_fps())

        self.lbl_fps_unit = tk.Label(self.fps_box, text="FPS (5-60)", font=("Segoe UI", 7), fg="#6B7280", bg="#1F1F2C")
        self.lbl_fps_unit.pack(side="left", padx=(4, 0))

        self._update_fps_visibility()

        # Status / Guidance Box
        self.info_box = tk.Frame(inner, bg="#1F1F2C", padx=10, pady=8, highlightthickness=1, highlightbackground="#2A2A38")
        self.info_box.pack(fill="x", pady=(0, 12))

        self.lbl_info1 = tk.Label(
            self.info_box,
            text="💡 Switch ON to extend desktop to your iPad.",
            font=("Segoe UI", 8),
            fg="#8E8EA0",
            bg="#1F1F2C",
        )
        self.lbl_info1.pack(anchor="w")

        self.lbl_info2 = tk.Label(
            self.info_box,
            text="   Switch OFF to disconnect and restore internal display.",
            font=("Segoe UI", 8),
            fg="#6B7280",
            bg="#1F1F2C",
        )
        self.lbl_info2.pack(anchor="w")

        # Footer
        self.footer = tk.Frame(inner, bg="#181822")
        self.footer.pack(fill="x", side="bottom")

        btn_hide = tk.Label(
            self.footer,
            text="Hide to Tray",
            font=("Segoe UI", 8),
            fg="#9CA3AF",
            bg="#181822",
            cursor="hand2",
        )
        btn_hide.pack(side="left")
        btn_hide.bind("<Button-1>", lambda e: self.hide())
        btn_hide.bind("<Enter>", lambda e: btn_hide.configure(fg="#E5E7EB"))
        btn_hide.bind("<Leave>", lambda e: btn_hide.configure(fg="#9CA3AF"))

        btn_exit = tk.Label(
            self.footer,
            text="Exit Application",
            font=("Segoe UI", 8, "underline"),
            fg="#9CA3AF",
            bg="#181822",
            cursor="hand2",
        )
        btn_exit.pack(side="right")
        btn_exit.bind("<Button-1>", lambda e: self._exit_app())
        btn_exit.bind("<Enter>", lambda e: btn_exit.configure(fg="#EF4444"))
        btn_exit.bind("<Leave>", lambda e: btn_exit.configure(fg="#9CA3AF"))

        self._update_mode_tiles()
        self._update_guidance()
        self.update_idletasks()
        self.width = max(370, self.winfo_reqwidth())
        self.height = self.winfo_reqheight()

    def _start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag(self, event):
        x = self.winfo_x() + (event.x - self._drag_x)
        y = self.winfo_y() + (event.y - self._drag_y)
        self.geometry(f"+{x}+{y}")

    def get_fps(self) -> int:
        try:
            val = int(self.fps_var.get().strip())
            if self.mode_var.get() == "wifi":
                return 30
            return max(5, min(60, val))
        except Exception:
            return 30 if self.mode_var.get() == "wifi" else 45

    def _select_fps(self, val: str):
        if self.mode_var.get() == "wifi" and val != "30":
            return
        self.fps_var.set(val)
        self._update_fps_buttons()
        if self.manager.is_running():
            self._on_fps_changed()

    def _on_custom_fps(self):
        val = self.get_fps()
        if self.mode_var.get() == "wifi":
            val = 30
        self.fps_var.set(str(val))
        self._update_fps_buttons()
        if self.manager.is_running():
            self._on_fps_changed()

    def _update_fps_buttons(self):
        cur = self.fps_var.get().strip()
        for val, btn in self.fps_buttons.items():
            if cur == val:
                btn.configure(bg="#0284C7", fg="#FFFFFF")
            else:
                btn.configure(bg="#282838", fg="#9CA3AF")

    def _update_fps_visibility(self):
        mode = self.mode_var.get()
        if mode == "wifi":
            self.fps_var.set("30")
            for val in ["45", "60"]:
                if val in self.fps_buttons:
                    self.fps_buttons[val].pack_forget()
            if "30" in self.fps_buttons:
                self.fps_buttons["30"].pack(side="left", padx=1)
            self.entry_fps.pack_forget()
            self.lbl_fps_unit.configure(text="FPS (30Hz Wi-Fi limit)")
        else:
            for val in ["30", "45", "60"]:
                if val in self.fps_buttons:
                    self.fps_buttons[val].pack(side="left", padx=1)
            self.entry_fps.pack(side="left", padx=(8, 0))
            self.lbl_fps_unit.configure(text="FPS (5-60)")
        self._update_fps_buttons()

    def _on_fps_changed(self):
        mode = self.mode_var.get()
        fps = self.get_fps()
        mode_name = "USB Cable" if mode == "cable" else "Wi-Fi"
        self.lbl_status.configure(text="UPDATING...", fg="#38BDF8")
        self.lbl_substatus.configure(text=f"Applying {fps} FPS to {mode_name}...", fg="#38BDF8")

        def _bg():
            self.manager.start(mode, fps=fps)
            self.after(0, lambda: self._on_start_finished(True))

        threading.Thread(target=_bg, daemon=True).start()

    def _select_mode(self, val: str):
        if self.mode_var.get() == val:
            return
        self.mode_var.set(val)
        self._update_mode_tiles()
        self._update_guidance()
        if self.manager.is_running():
            self._on_mode_change()

    def _update_mode_tiles(self):
        cur = self.mode_var.get()
        for m_val, (tile, canvas_radio, text_frame, lbl_m_title, lbl_m_desc) in self.mode_tiles.items():
            canvas_radio.delete("all")
            if m_val == cur:
                tile.configure(bg="#162536", highlightbackground="#0284C7", highlightthickness=1)
                canvas_radio.configure(bg="#162536")
                text_frame.configure(bg="#162536")
                lbl_m_title.configure(bg="#162536", fg="#FFFFFF")
                lbl_m_desc.configure(bg="#162536", fg="#94A3B8")

                # Prominent glowing radio indicator
                canvas_radio.create_oval(2, 2, 16, 16, outline="#38BDF8", width=2, fill="#0B132B")
                canvas_radio.create_oval(5, 5, 13, 13, outline="#38BDF8", fill="#38BDF8")
            else:
                tile.configure(bg="#1E1E2A", highlightbackground="#2E2E3E", highlightthickness=1)
                canvas_radio.configure(bg="#1E1E2A")
                text_frame.configure(bg="#1E1E2A")
                lbl_m_title.configure(bg="#1E1E2A", fg="#CBD5E1")
                lbl_m_desc.configure(bg="#1E1E2A", fg="#64748B")

                # Clear, visible unselected ring
                canvas_radio.create_oval(2, 2, 16, 16, outline="#94A3B8", width=1.8, fill="#181822")

        if cur == "wifi":
            self.pair_box.pack(fill="x", pady=(0, 10), before=self.fps_box)
            self.info_box.pack_forget()
            if self.manager.is_running() and self.manager.pair_code:
                self._update_pairing_display(self.manager.pair_code, self.manager.url)
            elif self.manager.is_running():
                self._update_pairing_display(None, None, starting=True)
            else:
                self._update_pairing_display(None, None, starting=False)
        else:
            self.pair_box.pack_forget()
            self.info_box.pack(fill="x", pady=(0, 12), before=self.footer)

        self._update_fps_visibility()
        self._reanchor_window()

    def _open_large_qr(self):
        code = self.manager.pair_code
        url = self.manager.url or f"http://{detect_lan_ip()}:8644"
        if not code:
            return
        LargeQRWindow(self, url, code)

    def _update_guidance(self):
        mode = self.mode_var.get()
        if mode == "cable":
            self.lbl_info1.configure(text="💡 USB Cable: Connect your iPad via Lightning/USB-C.")
            self.lbl_info2.configure(text="   No pairing required. 60 FPS ultra-low latency.")
        else:
            self.lbl_info1.configure(text="📶 Wi-Fi: Connect iPad to the same Wi-Fi network.")
            self.lbl_info2.configure(text="   Scan the QR code with iPad camera to pair instantly.")

    def _update_pairing_display(self, code: Optional[str], url: Optional[str], starting: bool = False):
        lan_ip = detect_lan_ip()
        active_url = url or f"http://{lan_ip}:8644"
        self.lbl_pair_url.configure(text=f"Host: {active_url}")

        if code:
            c_str = str(code)
            formatted = f"{c_str[:4]}  {c_str[4:]}" if len(c_str) == 8 else c_str
            self.lbl_pair_code.configure(text=f"Code: {formatted}", fg="#38BDF8")
            self.lbl_pair_status.configure(text="● Ready to Pair", fg="#34D399")
            self.btn_copy_code.configure(text="Copy", fg="#38BDF8")
            self.btn_copy_url.configure(text="Copy", fg="#38BDF8")

            # Generate large, crisp 260x260 QR code for pairing URL
            qr_payload = f"{active_url}/?code={c_str}"
            qr_img = render_qr_image(qr_payload, size=260)
            if qr_img:
                self.qr_photo = ImageTk.PhotoImage(qr_img)
                self.lbl_qr_placeholder.pack_forget()
                self.lbl_qr_image.configure(image=self.qr_photo)
                self.lbl_qr_image.pack(pady=4)
        elif starting or (self.manager.is_running() and self.mode_var.get() == "wifi"):
            self.lbl_pair_code.configure(text="Code: GENERATING...", fg="#FBBF24")
            self.lbl_pair_status.configure(text="● Starting...", fg="#FBBF24")
            self.lbl_qr_image.pack_forget()
            self.lbl_qr_placeholder.configure(text="Generating pairing\nQR code...", fg="#FBBF24")
            self.lbl_qr_placeholder.pack(fill="x", pady=28)
        else:
            self.lbl_pair_code.configure(text="Code: -- --  -- --", fg="#64748B")
            self.lbl_pair_status.configure(text="Turn ON to pair", fg="#94A3B8")
            self.lbl_qr_image.pack_forget()
            self.lbl_qr_placeholder.configure(text="Switch ON to display\npairing QR code", fg="#64748B")
            self.lbl_qr_placeholder.pack(fill="x", pady=28)

    def _copy_code(self):
        code = self.manager.pair_code
        if not code:
            return
        self.clipboard_clear()
        self.clipboard_append(code)
        self.btn_copy_code.configure(text="✓ Copied!", fg="#10B981")
        self.after(1400, lambda: self.btn_copy_code.configure(text="Copy", fg="#38BDF8"))

    def _copy_url(self):
        url = self.manager.url or f"http://{detect_lan_ip()}:8644"
        self.clipboard_clear()
        self.clipboard_append(url)
        self.btn_copy_url.configure(text="✓ Copied!", fg="#10B981")
        self.after(1400, lambda: self.btn_copy_url.configure(text="Copy", fg="#38BDF8"))

    def _reanchor_window(self):
        if not self.winfo_viewable():
            return
        self.update_idletasks()
        self.height = self.winfo_reqheight()
        self.width = max(370, self.winfo_reqwidth())
        x = self.winfo_x()
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        hmon = user32.MonitorFromPoint(pt, 2)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        work = mi.rcWork
        y = work.bottom - self.height - 14
        self.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def _on_toggle(self, is_on: bool):
        mode = self.mode_var.get()
        fps = self.get_fps()
        mode_name = "USB Cable" if mode == "cable" else "Wi-Fi"

        # 1. Update UI IMMEDIATELY so toggle knob and labels never lag
        if is_on:
            self.lbl_status.configure(text="STARTING...", fg="#38BDF8")
            self.lbl_substatus.configure(text=f"Launching {mode_name} ({fps} FPS)...", fg="#38BDF8")
            self.card.configure(highlightbackground="#0284C7", bg="#1A2234")
            self.card_left.configure(bg="#1A2234")
            self.lbl_serv.configure(bg="#1A2234")
            self.lbl_status.configure(bg="#1A2234")
            self.lbl_substatus.configure(bg="#1A2234")
            self.toggle.configure(bg="#1A2234")
            if mode == "wifi":
                self._update_pairing_display(None, None, starting=True)
        else:
            self.lbl_status.configure(text="STOPPING...", fg="#9CA3AF")
            self.lbl_substatus.configure(text="Stopping all services...", fg="#6B7280")
            self.card.configure(highlightbackground="#363648", bg="#22222E")
            self.card_left.configure(bg="#22222E")
            self.lbl_serv.configure(bg="#22222E")
            self.lbl_status.configure(bg="#22222E")
            self.lbl_substatus.configure(bg="#22222E")
            self.toggle.configure(bg="#22222E")
            if mode == "wifi":
                self._update_pairing_display(None, None, starting=False)

        # 2. Heavy process operations run asynchronously in background thread
        def _bg_action():
            if is_on:
                success = self.manager.start(mode, fps=fps)
                self.after(0, lambda: self._on_start_finished(success))
            else:
                self.manager.kill_all(reset_display=True)
                self.after(0, lambda: self._on_kill_finished())

        threading.Thread(target=_bg_action, daemon=True).start()

    def _on_start_finished(self, success: bool):
        if success:
            mode_name = "USB Cable" if self.mode_var.get() == "cable" else "Wi-Fi"
            fps = self.get_fps()
            self.lbl_status.configure(text="ON", fg="#38BDF8")
            self.lbl_substatus.configure(text=f"Active ({mode_name}) • {fps} FPS • Display extended", fg="#38BDF8")
            self.card.configure(highlightbackground="#0284C7", bg="#1A2234")
            if self.mode_var.get() == "wifi":
                self._update_pairing_display(self.manager.pair_code, self.manager.url)
        else:
            self.toggle.set_state(False)
            self.update_state(False)
            self.lbl_substatus.configure(text="Failed to start host service", fg="#EF4444")

    def _on_kill_finished(self):
        self.update_state(False)
        if self.mode_var.get() == "wifi":
            self._update_pairing_display(None, None)

    def _on_mode_change(self):
        if self.manager.is_running():
            mode = self.mode_var.get()
            fps = self.get_fps()
            mode_name = "USB Cable" if mode == "cable" else "Wi-Fi"
            self.lbl_status.configure(text="SWITCHING...", fg="#38BDF8")
            self.lbl_substatus.configure(text=f"Switching to {mode_name} ({fps} FPS)...", fg="#38BDF8")
            if mode == "wifi":
                self._update_pairing_display(None, None, starting=True)

            def _bg_restart():
                self.manager.start(mode, fps=fps)
                self.after(0, lambda: self._on_start_finished(True))

            threading.Thread(target=_bg_restart, daemon=True).start()

    def update_state(self, is_running: bool):
        self.toggle.set_state(is_running)
        fps = self.get_fps()
        if is_running:
            mode_name = "USB Cable" if self.mode_var.get() == "cable" else "Wi-Fi"
            self.lbl_status.configure(text="ON", fg="#38BDF8")
            self.lbl_substatus.configure(text=f"Active ({mode_name}) • {fps} FPS • Display extended", fg="#38BDF8")
            self.card.configure(highlightbackground="#0284C7", bg="#1A2234")
            self.card_left.configure(bg="#1A2234")
            self.lbl_serv.configure(bg="#1A2234")
            self.lbl_status.configure(bg="#1A2234")
            self.lbl_substatus.configure(bg="#1A2234")
            self.toggle.configure(bg="#1A2234")
            if self.mode_var.get() == "wifi":
                self._update_pairing_display(self.manager.pair_code, self.manager.url)
        else:
            self.lbl_status.configure(text="OFF", fg="#9CA3AF")
            self.lbl_substatus.configure(text="All processes stopped", fg="#6B7280")
            self.card.configure(highlightbackground="#363648", bg="#22222E")
            self.card_left.configure(bg="#22222E")
            self.lbl_serv.configure(bg="#22222E")
            self.lbl_status.configure(bg="#22222E")
            self.lbl_substatus.configure(bg="#22222E")
            self.toggle.configure(bg="#22222E")
            if self.mode_var.get() == "wifi":
                self._update_pairing_display(None, None)

    def show_window(self):
        # Recalculate dimensions dynamically
        self._update_mode_tiles()
        self._update_guidance()
        self.update_idletasks()
        self.width = max(370, self.winfo_reqwidth())
        self.height = self.winfo_reqheight()

        if os.name == "nt" and user32:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))

            # Detect the exact monitor where the mouse/tray icon is located
            hmon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST = 2
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW(hmon, ctypes.byref(mi))

            work = mi.rcWork

            # Horizontal positioning:
            # If clicked in tray area (right side of monitor), center relative to click
            if pt.x >= work.left + (work.right - work.left) // 2:
                cand_x = pt.x - self.width // 2
                x = max(work.left + 16, min(cand_x, work.right - self.width - 16))
            else:
                x = work.right - self.width - 16

            # Vertical positioning:
            # Hover cleanly 14px above the taskbar
            y = work.bottom - self.height - 14

            # If taskbar is at top of screen:
            if pt.y < (work.top + work.bottom) // 2 and work.top > mi.rcMonitor.top:
                y = work.top + 14

            self.geometry(f"{self.width}x{self.height}+{x}+{y}")
        else:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = max(20, sw - self.width - 24)
            y = max(20, sh - self.height - 48)
            self.geometry(f"{self.width}x{self.height}+{x}+{y}")

        self.update_state(self.manager.is_running())

        # Apply Windows 11 DWM rounded corners and dark mode
        if os.name == "nt" and user32:
            try:
                hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
                corner = ctypes.c_int(2)  # DWMWCP_ROUND
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), 33, ctypes.byref(corner), ctypes.sizeof(corner)
                )
                dark = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), 20, ctypes.byref(dark), ctypes.sizeof(dark)
                )
            except Exception:
                pass

        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()

        # Start dismissal listener with grace period
        self.opened_at = time.monotonic()
        self._poll_click_outside()

    def hide(self):
        self.withdraw()

    def toggle_window(self):
        if self.winfo_viewable():
            self.hide()
        else:
            self.show_window()

    def _poll_click_outside(self):
        if not self.winfo_viewable():
            return

        # 350ms grace period after opening so initial click is never misinterpreted
        if os.name == "nt" and user32:
            if time.monotonic() - self.opened_at > 0.35:
                if user32.GetAsyncKeyState(0x01) & 0x8000:
                    pt = POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    wx = self.winfo_x()
                    wy = self.winfo_y()
                    ww = max(self.width, self.winfo_width())
                    wh = max(self.height, self.winfo_height())

                    # If click occurred outside the flyout window
                    if not (wx <= pt.x <= wx + ww and wy <= pt.y <= wy + wh):
                        self.hide()
                        return

            self.after(60, self._poll_click_outside)

    def _exit_app(self):
        self.hide()
        if self.on_exit:
            self.on_exit()


# =====================================================================
# Main Application Controller (Pystray + Tkinter)
# =====================================================================

class ExtendScreenTrayApp:
    def __init__(self):
        ensure_tray_icons()

        # Check existing instance
        hwnd_existing = user32.FindWindowW(None, "ExtendScreen_MasterRoot")
        if hwnd_existing:
            sys.exit(0)

        self.root = tk.Tk()
        self.root.title("ExtendScreen_MasterRoot")
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.img_off = Image.open(TRAY_OFF_PNG)
        self.img_on = Image.open(TRAY_ON_PNG)

        self.manager = ExtendServiceManager(
            on_status_change=self._on_service_status_change,
            on_pairing_update=self._on_pairing_update,
        )
        self.control_win = ControlWindow(self.root, self.manager, on_exit=self.quit_app)

        # Setup Pystray Tray Icon
        menu = pystray.Menu(
            pystray.MenuItem("Open ExtendScreen", self._on_tray_click, default=True),
            pystray.MenuItem(lambda text: "Turn OFF (Kill All)" if self.manager.is_running() else "Turn ON (USB Cable)", self._tray_toggle_service),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda: self.root.after(0, self.quit_app)),
        )

        self.tray = pystray.Icon(
            "ExtendScreen",
            self.img_off,
            "ExtendScreen: OFF",
            menu=menu,
        )
        self.tray.run_detached()

    def _on_tray_click(self, icon=None, item=None):
        self.root.after(0, self.control_win.toggle_window)

    def _tray_toggle_service(self, icon=None, item=None):
        def _toggle():
            if self.manager.is_running():
                self.control_win.toggle.set_state(False, trigger_command=True)
            else:
                self.control_win.toggle.set_state(True, trigger_command=True)
        self.root.after(0, _toggle)

    def _on_service_status_change(self, is_running: bool):
        def _update():
            self._update_tray_state(is_running)
            self.control_win.update_state(is_running)
        self.root.after(0, _update)

    def _on_pairing_update(self, code: Optional[str], url: Optional[str]):
        def _update():
            self.control_win._update_pairing_display(code, url)
        self.root.after(0, _update)

    def _update_tray_state(self, is_running: bool):
        mode_name = "USB Cable" if self.control_win.mode_var.get() == "cable" else "Wi-Fi"
        fps = self.control_win.get_fps()
        self.tray.icon = self.img_on if is_running else self.img_off
        self.tray.title = f"ExtendScreen: {'ON (' + mode_name + ' @ ' + str(fps) + 'fps)' if is_running else 'OFF'}"
        try:
            self.tray.update_menu()
        except Exception:
            pass

    def quit_app(self):
        try:
            self.manager.kill_all(reset_display=True)
            set_display_internal()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


def main():
    app = ExtendScreenTrayApp()
    app.run()


if __name__ == "__main__":
    main()
