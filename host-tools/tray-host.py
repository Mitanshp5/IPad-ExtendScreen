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
import os
import pathlib
import subprocess
import sys
import threading
import time
import tkinter as tk
from typing import Optional

from PIL import Image, ImageTk, ImageDraw, ImageFilter
import pystray

# Ensure all threads bind to the interactive 'Default' desktop
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

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
HOST_SCRIPT = ROOT_DIR / "host-tools" / "screen-host.py"
ICON_PNG = ROOT_DIR / "screen" / "Resources" / "Icon.png"
TRAY_OFF_PNG = ROOT_DIR / "host-tools" / "tray_off.png"
TRAY_ON_PNG = ROOT_DIR / "host-tools" / "tray_on.png"
LOG_FILE = ROOT_DIR / "host-tools" / "tray.log"

SPI_GETWORKAREA = 0x0030
CREATE_NO_WINDOW = 0x08000000


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


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

        # Kill any prior orphan processes before starting
        self.kill_all(reset_display=False)

        cmd = [sys.executable, str(HOST_SCRIPT)]
        if mode == "cable":
            cmd.extend(["--cable", "--extend", "--fps", "45"])
        else:
            cmd.extend(["--extend"])

        try:
            with open(LOG_FILE, "a", encoding="utf-8") as log_out:
                log_out.write(f"\n--- ExtendScreen Started ({mode}) at {time.ctime()} ---\n")
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
        self.width = 330
        self.height = 280
        self.opened_at = 0.0

        self.withdraw()

        self.icon_photo: Optional[ImageTk.PhotoImage] = None
        self._init_ui()

        self.bind("<Escape>", lambda e: self.hide())

    def _init_ui(self):
        # 1px border frame around the card
        border_frame = tk.Frame(self, bg="#333342", padx=1, pady=1)
        border_frame.pack(fill="both", expand=True)

        inner = tk.Frame(border_frame, bg="#181822", padx=16, pady=14)
        inner.pack(fill="both", expand=True)

        # Header
        header = tk.Frame(inner, bg="#181822")
        header.pack(fill="x", pady=(0, 10))

        if ICON_PNG.exists():
            try:
                im = Image.open(ICON_PNG).resize((22, 22), Image.Resampling.LANCZOS)
                self.icon_photo = ImageTk.PhotoImage(im)
                lbl_icon = tk.Label(header, image=self.icon_photo, bg="#181822")
                lbl_icon.pack(side="left", padx=(0, 8))
            except Exception:
                pass

        lbl_title = tk.Label(
            header,
            text="ExtendScreen",
            font=("Segoe UI", 11, "bold"),
            fg="#FFFFFF",
            bg="#181822",
        )
        lbl_title.pack(side="left")

        btn_min = tk.Label(
            header,
            text="✕",
            font=("Segoe UI", 10),
            fg="#9CA3AF",
            bg="#181822",
            cursor="hand2",
        )
        btn_min.pack(side="right")
        btn_min.bind("<Button-1>", lambda e: self.hide())
        btn_min.bind("<Enter>", lambda e: btn_min.configure(fg="#FFFFFF"))
        btn_min.bind("<Leave>", lambda e: btn_min.configure(fg="#9CA3AF"))

        # Main Toggle Card
        self.card = tk.Frame(inner, bg="#22222E", padx=14, pady=10, highlightthickness=1, highlightbackground="#363648")
        self.card.pack(fill="x", pady=(0, 10))

        card_left = tk.Frame(self.card, bg="#22222E")
        card_left.pack(side="left", fill="both", expand=True)

        lbl_serv = tk.Label(card_left, text="Screen Sharing", font=("Segoe UI", 8), fg="#9CA3AF", bg="#22222E")
        lbl_serv.pack(anchor="w")

        self.lbl_status = tk.Label(card_left, text="OFF", font=("Segoe UI", 14, "bold"), fg="#9CA3AF", bg="#22222E")
        self.lbl_status.pack(anchor="w", pady=(1, 0))

        self.lbl_substatus = tk.Label(
            card_left,
            text="All services stopped",
            font=("Segoe UI", 8),
            fg="#6B7280",
            bg="#22222E",
        )
        self.lbl_substatus.pack(anchor="w")

        self.toggle = SmoothToggle(self.card, width=54, height=28, command=self._on_toggle, bg="#22222E")
        self.toggle.pack(side="right", padx=(6, 0))

        # Connection Mode Radio Group
        mode_box = tk.LabelFrame(
            inner,
            text=" Connection Mode ",
            font=("Segoe UI", 8, "bold"),
            fg="#9CA3AF",
            bg="#181822",
            padx=10,
            pady=6,
            highlightthickness=1,
            highlightbackground="#2C2C3C",
        )
        mode_box.pack(fill="x", pady=(0, 10))

        self.mode_var = tk.StringVar(value="cable")

        rb_cable = tk.Radiobutton(
            mode_box,
            text="USB Cable (Recommended, 45 fps)",
            variable=self.mode_var,
            value="cable",
            font=("Segoe UI", 8),
            fg="#E5E7EB",
            bg="#181822",
            selectcolor="#181822",
            activebackground="#181822",
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
            font=("Segoe UI", 8),
            fg="#E5E7EB",
            bg="#181822",
            selectcolor="#181822",
            activebackground="#181822",
            activeforeground="#FFFFFF",
            command=self._on_mode_change,
            cursor="hand2",
        )
        rb_wifi.pack(anchor="w", pady=(2, 0))

        # Footer
        footer = tk.Frame(inner, bg="#181822")
        footer.pack(fill="x", side="bottom")

        btn_hide = tk.Label(
            footer,
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
            footer,
            text="Exit App",
            font=("Segoe UI", 8, "underline"),
            fg="#9CA3AF",
            bg="#181822",
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
            self.card.configure(highlightbackground="#363648")

    def show_window(self):
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))

        # Detect the exact monitor where the mouse/tray icon is located
        hmon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST = 2
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))

        work = mi.rcWork
        # Position in bottom-right corner of that monitor, 16px from right edge, 12px above taskbar
        x = work.right - self.width - 16
        y = work.bottom - self.height - 12

        # If taskbar is at top of screen:
        if pt.y < (work.top + work.bottom) // 2:
            y = work.top + 12

        self.geometry(f"{self.width}x{self.height}+{x}+{y}")
        self.update_state(self.manager.is_running())
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
        if time.monotonic() - self.opened_at > 0.35:
            if user32.GetAsyncKeyState(0x01) & 0x8000:
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                wx = self.winfo_x()
                wy = self.winfo_y()
                ww = self.winfo_width()
                wh = self.winfo_height()

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

        self.img_off = Image.open(TRAY_OFF_PNG)
        self.img_on = Image.open(TRAY_ON_PNG)

        self.manager = ExtendServiceManager(on_status_change=self._on_service_status_change)
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
                self.manager.kill_all(reset_display=True)
            else:
                self.manager.start(self.control_win.mode_var.get())
            self.control_win.update_state(self.manager.is_running())
            self._update_tray_state(self.manager.is_running())
        self.root.after(0, _toggle)

    def _on_service_status_change(self, is_running: bool):
        def _update():
            self._update_tray_state(is_running)
            self.control_win.update_state(is_running)
        self.root.after(0, _update)

    def _update_tray_state(self, is_running: bool):
        mode_name = "USB Cable" if self.control_win.mode_var.get() == "cable" else "Wi-Fi"
        self.tray.icon = self.img_on if is_running else self.img_off
        self.tray.title = f"ExtendScreen: {'ON (' + mode_name + ')' if is_running else 'OFF'}"
        try:
            self.tray.update_menu()
        except Exception:
            pass

    def quit_app(self):
        self.manager.kill_all(reset_display=True)
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
