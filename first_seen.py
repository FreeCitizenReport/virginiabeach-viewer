"""Shared helper for stamping `firstSeenAt` on inmate records.

The canonical rule is in ~/richmond-scraper/SORT_RULE.md. Short version:

    firstSeenAt = the instant our scraper first wrote this record to data.json.
    Stored as ISO 8601 with Eastern offset, seconds precision:
        "2026-04-18T13:32:14-04:00"
    Stamped ONCE on first write. NEVER overwritten on re-scrape.

Every scraper calls `ensure_first_seen(new, existing)` for each record right
before pushing `data.json`. Viewers sort on `firstSeenAt DESC`.

This file is duplicated into each GH-hosted scraper repo (rrj-viewer/first_seen.py,
henrico-viewer/first_seen.py, etc.) so each repo is self-contained.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def now_eastern_iso() -> str:
    """Return the current time in Eastern as an ISO-8601 string with offset.

    Example: "2026-04-18T13:32:14-04:00".
    """
    return datetime.now(EASTERN).isoformat(timespec="seconds")


def ensure_first_seen(
    new_rec: dict,
    existing_rec: Mapping | None = None,
    now: str | None = None,
) -> dict:
    """Stamp `firstSeenAt` on a single record.

    - If the existing record already has `firstSeenAt`, carry it forward.
    - Otherwise, if the new record doesn't already have it, stamp `now`.
    - `now` defaults to the current Eastern time.

    Returns the same dict, mutated in place (and returned for chaining).
    """
    if existing_rec and existing_rec.get("firstSeenAt"):
        new_rec["firstSeenAt"] = existing_rec["firstSeenAt"]
        return new_rec
    if not new_rec.get("firstSeenAt"):
        new_rec["firstSeenAt"] = now or now_eastern_iso()
    return new_rec


def stamp_all(
    new_records: Iterable[dict],
    existing_records: Iterable[Mapping] | None = None,
    key: str = "jacket",
    now: str | None = None,
) -> list[dict]:
    """Stamp firstSeenAt across a full roster.

    `new_records`: the roster we're about to push.
    `existing_records`: the previous data.json (to preserve existing stamps).
    `key`: field that identifies a record across scrapes (default "jacket").
    `now`: override timestamp (handy in tests); defaults to current Eastern time.

    Records without a value for `key` are stamped unconditionally (we have no
    way to match them to their previous version, so we treat them as new). In
    practice this shouldn't happen — every scraper emits a jacket.
    """
    now = now or now_eastern_iso()
    by_key: dict = {}
    if existing_records is not None:
        for r in existing_records:
            k = r.get(key)
            if k:
                by_key[k] = r
    out: list[dict] = []
    for r in new_records:
        k = r.get(key)
        existing = by_key.get(k) if k else None
        ensure_first_seen(r, existing, now=now)
        out.append(r)
    return out


__all__ = ["EASTERN", "now_eastern_iso", "ensure_first_seen", "stamp_all"]
