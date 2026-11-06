#!/usr/bin/env bash
# ExtendScreen: Start System Tray Host Controller (Linux)
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "Starting ExtendScreen System Tray Controller..."
exec python3 host-tools/tray-host.py "$@"
