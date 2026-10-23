#!/usr/bin/env python3
"""How much of this Mac's Claude plan usage is used, for the dot-matrix gauge.

Reads the login Claude Code keeps in the macOS Keychain ("Claude Code-credentials")
and asks Anthropic's usage endpoint -- the same one Claude Code's /usage reads --
for the 5-hour session and weekly utilization. The gauge shows the 5-hour
session only; the weekly figure is kept for --diagnose. So the clock shows whichever
account is signed in to Claude Code on the Mac it's plugged into: work usage on a
work Mac, personal usage on a personal one.

The login is only ever sent to api.anthropic.com. It is never printed, logged or
written anywhere. The endpoint is not a published API and may change; if it
fails, the gauge simply goes blank.

The first read may show a Keychain prompt for `security`: click Always Allow so
the background agent can keep reading it.

    python mac/claude_usage.py              # print current usage (never the login)
    python mac/claude_usage.py --diagnose   # say which step fails
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

KEYCHAIN_SERVICE = "Claude Code-credentials"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
POLL_SECONDS = 300.0          # 5 min; usage moves slowly
RETRY_SECONDS = 60.0
RENEW_MARGIN_S = 600.0        # renew the login when it has under 10 min left
RENEW_MIN_INTERVAL_S = 1800.0 # never attempt a renewal more than every 30 min,
                              # so a persistent failure can't loop requests
CHECK_SECONDS = 5.0           # how often the off switch is noticed

# Off switch: while this file exists the gauge is off -- no Keychain reads, no
# renewals, no usage requests, grid blank. `./wfd2-usage off|on|status`.
OFF_FLAG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "claude-usage.off")


def is_off() -> bool:
    return os.path.exists(OFF_FLAG)


@dataclass
class Usage:
    session: float | None     # 5-hour window, 0..1 used
    weekly: float | None      # 7-day window, 0..1 used
    ok: bool

    @property
    def worst(self) -> float | None:
        """The limit closest to running out (informational; the gauge shows .session)."""
        vals = [v for v in (self.session, self.weekly) if v is not None]
        return max(vals) if vals else None


def _read_login() -> dict:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return {}
        return json.loads(out.stdout).get("claudeAiOauth", {}) or {}
    except Exception:
        return {}


def _claude_cli() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    default = os.path.expanduser("~/.local/bin/claude")
    return default if os.path.exists(default) else None


def _renew() -> None:
    """Have Claude Code renew its login (it lasts ~8 h). We never write the
    Keychain ourselves -- the CLI owns that item, so it does the refresh.

    `claude auth status` does NOT renew it (it reports logged-in off the
    refresh token and leaves the expired access token in place). Only a real
    request makes the CLI refresh, so send the smallest one there is: one
    Haiku turn, ~3 a day, from a neutral folder, with Claude Code's own system
    prompt, tools and MCP servers stripped (~900 tokens instead of ~47k) and no
    session saved to ~/.claude/projects."""
    cli = _claude_cli()
    if cli:
        try:
            subprocess.run(
                [cli, "-p", "Reply with OK", "--model", "haiku", "--max-turns", "1",
                 "--system-prompt", "Reply with OK.", "--tools", "",
                 "--strict-mcp-config", "--no-session-persistence"],
                capture_output=True, timeout=120, cwd=tempfile.gettempdir())
        except Exception:
            pass


_last_renew = 0.0


def _access_token() -> str | None:
    global _last_renew
    oauth = _read_login()
    expires = oauth.get("expiresAt")
    if expires and expires / 1000 < time.time() + RENEW_MARGIN_S:
        if time.time() - _last_renew < RENEW_MIN_INTERVAL_S:
            return None if expires / 1000 < time.time() else oauth.get("accessToken")
        _last_renew = time.time()
        _renew()
        oauth = _read_login()
        expires = oauth.get("expiresAt")
        if expires and expires / 1000 < time.time():
            return None
    return oauth.get("accessToken")


def _fraction(window: dict | None) -> float | None:
    if not isinstance(window, dict) or window.get("utilization") is None:
        return None
    return max(0.0, min(1.0, float(window["utilization"]) / 100.0))


def fetch() -> Usage:
    token = _access_token()
    if not token:
        return Usage(None, None, False)
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        u = Usage(_fraction(d.get("five_hour")), _fraction(d.get("seven_day")), True)
        return u if u.session is not None else Usage(None, None, False)
    except Exception:
        return Usage(None, None, False)


class UsageService(threading.Thread):
    """Polls usage in the background; the render loop reads `.latest`."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.latest = Usage(None, None, False)
        self._stop = threading.Event()

    def run(self) -> None:
        next_fetch = 0.0
        while not self._stop.is_set():
            if is_off():
                self.latest = Usage(None, None, False)
                next_fetch = 0.0            # fetch straight away once switched on
            elif time.time() >= next_fetch:
                u = fetch()
                self.latest = u
                next_fetch = time.time() + (POLL_SECONDS if u.ok else RETRY_SECONDS)
            self._stop.wait(CHECK_SECONDS)

    def stop(self) -> None:
        self._stop.set()


def diagnose() -> None:
    """Say which step fails, without ever printing the login itself."""
    out = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        print(f"1. Keychain read FAILED (security exit {out.returncode}): {out.stderr.strip()[:200]}")
        return
    print("1. Keychain read OK")
    try:
        data = json.loads(out.stdout)
    except Exception:
        print("2. Keychain item isn't JSON (unexpected format)")
        return
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        print(f"2. No claudeAiOauth entry; top-level keys: {sorted(data)}")
        return
    print(f"2. Login fields present: {sorted(oauth)}")
    expires = oauth.get("expiresAt")
    if expires:
        hours = (expires / 1000 - time.time()) / 3600
        print(f"3. Login {'EXPIRED %.1f h ago' % -hours if hours < 0 else 'valid for %.1f h' % hours}")
    if not oauth.get("accessToken"):
        print("4. No access token in the login")
        return
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {oauth['accessToken']}",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        print(f"4. Usage API OK; top-level keys: {sorted(d)}")
        for k in ("five_hour", "seven_day"):
            print(f"   {k}: {d.get(k)}")
    except urllib.error.HTTPError as e:
        print(f"4. Usage API HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    except Exception as e:
        print(f"4. Usage API request failed: {e!r}")


if __name__ == "__main__":
    import sys
    if "--diagnose" in sys.argv:
        diagnose()
        raise SystemExit
    u = fetch()
    if not u.ok:
        print("Couldn't read Claude usage. Is Claude Code signed in on this Mac, "
              "and did you allow the Keychain prompt?")
    else:
        pct = lambda v: "--" if v is None else f"{v * 100:.0f}% used"
        print(f"5-hour session: {pct(u.session)}")
        print(f"Weekly:         {pct(u.weekly)}")
        print(f"Grid shows the 5-hour session: {pct(u.session)}")
