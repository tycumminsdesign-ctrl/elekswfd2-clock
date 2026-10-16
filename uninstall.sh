#!/bin/bash
# Remove the EleksWFD 2 background agent. Leaves this folder in place.
set -euo pipefail
LABEL="com.elekswfd2.clock"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl unload "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
pkill -f 'mac/wfd2.py' 2>/dev/null || true
pkill -f 'wfd2-daemon' 2>/dev/null || true
echo "Removed. The clock will go dark. You can delete this folder."
