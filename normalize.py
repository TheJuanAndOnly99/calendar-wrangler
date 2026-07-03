"""Normalize an LFX webcal feed into an RFC 5545-conformant .ics.

The feed at https://webcal.prod.itx.linuxfoundation.org/lfx/<sub-id>_sub emits
three patterns that cause Google Calendar to silently drop events:

1. `ATTENDEE;VALUE=TEXT:` with an empty value on every RECURRENCE-ID override.
2. `DTSTAMP`, `CREATED`, `LAST-MODIFIED` with both a `TZID` parameter and a
   `Z` (UTC) suffix on the value (RFC 5545 forbids TZID on these).
3. `RRULE ... UNTIL=` set 100 years in the future, which is technically legal
   but interacts badly with Google Calendar's expansion horizon for subscribed
   feeds.

This script fetches the feed (or reads a local file), strips/rewrites those
patterns, and writes a cleaned .ics.

Usage:
    python normalize.py --url https://webcal.prod.itx.linuxfoundation.org/lfx/<id>_sub \\
        --output Calendar-clean.ics

    # or read a local .ics:
    python normalize.py --input Calendar.ics --output Calendar-clean.ics

    # URL can also come from the LFX_ICS_URL env var.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.request
from typing import Iterable

DEFAULT_UNTIL_CAP = "20280101T000000Z"

_ATTENDEE_EMPTY_RE = re.compile(r"^ATTENDEE;VALUE=TEXT:\s*$")
_TIMESTAMP_TZID_RE = re.compile(
    r"^(DTSTAMP|CREATED|LAST-MODIFIED);TZID=[^:]+:"
)
_UNTIL_RE = re.compile(r"UNTIL=(\d{8}T\d{6}Z?)")


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "lfx-ics-normalizer/1.0 (+https://github.com/)",
            "Accept": "text/calendar, */*;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def read_local(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def strip_preamble(text: str) -> str:
    """Drop anything before the first `BEGIN:VCALENDAR` line.

    The user's local export prepends `Source URL: ...` / `Title: ...` metadata
    which is not part of the .ics.
    """
    idx = text.find("BEGIN:VCALENDAR")
    return text[idx:] if idx > 0 else text


def unfold(text: str) -> str:
    """Undo RFC 5545 line folding (a CRLF followed by SPACE/TAB continues the previous line)."""
    return re.sub(r"\r?\n[ \t]", "", text)


def clean_lines(lines: Iterable[str], until_cap: str) -> list[str]:
    out: list[str] = []
    cap_yyyymmdd = until_cap[:8]

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")

        if _ATTENDEE_EMPTY_RE.match(line):
            continue

        line = _TIMESTAMP_TZID_RE.sub(r"\1:", line)

        if line.startswith("RRULE:") or line.startswith("EXRULE:"):
            def _cap(match: re.Match[str]) -> str:
                v = match.group(1)
                if not v.endswith("Z"):
                    v = v + "Z"
                return f"UNTIL={until_cap}" if v[:8] > cap_yyyymmdd else f"UNTIL={v}"

            line = _UNTIL_RE.sub(_cap, line)

        out.append(line)

    return out


def fold(lines: Iterable[str]) -> str:
    """Re-fold to <=75 octets per RFC 5545 §3.1, joining with CRLF."""
    result: list[str] = []
    for l in lines:
        while len(l.encode("utf-8")) > 75:
            cut = 75
            while cut > 0 and len(l[:cut].encode("utf-8")) > 75:
                cut -= 1
            if cut <= 0:
                break
            result.append(l[:cut])
            l = " " + l[cut:]
        result.append(l)
    return "\r\n".join(result) + "\r\n"


def normalize(text: str, until_cap: str = DEFAULT_UNTIL_CAP) -> str:
    text = strip_preamble(text)
    text = unfold(text)
    cleaned = clean_lines(text.split("\n"), until_cap)
    return fold(cleaned)


_ATTENDEE_EMPTY_MULTI = re.compile(r"^ATTENDEE;VALUE=TEXT:\s*$", re.MULTILINE)
_TIMESTAMP_TZID_MULTI = re.compile(
    r"^(DTSTAMP|CREATED|LAST-MODIFIED);TZID=[^:]+:", re.MULTILINE
)
_FAR_FUTURE_UNTIL = re.compile(r"UNTIL=2[1-9]\d{6}T\d{6}Z?")


def _report_stats(before: str, after: str) -> None:
    before_u = unfold(before)
    after_u = unfold(after)

    def fmt(label: str, b: int, a: int) -> str:
        return f"{label:<32} before={b:<4} after={a}"

    print(
        fmt(
            "VEVENT count:",
            before_u.count("BEGIN:VEVENT"),
            after_u.count("BEGIN:VEVENT"),
        ),
        file=sys.stderr,
    )
    print(
        fmt(
            "Empty ATTENDEE lines:",
            len(_ATTENDEE_EMPTY_MULTI.findall(before_u)),
            len(_ATTENDEE_EMPTY_MULTI.findall(after_u)),
        ),
        file=sys.stderr,
    )
    print(
        fmt(
            "TZID-on-timestamp lines:",
            len(_TIMESTAMP_TZID_MULTI.findall(before_u)),
            len(_TIMESTAMP_TZID_MULTI.findall(after_u)),
        ),
        file=sys.stderr,
    )
    print(
        fmt(
            "Far-future UNTIL values:",
            len(_FAR_FUTURE_UNTIL.findall(before_u)),
            len(_FAR_FUTURE_UNTIL.findall(after_u)),
        ),
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--url", help="LFX webcal URL to fetch (or set LFX_ICS_URL).")
    src.add_argument("--input", help="Read a local .ics file instead of fetching.")
    parser.add_argument(
        "--output",
        default="Calendar-clean.ics",
        help="Where to write the cleaned .ics (default: Calendar-clean.ics).",
    )
    parser.add_argument(
        "--until-cap",
        default=os.environ.get("UNTIL_CAP", DEFAULT_UNTIL_CAP),
        help="Cap RRULE UNTIL at this UTC datetime (default: 2028-01-01).",
    )
    args = parser.parse_args(argv)

    if args.input:
        raw = read_local(args.input)
    else:
        url = args.url or os.environ.get("LFX_ICS_URL")
        if not url:
            parser.error("Must supply --url, --input, or set LFX_ICS_URL.")
        raw = fetch(url)

    cleaned = normalize(raw, until_cap=args.until_cap)

    with open(args.output, "wb") as f:
        f.write(cleaned.encode("utf-8"))

    _report_stats(raw, cleaned)
    print(f"Wrote {args.output} ({len(cleaned)} bytes).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
