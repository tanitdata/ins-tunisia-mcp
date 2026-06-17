"""Request-builder tests: exact strings the server sends."""

import pytest

from ins_mcp import xml_build


def test_structure_is_empty_envelope():
    assert xml_build.build_structure() == "<QueryMessage></QueryMessage>"


def test_dimension_elements_uses_string_id_with_data():
    body = xml_build.build_dimension_elements("RDS_DICT_REGIONS_NSO")
    assert body == (
        "<QueryMessage><DataWhere>"
        "<DimensionId WithData=\"true\">RDS_DICT_REGIONS_NSO</DimensionId>"
        "</DataWhere></QueryMessage>"
    )


def test_data_includes_frequency_and_selections():
    body = xml_build.build_data(
        "C_NSO",
        {"RDS_DICT_INDICATORS_NSO": ["27523489"], "RDS_DICT_REGIONS_NSO": ["0"]},
        start="2000", end="2015", frequency="annual",
    )
    assert "SourceId=\"C_NSO\"" in body
    assert "From=\"2000\" To=\"2015\" Frequency=\"Y\"" in body
    assert "<Dimension Id=\"RDS_DICT_INDICATORS_NSO\"><Element>27523489</Element></Dimension>" in body


def test_data_quarterly_code():
    body = xml_build.build_data("OBJ5262999", {"OBJ5263019": ["1"]}, frequency="quarterly")
    assert "Frequency=\"Q\"" in body


def test_data_omits_period_when_no_frequency():
    body = xml_build.build_data("C_NSO", {"RDS_DICT_INDICATORS_NSO": ["27523489"]})
    assert "<Period" not in body


def test_monthly_passes_through():
    # M is accepted by the API (empty != unsupported), so the builder must not reject it.
    body = xml_build.build_data("C_NSO", {"X": ["1"]}, frequency="monthly")
    assert "Frequency=\"M\"" in body
    assert "Frequency=\"M\"" in xml_build.build_data("C_NSO", {"X": ["1"]}, frequency="M")


def test_semestrial_and_unknown_frequency_rejected():
    # S genuinely errors at the API, so we omit it from the builder vocabulary.
    for bad in ("S", "semestrial", "weekly"):
        with pytest.raises(ValueError):
            xml_build.build_data("C_NSO", {"X": ["1"]}, frequency=bad)
