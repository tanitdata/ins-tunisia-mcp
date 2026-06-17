"""Mandatory source attribution for INS data.

The INS conditions d'utilisation (https://www.ins.tn/conditions-utilisation) are an
attribution-style open licence (CC-BY / ODC-BY compatible). Article 4 requires every
reuse to carry the producer name, a source reference, and the data's last-update date.
Because our output flows into an LLM that may render it anywhere, the attribution must
travel WITH the payload — it is a compliance field, not optional metadata, and consumers
must not strip or alter it.

`last_updated` is always derived from the actual data returned (element TIMESTAMP /
dimension LastUpdated); it is never hardcoded. The raw inputs arrive in two formats —
element TIMESTAMP as `dd/mm/yyyy HH:MM:SS`, dimension LastUpdated as ISO 8601 — so we
normalize to a single machine-facing representation (`YYYY-MM-DD`) here, the one choke point
every tool's attribution flows through. Reformatting only: the date itself is unchanged (it
stays the real update date, a licence-compliance requirement). If genuinely unavailable it is
null, with a note saying so — we never invent a date.
"""

from __future__ import annotations

import re

PRODUCER = "Institut National de la Statistique (INS), Tunisie"
PORTAL_URL = "http://dataportal.ins.tn/"
LICENSE_URL = "https://www.ins.tn/conditions-utilisation"
# Neutral wording: covers non-endorsement + no-warranty; must not imply this is official INS.
DISCLAIMER = (
    "Provided as-is; not an official INS publication and not endorsed by INS. "
    "May contain errors or gaps."
)

# dd/mm/yyyy[...] (element TIMESTAMP) and yyyy-mm-dd[...] (ISO dimension LastUpdated).
_DMY = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})")
_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")


def normalize_date(value: str | None) -> str | None:
    """Reformat a raw update date to ISO `YYYY-MM-DD`. Accepts dd/mm/yyyy and ISO inputs.

    Returns None for an empty value. If a non-empty value matches neither known format it is
    returned unchanged — it is still the real update date (compliance), just not reformatted.
    """
    if not value:
        return None
    m = _DMY.match(value)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    m = _ISO.match(value)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{mo}-{d}"
    return value.strip()


def attribution_block(last_updated: str | None) -> dict:
    """Build the attribution block. `last_updated` is the real date from the data, or None.

    The date is normalized to ISO `YYYY-MM-DD` regardless of which raw form it arrived in.
    """
    normalized = normalize_date(last_updated)
    block = {
        "producer": PRODUCER,
        "source_url": PORTAL_URL,
        "license_url": LICENSE_URL,
        "last_updated": normalized,
        "disclaimer": DISCLAIMER,
    }
    if not normalized:
        block["last_updated_note"] = (
            "no last-update date was available for this selection; not inferred"
        )
    return block
