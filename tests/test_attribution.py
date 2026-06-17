"""Attribution date normalization: both raw input forms -> one ISO YYYY-MM-DD output."""

from ins_mcp.attribution import attribution_block, normalize_date


def test_element_timestamp_form_normalizes():
    # Element TIMESTAMP: dd/mm/yyyy HH:MM:SS
    assert normalize_date("17/04/2026 09:10:40") == "2026-04-17"
    assert normalize_date("21/04/2016 15:33:02") == "2016-04-21"
    # single-digit day/month still zero-pads
    assert normalize_date("1/2/2020") == "2020-02-01"


def test_iso_lastupdated_form_normalizes():
    # Dimension LastUpdated: ISO 8601 with time/fraction
    assert normalize_date("2016-12-26T06:17:07.927") == "2016-12-26"
    assert normalize_date("2024-02-26T17:09:44.997") == "2024-02-26"


def test_both_forms_reach_the_same_representation():
    # The same calendar date in either input form must produce the identical output.
    assert normalize_date("26/12/2016 06:17:07") == normalize_date("2016-12-26T06:17:07.927")


def test_missing_value_stays_null():
    assert normalize_date(None) is None
    assert normalize_date("") is None


def test_unrecognized_form_passed_through_not_dropped():
    # Still the real date (compliance) — returned unchanged rather than nulled.
    assert normalize_date("April 2016") == "April 2016"


def test_block_uses_normalized_date_and_keeps_null_behavior():
    block = attribution_block("21/04/2016 15:33:02")
    assert block["last_updated"] == "2016-04-21"
    assert "last_updated_note" not in block

    empty = attribution_block(None)
    assert empty["last_updated"] is None
    assert "last_updated_note" in empty
