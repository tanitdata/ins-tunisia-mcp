"""Step 3 (C1): cache versioning / auto-heal and tolerant deserialization.

A stale cache.db (written by an older serialization) must never crash the read tools with a
cryptic Dimension/Element TypeError — it must auto-heal (clear + re-seed) or degrade to
defaults. These tests use a temp DB and never hit the network.
"""

import json
import sqlite3

from ins_mcp import catalogue as cat_mod
from ins_mcp.catalogue import Catalogue


def _seed_legacy_row(db_path, dim_extra: dict):
    """Write a sources row whose dimensions_json carries `dim_extra` keys (a legacy shape),
    with NO cache_version stamp — simulating a cache from an older code revision. Columns are
    listed explicitly (not positional) so it represents a pre-has_data row honestly."""
    conn = sqlite3.connect(db_path)
    conn.executescript(cat_mod._SCHEMA)
    dims = [dict({"id": "D", "key": "1", "name": "N", "type": "Indicator"}, **dim_extra)]
    conn.execute(
        "INSERT OR REPLACE INTO sources "
        "(id, key, name, description, start_year, finish_year, dimensions_json) "
        "VALUES (?,?,?,?,?,?,?)",
        ("C_X", "1", "Name", "desc", 2000, 2020, json.dumps(dims)),
    )
    conn.commit()
    conn.close()


def test_stale_cache_is_healed_on_open(tmp_path, monkeypatch):
    db = tmp_path / "cache.db"
    # Legacy row with an unknown extra field and no version stamp.
    _seed_legacy_row(db, {"obsolete_field": "x"})

    # Opening the Catalogue should detect the missing version and wipe the stale data.
    c = Catalogue(db)
    try:
        assert c.is_seeded() is False  # healed: sources cleared
        assert c._cache_version() == cat_mod.CACHE_VERSION
    finally:
        c.close()


def test_current_version_cache_is_not_wiped(tmp_path):
    db = tmp_path / "cache.db"
    _seed_legacy_row(db, {"last_updated": "2020-01-01"})
    # Stamp the current version so it is considered fresh.
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('cache_version', ?)", (cat_mod.CACHE_VERSION,))
    conn.commit()
    conn.close()

    c = Catalogue(db)
    try:
        assert c.is_seeded() is True  # preserved
        src = c.get_source("C_X")
        assert src is not None and src.measure_dimension.id == "D"
    finally:
        c.close()


def test_tolerant_deserialize_drops_unknown_keys(tmp_path):
    # Even if the version check were bypassed, an unknown key must not crash deserialization.
    db = tmp_path / "cache.db"
    _seed_legacy_row(db, {"unexpected": "boom"})
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('cache_version', ?)", (cat_mod.CACHE_VERSION,))
    conn.commit()
    conn.close()

    c = Catalogue(db)
    try:
        src = c.get_source("C_X")  # would raise TypeError without tolerant _make()
        assert src.dimensions[0].id == "D"
    finally:
        c.close()


# --- Phase 2: has_data flag (root-borders probe), with FAIL-OPEN as the invariant ----------

from lxml import etree

from ins_mcp.models import Dimension, Source

_PROBE_SOURCE = Source(
    id="C_PROBE", key="1", name="probe", description="",
    start_year=2014, finish_year=2014,
    dimensions=[Dimension(id="IND", key="9", name="Indicateurs", type="Indicator")],
)


def _probe_catalogue(tmp_path, monkeypatch, *, borders_xml=None, raise_exc=None):
    """A Catalogue whose probe is stubbed via post(): the zero-fetch light probe needs no
    codelist, so we only stub the GetDataBorders response (or make it raise)."""
    c = Catalogue(tmp_path / "cache.db")

    def fake_post(op, body, **kw):
        if raise_exc is not None:
            raise raise_exc
        return etree.fromstring(borders_xml.encode("utf-8"))
    monkeypatch.setattr(cat_mod, "post", fake_post)
    return c


def test_has_data_false_only_on_clean_empty_sentinel(tmp_path, monkeypatch):
    # A clean Borders response with the 1899/0 sentinel => has_data False.
    c = _probe_catalogue(tmp_path, monkeypatch,
                         borders_xml='<Borders Status="Success"><Start>0</Start><Finish>0</Finish></Borders>')
    assert c._probe_has_data(_PROBE_SOURCE) is False


def test_has_data_true_on_real_range(tmp_path, monkeypatch):
    c = _probe_catalogue(tmp_path, monkeypatch,
                         borders_xml='<Borders Status="Success"><Start>2000</Start><Finish>2014</Finish></Borders>')
    assert c._probe_has_data(_PROBE_SOURCE) is True


def test_has_data_FAILS_OPEN_on_probe_exception(tmp_path, monkeypatch):
    # THE guard test: a probe that raises (network hiccup, changed API, parse error) must
    # yield has_data=True, NEVER False — a wrong False would hide real data.
    from ins_mcp.transport import InsApiError
    c = _probe_catalogue(tmp_path, monkeypatch, raise_exc=InsApiError("boom"))
    assert c._probe_has_data(_PROBE_SOURCE) is True

    c2 = _probe_catalogue(tmp_path, monkeypatch, raise_exc=RuntimeError("network down"))
    assert c2._probe_has_data(_PROBE_SOURCE) is True


def test_has_data_fails_open_when_no_measure(tmp_path, monkeypatch):
    # No Indicator dimension -> can't probe -> unknown -> fail open (True).
    c = Catalogue(tmp_path / "cache.db")
    no_measure = Source(id="X", key="1", name="n", description="",
                        dimensions=[])  # no Indicator dim
    assert c._probe_has_data(no_measure) is True


def test_has_data_roundtrips_through_cache(tmp_path, monkeypatch):
    # has_data persists and is read back; a pre-v3 row (no column value) reads as True.
    c = _probe_catalogue(tmp_path, monkeypatch,
                         borders_xml='<Borders Status="Success"><Start>0</Start><Finish>0</Finish></Borders>')
    with c.conn:
        c.conn.execute(
            "INSERT OR REPLACE INTO sources "
            "(id, key, name, description, start_year, finish_year, dimensions_json, has_data) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("C_EMPTY", "1", "shell", "d", 2014, 2014, "[]", 0),
        )
        c.conn.execute(
            "INSERT OR REPLACE INTO sources "
            "(id, key, name, description, start_year, finish_year, dimensions_json) "
            "VALUES (?,?,?,?,?,?,?)",  # pre-v3 row: has_data column defaulted
            ("C_LEGACY", "1", "legacy", "d", 2000, 2014, "[]"),
        )
    assert c.get_source("C_EMPTY").has_data is False
    assert c.get_source("C_LEGACY").has_data is True  # fail-open on read


# --- first-call latency fix: fast seed + background parallel probe -------------------------

_STRUCTURE_XML = (
    '<Structure Status="Success"><Sources>'
    '<Source Key="1" Id="POP" Name="Populated">'
    '<Dimensions>'
    '<Dimension Status="Success" Id="IND_P" Key="9" Name="Ind" DimensionType="Indicator"/>'
    '</Dimensions><Period><StartYear>2000</StartYear><FinishYear>2014</FinishYear></Period>'
    '</Source>'
    '<Source Key="2" Id="EMPTY" Name="Shell">'
    '<Dimensions>'
    '<Dimension Status="Success" Id="IND_E" Key="8" Name="Ind" DimensionType="Indicator"/>'
    '</Dimensions><Period><StartYear>2014</StartYear><FinishYear>2014</FinishYear></Period>'
    '</Source>'
    '</Sources></Structure>'
)


def _fake_api(borders_by_source):
    """Return a fake post() that serves the structure, a one-leaf elements doc, and per-source
    borders, while recording how many GetDataBorders calls happen."""
    calls = {"GetStructure": 0, "GetDimensionElements": 0, "GetDataBorders": 0}

    def fake_post(op, body, **kw):
        calls[op] = calls.get(op, 0) + 1
        if op == "GetStructure":
            return etree.fromstring(_STRUCTURE_XML.encode("utf-8"))
        if op == "GetDimensionElements":
            return etree.fromstring(
                b'<Dimension Status="Success"><Elements>'
                b'<Element KEY="100" NAME="leaf" ISCALCULATED=""/>'
                b'</Elements></Dimension>')
        if op == "GetDataBorders":
            sid = "EMPTY" if "EMPTY" in body else "POP"
            return etree.fromstring(borders_by_source[sid].encode("utf-8"))
        raise AssertionError(op)
    return fake_post, calls


def test_seed_runs_no_inline_borders_probe(tmp_path, monkeypatch):
    # Fast-seed path: refresh() must issue exactly ONE GetStructure and ZERO per-source
    # GetDataBorders before returning (probing is deferred to the background pass).
    borders = {"POP": '<Borders Status="Success"><Start>2000</Start><Finish>2014</Finish></Borders>',
               "EMPTY": '<Borders Status="Success"><Start>0</Start><Finish>0</Finish></Borders>'}
    fake_post, calls = _fake_api(borders)
    monkeypatch.setattr(cat_mod, "post", fake_post)

    c = Catalogue(tmp_path / "cache.db", probe_in_background=True)
    n = c.refresh()
    assert n == 2
    assert calls["GetStructure"] == 1
    assert calls["GetDataBorders"] == 0  # NONE synchronous — the whole point of the fix
    c.wait_for_probes(timeout=10)  # let the background pass finish before teardown


def test_background_probe_populates_has_data(tmp_path, monkeypatch):
    # After the background pass completes, the empty control is flagged false and the
    # populated control true — backgrounding did not silently drop the probe.
    borders = {"POP": '<Borders Status="Success"><Start>2000</Start><Finish>2014</Finish></Borders>',
               "EMPTY": '<Borders Status="Success"><Start>0</Start><Finish>0</Finish></Borders>'}
    fake_post, calls = _fake_api(borders)
    monkeypatch.setattr(cat_mod, "post", fake_post)

    c = Catalogue(tmp_path / "cache.db", probe_in_background=True)
    c.refresh()
    c.wait_for_probes(timeout=10)

    assert c.get_source("EMPTY").has_data is False
    assert c.get_source("POP").has_data is True
    assert calls["GetDataBorders"] == 2  # both sources probed to completion in the background


def test_synchronous_probe_mode_for_determinism(tmp_path, monkeypatch):
    # probe_in_background=False runs the pass inline (used where a thread join is undesirable).
    borders = {"POP": '<Borders Status="Success"><Start>2000</Start><Finish>2014</Finish></Borders>',
               "EMPTY": '<Borders Status="Success"><Start>0</Start><Finish>0</Finish></Borders>'}
    fake_post, calls = _fake_api(borders)
    monkeypatch.setattr(cat_mod, "post", fake_post)

    c = Catalogue(tmp_path / "cache.db", probe_in_background=False)
    c.refresh()  # probes complete before returning
    assert c.get_source("EMPTY").has_data is False
    assert c.get_source("POP").has_data is True
