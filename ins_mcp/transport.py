"""The only place HTTP lives. POSTs QueryMessage XML, returns the parsed root.

The endpoint always answers HTTP 200, even on error. Authoritative status is the
`State`/`Status` attribute on the response root (`<Series State>`, `<Structure Status>`,
`<Dimension Status>`, `<Borders Status>`). A soft error must never be mistaken for an
empty success, so we validate that attribute here and raise.
"""

from __future__ import annotations

import httpx
from lxml import etree

BASE_URL = "http://dataportal.ins.tn/WebApi/"
HEADERS = {"Content-Type": "text/xml; charset=UTF-8"}
TIMEOUT = 60.0

# lxml recover=True: the responses are valid but occasionally have odd whitespace in the XML
# declaration; recovery keeps us robust. Hardened for untrusted plain-HTTP content: no entity
# resolution, no DTD or network loading, no huge-tree expansion (XXE / billion-laughs defence).
_PARSER = etree.XMLParser(
    recover=True, encoding="utf-8",
    resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False,
)

# Roots for which a Status/State attribute is authoritative. For these we fail closed: a
# missing status means the body is not a genuine API success (proxy/error page, truncated
# recovery output), so we raise rather than let it parse as an empty success.
_STATUS_BEARING_ROOTS = {"Series", "Structure", "Dimension", "Borders"}


class InsApiError(RuntimeError):
    """A non-Success State/Status from the API, or a transport failure."""


def _status(root: etree._Element) -> str | None:
    # The status attribute is spelled `Status` on most roots but `State` on <Series>.
    return root.get("Status") or root.get("State")


def post(op: str, body: str, *, client: httpx.Client | None = None) -> etree._Element:
    """POST a QueryMessage body to an operation; return the validated XML root.

    Raises InsApiError if the root status attribute is not "Success", OR is missing on a
    known status-bearing root (Series/Structure/Dimension/Borders). A legitimate empty
    success such as `<Series State="Success"/>` still passes — only the absence of the
    status (or a non-Success value) fails closed.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    try:
        resp = client.post(BASE_URL + op, content=body.encode("utf-8"), headers=HEADERS)
        resp.raise_for_status()
        root = etree.fromstring(resp.content, parser=_PARSER)
    finally:
        if owns_client:
            client.close()

    if root is None:
        raise InsApiError(f"{op}: empty/unparseable response")

    status = _status(root)
    if status is not None and status != "Success":
        raise InsApiError(f"{op}: API returned status {status!r}")
    # Fail closed: a status-bearing root with NO status is not a genuine success.
    if status is None and root.tag in _STATUS_BEARING_ROOTS:
        raise InsApiError(
            f"{op}: <{root.tag}> response has no Status/State attribute; "
            "treating as failure rather than empty success"
        )
    return root
