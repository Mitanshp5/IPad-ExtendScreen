# ExtendScreen

Use an iPad mini (1st Gen, iOS 9.3.5) as an extended second monitor for Windows 10/11 over USB cable or Wi-Fi with touch and keyboard input forwarding.

---

## Features

- **USB Cable Mode (Recommended)**: Ultra-low latency, 45–60 fps streaming over a physical Lightning/30-pin cable via Apple `usbmuxd`. No Wi-Fi or passwords required.
- **Wi-Fi Mode**: LAN streaming over HTTP MJPEG with optional PIN pairing.
- **Automatic Display Management**: Automatically enables the Windows virtual extended monitor (`DisplaySwitch /extend`) on connect and reverts to single-screen (`/internal`) on disconnect.
- **Hardware Capture Acceleration**: Fast screen capture via Desktop Duplication API (`ddagrab`) with seamless fallback to `gdigrab`.
- **Bidirectional Input**: Touch taps, right clicks, drags, mouse wheel scrolls, and onscreen keyboard input forwarded to Windows via `SendInput`.
- **Auto-Hiding Chrome & Status Bar**: Clean full-screen display. The iOS status bar (time, battery, Wi-Fi) and buttons fade out after 4.5 seconds of inactivity. Tapping the top-right corner reveals them.
- **Persistent Diagnostics**: Dual-channel logging to `NSLog` and `/tmp/ExtendScreen.log`.

For deep-dive architectural details and protocol specifications, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Network Endpoints & Ports

| Service | Address / Port | Role |
|---|---|---|
| **iPad iFile Web Server** | `http://<iPad-IP>:10000` | Package upload and log inspection |
| **PC usbmuxd Service** | `127.0.0.1:27015` | Apple Mobile Device USB multiplexer |
| **iPad USB Cable Listener** | `127.0.0.1:8765` | Device-side loopback listener |
| **PC Wi-Fi Streaming Host** | `http://<PC-IP>:8644` | Wi-Fi HTTP streaming & pairing server |

---

## How to Compile the iOS App

The iOS app is built for rootful jailbroken iOS 9.3.5 using **Theos** inside WSL (Ubuntu).

### 1. Build Environment Prerequisites

- WSL (Ubuntu) installed.
- Theos toolchain installed at `/home/mitansh/.cache/hermespad-build/theos`.
- iOS 10.3 / 9.3 SDK configured at `$THEOS/sdks/iPhoneOS10.3.sdk`.
- Environment script: `/home/mitansh/.cache/hermespad-build/env.sh`.

### 2. Compilation Steps

From Windows PowerShell or Command Prompt:

```powershell
# 1. Sync Windows source files into the WSL build directory
wsl -d Ubuntu -e bash -c "rsync -a --delete --exclude packages --exclude .theos /mnt/c/Code/ExtendScreen/screen/ /home/mitansh/.cache/extendscreen-build/screen/"

# 2. Fix permissions and Unix line endings on Debian control scripts
wsl -d Ubuntu -e bash -c "chmod 755 /home/mitansh/.cache/extendscreen-build/screen/layout && chmod 755 /home/mitansh/.cache/extendscreen-build/screen/layout/DEBIAN && chmod 755 /home/mitansh/.cache/extendscreen-build/screen/layout/DEBIAN/postinst && sed -i 's/\r$//' /home/mitansh/.cache/extendscreen-build/screen/layout/DEBIAN/control"

# 3. Clean and build the .deb package
wsl -d Ubuntu -e bash -c "source /home/mitansh/.cache/hermespad-build/env.sh && cd /home/mitansh/.cache/extendscreen-build/screen && make clean && make package DEBUG=0 STRIP=0"

# 4. Copy the generated .deb to your Windows repository
wsl -d Ubuntu -e bash -c "cp /home/mitansh/.cache/extendscreen-build/screen/packages/*.deb /mnt/c/Code/ExtendScreen/ExtendScreen-1.0.7.deb"
```

---

## How to Upload & Install on the iPad (iFile)

### 1. Enable Web Server in iFile
1. Open **iFile** on the iPad.
2. Tap the **Wi-Fi icon** (center button on the bottom toolbar).
3. iFile will display: `Web Server on: http://<iPad-IP>:10000/` (for example `http://192.168.1.50:10000`).

### 2. Upload the Package via cURL
Run from your PC:
```powershell
curl.exe -s -m 120 -F "file=@C:\Code\ExtendScreen\ExtendScreen-1.0.7.deb" http://<iPad-IP>:10000/upload
```

### 3. Install on Device
1. Tap **Done** on the iPad to exit the iFile web server view.
2. Locate `ExtendScreen-1.0.7.deb` in the current folder.
3. Tap the file and select **Installer**.
4. Confirm it finishes with **`Result Code: 0`**.
5. Respring or restart if requested, then find **Extend Screen** on your Home Screen.

---

## Running the Host

### USB Cable Mode (Recommended)

1. Open **Extend Screen** on the iPad.
2. Tap **Settings**, select **Connection mode: USB cable**, and tap **Save**.
3. Plug the iPad into your PC with a USB cable (unlock the iPad and tap **Trust** if prompted).
4. Run:
   ```cmd
   start-cable-host.cmd
   ```
   Or directly with Python:
   ```cmd
   python host-tools\screen-host.py --cable --extend --fps 60
   ```
5. Windows automatically switches to extended desktop mode and streams to the iPad. Press `Ctrl+C` to stop (Windows automatically reverts to single-screen mode).

### Wi-Fi Mode

1. Open **Extend Screen** on the iPad.
2. In **Settings**, select **Connection mode: Wi-Fi**, and set Server URL to `http://<PC-IP>:8644`.
3. On the PC, run:
   ```cmd
   start-extend-host.cmd
   ```
4. Enter the pair code shown on the PC host into the iPad Settings, tap **Pair with host**, then tap **Save**.

---

## Diagnostics & Troubleshooting

### Reading On-Device Logs
The app writes diagnostic logs directly to `/tmp/ExtendScreen.log` with millisecond precision and call-stack traces on disconnection.

To read the log:
- **In iFile**: Navigate to `/` -> `tmp` -> `ExtendScreen.log`.
- **Via cURL (when iFile web server is on)**:
  ```powershell
  curl.exe -s http://<iPad-IP>:10000/../../tmp/ExtendScreen.log
  ```

### Checking USB Connection (`usbmuxd`)
To verify the PC sees the iPad over USB:
```powershell
python -c "import sys; sys.path.insert(0, 'host-tools'); from importlib.machinery import SourceFileLoader; sh = SourceFileLoader('sh', 'host-tools/screen-host.py').load_module(); s = sh.usbmux_connect('127.0.0.1:27015', 8765); print('Connected!'); s.close()"
```

### Common Issues
- **`[WinError 10054]` / Disconnections**: Usually caused by memory pressure or app resignation. In 1.0.7, memory allocation per frame is isolated in `@autoreleasepool` and `appResignedActive` is guarded during cable mode.
- **Port 8765 refused**: Ensure the app is running in foreground on the iPad and Connection Mode is set to **USB cable**.
- **Virtual Monitor missing**: Keep the MTT VDD Control utility running minimized to ensure the virtual display driver adapter is active.
