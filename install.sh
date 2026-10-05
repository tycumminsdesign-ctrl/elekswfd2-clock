#!/bin/bash
# One-step installer for the EleksWFD 2 clock on this Mac.
# Builds a self-contained virtualenv in this folder and a per-user background
# agent that drives the clock whenever it's plugged in. No admin password needed,
# nothing installed system-wide. Prompts for your own weather key (optional).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.elekswfd2.clock"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "EleksWFD 2 clock -- installing from:"
echo "  $HERE"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed. Run this once, let it finish, then re-run install.sh:"
  echo "    xcode-select --install"
  exit 1
fi

echo "1/4  Creating a self-contained Python environment..."
python3 -m venv "$HERE/.venv"
"$HERE/.venv/bin/python" -m pip install --quiet --upgrade pip
echo "2/4  Installing dependencies (pyserial, psutil, numpy, sounddevice, esptool)..."
"$HERE/.venv/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"

# --- Bring your own weather key (optional) ---
OWM_KEY="${WFD2_OWM_KEY:-}"; LAT="${WFD2_LAT:-}"; LON="${WFD2_LON:-}"; UNITS="${WFD2_UNITS:-imperial}"
if [ -t 0 ] && [ -z "$OWM_KEY" ]; then
  echo
  echo "Weather (optional). Get a free key at https://openweathermap.org/api"
  printf "  OpenWeatherMap API key (blank to skip weather): "; read -r OWM_KEY
  if [ -n "$OWM_KEY" ]; then
    printf "  Latitude  (e.g. 40.56):  "; read -r LAT
    printf "  Longitude (e.g. -111.84): "; read -r LON
    printf "  Units [imperial/metric] (default imperial): "; read -r U; UNITS="${U:-imperial}"
  fi
fi

ENV_BLOCK=""
if [ -n "$OWM_KEY" ] && [ -n "$LAT" ] && [ -n "$LON" ]; then
  ENV_BLOCK=$(cat <<EOF
  <key>EnvironmentVariables</key>
  <dict>
    <key>WFD2_OWM_KEY</key><string>$OWM_KEY</string>
    <key>WFD2_LAT</key><string>$LAT</string>
    <key>WFD2_LON</key><string>$LON</string>
    <key>WFD2_UNITS</key><string>$UNITS</string>
  </dict>
EOF
)
fi

echo "3/4  Installing the background agent..."
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HERE/.venv/bin/python</string>
    <string>$HERE/wfd2-daemon</string>
  </array>
$ENV_BLOCK
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/wfd2-daemon.log</string>
  <key>StandardErrorPath</key><string>/tmp/wfd2-daemon.log</string>
</dict>
</plist>
EOF

chmod +x "$HERE/wfd2-recover" "$HERE/wfd2-daemon" 2>/dev/null || true

echo "4/4  Starting it..."
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo
echo "Done. The clock lights up whenever it's plugged into this Mac."
[ -z "$OWM_KEY" ] && echo "Weather is off (no key set). Re-run install.sh to add one later."
echo
echo "  * First run of the VU bars triggers a microphone prompt -- click Allow."
echo "  * Cold-boot recovery needs your own stock backup at wfd2-stock-fullflash-8MB.bin"
echo "    (see README). Without it the clock still runs; it just can't self-heal a"
echo "    full power loss until re-initialized once."
echo "  * Live log:  tail -f /tmp/wfd2-daemon.log     Remove:  ./uninstall.sh"
