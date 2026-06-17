"""Tool-layer tests: frequency-window trap fix and coverage, with transport stubbed.

These exercise ins_get_data's windowing logic without hitting the network: we capture
the request body the tool builds and feed back a canned <Series>.
"""

from lxml import etree

from ins_mcp import server
from ins_mcp.models import Source, Dimension


class _FakeCat:
    """Minimal Catalogue stand-in for the data path."""

    def __init__(self, source):
        self._source = source

    def get_source(self, sid):
        return self._source

    def get_elements(self, sid, did):
        return []


_SECTEUR = Source(
    id="OBJ5262999", key="5262999", name="PIB secteurs",
    description="", start_year=2000, finish_year=2022,
    dimensions=[
        Dimension(id="OBJ5263019", key="1", name="Secteur", type="Indicator"),
        Dimension(id="OBJ5263029", key="2", name="Valeur", type="Custom"),
    ],
)


def _install(monkeypatch, source, response_xml):
    captured = {}

    def fake_post(op, body, **kw):
        captured["op"] = op
        captured["body"] = body
        return etree.fromstring(response_xml.encode("utf-8"))

    monkeypatch.setattr(server, "catalogue", lambda: _FakeCat(source))
    monkeypatch.setattr(server, "post", fake_post)
    return captured


def test_frequency_without_window_applies_declared_range(monkeypatch):
    resp = ('<Series State="Success">'
            '<Set Period="QUARTERS:1.2000" OBJ5263019="1" OBJ5263029="2">1.0</Set>'
            '</Series>')
    captured = _install(monkeypatch, _SECTEUR, resp)

    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"], "OBJ5263029": ["2"]},
                              frequency="quarterly")
    # The declared range 2000-2022 must have been injected as the window.
    assert 'From="2000" To="2022" Frequency="Q"' in captured["body"]
    assert any("declared range 2000-2022" in n for n in out["notes"])


def test_mixed_grain_response_is_flagged(monkeypatch):
    # API returns a mix despite a single requested grain -> must be surfaced, not hidden.
    resp = ('<Series State="Success">'
            '<Set Period="YEARS:2000" OBJ5263019="1" OBJ5263029="2">1.0</Set>'
            '<Set Period="QUARTERS:1.2000" OBJ5263019="1" OBJ5263029="2">0.5</Set>'
            '</Series>')
    out = _install_and_call(monkeypatch, resp)
    assert any("mixes" in n for n in out["notes"])


def _install_and_call(monkeypatch, resp):
    _install(monkeypatch, _SECTEUR, resp)
    return server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"], "OBJ5263029": ["2"]},
                               start="2000", end="2000", frequency="quarterly")


def test_coverage_summary(monkeypatch):
    resp = ('<Series State="Success">'
            '<Set Period="QUARTERS:1.2015" OBJ5263019="1" OBJ5263029="2">1.0</Set>'
            '<Set Period="QUARTERS:2.2015" OBJ5263019="1" OBJ5263029="2">2.0</Set>'
            '</Series>')
    _install(monkeypatch, _SECTEUR, resp)
    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"], "OBJ5263029": ["2"]},
                              start="2015", end="2015", frequency="quarterly")
    assert out["coverage"] == {"start": "2015-Q1", "end": "2015-Q2", "count": 2}


# --- attribution (mandatory licence-compliance field) ----------------------

class _CatWithElements(_FakeCat):
    def __init__(self, source, elements):
        super().__init__(source)
        self._elements = elements

    def get_elements(self, sid, did):
        return self._elements


def test_attribution_present_and_populated_on_get_data(monkeypatch):
    from ins_mcp.models import Element
    measure_el = Element(key="1", name="Mesure", level=0, parent_key=None,
                         is_aggregate=False, has_children=False, unit="DT",
                         timestamp="21/04/2016 15:33:02")
    resp = ('<Series State="Success">'
            '<Set Period="YEARS:2015" OBJ5263019="1" OBJ5263029="2">9.0</Set>'
            '</Series>')

    def fake_post(op, body, **kw):
        return etree.fromstring(resp.encode("utf-8"))

    monkeypatch.setattr(server, "catalogue", lambda: _CatWithElements(_SECTEUR, [measure_el]))
    monkeypatch.setattr(server, "post", fake_post)

    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"], "OBJ5263029": ["2"]},
                              start="2015", end="2015", frequency="annual")
    attr = out["attribution"]
    assert attr["producer"] == "Institut National de la Statistique (INS), Tunisie"
    assert attr["source_url"].startswith("http")
    assert "ins.tn/conditions-utilisation" in attr["license_url"]
    assert attr["disclaimer"]  # non-empty
    # last_updated must reflect the ACTUAL data (the element TIMESTAMP 21/04/2016), not a
    # constant — and be normalized to ISO YYYY-MM-DD.
    assert attr["last_updated"] == "2016-04-21"


def test_attribution_present_even_on_error_returns(monkeypatch):
    monkeypatch.setattr(server, "catalogue", lambda: _FakeCat(None))
    out = server.ins_get_data("NOPE", {"X": ["1"]})
    assert out["error"] == "unknown_source"
    assert out["attribution"]["producer"].startswith("Institut National")
    # no data -> no invented date
    assert out["attribution"]["last_updated"] is None
    assert "last_updated_note" in out["attribution"]


def test_latest_update_picks_most_recent(monkeypatch):
    # dd/mm/yyyy comparison must be chronological, not lexical on the raw string.
    assert server._latest_update(["01/12/2010 00:00:00", "21/04/2016 15:33:02"], "") \
        == "21/04/2016 15:33:02"
    # falls back to dimension LastUpdated when no element timestamps
    assert server._latest_update([], "2026-03-30T11:50:47.887") == "2026-03-30T11:50:47.887"
    # nothing available -> None, never invented
    assert server._latest_update([], "") is None


# --- single-dimension source (shape 1,0,0) ---------------------------------

_SINGLEDIM = Source(
    id="OBJ5259889", key="5259889", name="Principaux agrégats",
    description="", start_year=1997, finish_year=2019,
    dimensions=[Dimension(id="OBJ5259909", key="1", name="Indicateur", type="Indicator")],
)


def test_single_dimension_source_serializes(monkeypatch):
    # The only dimension is the measure itself; nothing may assume a region/custom companion.
    resp = ('<Series State="Success">'
            '<Set Period="YEARS:2000" OBJ5259909="27913139">383.7</Set>'
            '</Series>')
    _install(monkeypatch, _SINGLEDIM, resp)
    out = server.ins_get_data("OBJ5259889", {"OBJ5259909": ["27913139"]},
                              start="2000", end="2005", frequency="annual")
    assert out["coverage"]["count"] == 1
    assert out["observations"][0]["OBJ5259909"] == "27913139"
    assert out["observations"][0]["period"] == "2000"


# --- error / edge behavior -------------------------------------------------

def test_empty_selection_is_structured_error(monkeypatch):
    _install(monkeypatch, _SECTEUR, '<Series State="Success"/>')
    out = server.ins_get_data("OBJ5262999", {})
    assert out["error"] == "empty_selection"
    assert out["observations"] == []


def test_unknown_dimension_caught_before_call(monkeypatch):
    # The API would silently dump the whole source for an unknown dim id; we must catch it.
    captured = _install(monkeypatch, _SECTEUR, '<Series State="Success"/>')
    out = server.ins_get_data("OBJ5262999", {"OBJ_BOGUS": ["1"]},
                              start="2010", end="2011", frequency="annual")
    assert out["error"] == "unknown_dimension"
    assert "body" not in captured  # no request was ever sent


def test_unknown_source_is_structured_error(monkeypatch):
    monkeypatch.setattr(server, "catalogue", lambda: _FakeCat(None))
    out = server.ins_get_data("NOPE", {"X": ["1"]})
    assert out["error"] == "unknown_source"


def test_window_without_frequency_is_structured_error(monkeypatch):
    # H1: a bound without a frequency must NOT silently fall back to the full native range.
    captured = _install(monkeypatch, _SECTEUR, '<Series State="Success"/>')
    for kwargs in ({"start": "2010"}, {"end": "2010"}, {"start": "2010", "end": "2012"}):
        out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"]}, **kwargs)
        assert out["error"] == "frequency_required"
        assert "frequency=annual" in out["message"]
        assert out["observations"] == []
        assert "body" not in captured  # refused before any API call


def test_window_with_frequency_is_allowed(monkeypatch):
    # The guard must only fire when frequency is absent.
    resp = ('<Series State="Success">'
            '<Set Period="YEARS:2010" OBJ5263019="1" OBJ5263029="2">1.0</Set>'
            '</Series>')
    _install(monkeypatch, _SECTEUR, resp)
    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"]},
                              start="2010", end="2010", frequency="annual")
    assert "error" not in out
    assert out["coverage"]["count"] == 1


def test_zero_observations_reads_as_no_data_not_error(monkeypatch):
    _install(monkeypatch, _SECTEUR, '<Series State="Success"/>')
    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"]},
                              start="2010", end="2011", frequency="annual")
    assert "error" not in out
    assert out["coverage"]["count"] == 0
    assert any("not an error" in n for n in out["notes"])


# --- step 2: validation cluster (M1 empty key list, B1 wrapping, H3/B2 availability) ---

def _install_raising(monkeypatch, source, exc):
    """Stub catalogue with the given source and a post() that raises exc."""
    def fake_post(op, body, **kw):
        raise exc
    monkeypatch.setattr(server, "catalogue", lambda: _FakeCat(source))
    monkeypatch.setattr(server, "post", fake_post)


def test_empty_dimension_key_list_rejected(monkeypatch):
    # M1: {"DIM": []} would emit an empty <Dimension> the API may read as "all".
    captured = _install(monkeypatch, _SECTEUR, '<Series State="Success"/>')
    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": []})
    assert out["error"] == "empty_dimension_selection"
    assert "OBJ5263019" in out["message"]
    assert "body" not in captured  # refused before any API call


def test_api_rejected_wrapped_as_structured_error(monkeypatch):
    # B1: a soft IncorrectRequest from transport must not leak as a raw exception.
    from ins_mcp.transport import InsApiError
    _install_raising(monkeypatch, _SECTEUR, InsApiError("GetData: API returned status 'IncorrectRequest'"))
    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["9999999999"]},
                              start="2010", end="2011", frequency="annual")
    assert out["error"] == "api_rejected"
    assert "rejected" in out["message"]
    assert out["attribution"]["producer"].startswith("Institut National")


def test_unsupported_frequency_wrapped_as_structured_error(monkeypatch):
    # B1: builder ValueError for frequency 'S' must become a structured error, not raise.
    _install(monkeypatch, _SECTEUR, '<Series State="Success"/>')
    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"]},
                              start="2010", end="2011", frequency="S")
    assert out["error"] == "unsupported_frequency"
    assert "attribution" in out


def test_availability_validates_and_carries_attribution(monkeypatch):
    # H3 + B2: availability gets the same validation and the mandatory attribution block.
    _install(monkeypatch, _SECTEUR,
             '<Borders Status="Success"><Start>2000</Start><Finish>2014</Finish></Borders>')
    ok = server.ins_get_availability("OBJ5262999", {"OBJ5263019": ["1"]})
    assert ok["available"] is True
    assert ok["start_year"] == 2000 and ok["finish_year"] == 2014
    assert ok["attribution"]["producer"].startswith("Institut National")

    # unknown dimension -> structured error in availability shape, still with attribution
    bad = server.ins_get_availability("OBJ5262999", {"OBJ_BOGUS": ["1"]})
    assert bad["error"] == "unknown_dimension"
    assert bad["available"] is False
    assert "attribution" in bad


def test_availability_empty_selection_and_unknown_source(monkeypatch):
    _install(monkeypatch, _SECTEUR, '<Borders Status="Success"/>')
    empty = server.ins_get_availability("OBJ5262999", {})
    assert empty["error"] == "empty_selection"

    monkeypatch.setattr(server, "catalogue", lambda: _FakeCat(None))
    unknown = server.ins_get_availability("NOPE", {"X": ["1"]})
    assert unknown["error"] == "unknown_source"
    assert "attribution" in unknown


def test_availability_api_rejection_wrapped(monkeypatch):
    from ins_mcp.transport import InsApiError
    _install_raising(monkeypatch, _SECTEUR, InsApiError("GetDataBorders: API returned status 'IncorrectRequest'"))
    out = server.ins_get_availability("OBJ5262999", {"OBJ5263019": ["bad"]})
    assert out["error"] == "api_rejected"
    assert out["available"] is False
    assert "attribution" in out


# --- step 4: M2 wrong-but-uniform grain flagging ---------------------------

def test_uniform_wrong_grain_is_flagged(monkeypatch):
    # Quarterly requested, but the source returns uniformly ANNUAL rows -> must be noted.
    resp = ('<Series State="Success">'
            '<Set Period="YEARS:2015" OBJ5263019="1" OBJ5263029="2">1.0</Set>'
            '<Set Period="YEARS:2016" OBJ5263019="1" OBJ5263029="2">2.0</Set>'
            '</Series>')
    _install(monkeypatch, _SECTEUR, resp)
    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"], "OBJ5263029": ["2"]},
                              start="2015", end="2016", frequency="quarterly")
    assert any("does not serve the requested grain" in n for n in out["notes"])


def test_correct_grain_not_flagged(monkeypatch):
    # Quarterly requested and quarterly returned -> no wrong-grain note.
    resp = ('<Series State="Success">'
            '<Set Period="QUARTERS:1.2015" OBJ5263019="1" OBJ5263029="2">1.0</Set>'
            '</Series>')
    _install(monkeypatch, _SECTEUR, resp)
    out = server.ins_get_data("OBJ5262999", {"OBJ5263019": ["1"], "OBJ5263029": ["2"]},
                              start="2015", end="2015", frequency="quarterly")
    notes = out.get("notes", [])
    assert not any("grain" in n for n in notes)


# --- step 5 (L2): standardized not-found across tools ----------------------

def test_get_source_unknown_returns_structured_error(monkeypatch):
    monkeypatch.setattr(server, "catalogue", lambda: _FakeCat(None))
    out = server.ins_get_source("NOPE")
    assert out["error"] == "unknown_source"
    assert "attribution" in out


def test_get_dimension_elements_unknown_source_and_dim(monkeypatch):
    monkeypatch.setattr(server, "catalogue", lambda: _FakeCat(None))
    assert server.ins_get_dimension_elements("NOPE", "D")["error"] == "unknown_source"

    monkeypatch.setattr(server, "catalogue", lambda: _FakeCat(_SECTEUR))
    out = server.ins_get_dimension_elements("OBJ5262999", "BOGUS_DIM")
    assert out["error"] == "unknown_dimension"


def test_get_dimension_elements_empty_match_is_list_not_error(monkeypatch):
    # A valid dimension with no matching children/search must stay an empty list.
    class _Cat(_FakeCat):
        def browse_elements(self, *a, **k):
            return []
    monkeypatch.setattr(server, "catalogue", lambda: _Cat(_SECTEUR))
    out = server.ins_get_dimension_elements("OBJ5262999", "OBJ5263019", search="zzz")
    assert out == []
