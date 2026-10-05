#!/usr/bin/env python3
"""Fetch current weather for the clock from the Mac (no cloud dependency on the
device itself -- the point of the whole rebuild is that the display gets its
data from here, not from the vendor's servers).

Uses OpenWeatherMap's free current-weather endpoint. BRING YOUR OWN KEY: set
these environment variables before running (install.sh will prompt you and wire
them into the launch agent for you):

    WFD2_OWM_KEY   your OpenWeatherMap API key (free at openweathermap.org/api)
    WFD2_LAT       your latitude,  e.g.  40.56
    WFD2_LON       your longitude, e.g. -111.84
    WFD2_UNITS     imperial (default, degF) or metric (degC)

If the key or location is not set, weather is simply disabled: the SUNNY/CLOUD/
RAINS label stays off and everything else runs normally.

The reading is fetched on a background thread at a slow cadence (weather does not
change second to second, and the free tier is rate-limited), so the render loop
just reads whatever the latest value is.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

OWM_KEY = os.environ.get("WFD2_OWM_KEY", "")
LAT = os.environ.get("WFD2_LAT", "")
LON = os.environ.get("WFD2_LON", "")
UNITS = os.environ.get("WFD2_UNITS", "imperial")   # imperial=degF, metric=degC
POLL_SECONDS = 600.0                                # 10 min

CONFIGURED = bool(OWM_KEY and LAT and LON)


@dataclass
class Weather:
    temp_f: float | None       # in the configured units, despite the name
    condition: str | None      # one of "sunny" / "cloud" / "rains" / None
    ok: bool


def _map_condition(owm_main: str, owm_id: int) -> str:
    """Collapse OpenWeatherMap's many codes onto the panel's three words."""
    m = owm_main.lower()
    if owm_id < 700 or m in ("rain", "drizzle", "thunderstorm", "snow"):
        return "rains"
    if m in ("clear",):
        return "sunny"
    return "cloud"   # clouds, mist, fog, haze, etc.


def fetch() -> Weather:
    if not CONFIGURED:
        return Weather(temp_f=None, condition=None, ok=False)
    url = "https://api.openweathermap.org/data/2.5/weather?" + urllib.parse.urlencode(
        {"lat": LAT, "lon": LON, "appid": OWM_KEY, "units": UNITS}
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.load(r)
        w = d["weather"][0]
        return Weather(
            temp_f=float(d["main"]["temp"]),
            condition=_map_condition(w.get("main", ""), int(w.get("id", 800))),
            ok=True,
        )
    except Exception:
        return Weather(temp_f=None, condition=None, ok=False)


class WeatherService(threading.Thread):
    """Polls weather in the background; render loop reads `.latest`.

    When no key/location is configured the thread exits immediately and `.latest`
    stays disabled, so weather just never lights -- no errors, no retries.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.latest = Weather(None, None, False)
        self._stop = threading.Event()

    def run(self) -> None:
        if not CONFIGURED:
            print("weather: WFD2_OWM_KEY / WFD2_LAT / WFD2_LON not set -- "
                  "weather disabled (see mac/weather.py).", flush=True)
            return
        while not self._stop.is_set():
            w = fetch()
            if w.ok:
                self.latest = w
            self._stop.wait(POLL_SECONDS if w.ok else 30.0)

    def stop(self) -> None:
        self._stop.set()


if __name__ == "__main__":
    if not CONFIGURED:
        print("weather disabled: set WFD2_OWM_KEY, WFD2_LAT, WFD2_LON first.")
    else:
        w = fetch()
        print(f"temp={w.temp_f:.0f}  condition={w.condition}" if w.ok
              else "weather fetch failed (check your key/location)")
