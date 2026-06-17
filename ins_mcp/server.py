"""FastMCP server. Tools take structured params and return JSON — never XML.

Identifiers are native and positionally unambiguous:
  - dimensions are addressed by their string `Id` (e.g. "RDS_DICT_INDICATORS_NSO"),
  - elements by their numeric `KEY` (e.g. "27523489").
`selections` maps dimension Id -> list of element KEYs.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import xml_build, xml_parse
from .attribution import attribution_block
from .catalogue import Catalogue
from .transport import InsApiError, post

mcp = FastMCP("ins-data-portal")
_cat: Catalogue | None = None

# Maps the requested frequency vocabulary to the canonical grain label parse_period emits,
# so we can detect a returned grain that differs from the one requested.
_FREQUENCY_GRAIN = {
    "annual": "annual", "Y": "annual",
    "quarterly": "quarterly", "Q": "quarterly",
    "monthly": "monthly", "M": "monthly",
}


def catalogue() -> Catalogue:
    global _cat
    if _cat is None:
        _cat = Catalogue()
    return _cat


def _element_json(e) -> dict:
    return {
        "key": e.key, "name": e.name, "level": e.level, "parent_key": e.parent_key,
        "is_aggregate": e.is_aggregate, "has_children": e.has_children,
        "full_name": e.full_name, "unit": e.unit,
    }


@mcp.tool()
def ins_list_sources() -> list[dict]:
    """List every data source (cube) in the INS catalogue, with its dimensions and period.

    Period start/finish are whole years and are NOT data availability — for
    projection sources finish is a horizon. Use ins_get_source for true availability.

    Each source carries `has_data`. `false` is authoritative — a defined-but-unpopulated shell
    (full structure published, no values served via the API, e.g. some 2014-census household
    cross-tabs); skip it rather than drilling its dimensions. `true` means "populated, OR not
    yet probed since the last reseed" — the flag is filled in by a background pass after a
    reseed, and reads fail-open (`true`) until a source's probe completes. So only `false`
    carries information; `true` may be provisional for a short window after a reseed.
    """
    return [s.to_json() for s in catalogue().list_sources()]


@mcp.tool()
def ins_search_sources(query: str) -> list[dict]:
    """Find a data source (cube) by topic. Accent/case-insensitive substring match over
    source TITLES and descriptions only — NOT over the indicators inside them.

    A specific measure like "superficie agricole" or "taux de chômage" is an INDICATOR
    living inside a broad source, so it will NOT be found here. Typical flow:
      1. search/browse for the broad source by theme (e.g. "socio-économique", "emploi",
         "comptes nationaux"); the catch-all socio-economic cube is `C_NSO`.
      2. then call ins_get_dimension_elements(source_id, measure_dimension_id, search=...)
         to find the actual indicator KEY inside it.
    Returns each source's id, name, dimensions (+ measure_dimension_id) and declared range.
    """
    return [s.to_json() for s in catalogue().search_sources(query)]


@mcp.tool()
def ins_get_source(source_id: str) -> dict:
    """Full detail for one source: dimensions (+ types) and its declared period.

    `declared_range` is the source's declared StartYear/FinishYear. It MAY be a
    projection horizon (e.g. 2050) and actual data coverage varies by indicator — it is
    NOT availability. To see real coverage, call ins_get_data and read its `coverage`
    summary (per-selection availability is a planned tool). No live probe runs here.

    `has_data` (also on ins_list_sources): `false` is the authoritative "this cube serves no
    values via the API" signal — skip it. `true` means "populated, OR not yet probed since the
    last reseed" (the flag is populated by a background pass after a reseed and reads fail-open
    `true` until each probe completes), so only `false` is informative. It layers WITH
    declared_range, not instead of it — e.g. `declared_range: 2014-2014` + `has_data: false`
    reads as "INS declares 2014 coverage, but no values are actually served" (published but
    unpopulated), which is more informative than either alone. fail-open: `false` only when
    emptiness was cleanly proven, never on an error or before probing.

    `source_id` is the source @Id (e.g. "C_NSO"). An unknown id returns a structured
    {error:"unknown_source"} (consistent with the other tools), not null.
    """
    source = catalogue().get_source(source_id)
    if source is None:
        return {"source_id": source_id, "error": "unknown_source",
                "message": f"no source with id {source_id!r}; use ins_search_sources to find one",
                "attribution": attribution_block(None)}
    out = source.to_json()
    out["declared_range"] = {
        "start_year": source.start_year,
        "finish_year": source.finish_year,
        "note": "declared range — may be a projection horizon; actual coverage varies by indicator",
    }
    # Source-level attribution: best available update date is the measure dimension's
    # LastUpdated (the most meaningful per-source signal we cache).
    measure = source.measure_dimension
    out["attribution"] = attribution_block(measure.last_updated if measure else None)
    return out


@mcp.tool()
def ins_get_dimension_elements(
    source_id: str, dimension_id: str,
    parent_key: str | None = None, search: str | None = None,
) -> list[dict] | dict:
    """Find the element KEYs to use in an ins_get_data selection, by searching or browsing
    one dimension's codelist. This is how you turn a word ("superficie agricole", "Ariana")
    into the KEY ins_get_data needs.

    Codelists are hierarchical and large (thousands of nodes), so this never dumps the whole
    tree — pick ONE of:
      - `search` (recommended first move): accent/case-insensitive substring over names/full
        names across ALL levels. Use the source's `measure_dimension_id` to find an indicator,
        or the Region dimension to find a place. Matching is tolerant ("utilisee" finds
        "utilisée"), but it is still substring — try a distinctive word, not the whole phrase.
      - `parent_key`: drill-down — direct children of that node (omit for the TOP level, which
        for regions is just the single "Tunisie" total; pass its key "0" to get the regions,
        then a region's key to get its gouvernorats, etc.).
    `search` takes precedence if both are given. `is_aggregate` flags total/computed nodes
    (e.g. region KEY "0" = "Tunisie", the all-regions total); `has_children` tells you whether
    you can drill further.

    `dimension_id` is the dimension @Id; returned `key` values are element KEYs for
    ins_get_data selections. Returns a list of nodes (possibly empty — no children / no search
    match is a valid empty result, not an error). An unknown source or a dimension that is not
    part of the source returns a structured {error} instead, so a real not-found is never
    confused with an empty match.
    """
    cat = catalogue()
    source = cat.get_source(source_id)
    if source is None:
        return {"source_id": source_id, "error": "unknown_source",
                "message": f"no source with id {source_id!r}; use ins_search_sources to find one"}
    valid_dims = {d.id for d in source.dimensions}
    if dimension_id not in valid_dims:
        return {"source_id": source_id, "error": "unknown_dimension",
                "message": f"dimension id {dimension_id!r} is not part of {source_id}; "
                           f"valid dimension ids: {sorted(valid_dims)}"}
    elements = cat.browse_elements(
        source_id, dimension_id, parent_key=parent_key, search=search,
    )
    return [_element_json(e) for e in elements]


def _error(source_id: str, code: str, message: str) -> dict:
    """Structured tool-level error. Carries attribution (compliance) and empty data shape."""
    return {"source_id": source_id, "error": code, "message": message,
            "observations": [], "coverage": _empty_coverage(),
            "attribution": attribution_block(None)}


def _validate_selection(source, source_id: str, selections: dict[str, list[str]]) -> dict | None:
    """Shared pre-call validation for selection-scoped tools (ins_get_data, ins_get_availability).

    Returns a structured error dict if the request is invalid, else None. Catches the traps
    where the API would otherwise mislead: an unknown dimension id is ignored by the API and
    yields the ENTIRE source; an empty per-dimension key list emits an empty <Dimension> block
    that the API may treat as "all".
    """
    if source is None:
        return _error(source_id, "unknown_source",
                      f"no source with id {source_id!r}; use ins_search_sources to find one")
    if not selections:
        return _error(source_id, "empty_selection",
                      "selections is empty; provide at least the measure dimension, "
                      "e.g. {measure_dimension_id: [element_key]}")
    valid_dims = {d.id for d in source.dimensions}
    unknown = [d for d in selections if d not in valid_dims]
    if unknown:
        return _error(source_id, "unknown_dimension",
                      f"dimension id(s) {unknown} are not part of {source_id}; "
                      f"valid dimension ids: {sorted(valid_dims)}")
    empty_dims = [d for d, keys in selections.items() if not keys]
    if empty_dims:
        return _error(source_id, "empty_dimension_selection",
                      f"dimension id(s) {empty_dims} have no element keys; an empty list emits "
                      "an empty selection the API may treat as 'all'. Provide at least one key "
                      "per selected dimension (use ins_get_dimension_elements to find keys).")
    return None


@mcp.tool()
def ins_get_data(
    source_id: str,
    selections: dict[str, list[str]],
    start: str | None = None,
    end: str | None = None,
    frequency: str | None = None,
) -> dict:
    """Fetch observations as tidy records.

    `selections` maps dimension @Id -> list of element KEYs (from
    ins_get_dimension_elements). `frequency` is "annual" or "quarterly" ("monthly"/`M` is
    accepted by the API but returns no data on known sources); omit it to get the source's
    full native range. `start`/`end` accept a year ("2010") and REQUIRE a frequency — a
    window without one returns a structured {error:"frequency_required"} rather than silently
    falling back to the full native range.

    Each observation carries its own `period`, `frequency`, `value`, `unit` and `scale`
    (scale = the Échelle/UNITS attribute, default "1"; the order-of-magnitude lives in the
    `unit` label, so `unit` is always populated and never dropped). Missing cells are absent
    from the result, so a present value of 0 is a real 0. The response also includes a
    `coverage` summary {start, end, count} over the returned observations.

    The response ALWAYS includes an `attribution` block (producer, source_url, license_url,
    last_updated, disclaimer). This is a MANDATORY licence-compliance field under the INS
    conditions d'utilisation — when presenting INS data to a user, reproduce the producer,
    source and last_updated, and do not strip, alter, or contradict the disclaimer.
    `last_updated` is the real update date of the data returned (latest element TIMESTAMP,
    falling back to the measure dimension's LastUpdated), or null if none is available.

    Returns {source_id, observations:[...], coverage:{...}, attribution:{...}}.
    """
    cat = catalogue()
    source = cat.get_source(source_id)

    # Shared selection validation (unknown source / empty selection / unknown dim / empty
    # per-dim key list) — structured errors before any API call.
    invalid = _validate_selection(source, source_id, selections)
    if invalid is not None:
        return invalid
    # A window without a frequency is the silent-wrong-grain trap: the API ignores bounds
    # when no Frequency is present and returns the full native range, which the caller would
    # read as their requested window. Refuse rather than guess a grain.
    if (start is not None or end is not None) and frequency is None:
        return _error(source_id, "frequency_required",
                      "start/end were given without a frequency; the API ignores a window "
                      "unless a grain is set. Specify frequency=annual or frequency=quarterly "
                      "(or omit start/end for the full native range).")

    dimension_ids = list(selections.keys())

    # Frequency-window trap: the API only slices by Frequency when a From/To window is
    # present. With a frequency but no window it returns a MIX of native grains (annual +
    # quarterly), silently ignoring the requested grain. So when a frequency is requested
    # without bounds, fill the window from the source's declared range to force the grain.
    notes = []
    eff_start, eff_end = start, end
    if frequency is not None and start is None and end is None:
        if source and source.start_year is not None and source.finish_year is not None:
            eff_start, eff_end = str(source.start_year), str(source.finish_year)
            notes.append(
                f"no window given with frequency={frequency!r}; applied the source's declared "
                f"range {eff_start}-{eff_end} so the grain is honored (the API ignores "
                "frequency without a window)"
            )
        else:
            notes.append(
                f"frequency={frequency!r} requested without a window and the source has no "
                "declared range to apply; the API may return mixed grains — pass start/end"
            )

    # Translate builder/transport failures into structured errors at the tool boundary, so
    # the agent never sees a raw exception or the hidden transport layer (B1). The State-based
    # raise stays the source of truth in transport; we only convert it here.
    try:
        body = xml_build.build_data(
            source_id, selections, start=eff_start, end=eff_end, frequency=frequency,
        )
    except ValueError as exc:
        return _error(source_id, "unsupported_frequency", str(exc))
    try:
        root = post("GetData", body)
    except InsApiError as exc:
        return _error(source_id, "api_rejected",
                      f"the data portal rejected this request ({exc}). Check that the element "
                      "keys exist in their dimensions (use ins_get_dimension_elements).")
    observations = xml_parse.parse_data(root, dimension_ids=dimension_ids)

    # Guard: a grain different from the one requested must never be returned silently —
    # whether the response MIXES grains, or is uniformly the WRONG grain (e.g. all annual rows
    # for a quarterly request). Flag both against the requested, normalized grain.
    if frequency is not None and observations:
        returned = {o.frequency for o in observations}
        requested_grain = _FREQUENCY_GRAIN.get(frequency)
        if len(returned) > 1:
            notes.append(f"requested frequency was not cleanly applied; response mixes {sorted(returned)}")
        elif requested_grain is not None and returned != {requested_grain}:
            notes.append(
                f"requested frequency={frequency!r} ({requested_grain}) but the response is "
                f"{sorted(returned)[0]!r} — the source does not serve the requested grain"
            )

    # Enrich each observation's unit from the measure element, and collect the elements'
    # update timestamps for the attribution block (the real "date of last update").
    measure_elems = _measure_elements(cat, source)
    measure = source.measure_dimension
    measure_id = measure.id if measure else None
    out = []
    seen_timestamps: set[str] = set()
    for o in observations:
        j = o.to_json()
        if measure_id and measure_id in o.selection:
            el = measure_elems.get(o.selection[measure_id])
            if el is not None:
                if el.unit:
                    j["unit"] = el.unit
                if el.timestamp:
                    seen_timestamps.add(el.timestamp)
        out.append(j)

    periods = [o.period for o in observations]
    coverage = {
        "start": min(periods) if periods else None,
        "end": max(periods) if periods else None,
        "count": len(out),
    }
    last_updated = _latest_update(
        sorted(seen_timestamps), measure.last_updated if measure else "",
    )
    result = {
        "source_id": source_id,
        "observations": out,
        "coverage": coverage,
        "attribution": attribution_block(last_updated),
    }
    # A valid selection that yields nothing is NOT an error — it's "no data here". Say so
    # explicitly so an agent reads it as a fact about the data, not a failed call.
    if not out:
        notes.append("no data for this selection (valid request, but the cube has no values "
                     "for this combination/period); this is not an error")
    if notes:
        result["notes"] = notes
    return result


def _empty_coverage() -> dict:
    return {"start": None, "end": None, "count": 0}


def _measure_elements(cat: Catalogue, source) -> dict[str, object]:
    """Measure dimension elements keyed by KEY (carries unit + timestamp for attribution)."""
    measure = source.measure_dimension
    if measure is None:
        return {}
    return {e.key: e for e in cat.get_elements(source.id, measure.id)}


def _latest_update(timestamps: list[str], fallback: str) -> str | None:
    """Pick the most recent update date from element TIMESTAMPs (dd/mm/yyyy HH:MM:SS),
    falling back to the measure dimension's LastUpdated (ISO). Returns the raw string of
    the latest, or None if nothing is available. Never invents a date."""
    def sort_key(ts: str):
        ts = ts.strip()
        # dd/mm/yyyy [HH:MM:SS]
        if "/" in ts:
            date = ts.split(" ", 1)[0]
            parts = date.split("/")
            if len(parts) == 3:
                d, m, y = parts
                return (y, m.zfill(2), d.zfill(2))
        # ISO yyyy-mm-ddTHH:MM:SS -> already lexically sortable
        if len(ts) >= 10 and ts[4] == "-":
            return (ts[0:4], ts[5:7], ts[8:10])
        return ("", "", "")

    candidates = [t for t in timestamps if t]
    if not candidates and fallback:
        candidates = [fallback]
    if not candidates:
        return None
    return max(candidates, key=sort_key)


@mcp.tool()
def ins_get_availability(source_id: str, selections: dict[str, list[str]]) -> dict:
    """Real data coverage (start/finish years) for a SPECIFIC selection, via GetDataBorders.

    This is the true-availability answer that ins_get_source's `declared_range` deliberately
    does not give: the declared range may be a projection horizon (e.g. 2050), while actual
    coverage is a property of which elements you select. `selections` maps dimension @Id ->
    list of element KEYs, same as ins_get_data.

    Borders are always reported in whole years even for sub-annual sources. A `1899` sentinel
    (or 0/empty) means the API has no range for the selection → returned as null with
    `available: false`.

    Validates the selection like ins_get_data (unknown source / empty selection / unknown dim /
    empty per-dim key list → structured {error}), and always includes the mandatory
    `attribution` block.
    """
    cat = catalogue()
    source = cat.get_source(source_id)

    invalid = _validate_selection(source, source_id, selections)
    if invalid is not None:
        # Reshape the data-tool error to the availability shape, preserving error + attribution.
        return {"source_id": source_id, "error": invalid["error"],
                "message": invalid["message"], "available": False,
                "start_year": None, "finish_year": None,
                "attribution": invalid["attribution"]}

    measure = source.measure_dimension
    attribution = attribution_block(measure.last_updated if measure else None)
    try:
        root = post("GetDataBorders", xml_build.build_borders(source_id, selections))
    except InsApiError as exc:
        return {"source_id": source_id, "error": "api_rejected",
                "message": f"the data portal rejected this request ({exc}). Check that the "
                           "element keys exist (use ins_get_dimension_elements).",
                "available": False, "start_year": None, "finish_year": None,
                "attribution": attribution}
    borders = xml_parse.parse_borders(root)
    start = borders.get("start_year")
    finish = borders.get("finish_year")
    if start in (None, 0, 1899):
        return {"source_id": source_id, "available": False,
                "start_year": None, "finish_year": None, "attribution": attribution}
    return {"source_id": source_id, "available": True,
            "start_year": start, "finish_year": finish, "attribution": attribution}


@mcp.tool()
def ins_refresh_catalogue() -> dict:
    """Re-pull the source catalogue from the live API. Returns the source count."""
    return {"sources": catalogue().refresh()}
