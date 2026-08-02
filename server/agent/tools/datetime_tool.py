#!/usr/bin/env python3
"""datetime tool — current date/time in any IANA timezone (stdlib zoneinfo).

Deterministic: the clock comes from the server, not the LLM (which has no real
sense of "now"). Handy for "what time is it in Tokyo?" style questions.
"""
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo, available_timezones
    _HAVE_ZONEINFO = True
except Exception:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    _HAVE_ZONEINFO = False

from ..registry import Tool

# a few friendly aliases -> IANA names
_ALIASES = {
    "utc": "UTC", "gmt": "UTC",
    "kyiv": "Europe/Kyiv", "kiev": "Europe/Kyiv", "ukraine": "Europe/Kyiv",
    "london": "Europe/London", "uk": "Europe/London",
    "new york": "America/New_York", "nyc": "America/New_York", "est": "America/New_York",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles", "pst": "America/Los_Angeles",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "berlin": "Europe/Berlin", "paris": "Europe/Paris",
    "sydney": "Australia/Sydney", "india": "Asia/Kolkata", "delhi": "Asia/Kolkata",
}


def _resolve(tz):
    tz = (tz or "UTC").strip()
    low = tz.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    return tz


def _run(args):
    tz_in = args.get("timezone") or "UTC"
    name = _resolve(tz_in)
    if not _HAVE_ZONEINFO:
        now = datetime.now(timezone.utc)
        return {"summary": "%s UTC (zoneinfo unavailable)" % now.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        tzinfo = ZoneInfo(name)
    except Exception:
        return {"ok": False,
                "summary": 'unknown timezone "%s" — use an IANA name like Europe/Kyiv' % tz_in}
    now = datetime.now(tzinfo)
    offset = now.strftime("%z")
    offset = "%s:%s" % (offset[:3], offset[3:]) if offset else ""
    pretty = now.strftime("%A, %d %B %Y, %H:%M:%S")
    return {
        "summary": "%s in %s (UTC%s)" % (pretty, name, offset),
        "iso": now.isoformat(),
        "timezone": name,
    }


TOOL = Tool(
    name="datetime",
    description="Get the current date and time in a given timezone (IANA name or common city).",
    args={"timezone": "IANA zone or city, e.g. Europe/Kyiv, Asia/Tokyo, UTC (default UTC)"},
    examples=['{"tool":"datetime","args":{"timezone":"Asia/Tokyo"}}'],
    run=_run,
    terminal=True,
)
