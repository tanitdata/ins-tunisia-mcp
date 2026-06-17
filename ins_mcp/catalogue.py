"""On-disk catalogue cache (SQLite) + keyword search.

One GetStructure call seeds sources + dimensions. Codelists are fetched lazily on
first GetDimensionElements and cached per dimension. Search is accent/case-insensitive
substring (via normalize.fold) over cleaned names/descriptions — no embeddings.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import fields
from pathlib import Path

import httpx

from . import xml_build, xml_parse
from .models import Dimension, Element, Source
from .normalize import fold
from .transport import post

# Bounded concurrency for the background has_data probe pass. Modest on purpose: parallel
# enough to finish in seconds-to-low-tens, but polite to a plain-HTTP government endpoint
# (never 78 simultaneous connections — that would hammer dataportal.ins.tn / risk throttling).
_PROBE_POOL_SIZE = 8

DB_PATH = Path(__file__).with_name("cache.db")

# Bump whenever the on-disk serialization of sources/dimensions/elements changes (e.g. a new
# model field). On mismatch the cache auto-heals: it is cleared and re-seeded on next use, so a
# stale cache.db can never crash the read tools with a cryptic Dimension/Element TypeError.
# v3: added the per-source has_data flag (root GetDataBorders probe at seed time) — a cache
# seeded before v3 re-probes on upgrade rather than showing a stale/absent flag.
CACHE_VERSION = "3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, key TEXT, name TEXT, description TEXT,
    start_year INTEGER, finish_year INTEGER, dimensions_json TEXT,
    has_data INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS elements (
    source_id TEXT, dimension_id TEXT, payload_json TEXT,
    PRIMARY KEY (source_id, dimension_id)
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

# Known field names per model, for tolerant deserialization (drop unknown keys, let dataclass
# defaults fill any missing). This is the backstop that makes the cache never crash even if the
# version bump above is forgotten.
_DIM_FIELDS = {f.name for f in fields(Dimension)}
_ELEM_FIELDS = {f.name for f in fields(Element)}


def _make(cls, allowed: set[str], data: dict):
    """Build a dataclass from cached JSON, dropping unknown keys so a forgotten cache-version
    bump degrades to defaults instead of crashing with an unexpected-keyword TypeError."""
    return cls(**{k: v for k, v in data.items() if k in allowed})


class Catalogue:
    def __init__(self, db_path: Path | str = DB_PATH, *, client: httpx.Client | None = None,
                 probe_in_background: bool = True):
        self.db_path = Path(db_path)
        self._client = client
        # When False (tests), the has_data probe pass runs synchronously inside refresh() so
        # results are deterministic without joining a thread. Production default is background.
        self._probe_in_background = probe_in_background
        self._probe_thread: threading.Thread | None = None
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._heal_if_stale()

    def close(self) -> None:
        self.conn.close()

    # --- seeding / refresh --------------------------------------------------

    def _cache_version(self) -> str | None:
        row = self.conn.execute("SELECT v FROM meta WHERE k = 'cache_version'").fetchone()
        return row["v"] if row else None

    def _heal_if_stale(self) -> None:
        """Rebuild a cache written by an older version so it re-seeds cleanly (C1).

        A cache with no/old version predates the current on-disk shape. We DROP and recreate
        the data tables (not just clear rows) so a SCHEMA change — like the v3 has_data column
        — takes effect; `CREATE TABLE IF NOT EXISTS` alone would leave an old table's columns
        in place and a row insert would mismatch. The empty cache then re-pulls on next use.
        """
        stored = self._cache_version()
        if stored != CACHE_VERSION:
            with self.conn:
                self.conn.execute("DROP TABLE IF EXISTS sources")
                self.conn.execute("DROP TABLE IF EXISTS elements")
                self.conn.executescript(_SCHEMA)
                self.conn.execute(
                    "INSERT OR REPLACE INTO meta VALUES ('cache_version', ?)", (CACHE_VERSION,)
                )

    def is_seeded(self) -> bool:
        return self.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] > 0

    def ensure_seeded(self) -> None:
        if not self.is_seeded():
            self.refresh()

    def refresh(self) -> int:
        """Re-pull the full catalogue from GetStructure. Returns source count.

        FAST PATH: one GetStructure call (~2s) seeds sources + dimensions and returns. Per-source
        has_data probes are NOT run inline (they used to add ~7 min of serial GetDataBorders
        calls, blocking the first tool call after a version bump). Each source is seeded with
        has_data=1 (fail-open default), then a background pass probes all of them concurrently
        and updates the column as results land. Clears cached elements and stamps the version.
        """
        root = post("GetStructure", xml_build.build_structure(), client=self._client)
        sources = xml_parse.parse_structure(root)
        with self.conn:
            self.conn.execute("DELETE FROM sources")
            self.conn.execute("DELETE FROM elements")
            self.conn.execute(
                "INSERT OR REPLACE INTO meta VALUES ('cache_version', ?)", (CACHE_VERSION,)
            )
            for s in sources:
                dims = [
                    {"id": d.id, "key": d.key, "name": d.name, "type": d.type,
                     "last_updated": d.last_updated}
                    for d in s.dimensions
                ]
                self.conn.execute(
                    "INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?)",
                    (s.id, s.key, s.name, s.description, s.start_year, s.finish_year,
                     json.dumps(dims, ensure_ascii=False), 1),  # 1 = fail-open until probed
                )
        # Populate has_data out-of-band. MUST run to completion for ALL sources (not lazy /
        # on-access) — otherwise an empty cube nobody has queried yet stays has_data=true, and
        # the empty cubes are exactly the ones a user is about to explore.
        if self._probe_in_background:
            self._probe_thread = threading.Thread(
                target=self._run_probe_pass, args=(sources,), daemon=True
            )
            self._probe_thread.start()
        else:
            self._run_probe_pass(sources)
        return len(sources)

    def wait_for_probes(self, timeout: float | None = None) -> None:
        """Block until the background has_data probe pass finishes (test/diagnostic helper)."""
        t = self._probe_thread
        if t is not None:
            t.join(timeout)

    def _run_probe_pass(self, sources: list[Source]) -> None:
        """Probe every source's has_data concurrently (bounded pool) and write each result as
        it completes. Uses a dedicated SQLite connection (sqlite3 connections are not shareable
        across threads) and applies all writes from THIS thread only — workers do network only.
        """
        writer = sqlite3.connect(self.db_path)
        try:
            with ThreadPoolExecutor(max_workers=_PROBE_POOL_SIZE) as pool:
                futures = {pool.submit(self._probe_has_data, s): s.id for s in sources}
                for fut in as_completed(futures):
                    sid = futures[fut]
                    try:
                        has_data = fut.result()
                    except Exception:
                        has_data = True  # fail-open: never write False on a failed probe
                    with writer:
                        writer.execute(
                            "UPDATE sources SET has_data = ? WHERE id = ?",
                            (1 if has_data else 0, sid),
                        )
        finally:
            writer.close()

    def _probe_has_data(self, source: Source) -> bool:
        """Root GetDataBorders probe → does this cube actually serve values? (network only)

        FAIL-OPEN invariant: returns True unless we get a CLEAN, successful borders response
        whose range is the empty sentinel (None/0/1899). Any error, missing measure, or
        unexpected shape returns True — we never write has_data=False on a probe that didn't
        cleanly prove emptiness, because a wrong False hides real data.

        ZERO-FETCH probe: the measure (Indicator) dimension is carried with an EMPTY element
        list (borders across ALL members) + national region "0" where a Region dim exists. No
        codelist download — only the dimension id from the seed and the known region key are
        needed. This both avoids the multi-MB codelist fetch the old leaf probe paid per source
        (cutting the background pass from minutes to tens of seconds) AND sidesteps the
        aggregate-node trap entirely: no specific node is selected, so there is no risk of a
        theme node's sentinel false-flagging a populated cube. Verified against all 78 sources
        to reproduce the leaf probe's classification exactly (same 10 empties, zero populated
        cubes mislabeled) — see scratch/verify_lightprobe.

        Runs on background worker threads, so it must NOT touch self.conn; `post` creates its
        own client per call.
        """
        try:
            measure = source.measure_dimension
            if measure is None:
                return True  # can't probe → unknown → fail open
            sel: dict[str, list] = {measure.id: []}  # all members, no codelist fetch
            region = next((d for d in source.dimensions if d.type == "Region"), None)
            if region is not None:
                sel[region.id] = ["0"]
            root = post("GetDataBorders", xml_build.build_borders(source.id, sel))
            borders = xml_parse.parse_borders(root)
            start = borders.get("start_year")
            # Empty sentinel → genuinely no data. Anything else → has data.
            return start not in (None, 0, 1899)
        except Exception:
            # Network hiccup, new cube shape, changed API response, parse error — all fail open.
            return True

    # --- sources ------------------------------------------------------------

    def _row_to_source(self, row: sqlite3.Row) -> Source:
        dims = [_make(Dimension, _DIM_FIELDS, d) for d in json.loads(row["dimensions_json"])]
        # Fail-open on read too: a missing/null has_data column (older row) reads as True.
        keys = row.keys()
        has_data = bool(row["has_data"]) if "has_data" in keys and row["has_data"] is not None else True
        return Source(
            id=row["id"], key=row["key"], name=row["name"], description=row["description"],
            dimensions=dims, start_year=row["start_year"], finish_year=row["finish_year"],
            has_data=has_data,
        )

    def list_sources(self) -> list[Source]:
        self.ensure_seeded()
        rows = self.conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
        return [self._row_to_source(r) for r in rows]

    def get_source(self, source_id: str) -> Source | None:
        self.ensure_seeded()
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._row_to_source(row) if row else None

    def search_sources(self, query: str) -> list[Source]:
        """Accent/case-insensitive substring match over source name, description and id."""
        self.ensure_seeded()
        q = fold(query)
        if not q:
            return []
        out = []
        for s in self.list_sources():
            haystack = fold(f"{s.name} {s.description} {s.id}")
            if q in haystack:
                out.append(s)
        return out

    # --- dimension elements (lazy, cached) ----------------------------------

    def get_elements(self, source_id: str, dimension_id: str) -> list[Element]:
        """Cached codelist for a dimension. Fetched once, reused (shared region
        codelist is identical across sources, so we key by dimension_id too)."""
        row = self.conn.execute(
            "SELECT payload_json FROM elements WHERE source_id = ? AND dimension_id = ?",
            (source_id, dimension_id),
        ).fetchone()
        # The shared region dict (RDS_DICT_REGIONS_NSO) is reusable across sources.
        if row is None:
            shared = self.conn.execute(
                "SELECT payload_json FROM elements WHERE dimension_id = ? LIMIT 1",
                (dimension_id,),
            ).fetchone()
            row = shared
        if row is not None:
            return [_make(Element, _ELEM_FIELDS, e) for e in json.loads(row["payload_json"])]

        root = post(
            "GetDimensionElements",
            xml_build.build_dimension_elements(dimension_id),
            client=self._client,
        )
        elements = xml_parse.parse_elements(root)
        payload = json.dumps([e.__dict__ for e in elements], ensure_ascii=False)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO elements VALUES (?,?,?)",
                (source_id, dimension_id, payload),
            )
        return elements

    def browse_elements(
        self, source_id: str, dimension_id: str,
        *, parent_key: str | None = None, search: str | None = None,
    ) -> list[Element]:
        """Drill-down (parent_key) for browsing, OR keyword search-within-dimension.

        search wins if both are given. Search is a substring match over name/full_name
        across the whole cached codelist (any level); drill-down returns direct children
        of parent_key (top level when None).
        """
        elements = self.get_elements(source_id, dimension_id)
        if search:
            q = fold(search)
            return [e for e in elements if q in fold(e.name) or q in fold(e.full_name)]
        return [e for e in elements if e.parent_key == parent_key]
