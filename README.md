# EleksWFD 2 — Mac-driven clock

Custom firmware and Mac software that turn an [EleksWFD 2](https://elekstube.com/products/elekswfd-2)
VFD-style clock into a live readout of **whichever Mac is plugged into it**:
CPU / GPU / RAM meters, 12- or 24-hour time, the date on the top 14-segment row, a
weather condition label, mic-reactive VU bars, and the ELEKSMAKER logo.

The device never touches the network. The stock firmware is replaced with a
tiny **dumb USB→panel bridge**, and *all* rendering happens on the Mac, so the
clock has no WiFi credentials, contacts nothing, and goes dark if the driver
stops. Whichever Mac holds the cable owns the display — dock a different machine
and it takes over automatically.

> Reverse-engineered and built from scratch for one specific unit (ESP32-S3,
> HT16K33-family LED controller at I²C 0x73). The full LED map lives in
> [`mac/panel.py`](mac/panel.py). Your unit's wiring may differ.

## ⚠️ Back up your stock firmware BEFORE you flash anything

This is the one step you cannot skip and cannot undo. After a full power loss the
LED controller comes back in a "cold" state that the bridge firmware **cannot**
wake on its own — only the stock EleksMaker firmware's boot sequence brings it
up. So the project needs a copy of the stock firmware to recover from a power
cycle, and **the only time you can get it is before you overwrite it.**

Once the bridge is flashed, the stock firmware is gone from the device and
**there is no way to get it back** — it is not included here (it is EleksMaker's
proprietary firmware and a full dump contains the device's saved WiFi password),
and you cannot dump it from a device that no longer has it.

So, with the clock plugged in and **before running anything else:**

```bash
# find your port (usually /dev/cu.usbmodemXXXX)
ls /dev/cu.usbmodem*
# get esptool into this folder's Python environment (install.sh reuses it)
python3 -m venv .venv && .venv/bin/python -m pip install esptool
# dump the full 8 MB flash and keep this file safe forever
.venv/bin/python -m esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
  read_flash 0 0x800000 wfd2-stock-fullflash-8MB.bin
```

Use `read_flash` (underscore), not `read-flash`: macOS's built-in Python 3.9 can
only install esptool v4, which doesn't accept the dashed v5 spelling. The
underscore form works on both.

Keep `wfd2-stock-fullflash-8MB.bin` in this folder. `wfd2-recover` uses it to
re-initialize the panel after any power loss. Without it, a cold boot leaves the
display dark until you re-flash stock from a backup you don't have.

## Install (macOS)

With the clock plugged into this Mac:

```bash
bash install.sh
```

It builds a self-contained Python environment in this folder and installs a
per-user launch agent — no admin password, nothing system-wide. It starts now
and at every login. When macOS asks whether **WFD2 Clock** may use the microphone
(for the VU bars), click **Allow**. Remove it all with `bash uninstall.sh`.

### VU bars and the microphone

The bars follow whatever mic is selected in macOS Sound settings, switching within
a couple of seconds when you change it, and calibrate themselves to that mic's
room noise. Muting the mic drops them to zero. Set `WFD2_MIC` to a device name to
pin one mic instead. Sensitivity lives at the top of `mac/audio.py` (`DB_EMPTY`,
`DB_FULL`, `LEVEL_CURVE`, `GAIN`).

The agent runs through a tiny app bundle, `WFD2Clock.app`, which `install.sh`
builds from `wfd2-launcher.c`. A bare Python started by launchd can't ask for
microphone access, so macOS would silently feed it all-zero audio.

### Weather — bring your own key

Weather is optional and off until you add a free
[OpenWeatherMap](https://openweathermap.org/api) key. `install.sh` prompts for
it and your lat/long and wires them into the launch agent (they never go in this
repo). To set them by hand instead:

```bash
export WFD2_OWM_KEY="your_key"
export WFD2_LAT="51.5074"
export WFD2_LON="-0.1278"
```

### Clock face

The time is **24-hour** by default. For a 12-hour clock, answer `12` when
`install.sh` asks, or set `WFD2_HOURS=12` (re-run `install.sh` to apply).

### Claude usage on the dot matrix

The 7x7 dot grid in the middle fills from the bottom-left as you use your Claude
plan's **5-hour session**: blank means the session is fresh, full means you've
hit the session limit. It refreshes every 5 minutes, and because the session
window resets every 5 hours you'll see it drain back out regularly (the weekly
limit is read too, for `--diagnose`, but isn't shown). It follows the account signed in to **Claude
Code** on the Mac the clock is plugged into, so a work Mac shows work usage and
a personal Mac shows personal usage.

One-time setup on each Mac:

```bash
curl -fsSL https://claude.ai/install.sh | bash   # Claude Code CLI, if not installed
~/.local/bin/claude                              # then /login with your Claude account
.venv/bin/python mac/claude_usage.py             # check: prints your usage
```

The Claude desktop app keeps its login where other programs can't read it, so
the CLI login is what the clock uses. If macOS asks whether `security` may use
"Claude Code-credentials", click **Always Allow**. The login is read locally and
sent only to `api.anthropic.com`; it is never printed, logged or stored
elsewhere. The usage endpoint isn't a published API and may change — if it
stops answering, the grid just stays blank. `--diagnose` says which step fails.

The fill is animated: each new dot drops from the top of its column and lands on
the stack like a Tetris piece, and when a usage window resets the dots lift off
the top and rise back out. Speed is `RELEASE_FRAMES` in `mac/grid_anim.py`
(frames between dots; the clock renders at 10 fps).

Two more things to know:

- The clock keeps the CLI login fresh with a tiny Haiku request about every
  8 hours (around 900 tokens each), because `claude auth status` alone does not
  renew an expired login. It renews shortly before the login expires, with a
  second try if the first fails. Renewal and Keychain problems are logged to
  `/tmp/wfd2-daemon.log`.
- `./wfd2-usage off` turns the grid off completely: no login reads, no renewals,
  no usage requests. `./wfd2-usage on` turns it back on. The switch is a file
  (`claude-usage.off`), so it persists across restarts; `./wfd2-usage status`
  exits 0 when on and 1 when off. The check command `mac/claude_usage.py` (and
  `--diagnose`) respects the switch too, so turn it on first.

## Flashing the firmware

The clock runs the bridge firmware in [`firmware/`](firmware/). A pre-merged
image is at `firmware/artifacts/bridge-merged.bin`, or build with PlatformIO.

**Back up your stock firmware first** — it's the only thing that can re-init the
LED controller after a full power loss, and this repo does **not** ship one
(the vendor's image is proprietary and a full dump contains the device's saved
WiFi credentials). Make your own before flashing anything:

```bash
.venv/bin/python -m esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
  read_flash 0 0x800000 wfd2-stock-fullflash-8MB.bin
```

With that file present, `wfd2-recover` (run automatically by the daemon on a
power cycle) re-initializes the panel and reflashes the bridge.

## Layout

- `mac/` — the driver. `wfd2.py` streams frames; `panel.py` is the LED map and
  framebuffer; `wfd2_stats.py` / `weather.py` / `audio.py` feed it. The `*test`,
  `sweep`, `hold`, `catlab`, `catplay`, `mapf` files are the reverse-engineering
  tools used to map the panel.
- `firmware/` — the ESP32-S3 bridge firmware (dumb USB→panel pipe) + merged image.
- `mapping/` — notes and data from mapping each LED.
- `wfd2-daemon` / `wfd2-recover` — the supervisor and cold-boot recovery.

The dot-matrix cat animation is a work in progress (grid mapped in
`mapping/dotmatrix_rough.json`, design tools in `mac/catplay.py`).

## License

MIT — see [LICENSE](LICENSE).
