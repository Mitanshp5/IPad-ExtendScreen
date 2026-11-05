#!/usr/bin/env python3
"""ExtendScreen System Tray Host Controller.

Provides a system tray icon with an on/off toggle flyout box:
- Left-click on the tray icon reveals a small flyout right above the system tray.
- Large ON/OFF toggle switch to start or kill all services.
- In OFF mode: kills all screen-host.py and ffmpeg processes, and reverts display to internal.
- In ON mode: launches the ExtendScreen host service (USB Cable or Wi-Fi).
- Right-click shows a context menu with quick toggle and exit.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import pathlib
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

try:
    from PIL import Image, ImageTk, ImageEnhance
except ImportError:
    Image = None
    ImageTk = None
    ImageEnhance = None

# DPI Awareness
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
HOST_SCRIPT = ROOT_DIR / "host-tools" / "screen-host.py"
ICON_PNG = ROOT_DIR / "screen" / "Resources" / "Icon.png"
ICO_PATH = ROOT_DIR / "host-tools" / "icon.ico"
ICO_ON_PATH = ROOT_DIR / "host-tools" / "icon_on.ico"
ICO_OFF_PATH = ROOT_DIR / "host-tools" / "icon_off.ico"
LOG_FILE = ROOT_DIR / "host-tools" / "tray.log"

# Win32 Constants
WM_USER = 0x0400
WM_TRAY_CALLBACK = WM_USER + 20
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_GRAYED = 0x0001
SPI_GETWORKAREA = 0x0030
CREATE_NO_WINDOW = 0x08000000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

LRESULT = ctypes.c_int64
WPARAM = ctypes.c_uint64
LPARAM = ctypes.c_int64

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HICON),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


def ensure_ico_files() -> None:
    """Ensure icon.ico, icon_on.ico, and icon_off.ico exist."""
    if not ICO_PATH.exists() or not ICO_ON_PATH.exists() or not ICO_OFF_PATH.exists():
        if Image and ICON_PNG.exists():
            try:
                base = Image.open(ICON_PNG)
                base.save(ICO_PATH, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
                base.save(ICO_ON_PATH, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
                if ImageEnhance:
                    enh = ImageEnhance.Brightness(base.convert("L").convert("RGBA"))
                    dimmed = enh.enhance(0.55)
                    dimmed.save(ICO_OFF_PATH, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
                else:
                    base.save(ICO_OFF_PATH, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
            except Exception as e:
                print(f"Error generating ICOs: {e}", file=sys.stderr)


# =====================================================================
# Service Controller (Starts, Kills, and Monitors screen-host.py)
# =====================================================================

class ExtendServiceManager:
    """Manages the background ExtendScreen host processes and display switching."""

    def __init__(self, on_status_change=None) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.mode = "cable"  # "cable" or "wifi"
        self.on_status_change = on_status_change
        self.monitor_thread: Optional[threading.Thread] = None
        self.stop_requested = False

    def is_running(self) -> bool:
        if self.proc is not None:
            if self.proc.poll() is None:
                return True
            self.proc = None
        return False

    def start(self, mode: str = "cable") -> bool:
        self.stop_requested = False
        self.mode = mode

        # First ensure no previous stale instances exist
        self.kill_all(reset_display=False)

        cmd = [sys.executable, str(HOST_SCRIPT)]
        if mode == "cable":
            cmd.extend(["--cable", "--extend", "--fps", "45"])
        else:
            cmd.extend(["--extend"])

        try:
            with open(LOG_FILE, "a", encoding="utf-8") as log_out:
                log_out.write(f"\n--- ExtendScreen Service Started ({mode}) at {time.ctime()} ---\n")
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

            # Background thread to pipe logs and detect unexpected terminations
            self.monitor_thread = threading.Thread(target=self._monitor_process, daemon=True)
            self.monitor_thread.start()

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
        except Exception:
            pass
        finally:
            if not self.stop_requested:
                # Process exited on its own (or crashed/disconnected)
                self.proc = None
                if self.on_status_change:
                    self.on_status_change(False)

    def kill_all(self, reset_display: bool = True) -> None:
        """Kill all host processes (screen-host.py, ffmpeg) and reset display."""
        self.stop_requested = True

        # 1. Kill tracked child process tree
        if self.proc is not None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
            except Exception:
                pass
            self.proc = None

        # 2. Terminate any other screen-host.py python processes
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

        # 3. Terminate any orphaned ffmpeg instances running screen capture
        try:
            ps_ffmpeg = (
                'Get-CimInstance Win32_Process | '
                'Where-Object { $_.Name -eq "ffmpeg.exe" -and ($_.CommandLine -like "*ddagrab*" -or $_.CommandLine -like "*gdigrab*") } | '
                'ForEach-Object { Stop-Process -Id $_.ProcessId -Force }'
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_ffmpeg],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass

        # 4. Revert Windows display layout to single-screen (internal)
        if reset_display:
            try:
                display_switch = os.path.join(
                    os.environ.get("WINDIR", r"C:\Windows"), "System32", "DisplaySwitch.exe"
                )
                subprocess.run([display_switch, "/internal"], capture_output=True, creationflags=CREATE_NO_WINDOW)
            except Exception:
                pass

        with open(LOG_FILE, "a", encoding="utf-8") as log_out:
            log_out.write(f"--- All processes killed at {time.ctime()} ---\n")

        if self.on_status_change:
            self.on_status_change(False)


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
        self.current_x = 3.0  # knob position
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
            self.command(self.is_on)

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

        # Interpolate color: OFF (#3B3B44) to ON (#0078D4)
        t = (self.current_x - pad) / max(1.0, (self.width - kd - pad * 2))
        t = max(0.0, min(1.0, t))

        r_c = int(59 * (1 - t) + 0 * t)
        g_c = int(59 * (1 - t) + 120 * t)
        b_c = int(68 * (1 - t) + 212 * t)
        track_color = f"#{r_c:02x}{g_c:02x}{b_c:02x}"

        # Draw pill track
        self.create_oval(0, 0, self.height, self.height, fill=track_color, outline="")
        self.create_oval(self.width - self.height, 0, self.width, self.height, fill=track_color, outline="")
        self.create_rectangle(r, 0, self.width - r, self.height, fill=track_color, outline="")

        # Draw Knob
        kx = self.current_x
        self.create_oval(kx, pad, kx + kd, pad + kd, fill="#FFFFFF", outline="")


# =====================================================================
# Flyout Window (Small box appearing above the system tray)
# =====================================================================

class TrayFlyout(tk.Toplevel):
    """Modern dark flyout box positioned directly above the system tray."""

    def __init__(self, master, manager: ExtendServiceManager, on_exit=None):
        super().__init__(master)
        self.manager = manager
        self.on_exit = on_exit

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#1A1A22")
        self.withdraw()  # start hidden

        self.width = 330
        self.height = 310

        self.icon_photo: Optional[ImageTk.PhotoImage] = None
        self._init_ui()

        # Keyboard & click dismissals
        self.bind("<Escape>", lambda e: self.hide())
        self.poll_active = False

    def _init_ui(self):
        # Outer Border / Container
        outer = tk.Frame(self, bg="#33333E", padx=1, pady=1)
        outer.pack(fill="both", expand=True)

        inner = tk.Frame(outer, bg="#181820", padx=16, pady=14)
        inner.pack(fill="both", expand=True)

        # 1. Header (Icon, Title, Close Button)
        header = tk.Frame(inner, bg="#181820")
        header.pack(fill="x", pady=(0, 12))

        if Image and ImageTk and ICON_PNG.exists():
            try:
                im = Image.open(ICON_PNG).resize((22, 22), Image.Resampling.LANCZOS)
                self.icon_photo = ImageTk.PhotoImage(im)
                lbl_icon = tk.Label(header, image=self.icon_photo, bg="#181820")
                lbl_icon.pack(side="left", padx=(0, 8))
            except Exception:
                pass

        lbl_title = tk.Label(
            header,
            text="ExtendScreen",
            font=("Segoe UI", 11, "bold"),
            fg="#FFFFFF",
            bg="#181820",
        )
        lbl_title.pack(side="left")

        btn_close = tk.Label(
            header,
            text="✕",
            font=("Segoe UI", 10),
            fg="#9CA3AF",
            bg="#181820",
            cursor="hand2",
        )
        btn_close.pack(side="right")
        btn_close.bind("<Button-1>", lambda e: self.hide())
        btn_close.bind("<Enter>", lambda e: btn_close.configure(fg="#FFFFFF"))
        btn_close.bind("<Leave>", lambda e: btn_close.configure(fg="#9CA3AF"))

        # 2. Main Toggle Card
        self.card = tk.Frame(inner, bg="#22222C", padx=14, pady=12, highlightthickness=1, highlightbackground="#363644")
        self.card.pack(fill="x", pady=(0, 12))

        card_left = tk.Frame(self.card, bg="#22222C")
        card_left.pack(side="left", fill="both", expand=True)

        lbl_serv = tk.Label(card_left, text="Screen Sharing", font=("Segoe UI", 9), fg="#9CA3AF", bg="#22222C")
        lbl_serv.pack(anchor="w")

        self.lbl_status = tk.Label(card_left, text="OFF", font=("Segoe UI", 14, "bold"), fg="#9CA3AF", bg="#22222C")
        self.lbl_status.pack(anchor="w", pady=(2, 0))

        self.lbl_substatus = tk.Label(
            card_left,
            text="All services stopped",
            font=("Segoe UI", 8),
            fg="#6B7280",
            bg="#22222C",
        )
        self.lbl_substatus.pack(anchor="w")

        self.toggle = SmoothToggle(self.card, width=54, height=28, command=self._on_toggle, bg="#22222C")
        self.toggle.pack(side="right", padx=(8, 0))

        # 3. Connection Mode Radio Group
        mode_box = tk.LabelFrame(
            inner,
            text=" Connection Mode ",
            font=("Segoe UI", 8, "bold"),
            fg="#9CA3AF",
            bg="#181820",
            padx=10,
            pady=8,
            highlightthickness=1,
            highlightbackground="#2C2C38",
        )
        mode_box.pack(fill="x", pady=(0, 12))

        self.mode_var = tk.StringVar(value="cable")

        rb_cable = tk.Radiobutton(
            mode_box,
            text="USB Cable (Recommended, 45 fps)",
            variable=self.mode_var,
            value="cable",
            font=("Segoe UI", 9),
            fg="#E5E7EB",
            bg="#181820",
            selectcolor="#181820",
            activebackground="#181820",
            activeforeground="#FFFFFF",
            command=self._on_mode_change,
            cursor="hand2",
        )
        rb_cable.pack(anchor="w")

        rb_wifi = tk.Radiobutton(
            mode_box,
            text="Wi-Fi (HTTP Streaming)",
            variable=self.mode_var,
            value="wifi",
            font=("Segoe UI", 9),
            fg="#E5E7EB",
            bg="#181820",
            selectcolor="#181820",
            activebackground="#181820",
            activeforeground="#FFFFFF",
            command=self._on_mode_change,
            cursor="hand2",
        )
        rb_wifi.pack(anchor="w", pady=(4, 0))

        # 4. Info Footer & Action Bar
        footer = tk.Frame(inner, bg="#181820")
        footer.pack(fill="x", side="bottom")

        self.startup_var = tk.BooleanVar(value=self._is_startup_enabled())
        cb_startup = tk.Checkbutton(
            footer,
            text="Start with Windows",
            variable=self.startup_var,
            font=("Segoe UI", 8),
            fg="#9CA3AF",
            bg="#181820",
            selectcolor="#181820",
            activebackground="#181820",
            activeforeground="#FFFFFF",
            command=self._on_toggle_startup,
            cursor="hand2",
        )
        cb_startup.pack(side="left")

        btn_exit = tk.Label(
            footer,
            text="Exit Tray",
            font=("Segoe UI", 8, "underline"),
            fg="#9CA3AF",
            bg="#181820",
            cursor="hand2",
        )
        btn_exit.pack(side="right")
        btn_exit.bind("<Button-1>", lambda e: self._exit_app())
        btn_exit.bind("<Enter>", lambda e: btn_exit.configure(fg="#EF4444"))
        btn_exit.bind("<Leave>", lambda e: btn_exit.configure(fg="#9CA3AF"))

    def _on_toggle(self, is_on: bool):
        if is_on:
            mode = self.mode_var.get()
            success = self.manager.start(mode)
            if not success:
                self.toggle.set_state(False)
        else:
            self.manager.kill_all(reset_display=True)
        self.update_state(self.manager.is_running())

    def _on_mode_change(self):
        if self.manager.is_running():
            # Restart in new mode
            mode = self.mode_var.get()
            self.manager.start(mode)
            self.update_state(True)

    def update_state(self, is_running: bool):
        self.toggle.set_state(is_running)
        if is_running:
            mode_name = "USB Cable" if self.mode_var.get() == "cable" else "Wi-Fi"
            self.lbl_status.configure(text="ON", fg="#38BDF8")
            self.lbl_substatus.configure(text=f"Active ({mode_name}) • Display extended", fg="#38BDF8")
            self.card.configure(highlightbackground="#0078D4")
        else:
            self.lbl_status.configure(text="OFF", fg="#9CA3AF")
            self.lbl_substatus.configure(text="All processes stopped", fg="#6B7280")
            self.card.configure(highlightbackground="#363644")

    def show_above_tray(self):
        """Position the box neatly right above the system tray."""
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))

        # Query usable desktop work area
        wa = RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)

        # Center horizontally over cursor if cursor is in bottom right
        x = min(wa.right - self.width - 12, max(wa.left + 12, pt.x - self.width // 2))
        y = wa.bottom - self.height - 8

        self.geometry(f"{self.width}x{self.height}+{x}+{y}")
        self.deiconify()
        self.lift()
        self.focus_force()

        self.poll_active = True
        self._poll_click_outside()

    def hide(self):
        self.poll_active = False
        self.withdraw()

    def toggle_visibility(self):
        if self.winfo_viewable():
            self.hide()
        else:
            self.update_state(self.manager.is_running())
            self.show_above_tray()

    def _poll_click_outside(self):
        """Dismiss popup if the user clicks anywhere outside."""
        if not self.poll_active or not self.winfo_viewable():
            return

        # Check left mouse button state (0x01 = VK_LBUTTON)
        if user32.GetAsyncKeyState(0x01) & 0x8000:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            wx = self.winfo_x()
            wy = self.winfo_y()
            ww = self.winfo_width()
            wh = self.winfo_height()

            # If click was outside our popup window
            if not (wx <= pt.x <= wx + ww and wy <= pt.y <= wy + wh):
                # Allow a tiny margin for tray icon clicks
                self.hide()
                return

        self.after(80, self._poll_click_outside)

    def _is_startup_enabled(self) -> bool:
        startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        cmd_file = os.path.join(startup_dir, "ExtendScreenTray.cmd")
        return os.path.exists(cmd_file)

    def _on_toggle_startup(self):
        startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        cmd_file = os.path.join(startup_dir, "ExtendScreenTray.cmd")
        start_script = ROOT_DIR / "start-tray.cmd"

        if self.startup_var.get():
            try:
                with open(cmd_file, "w", encoding="utf-8") as f:
                    f.write(f'@echo off\r\nstart "" "{start_script}"\r\n')
            except Exception as e:
                print(f"Error creating startup file: {e}", file=sys.stderr)
        else:
            if os.path.exists(cmd_file):
                try:
                    os.remove(cmd_file)
                except Exception as e:
                    print(f"Error removing startup file: {e}", file=sys.stderr)

    def _exit_app(self):
        self.hide()
        if self.on_exit:
            self.on_exit()


# =====================================================================
# Win32 System Tray Icon
# =====================================================================

class Win32Tray:
    """Creates a Win32 system tray notification icon and message handler."""

    def __init__(self, on_left_click=None, on_right_click=None):
        self.on_left_click = on_left_click
        self.on_right_click = on_right_click
        self.hwnd: Optional[wintypes.HWND] = None
        self.hicon: Optional[wintypes.HICON] = None
        self.atom = 0
        self.is_running = True
        self.thread: Optional[threading.Thread] = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY_CALLBACK:
                event = lparam & 0xFFFF
                if event == WM_LBUTTONUP:
                    if self.on_left_click:
                        self.on_left_click()
                elif event == WM_RBUTTONUP:
                    if self.on_right_click:
                        self.on_right_click()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self.proc = WNDPROC(wnd_proc)

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self.proc
        wc.hInstance = hinst
        wc.lpszClassName = f"ExtendScreenTray_{os.getpid()}"

        self.atom = user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, self.atom, "ExtendScreenTrayHostWindow", 0, 0, 0, 0, 0, 0, 0, hinst, None)

        self.update_icon(is_active=False)

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def update_icon(self, is_active: bool):
        if not self.hwnd:
            return

        ico_file = ICO_ON_PATH if is_active else ICO_OFF_PATH
        if not ico_file.exists():
            ico_file = ICO_PATH

        hicon = user32.LoadImageW(None, str(ico_file), IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if not hicon:
            hicon = user32.LoadIconW(None, 32512)  # IDI_APPLICATION fallback

        tip = f"ExtendScreen: {'ON (Running)' if is_active else 'OFF (Stopped)'}"

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        nid.hIcon = hicon
        nid.szTip = tip

        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)) or shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def remove(self):
        if self.hwnd:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            user32.PostMessageW(self.hwnd, 0x0012, 0, 0)  # WM_QUIT


# =====================================================================
# Main Application Controller
# =====================================================================

class ExtendScreenTrayApp:
    def __init__(self):
        ensure_ico_files()

        # Check if already running by window title
        existing = user32.FindWindowW(None, "ExtendScreenTrayHostWindow")
        if existing:
            # Wake up existing instance and toggle flyout
            user32.PostMessageW(existing, WM_TRAY_CALLBACK, 0, WM_LBUTTONUP)
            sys.exit(0)

        self.root = tk.Tk()
        self.root.withdraw()  # Root remains invisible

        self.manager = ExtendServiceManager(on_status_change=self._on_service_status_change)
        self.flyout = TrayFlyout(self.root, self.manager, on_exit=self.quit_app)

        self.tray = Win32Tray(
            on_left_click=self._on_tray_left_click,
            on_right_click=self._on_tray_right_click,
        )
        self.tray.start()

    def _on_tray_left_click(self):
        self.root.after(0, self.flyout.toggle_visibility)

    def _on_tray_right_click(self):
        self.root.after(0, self._show_native_menu)

    def _show_native_menu(self):
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))

        hmenu = user32.CreatePopupMenu()
        is_on = self.manager.is_running()

        status_text = f"ExtendScreen: {'ON' if is_on else 'OFF'}"
        user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED, 0, status_text)
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, 1, "Turn OFF (Kill All)" if is_on else "Turn ON")
        user32.AppendMenuW(hmenu, MF_STRING, 2, "Open Controls...")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, 3, "Exit")

        # Required before and after TrackPopupMenu for tray icons
        user32.SetForegroundWindow(self.tray.hwnd or self.flyout.winfo_id())
        cmd = user32.TrackPopupMenuEx(hmenu, TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, self.tray.hwnd, None)
        user32.DestroyMenu(hmenu)
        user32.PostMessageW(self.tray.hwnd, 0, 0, 0)

        if cmd == 1:
            if is_on:
                self.manager.kill_all(reset_display=True)
            else:
                self.manager.start(self.flyout.mode_var.get())
            self.flyout.update_state(self.manager.is_running())
        elif cmd == 2:
            self.flyout.show_above_tray()
        elif cmd == 3:
            self.quit_app()

    def _on_service_status_change(self, is_running: bool):
        def _update():
            self.tray.update_icon(is_active=is_running)
            self.flyout.update_state(is_running)
        self.root.after(0, _update)

    def quit_app(self):
        # Kill all processes and reset display before exiting
        self.manager.kill_all(reset_display=True)
        self.tray.remove()
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


def main():
    try:
        app = ExtendScreenTrayApp()
        app.run()
    except Exception:
        import traceback
        with open(ROOT_DIR / "host-tools" / "tray_crash.log", "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        raise


if __name__ == "__main__":
    main()
