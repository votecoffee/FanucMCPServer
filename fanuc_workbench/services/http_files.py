"""Bounded reads of exact controller MD files; no CGI dispatch or write API.

The MD/_TEMPLATE request convention is adapted from ONE Robotics go-fanuc.
Copyright (c) 2019 ONE Robotics Company. See licenses/go-fanuc-MIT.txt.
"""
from __future__ import annotations

import base64
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


def read_origin(value: str, host: str) -> str:
    if (not isinstance(value, str) or len(value) > 2048
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise ValueError("HTTP read URL must be an HTTP(S) origin on the configured host")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname != host.casefold()
            or parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"}
            or '?' in value or '#' in value
            or parsed.query or parsed.fragment or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
        raise ValueError("HTTP read URL must be an HTTP(S) origin on the configured host")
    return value.rstrip("/")


def read_path(remote: str) -> str:
    # Exact flat MD files only. Never accept KAREL/KCL, arbitrary URLs or queries.
    match = re.fullmatch(r"md:([A-Za-z0-9_$-]{1,80}\.(?:VA|DG|LS|LOG|TXT|CSV))", remote, re.I)
    if match is None:
        raise ValueError("HTTP reads require an exact supported text file directly under MD:")
    return "/MD/" + quote(match.group(1), safe="") + "?_TEMPLATE="


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, "HTTP read redirects are refused", headers, response)


class HttpFileReader:
    def __init__(self, origin: str, host: str, timeout: float = 10, *, username="", password=""):
        self.origin = read_origin(origin, host)
        if not 0 < timeout <= 120:
            raise ValueError("HTTP read timeout must be in (0, 120]")
        self.timeout, self.username, self.password = timeout, username, password

    def download_file(self, remote: str, local: Path, *, maximum: int):
        if type(maximum) is not int or not 1 <= maximum <= 64 * 1024 * 1024:
            raise ValueError("HTTP read size limit is invalid")
        request = Request(self.origin + read_path(remote), headers={"Accept-Encoding": "identity"}, method="GET")
        if self.username:
            token = base64.b64encode((self.username + ":" + self.password).encode("utf-8")).decode("ascii")
            request.add_header("Authorization", "Basic " + token)
        created = False
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise RuntimeError("HTTP file read did not return status 200")
                if response.headers.get_content_type() in {"text/html", "application/xhtml+xml"}:
                    raise ValueError("HTTP returned an HTML page, not exact controller file bytes")
                if response.headers.get("Content-Encoding", "identity").casefold() != "identity":
                    raise ValueError("Encoded HTTP responses are unsupported")
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdecimal() or int(length) > maximum):
                    raise ValueError("HTTP response exceeds its size limit or has an invalid length")
                total = 0
                with local.open("xb") as destination:
                    created = True
                    while chunk := response.read(min(65536, maximum - total + 1)):
                        probe = chunk.lstrip()
                        if probe.startswith(b"\xef\xbb\xbf"):
                            probe = probe[3:].lstrip()
                        if not total and probe.lower().startswith((b"<html", b"<!doctype html")):
                            raise ValueError("HTTP returned an HTML page, not exact controller file bytes")
                        total += len(chunk)
                        if total > maximum:
                            raise ValueError("HTTP response exceeds its size limit")
                        destination.write(chunk)
                    if length is not None and total != int(length):
                        raise ValueError("HTTP response ended before its declared length")
        except Exception as exc:
            if created:
                local.unlink(missing_ok=True)
            if isinstance(exc, HTTPError):
                raise RuntimeError(f"HTTP file read refused (status {exc.code})") from None
            if isinstance(exc, (URLError, OSError)):
                raise RuntimeError("HTTP file read failed; check the configured service and access") from None
            raise
