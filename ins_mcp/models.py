"""Dataclasses for the cleaned, JSON-facing domain. No XML leaks past here."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Dimension:
    id: str  # @Id — the stable string identifier used to address the dimension
    key: str  # @Key
    name: str  # cleaned @Name
    type: str  # DimensionType: Indicator | Region | Custom
    last_updated: str = ""  # @LastUpdated (ISO), for attribution; "" if absent


@dataclass(frozen=True)
class Source:
    id: str  # @Id (curated like C_NSO, or OBJ<number>)
    key: str  # @Key
    name: str
    description: str
    dimensions: list[Dimension] = field(default_factory=list)
    start_year: int | None = None  # Period.StartYear (NOT availability)
    finish_year: int | None = None  # Period.FinishYear (horizon for projections)
    # Whether the cube actually serves values via the API. Set false ONLY on a clean root
    # GetDataBorders probe returning the empty sentinel (a defined-but-unpopulated shell, e.g.
    # the 2014-census household cross-tabs). Default True is the FAIL-OPEN invariant: an
    # unprobed source, or any probe that errors/times out/looks ambiguous, must read as
    # has_data=True — never silently false, which would hide real data. Distinct from
    # declared_range, which stays as-is: declared_range says "INS declares this period",
    # has_data says "but values are (not) actually served".
    has_data: bool = True

    @property
    def measure_dimension(self) -> Dimension | None:
        """The Indicator dimension, resolved by TYPE not by label."""
        return next((d for d in self.dimensions if d.type == "Indicator"), None)

    def to_json(self) -> dict:
        d = asdict(self)
        d["measure_dimension_id"] = self.measure_dimension.id if self.measure_dimension else None
        return d


@dataclass(frozen=True)
class Element:
    """One codelist node. Hierarchy via parent_key/level; aggregate via is_aggregate."""

    key: str
    name: str
    level: int
    parent_key: str | None
    is_aggregate: bool  # True iff ISCALCULATED non-empty (a total/computed node)
    has_children: bool
    full_name: str = ""
    unit: str = ""  # indicator UNIT attribute, e.g. "Hectar"
    timestamp: str = ""  # @TIMESTAMP (dd/mm/yyyy ...), the element's last-update date


@dataclass(frozen=True)
class Observation:
    """One data cell. Frequency-aware: period + frequency travel with each value,
    as do unit and scale (never hoisted to the response top level)."""

    period: str  # the bare period token, e.g. "2000" or "2015-Q1"
    frequency: str  # "annual" | "quarterly" | raw prefix if unknown
    value: float | None
    scale: str  # the UNITS ("Échelle") attribute, default "1"; no numeric factor exists
    unit: str  # measurement unit label (may be filled by the tool layer)
    selection: dict[str, str]  # dim_id -> element KEY for this cell
    # True when a <Set> had non-empty text that failed to parse as a number. Distinguishes
    # malformed upstream content from legitimate missingness (INS omits missing cells), so a
    # null here is never silently confused with "no data".
    value_error: bool = False

    def to_json(self) -> dict:
        d = {
            "period": self.period,
            "frequency": self.frequency,
            "value": self.value,
            "scale": self.scale,
            "unit": self.unit,
        }
        if self.value_error:
            d["value_error"] = True
        d.update(self.selection)
        return d
