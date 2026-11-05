# ExtendScreen Architecture & Protocol Specification

ExtendScreen allows using an iPad mini (1st Generation, iOS 9.3.5) as an extended second display for Windows 10/11 with bidirectional input forwarding.

---

## 1. System Topology

```
+---------------------------------------------------------------------------------+
|                                WINDOWS PC HOST                                  |
|                                                                                 |
|  +------------------------+        +-----------------------------------------+  |
|  | Windows Desktop / VDD  |        | host-tools/screen-host.py               |  |
|  | Virtual Display Driver | <====> | - DisplayTopology (DisplaySwitch.exe)   |  |
|  | (1024x768 @ (2880, 0))  |        | - FFmpeg Capture (ddagrab / gdigrab)    |  |
|  +------------------------+        | - Win32 SendInput (Mouse/Keyboard)      |  |
|                                    +-----------------------------------------+  |
|                                              |                 |                |
|               (USB Mode)                     |                 | (Wi-Fi Mode)   |
|                   v                          |                 v                |
|  +-------------------------------------+     |   +---------------------------+  |
|  | Apple Mobile Device usbmuxd         |     |   | HTTP Server               |  |
|  | Listening on 127.0.0.1:27015        |     |   | 192.168.0.108:8644        |  |
|  +-------------------------------------+     |   +---------------------------+  |
+-------------------|--------------------------+-----------------|----------------+
                    | (USB Lightning Cable)                      | (LAN Wi-Fi)
                    v                                            v
+---------------------------------------------------------------------------------+
|                               IPAD (iOS 9.3.5)                                  |
|                                                                                 |
|  +---------------------------------------------------------------------------+  |
|  | ExtendScreen.app (Rootful Jailbreak)                                      |  |
|  |                                                                           |  |
|  |  [USB Transport]                     [Wi-Fi Transport]                    |  |
|  |  PSCableServer                        PSStreamClient                      |  |
|  |  Listens: 127.0.0.1:8765              Connects: http://192.168.0.108:8644 |  |
|  |                                                                           |  |
|  |  [View & Input Controller]                                                |  |
|  |  PSScreenViewController                                                   |  |
|  |  - ImageIO & CoreGraphics JPEG decompression                              |  |
|  |  - Auto-hiding HUD & Status Bar via PSNavigationController                |  |
|  |  - Touch Gestures (tap, right click, pan/drag)                            |  |
|  |  - Forwarding to PSCableServer / PSInputClient                            |  |
|  |                                                                           |  |
|  |  (Verbose diagnostics & PSLogger are maintained on the `development` branch)|
|  +---------------------------------------------------------------------------+  |
|                                                                                 |
|  +---------------------------------------------------------------------------+  |
|  | iFile Web Server (for package install & log inspection)                   |  |
|  | Listening on: http://192.168.0.109:10000                                  |  |
|  +---------------------------------------------------------------------------+  |
+---------------------------------------------------------------------------------+
```

---

## 2. Network Endpoints & Ports Reference

| Entity | Protocol / Transport | Address / Port | Description |
|---|---|---|---|
| **iPad iFile Web Server** | HTTP | `http://192.168.0.109:10000` | Used for uploading `.deb` installers and reading `/tmp/ExtendScreen.log`. |
| **PC usbmuxd Daemon** | Apple USBMUX | `127.0.0.1:27015` | Apple Mobile Device Service multiplexer on PC. |
| **iPad USB Cable Listener** | TCP (Local loopback) | `127.0.0.1:8765` | Reached through usbmuxd tunnel over the USB cable. |
| **PC Wi-Fi Streaming Host** | HTTP MJPEG | `http://192.168.0.108:8644` | Wi-Fi streaming server (serves `/stream`, handles `/pair` & `/input`). |

---

## 3. Communication Protocols

### A. USB Cable Protocol (`ESP1`)

1. **Connection**:
   - The iPad app listens on `127.0.0.1:8765`.
   - The PC host connects through `usbmuxd` (port 27015) requesting a tunnel to device port 8765.
   - Handshake requires no password or credentials since it is over a private physical USB cable.

2. **Handshake**:
   - PC sends: `ESP1\n`
   - PC sends JSON line:
     ```json
     {"type": "hello", "version": 1, "width": 1024, "height": 768, "fps": 60, "input": true}
     ```
   - iPad replies JSON line:
     ```json
     {"type": "welcome", "input": true, "version": "1.0.7"}
     ```

3. **Frame Stream (PC -> iPad)**:
   - Binary framed stream:
     ```
     [4 bytes big-endian length N] [N bytes raw JPEG payload]
     ```
   - Max frame size allowed: `PSCableMaximumFrame = 4 * 1024 * 1024` (4 MB).
   - In iPad's `PSCableServer.m`, each incoming frame is decoded inside an isolated `@autoreleasepool` to avoid memory accumulation on 512 MB devices.

4. **Input Forwarding (iPad -> PC)**:
   - Sent asynchronously over the same TCP socket as newline-delimited JSON:
     - Tap: `{"type": "tap", "x": 0.521, "y": 0.412}`
     - Right Click: `{"type": "rclick", "x": 0.521, "y": 0.412}`
     - Mouse Down: `{"type": "down", "x": 0.521, "y": 0.412}`
     - Mouse Move: `{"type": "move", "x": 0.525, "y": 0.415}`
     - Mouse Up: `{"type": "up", "x": 0.525, "y": 0.415}`
     - Scroll: `{"type": "scroll", "dy": -6}`
     - Key Input: `{"type": "key", "key": "a"}` or `{"type": "backspace"}`

### B. Wi-Fi Protocol (HTTP MJPEG)

- **Pairing**: Optional 8-digit pair code exchange (`/pair`).
- **Streaming**: Standard HTTP multipart stream (`multipart/x-mixed-replace; boundary=--frame`).
- **Input**: HTTP POST requests to `/input` with HMAC authentication token.

---

## 4. Key iOS Subsystems

- **`PSCableServer`**: Manages socket lifecycle, protocol handshake, packet framing, and delegating frames to the view controller.
- **`PSScreenViewController`**:
  - Image decoding: Uses ImageIO (`CGImageSourceCreateWithData`) off the main thread (`decodeQueue`), then presents via `UIImageView`.
  - Frame dropping: Automatically drops intermediate frames if the main thread or decoder is busy (`decoding` flag and `pendingFrame` single-slot buffer).
  - Interruption Handling: Protects USB link during notifications/banners via `appResignedActive` guard (`if (self.cableMode) return;`).
- **`PSNavigationController`**:
  - Subclasses `UINavigationController` to override `childViewControllerForStatusBarHidden` returning `topViewController`.
  - Auto-hides top status bar (battery/time/Wi-Fi) after 4.5 seconds alongside chrome buttons (`setNeedsStatusBarAppearanceUpdate`).

> **Note on Diagnostics (`development` branch)**:
> In the `development` branch, `PSLogger` is included for file logging to `/tmp/ExtendScreen.log` and ASL console with full callstack symbols on connection drops. In the `main` branch, logging overhead is stripped for optimal performance and memory efficiency.

---

## 5. PC Host Subsystems (`host-tools/screen-host.py`)

- **`DisplayTopology`**:
  - Automatically runs `DisplaySwitch.exe /extend` when the iPad connects.
  - Automatically runs `DisplaySwitch.exe /internal` when the iPad disconnects (returning PC to single monitor).
  - Coordinates with the MTT Virtual Display Driver (`mttvdd`).
- **`FallbackFrameSource` / `FFmpegFrameSource`**:
  - Desktop capture using Desktop Duplication API (`ddagrab=output_idx=N`).
  - Automatically detects monitor region and maps to DirectX output index.
  - Gracefully falls back to `gdigrab` if Desktop Duplication is unavailable or encounters an access error.
  - Encodes frames directly to MJPEG (`-c:v mjpeg -q:v 5 -pix_fmt yuvj420p`).

