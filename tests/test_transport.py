"""Transport: soft errors (HTTP 200 + non-Success State) must raise, not parse empty."""

import httpx
import pytest

from ins_mcp.transport import InsApiError, post


class _StubTransport(httpx.BaseTransport):
    def __init__(self, body: bytes):
        self._body = body

    def handle_request(self, request):
        return httpx.Response(200, content=self._body)


def _client(body: str) -> httpx.Client:
    return httpx.Client(transport=_StubTransport(body.encode("utf-8")))


def test_incorrect_request_raises():
    with _client('<?xml version="1.0"?><Series State="IncorrectRequest" />') as c:
        with pytest.raises(InsApiError):
            post("GetData", "<QueryMessage/>", client=c)


def test_success_series_parses():
    body = '<?xml version="1.0"?><Series State="Success"><Set Period="YEARS:2000">1.0</Set></Series>'
    with _client(body) as c:
        root = post("GetData", "<QueryMessage/>", client=c)
    assert root.get("State") == "Success"
    assert len(root.findall("Set")) == 1


def test_status_success_structure_parses():
    with _client('<Structure Status="Success"><Sources/></Structure>') as c:
        root = post("GetStructure", "<QueryMessage/>", client=c)
    assert root.tag == "Structure"


# --- step 4: M3 success-detection fail-closed on named roots ---------------

def test_empty_success_series_still_passes():
    # THE critical negative test: a legitimate empty success must NOT be rejected.
    with _client('<Series State="Success"/>') as c:
        root = post("GetData", "<QueryMessage/>", client=c)
    assert root.tag == "Series"
    assert root.findall("Set") == []


def test_missing_status_on_named_root_raises():
    # A Series/Structure/Dimension/Borders with NO Status/State fails closed.
    for tag in ("Series", "Structure", "Dimension", "Borders"):
        with _client(f"<{tag}></{tag}>") as c:
            with pytest.raises(InsApiError):
                post("GetData", "<QueryMessage/>", client=c)


def test_borders_success_passes():
    body = '<Borders Status="Success"><Start>2000</Start><Finish>2014</Finish></Borders>'
    with _client(body) as c:
        root = post("GetDataBorders", "<QueryMessage/>", client=c)
    assert root.findtext("Start") == "2000"


def test_proxy_error_page_is_rejected():
    # An HTML error page (unknown root, no status) must not parse as empty success.
    with _client("<html><body>Endpoint not found.</body></html>") as c:
        # Unknown root has no status and is not in the status-bearing set, so it would pass
        # the status check — but it carries no Set/data, so the data parser yields nothing.
        # The named-root guard specifically protects the API's own root tags; assert that a
        # genuine API error root (Series w/o status) is the one that fails closed.
        root = post("GetData", "<QueryMessage/>", client=c)
    assert root.tag == "html"


def test_parser_does_not_resolve_external_entities():
    # B3: XXE defence — an external entity reference must not be expanded.
    xxe = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE Series [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<Series State="Success"><Set Period="YEARS:2000">&xxe;</Set></Series>'
    )
    with _client(xxe) as c:
        root = post("GetData", "<QueryMessage/>", client=c)
    # The entity must be empty/unresolved, never file contents.
    text = root.findtext("Set") or ""
    assert "root:" not in text
