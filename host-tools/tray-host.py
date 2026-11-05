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
from typing import Optional

from PIL import Image, ImageTk, ImageEnhance
import pystray

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
LOG_FILE = ROOT_DIR / "host-tools" / "tray.log"

SPI_GETWORKAREA = 0x0030
CREATE_NO_WINDOW = 0x08000000

user32 = ctypes.windll.user32


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

        r_c = int(59 * (1 - t) + 0 * t)
        g_c = int(59 * (1 - t) + 120 * t)
        b_c = int(68 * (1 - t) + 212 * t)
        track_color = f"#{r_c:02x}{g_c:02x}{b_c:02x}"

        self.create_oval(0, 0, self.height, self.height, fill=track_color, outline="")
        self.create_oval(self.width - self.height, 0, self.width, self.height, fill=track_color, outline="")
        self.create_rectangle(r, 0, self.width - r, self.height, fill=track_color, outline="")

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
        self.withdraw()

        self.width = 330
        self.height = 310

        self.icon_photo: Optional[ImageTk.PhotoImage] = None
        self._init_ui()

        self.bind("<Escape>", lambda e: self.hide())
        self.poll_active = False

    def _init_ui(self):
        outer = tk.Frame(self, bg="#33333E", padx=1, pady=1)
        outer.pack(fill="both", expand=True)

        inner = tk.Frame(outer, bg="#181820", padx=16, pady=14)
        inner.pack(fill="both", expand=True)

        # Header
        header = tk.Frame(inner, bg="#181820")
        header.pack(fill="x", pady=(0, 12))

        if ICON_PNG.exists():
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

        # Main Toggle Card
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

        # Connection Mode Radio Group
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

        # Footer
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
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))

        wa = RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)

        x = min(wa.right - self.width - 12, max(wa.left + 12, pt.x - self.width // 2))
        y = wa.bottom - self.height - 8
        if pt.y < (wa.top + wa.bottom) // 2:
            y = wa.top + 8

        self.geometry(f"{self.width}x{self.height}+{x}+{y}")
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
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
        if not self.poll_active or not self.winfo_viewable():
            return

        if user32.GetAsyncKeyState(0x01) & 0x8000:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            wx = self.winfo_x()
            wy = self.winfo_y()
            ww = self.winfo_width()
            wh = self.winfo_height()

            if not (wx <= pt.x <= wx + ww and wy <= pt.y <= wy + wh):
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
# Main Application Controller (Pystray + Tkinter)
# =====================================================================

class ExtendScreenTrayApp:
    def __init__(self):
        # Prevent duplicate instances
        hwnd_existing = user32.FindWindowW(None, "ExtendScreenTrayHiddenHost")
        if hwnd_existing:
            sys.exit(0)

        self.root = tk.Tk()
        self.root.title("ExtendScreenTrayHiddenHost")
        self.root.withdraw()

        self.img_on = Image.open(ICON_PNG)
        enh = ImageEnhance.Brightness(self.img_on.convert("L").convert("RGBA"))
        self.img_off = enh.enhance(0.55)

        self.manager = ExtendServiceManager(on_status_change=self._on_service_status_change)
        self.flyout = TrayFlyout(self.root, self.manager, on_exit=self.quit_app)

        # Setup Pystray Tray Icon
        menu = pystray.Menu(
            pystray.MenuItem("Open Controls", self._tray_left_click, default=True),
            pystray.MenuItem(lambda text: "Turn OFF (Kill All)" if self.manager.is_running() else "Turn ON", self._tray_toggle_service),
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

    def _tray_left_click(self, icon=None, item=None):
        self.root.after(0, self.flyout.toggle_visibility)

    def _tray_toggle_service(self, icon=None, item=None):
        def _toggle():
            if self.manager.is_running():
                self.manager.kill_all(reset_display=True)
            else:
                self.manager.start(self.flyout.mode_var.get())
            self.flyout.update_state(self.manager.is_running())
            self._update_tray_state(self.manager.is_running())
        self.root.after(0, _toggle)

    def _on_service_status_change(self, is_running: bool):
        def _update():
            self._update_tray_state(is_running)
            self.flyout.update_state(is_running)
        self.root.after(0, _update)

    def _update_tray_state(self, is_running: bool):
        mode_name = "USB Cable" if self.flyout.mode_var.get() == "cable" else "Wi-Fi"
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
