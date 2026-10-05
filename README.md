# EleksWFD 2 — Mac-driven clock

Custom firmware and Mac software that turn an [EleksWFD 2](https://elekstube.com/products/elekswfd-2)
VFD-style clock into a live readout of **whichever Mac is plugged into it**:
CPU / GPU / RAM meters, 12-hour time, the date on the top 14-segment row, a
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
# dump the full 8 MB flash and keep this file safe forever
python3 -m esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
  read-flash 0 0x800000 wfd2-stock-fullflash-8MB.bin
```

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
and at every login. When macOS asks for microphone access (for the VU bars),
click **Allow**. Remove it all with `bash uninstall.sh`.

### Weather — bring your own key

Weather is optional and off until you add a free
[OpenWeatherMap](https://openweathermap.org/api) key. `install.sh` prompts for
it and your lat/long and wires them into the launch agent (they never go in this
repo). To set them by hand instead:

```bash
export WFD2_OWM_KEY="your_key"
export WFD2_LAT="40.56"
export WFD2_LON="-111.84"
```

## Flashing the firmware

The clock runs the bridge firmware in [`firmware/`](firmware/). A pre-merged
image is at `firmware/artifacts/bridge-merged.bin`, or build with PlatformIO.

**Back up your stock firmware first** — it's the only thing that can re-init the
LED controller after a full power loss, and this repo does **not** ship one
(the vendor's image is proprietary and a full dump contains the device's saved
WiFi credentials). Make your own before flashing anything:

```bash
.venv/bin/python -m esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
  read-flash 0 0x800000 wfd2-stock-fullflash-8MB.bin
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
