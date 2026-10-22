"""Fixed release identity. Builds and process startup never read the clock."""
from datetime import datetime
import re


DISPLAY_VERSION = "26.1022.180303"
VERSION_TIMEZONE = "America/New_York"


def timestamp_parts(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,4}\.[0-9]{1,6}", value):
        raise ValueError("Use YY.MMDD.HHmmss or its numeric package identity")
    year, date, clock = map(int, value.split("."))
    month, day = divmod(date, 100)
    hour, remainder = divmod(clock, 10000)
    minute, second = divmod(remainder, 100)
    datetime(2000 + year, month, day, hour, minute, second)
    return year, date, clock


def display_timestamp(value):
    year, date, clock = timestamp_parts(value)
    return f"{year:02d}.{date:04d}.{clock:06d}"


def canonical_timestamp(value):
    """PEP 440 and strict SemVer use integers without leading zeroes."""
    return ".".join(map(str, timestamp_parts(value)))


def same_version(left, right):
    """Compare numeric components; retain exact historical prerelease suffixes."""
    def key(value):
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)([-.][0-9A-Za-z][0-9A-Za-z.-]*)?", value)
        return (*map(int, match.groups()[:3]), match[4] or "") if match else None
    first, second = key(left), key(right)
    return first is not None and first == second


def display_if_timestamp(value):
    try:
        return display_timestamp(value)
    except ValueError:
        return value  # Historical releases and unknown installed versions stay visible.


def windows_file_version(value):
    year, date, clock = timestamp_parts(value)
    minute_clock, second = divmod(clock, 100)
    return f"{year}.{date}.{minute_clock}.{second}"


PACKAGE_VERSION = canonical_timestamp(DISPLAY_VERSION)


def release_metadata():
    return {"display_version": DISPLAY_VERSION, "package_version": PACKAGE_VERSION,
            "plugin_version": PACKAGE_VERSION, "windows_file_version": windows_file_version(DISPLAY_VERSION),
            "timezone": VERSION_TIMEZONE}


def add_version_argument(parser):
    parser.add_argument("--version", action="version", version=f"FanucMCPServer {DISPLAY_VERSION}")
