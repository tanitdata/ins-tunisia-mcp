"""Parse INS/Prognoz XML into the cleaned domain. All parsing rules live here.

Key rules (verified live, see ins-mcp-build-spec.md):
  - Identity is @Id/@Key, never @Name; names are cleaned via normalize.clean_text.
  - Dimension role comes from DimensionType, not the label.
  - Codelists are hierarchical; a node is an aggregate iff ISCALCULATED is non-empty.
  - GetData omits missing cells entirely (absent = no-data, present 0 = real 0).
  - Time is Period="<PREFIX>:<value>"; parse generically, prefix -> frequency.
"""

from __future__ import annotations

from lxml import etree

from .models import Dimension, Element, Observation, Source
from .normalize import clean_text

# Period prefix -> our frequency label and a renderer for the value part.
# YEARS:2015 -> ("2015", "annual"); QUARTERS:1.2015 -> ("2015-Q1", "quarterly").
_FREQ_BY_PREFIX = {"YEARS": "annual", "QUARTERS": "quarterly", "MONTHS": "monthly",
                   "HALFYEARS": "semestrial", "SEMESTERS": "semestrial"}


def parse_period(token: str) -> tuple[str, str]:
    """('YEARS:2015' | 'QUARTERS:1.2015') -> (display_period, frequency)."""
    if ":" not in token:
        return token, "unknown"
    prefix, value = token.split(":", 1)
    freq = _FREQ_BY_PREFIX.get(prefix.upper(), prefix.lower())
    if prefix.upper() == "QUARTERS" and "." in value:
        q, year = value.split(".", 1)
        return f"{year}-Q{q}", freq
    if prefix.upper() == "MONTHS" and "." in value:
        m, year = value.split(".", 1)
        return f"{year}-{int(m):02d}", freq
    return value, freq


def _int_or_none(text: str | None) -> int | None:
    try:
        return int(text) if text not in (None, "") else None
    except (TypeError, ValueError):
        return None


# --- GetStructure -----------------------------------------------------------

def parse_structure(root: etree._Element) -> list[Source]:
    """<Structure> -> list[Source]. Skips any source/dimension not Status=Success."""
    sources: list[Source] = []
    for s in root.iter("Source"):
        if s.get("Status") not in (None, "Success"):
            continue
        dims = []
        for d in s.iter("Dimension"):
            if d.get("Status") not in (None, "Success"):
                continue
            dims.append(Dimension(
                id=d.get("Id"),
                key=d.get("Key"),
                name=clean_text(d.get("Name")),
                type=d.get("DimensionType"),
                last_updated=(d.get("LastUpdated") or "").strip(),
            ))
        period = s.find("Period")
        start = _int_or_none(period.findtext("StartYear")) if period is not None else None
        finish = _int_or_none(period.findtext("FinishYear")) if period is not None else None
        desc_el = s.find("Description")
        sources.append(Source(
            id=s.get("Id"),
            key=s.get("Key"),
            name=clean_text(s.get("Name")),
            description=clean_text(desc_el.text if desc_el is not None else ""),
            dimensions=dims,
            start_year=start,
            finish_year=finish,
        ))
    return sources


# --- GetDimensionElements ---------------------------------------------------

def _is_aggregate(el: etree._Element) -> bool:
    # A node is computed/aggregate iff its ISCALCULATED attr is non-empty
    # (it holds the contributing source keys). True leaves have it empty.
    return bool((el.get("ISCALCULATED") or "").strip())


def parse_elements(root: etree._Element) -> list[Element]:
    """<Dimension><Elements> hierarchy -> flat list with level/parent_key.

    Pre-order traversal preserves browse order; hierarchy is reconstructable from
    parent_key + level. Element attribute names (KEY/NAME/FULLNAME/UNIT) are upper-case.
    """
    out: list[Element] = []
    container = root.find("Elements")
    if container is None:
        return out

    def walk(node: etree._Element, level: int, parent_key: str | None) -> None:
        for el in node.findall("Element"):
            children = el.findall("Element")
            out.append(Element(
                key=el.get("KEY"),
                name=clean_text(el.get("NAME")),
                level=level,
                parent_key=parent_key,
                is_aggregate=_is_aggregate(el),
                has_children=bool(children),
                full_name=clean_text(el.get("FULLNAME")),
                unit=clean_text(el.get("UNIT")),
                timestamp=(el.get("TIMESTAMP") or "").strip(),
            ))
            if children:
                walk(el, level + 1, el.get("KEY"))

    walk(container, 0, None)
    return out


# --- GetData ----------------------------------------------------------------

# <Set> attributes that are NOT dimension keys; everything else is a dimension's selected
# element key. Period/UNITS are the meta attributes carried on every Set.
_SET_META_ATTRS = {"Period", "UNITS"}


def parse_data(root: etree._Element, *, dimension_ids: list[str] | None = None) -> list[Observation]:
    """<Series> of <Set> -> list[Observation]. Missing cells are simply absent.

    Every dimension key present on a <Set> is echoed into the observation's selection — not
    just the dimensions the caller selected. When a source dimension is LEFT OUT of the
    selection, the API still breaks it out into multiple rows (e.g. Milieu: communal /
    non-communal / total); without its key on each row those rows are silently ambiguous and
    an agent can grab the wrong one or double-count components + total. Surfacing all keys
    labels the breakout. (`dimension_ids` is retained for API compatibility but no longer
    filters — all dimension attributes are emitted.)
    """
    obs: list[Observation] = []
    for s in root.iter("Set"):
        period_token = s.get("Period") or ""
        display, freq = parse_period(period_token)
        raw = (s.text or "").strip()
        value_error = False
        if raw == "":
            value = None
        else:
            try:
                value = float(raw)
            except ValueError:
                # Present but unparsable text is malformed upstream content, not missingness.
                value = None
                value_error = True
        selection = {k: v for k, v in s.attrib.items() if k not in _SET_META_ATTRS}
        obs.append(Observation(
            period=display,
            frequency=freq,
            value=value,
            scale=s.get("UNITS") or "1",
            unit="",
            selection=selection,
            value_error=value_error,
        ))
    return obs


# --- GetDataBorders ---------------------------------------------------------

def parse_borders(root: etree._Element) -> dict:
    """<Borders> -> {start, finish} in whole years (always, even for sub-annual sources)."""
    return {
        "start_year": _int_or_none(root.findtext("Start")),
        "finish_year": _int_or_none(root.findtext("Finish")),
    }
