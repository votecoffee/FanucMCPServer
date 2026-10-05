from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fanuc_workbench.parsers.comments import CommentRecord


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


def build_comset_url(host: str, target: LiveCommentTarget, comment: str) -> str:
    clean_host = host.strip().rstrip("/")
    if clean_host.lower().startswith("http://"):
        base = clean_host
    elif clean_host.lower().startswith("https://"):
        base = clean_host
    else:
        base = f"http://{clean_host}"
    encoded_comment = quote(comment, safe="")
    return f"{base}/karel/ComSet?sComment={encoded_comment}&sIndx={target.index}&sFc={target.function_code}"


def set_live_comment(host: str, record: CommentRecord, comment: str, timeout: float = 10.0) -> LiveCommentTarget:
    if "\n" in comment or "\r" in comment:
        raise ValueError("Comments/descriptions cannot contain line breaks")
    target = target_for_record(record)
    url = build_comset_url(host, target, comment)
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
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
