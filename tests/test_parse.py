"""Offline parser tests against real saved fixtures (no network)."""

from ins_mcp import xml_parse
from ins_mcp.normalize import clean_text, fold


def test_fold_is_accent_and_case_insensitive():
    assert fold("Superficie agricole utilisée") == "superficie agricole utilisee"
    assert fold("ÉCHANGES éxtérieurs") == "echanges exterieurs"
    # the search comparison both folds query and haystack, so unaccented matches accented
    assert fold("agricole utilisee") in fold("Superficie agricole utilisée totale")


def test_structure_single_database_78_sources(fixture_root):
    sources = xml_parse.parse_structure(fixture_root("get_structure.xml"))
    assert len(sources) == 78
    ids = {s.id for s in sources}
    assert "C_NSO" in ids
    assert "C_FOREIGNTRADE" not in ids  # placeholder, never real
    assert "OBJ5259409" in ids  # foreign trade is an ordinary cube


def test_structure_measure_dimension_by_type(fixture_root):
    sources = {s.id: s for s in xml_parse.parse_structure(fixture_root("get_structure.xml"))}
    nso = sources["C_NSO"]
    measure = nso.measure_dimension
    assert measure is not None
    assert measure.type == "Indicator"
    assert measure.id == "RDS_DICT_INDICATORS_NSO"


def test_structure_finish_year_is_horizon_not_availability(fixture_root):
    sources = {s.id: s for s in xml_parse.parse_structure(fixture_root("get_structure.xml"))}
    # C_NSO declares 1970..2050 but real data (see borders) ends 2014.
    assert sources["C_NSO"].finish_year == 2050


def test_clean_text_strips_tabs_and_combining_marks():
    assert clean_text("Indicateurs\t\t\t") == "Indicateurs"
    assert clean_text("Mileu") == "Mileu"  # typos preserved (identity is by Id, not this)
    # kasra embedded in "Cِette" must be normalized away
    assert clean_text("Cِette base") == "Cette base"
    assert clean_text("a<br>b") == "a b"


def test_regions_hierarchy_and_aggregate_flag(fixture_root):
    els = xml_parse.parse_elements(fixture_root("elements_regions.xml"))
    assert len(els) == 2374
    assert max(e.level for e in els) == 4  # 5 element levels, 0-indexed from root
    total = next(e for e in els if e.key == "0")
    assert total.name == "Tunisie"
    assert total.is_aggregate is True   # ISCALCULATED populated
    assert total.parent_key is None
    leaf = next(e for e in els if e.key == "1151")  # Délégation de Carthage
    assert leaf.is_aggregate is False


def test_indicators_carry_unit_and_full_name(fixture_root):
    els = xml_parse.parse_elements(fixture_root("elements_indicators_nso.xml"))
    sau = next(e for e in els if e.key == "27523489")
    assert "Superficie agricole" in sau.name
    assert sau.unit == "Hectar"
    assert sau.is_aggregate is False


def test_data_annual_omits_missing_cells(fixture_root):
    # Fixture requested 1970..2025 but only 2000..2014 exist.
    obs = xml_parse.parse_data(
        fixture_root("getdata_nso_sparse.xml"),
        dimension_ids=["RDS_DICT_INDICATORS_NSO", "RDS_DICT_REGIONS_NSO"],
    )
    assert len(obs) == 15
    periods = [o.period for o in obs]
    assert periods[0] == "2000" and periods[-1] == "2014"
    assert all(o.frequency == "annual" for o in obs)
    assert all(o.value is not None for o in obs)  # no null placeholders
    first = obs[0]
    assert first.scale == "1"
    assert first.selection["RDS_DICT_REGIONS_NSO"] == "0"


def test_data_quarterly_period_format(fixture_root):
    obs = xml_parse.parse_data(
        fixture_root("getdata_secteur_quarterly.xml"),
        dimension_ids=["OBJ5263019", "OBJ5263029"],
    )
    assert len(obs) == 8
    assert obs[0].period == "2015-Q1"
    assert obs[0].frequency == "quarterly"
    assert obs[3].period == "2015-Q4"


def test_quarterly_periods_sort_lexically_for_coverage(fixture_root):
    # period is the string "YYYY-QN"; min/max must order chronologically.
    obs = xml_parse.parse_data(
        fixture_root("getdata_secteur_quarterly_e2e.xml"),
        dimension_ids=["OBJ5263019", "OBJ5263029"],
    )
    periods = [o.period for o in obs]
    assert min(periods) == "2015-Q1"
    assert max(periods) == "2016-Q4"


def test_single_dimension_observation(fixture_root):
    obs = xml_parse.parse_data(
        fixture_root("getdata_singledim_e2e.xml"), dimension_ids=["OBJ5259909"],
    )
    assert len(obs) == 6
    assert obs[0].selection == {"OBJ5259909": "27913139"}
    assert obs[0].period == "2000"


def test_census_observation_all_dimensions(fixture_root):
    dims = ["OBJ5258239", "RDS_DICT_REGIONS_NSO", "OBJ5258249", "OBJ5258259", "OBJ5258269"]
    obs = xml_parse.parse_data(fixture_root("getdata_census_e2e.xml"), dimension_ids=dims)
    assert len(obs) == 1
    assert obs[0].period == "2014"
    assert set(obs[0].selection.keys()) == set(dims)


def test_unselected_dimension_keys_are_echoed(fixture_root):
    # Milieu (OBJ4325099) was NOT selected, but the API broke it out into 3 rows per region.
    # Each row must carry its Milieu key so the breakout is not silently ambiguous.
    obs = xml_parse.parse_data(
        fixture_root("getdata_unselected_breakout.xml"),
        dimension_ids=["OBJ4325089", "RDS_DICT_REGIONS_NSO"],  # the SELECTED subset only
    )
    assert len(obs) == 6  # 2 regions x 3 Milieu members
    for o in obs:
        # every dimension key present on the <Set> is surfaced, including the unselected one
        assert set(o.selection) == {"OBJ4325089", "RDS_DICT_REGIONS_NSO", "OBJ4325099"}
        assert "Period" not in o.selection and "UNITS" not in o.selection  # meta excluded
    # the three Tunis (region 11) rows are now distinguishable by their Milieu key
    tunis = [o for o in obs if o.selection["RDS_DICT_REGIONS_NSO"] == "11"]
    by_milieu = {o.selection["OBJ4325099"]: o.value for o in tunis}
    assert by_milieu == {"27667449": 1056247.0, "27667459": 0.0, "27667469": 1056247.0}


def test_selected_dimension_keys_unchanged(fixture_root):
    # Regression: keys we already emitted for a fully-selected query must still be present.
    obs = xml_parse.parse_data(
        fixture_root("getdata_census_e2e.xml"),
        dimension_ids=["OBJ5258239", "RDS_DICT_REGIONS_NSO", "OBJ5258249",
                       "OBJ5258259", "OBJ5258269"],
    )
    assert {"OBJ5258239", "RDS_DICT_REGIONS_NSO", "OBJ5258249",
            "OBJ5258259", "OBJ5258269"} <= set(obs[0].selection)


def test_parse_period_generic():
    assert xml_parse.parse_period("YEARS:2015") == ("2015", "annual")
    assert xml_parse.parse_period("QUARTERS:1.2015") == ("2015-Q1", "quarterly")
    assert xml_parse.parse_period("MONTHS:3.2015") == ("2015-03", "monthly")


def test_borders_whole_years(fixture_root):
    b = xml_parse.parse_borders(fixture_root("getborders_nso.xml"))
    # True availability 2000..2014, vs the declared horizon 2050.
    assert b == {"start_year": 2000, "finish_year": 2014}


def test_unparsable_value_flagged_distinct_from_missing():
    # L3: a present-but-unparsable value must be marked, not silently confused with missingness.
    from lxml import etree
    xml = ('<Series State="Success">'
           '<Set Period="YEARS:2000" D="1">n/a</Set>'      # garbage text
           '<Set Period="YEARS:2001" D="1"></Set>'          # legitimately empty (missing)
           '</Series>')
    obs = xml_parse.parse_data(etree.fromstring(xml.encode()), dimension_ids=["D"])
    bad, missing = obs[0], obs[1]
    assert bad.value is None and bad.value_error is True
    assert bad.to_json().get("value_error") is True
    assert missing.value is None and missing.value_error is False
    assert "value_error" not in missing.to_json()  # not emitted for legitimate missingness
