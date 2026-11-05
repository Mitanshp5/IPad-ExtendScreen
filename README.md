# ExtendScreen

Use an iPad mini (iOS 9.3.5) as an extended second monitor for Windows 10/11 over USB cable or Wi-Fi.

## Architecture

- **iOS Client (`screen/`)**: Native Objective-C iOS 9.3.5 jailbroken application built with Theos.
  - USB mode connects to PC host through loopback listener on port 8765 forwarded by Apple `usbmuxd`.
  - Wi-Fi mode streams MJPEG over HTTP.
  - Full-screen display with auto-hiding controls and status bar.
  - Forwarding touch taps, right clicks, drags, and onscreen keyboard input to the PC.
  - Diagnostic logging to `/tmp/ExtendScreen.log` and `NSLog`.

- **PC Host (`host-tools/screen-host.py`)**: Python streaming host.
  - Automatic desktop extension via Windows Virtual Display Driver (VDD) and `DisplaySwitch.exe`.
  - Screen capture via Desktop Duplication API (`ddagrab`) with fallback to `gdigrab`.
  - USB transport over Apple Mobile Device Service `usbmuxd` (port 27015).
  - Forwarded touch and mouse input injection via Windows `SendInput`.

## Quick Start

### 1. Install on iPad
Transfer `ExtendScreen-1.0.7.deb` to the iPad (e.g., via iFile web server) and install it.

### 2. USB Cable Mode
1. Open **Extend Screen** on the iPad, tap **Settings**, set **Connection mode** to **USB cable**, and tap **Save**.
2. Connect the iPad by USB and unlock it (tap **Trust** if prompted).
3. On the PC, run:
   ```cmd
   start-cable-host.cmd
   ```
   Or directly:
   ```cmd
   python host-tools\screen-host.py --cable --extend --fps 60
   ```

### 3. Wi-Fi Mode
Run:
```cmd
start-extend-host.cmd
```
