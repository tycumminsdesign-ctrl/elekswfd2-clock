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

Off switch: `./wfd2-usage off` creates claude-usage.off (a file, so it survives
restarts); while it exists nothing here reads the Keychain, renews or fetches,
and the grid is blank. `./wfd2-usage on` removes it. The commands below respect
the switch too.

Problems (a renewal that fails, a Keychain read that is refused) are printed
once, and the daemon forwards them to /tmp/wfd2-daemon.log.

    python mac/claude_usage.py              # print current usage (never the login)
    python mac/claude_usage.py --diagnose   # say which step fails
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
CHECK_SECONDS = 5.0           # how often the off switch is noticed

# Renewal timing. The login lasts ~8 h. Start renewing RENEW_MARGIN_S before it
# expires, and never attempt more than once per RENEW_MIN_INTERVAL_S so a
# persistent failure can't loop requests. The margin is deliberately LARGER
# than the interval: if the first attempt fails there is still time for a
# second one before the login actually expires, so one hiccup doesn't blank the
# grid.
RENEW_MARGIN_S = 2400.0       # 40 min before expiry
RENEW_MIN_INTERVAL_S = 1800.0 # at most one attempt per 30 min
assert RENEW_MARGIN_S > RENEW_MIN_INTERVAL_S, "margin must leave room for a retry"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Off switch: while this file exists the gauge is off -- no Keychain reads, no
# renewals, no usage requests, grid blank. `./wfd2-usage off|on|status`.
OFF_FLAG = os.path.join(_ROOT, "claude-usage.off")
# When the last renewal was attempted, kept on disk so the 30-minute throttle
# survives the daemon relaunching the clock (a crash loop could otherwise spend
# a request per restart). Gitignored.
RENEW_STAMP = os.path.join(_ROOT, ".claude-usage-last-renew")


def is_off() -> bool:
    return os.path.exists(OFF_FLAG)


def _log(msg: str) -> None:
    """One line to stdout; the daemon forwards the clock's output to its log."""
    print(f"claude usage: {msg}", flush=True)


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


_login_failing = False        # so a refused Keychain read is reported once, not every poll


def _read_login() -> dict:
    global _login_failing
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            if not _login_failing:
                _login_failing = True
                _log(f"Keychain read refused (security exit {out.returncode}: "
                     f"{out.stderr.strip()[:160]}). If this Mac never granted access, run "
                     f"`.venv/bin/python mac/claude_usage.py --diagnose` in Terminal and "
                     f"click Always Allow.")
            return {}
        login = json.loads(out.stdout).get("claudeAiOauth", {}) or {}
        if _login_failing:
            _login_failing = False
            _log("Keychain read OK again.")
        return login
    except Exception as exc:
        if not _login_failing:
            _login_failing = True
            _log(f"Keychain read failed: {exc!r}")
        return {}


def _claude_cli() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    default = os.path.expanduser("~/.local/bin/claude")
    return default if os.path.exists(default) else None


def _renew() -> bool:
    """Have Claude Code renew its login (it lasts ~8 h). We never write the
    Keychain ourselves -- the CLI owns that item, so it does the refresh.

    `claude auth status` does NOT renew it (it reports logged-in off the
    refresh token and leaves the expired access token in place). Only a real
    request makes the CLI refresh, so send the smallest one there is: one
    Haiku turn, ~3 a day, from a neutral folder, with Claude Code's own system
    prompt, tools and MCP servers stripped (~900 tokens instead of ~47k) and no
    session saved to ~/.claude/projects.

    Returns True if the CLI exited 0. Any failure is logged (exit code and the
    tail of stderr, never the output) so a dead login is diagnosable from the
    daemon log instead of just a blank grid."""
    cli = _claude_cli()
    if not cli:
        _log("cannot renew login: `claude` CLI not found (see README setup)")
        return False
    try:
        out = subprocess.run(
            [cli, "-p", "Reply with OK", "--model", "haiku", "--max-turns", "1",
             "--system-prompt", "Reply with OK.", "--tools", "",
             "--strict-mcp-config", "--no-session-persistence"],
            capture_output=True, text=True, timeout=120, cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        _log("login renewal timed out after 120 s")
        return False
    except Exception as exc:
        _log(f"login renewal failed to run: {exc!r}")
        return False
    if out.returncode != 0:
        _log(f"login renewal failed (claude exit {out.returncode}): {out.stderr.strip()[:200]}")
        return False
    _log("login renewed via a minimal claude request")
    return True


def _load_stamp() -> float:
    try:
        with open(RENEW_STAMP) as f:
            return float(f.read().strip() or 0)
    except Exception:
        return 0.0


_last_renew = _load_stamp()


def _mark_renew() -> None:
    global _last_renew
    _last_renew = time.time()
    try:
        with open(RENEW_STAMP, "w") as f:
            f.write(f"{_last_renew:.0f}\n")
    except Exception:
        pass                  # in-memory throttle still applies


def _access_token() -> str | None:
    oauth = _read_login()
    expires = oauth.get("expiresAt")
    if expires and expires / 1000 < time.time() + RENEW_MARGIN_S:
        if time.time() - _last_renew < RENEW_MIN_INTERVAL_S:
            return None if expires / 1000 < time.time() else oauth.get("accessToken")
        _mark_renew()
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
    ago = time.time() - _last_renew
    print("0. Grid switch: ON; last renewal attempt: "
          + ("never" if _last_renew == 0 else f"{ago / 60:.0f} min ago"))
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


def main(argv: list[str]) -> int:
    if is_off():
        # Honour the off switch here too: "off" promises no login reads at all.
        print("Claude usage grid is OFF (./wfd2-usage on to re-enable). Not reading the login.")
        return 0
    if "--diagnose" in argv:
        diagnose()
        return 0
    u = fetch()
    if not u.ok:
        print("Couldn't read Claude usage. Is Claude Code signed in on this Mac, "
              "and did you allow the Keychain prompt?")
        return 1
    pct = lambda v: "--" if v is None else f"{v * 100:.0f}% used"
    print(f"5-hour session: {pct(u.session)}")
    print(f"Weekly:         {pct(u.weekly)}")
    print(f"Grid shows the 5-hour session: {pct(u.session)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
