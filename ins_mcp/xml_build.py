"""Build QueryMessage request envelopes. The only place request XML is constructed.

Shapes verified live against dataportal.ins.tn (see M1-prebuild-note.md):
  - GetStructure: empty <QueryMessage/> returns the full catalogue (SourceId ignored).
  - GetDimensionElements: <DataWhere><DimensionId WithData='true'>{Id}</DimensionId></DataWhere>
    (dimension is addressed by its string Id, not Key).
  - GetData / GetDataBorders: <QueryMessage SourceId='{Id}'> with an optional <Period>
    (Frequency REQUIRED when present; codes Y|Q) and a <DataWhere> of <Dimension Id=…>
    blocks holding <Element>{KEY}</Element> selections.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

# Map our frequency vocabulary to the API's Frequency codes. Y and Q are confirmed
# to return data here. M (monthly) is an ACCEPTED code (request succeeds) but returned
# no data on tested sources — we pass it through rather than reject it, since empty is
# not the same as unsupported. S (semestrial) is genuinely rejected (IncorrectRequest)
# and is left out so the builder fails fast.
FREQUENCY_CODES = {
    "annual": "Y", "quarterly": "Q", "monthly": "M",
    "Y": "Y", "Q": "Q", "M": "M",
}


def build_structure() -> str:
    """Full catalogue in one call."""
    return "<QueryMessage></QueryMessage>"


def build_dimension_elements(dimension_id: str) -> str:
    """Codelist for one dimension, addressed by string Id. Always WithData='true'."""
    return (
        "<QueryMessage><DataWhere>"
        f"<DimensionId WithData={quoteattr('true')}>{escape(dimension_id)}</DimensionId>"
        "</DataWhere></QueryMessage>"
    )


def _period(start: str | None, end: str | None, frequency: str | None) -> str:
    """Render <Period>. Frequency is required by the API when a period is given.

    If no frequency is supplied we omit the whole element, which makes the server
    return the source's full native range at its native frequency.
    """
    if frequency is None:
        return ""
    code = FREQUENCY_CODES.get(frequency)
    if code is None:
        raise ValueError(f"unsupported frequency {frequency!r}; use one of {sorted(FREQUENCY_CODES)}")
    attrs = [f"Frequency={quoteattr(code)}"]
    if start is not None:
        attrs.insert(0, f"From={quoteattr(str(start))}")
    if end is not None:
        attrs.insert(1 if start is not None else 0, f"To={quoteattr(str(end))}")
    return f"<Period {' '.join(attrs)}></Period>"


def _data_where(selections: dict[str, list]) -> str:
    parts = ["<DataWhere>"]
    for dim_id, keys in selections.items():
        parts.append(f"<Dimension Id={quoteattr(dim_id)}>")
        for key in keys:
            parts.append(f"<Element>{escape(str(key))}</Element>")
        parts.append("</Dimension>")
    parts.append("</DataWhere>")
    return "".join(parts)


def build_data(
    source_id: str,
    selections: dict[str, list],
    *,
    start: str | None = None,
    end: str | None = None,
    frequency: str | None = None,
) -> str:
    """GetData request. `selections` maps dimension Id -> list of element KEYs."""
    return (
        f"<QueryMessage SourceId={quoteattr(source_id)}>"
        f"{_period(start, end, frequency)}"
        f"{_data_where(selections)}"
        "</QueryMessage>"
    )


def build_borders(source_id: str, selections: dict[str, list]) -> str:
    """GetDataBorders request. Needs a DataWhere selection to report a real range."""
    return (
        f"<QueryMessage SourceId={quoteattr(source_id)}>"
        f"{_data_where(selections)}"
        "</QueryMessage>"
    )
