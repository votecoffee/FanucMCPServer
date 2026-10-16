from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener

from fanuc_workbench.parsers.comments import CommentRecord


class _NoWriteRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, "Controller write redirects are refused", headers, response)


# FANUC KAREL ComSet sFc values used by the controller's HTTP comment utility.
FUNCTION_CODES = {
    "Numeric Register": 1,
    "Position Register": 3,
    "RI": 6,
    "RO": 7,
    "DIN": 8,
    "DOUT": 9,
    "GIN": 10,
    "GOUT": 11,
    "AIN": 12,
    "AOUT": 13,
    "SREG": 14,
    "FLG": 19,
}


@dataclass(frozen=True, slots=True)
class LiveCommentTarget:
    function_code: int
    index: int
    display_name: str


def target_for_record(record: CommentRecord) -> LiveCommentTarget:
    if record.kind == "Numeric Register":
        return LiveCommentTarget(1, int(record.metadata["index"]), record.identifier)
    if record.kind == "Position Register":
        group = int(record.metadata.get("group", 1))
        if group != 1:
            raise ValueError("Live ComSet editing is only enabled for group-1 position registers because the endpoint identifies PRs by index only.")
        return LiveCommentTarget(3, int(record.metadata["index"]), record.identifier)
    if record.kind == "I/O":
        io_type = str(record.metadata["io_type"]).upper()
        code = FUNCTION_CODES.get(io_type)
        if code is None:
            raise ValueError(f"Live comment editing is not enabled for {io_type}.")
        return LiveCommentTarget(code, int(record.metadata["index"]), record.identifier)
    raise ValueError(f"{record.kind} descriptions are local-file edits only in this version.")


def validate_live_comment(comment: str, *, max_bytes: int = 16, encoding: str = "ascii") -> None:
    if type(max_bytes) is not int or not 1 <= max_bytes <= 256 or encoding not in {"ascii", "latin-1", "utf-8"}:
        raise ValueError("Comment policy requires 1-256 bytes and ascii, latin-1 or utf-8 encoding")
    if not isinstance(comment, str) or any(not char.isprintable() for char in comment):
        raise ValueError("Comments/descriptions require printable text without control characters")
    try:
        encoded = comment.encode(encoding, errors="strict")
    except UnicodeEncodeError:
        raise ValueError("Comment cannot be represented in the configured controller encoding") from None
    if len(encoded) > max_bytes:
        raise ValueError(f"Comment exceeds the configured {max_bytes}-byte controller limit")


def build_comset_url(host: str, target: LiveCommentTarget, comment: str, *,
                     max_bytes: int = 16, encoding: str = "ascii") -> str:
    validate_live_comment(comment, max_bytes=max_bytes, encoding=encoding)
    clean_host = host.strip().rstrip("/")
    if clean_host.lower().startswith("http://"):
        base = clean_host
    elif clean_host.lower().startswith("https://"):
        base = clean_host
    else:
        base = f"http://{clean_host}"
    encoded_comment = quote(comment, safe="", encoding=encoding, errors="strict")
    return f"{base}/karel/ComSet?sComment={encoded_comment}&sIndx={target.index}&sFc={target.function_code}"


def set_live_comment(host: str, record: CommentRecord, comment: str, timeout: float = 10.0, *,
                     max_bytes: int = 16, encoding: str = "ascii") -> LiveCommentTarget:
    target = target_for_record(record)
    url = build_comset_url(host, target, comment, max_bytes=max_bytes, encoding=encoding)
    request = Request(url, method="GET")
    try:
        # Controller traffic is direct; do not route robot comments through an
        # environment/system HTTP proxy or follow a redirect to another target.
        with build_opener(ProxyHandler({}), _NoWriteRedirect()).open(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise RuntimeError(f"FANUC ComSet returned HTTP {status}")
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(
                "FANUC HTTP/KAREL access was denied. Check Setup > Host Comm > HTTP and the controller's web/KAREL security settings."
            ) from exc
        raise RuntimeError(f"FANUC ComSet returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach FANUC HTTP comment service: {exc.reason}") from exc
    return target
