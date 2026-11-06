#!/usr/bin/env bash
# ExtendScreen: Start Wi-Fi Streaming Host (Linux)
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "========================================================"
echo " Starting ExtendScreen Host in Wi-Fi Mode (Linux)"
echo "========================================================"
echo "On your iPad:"
echo "  1. Open Extend Screen -> Settings (or tap the camera icon)."
echo "  2. Point iPad camera at the PC pairing QR code."
echo "     (Or enter the 8-digit code manually under Wi-Fi mode)."
echo "========================================================"
echo

exec python3 host-tools/screen-host.py --fps 30 "$@"

