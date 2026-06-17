"""Opt-in live end-to-end smoke against the real endpoint. Run with: pytest -m live"""

import pytest

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def cat(tmp_path_factory):
    from ins_mcp.catalogue import Catalogue
    c = Catalogue(tmp_path_factory.mktemp("cache") / "cache.db")
    yield c
    c.close()


def test_seed_and_get_data(cat):
    assert cat.refresh() == 78
    nso = cat.get_source("C_NSO")
    assert nso.measure_dimension.id == "RDS_DICT_INDICATORS_NSO"

    hits = cat.browse_elements("C_NSO", "RDS_DICT_INDICATORS_NSO",
                               search="Superficie agricole utilis")
    assert any(e.key == "27523489" for e in hits)

    from ins_mcp import xml_build, xml_parse
    from ins_mcp.transport import post
    body = xml_build.build_data(
        "C_NSO",
        {"RDS_DICT_INDICATORS_NSO": ["27523489"], "RDS_DICT_REGIONS_NSO": ["0"]},
        start="2010", end="2012", frequency="annual",
    )
    obs = xml_parse.parse_data(post("GetData", body),
                               dimension_ids=["RDS_DICT_INDICATORS_NSO", "RDS_DICT_REGIONS_NSO"])
    assert [o.period for o in obs] == ["2010", "2011", "2012"]
    assert obs[0].value > 0


def test_quarterly_source_live(cat):
    from ins_mcp import xml_build, xml_parse
    from ins_mcp.transport import post
    body = xml_build.build_data(
        "OBJ5262999", {"OBJ5263019": ["27932009"], "OBJ5263029": ["27932329"]},
        start="2015", end="2015", frequency="quarterly",
    )
    obs = xml_parse.parse_data(post("GetData", body),
                               dimension_ids=["OBJ5263019", "OBJ5263029"])
    assert len(obs) == 4
    assert obs[0].period == "2015-Q1"
    assert all(o.frequency == "quarterly" for o in obs)
