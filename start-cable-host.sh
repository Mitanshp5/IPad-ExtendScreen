#!/usr/bin/env bash
# ExtendScreen: Start USB Cable Streaming Host (Linux)
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "========================================================"
echo " Starting ExtendScreen Host in USB Cable Mode (Linux)"
echo "========================================================"
echo "Prerequisites:"
echo "  - Apple usbmuxd daemon running (sudo apt install usbmuxd libimobiledevice-utils)"
echo "  - Connect iPad via USB cable, unlock it, and tap 'Trust' if prompted"
echo
echo "On your iPad:"
echo "  1. Open Extend Screen -> Settings."
echo "  2. Select Connection Mode: USB cable, then tap Save."
echo "========================================================"
echo

exec python3 host-tools/screen-host.py --cable --fps 45 "$@"

