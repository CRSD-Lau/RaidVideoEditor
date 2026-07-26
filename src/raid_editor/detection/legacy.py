"""Legacy 3.3.5 damage-activity fallback for logs without encounter markers."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime

from raid_editor.models import PullCandidate

_EVENT = re.compile(
    r"^\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+\d{1,2}:\d{2}:\d{2}"
    r"(?:\.\d+)?\s+(?P<event>[A-Z][A-Z0-9_]*),"
)
_ACTIVE_EVENTS = frozenset(
    {
        "DAMAGE_SHIELD",
        "DAMAGE_SPLIT",
        "ENVIRONMENTAL_DAMAGE",
        "PARTY_KILL",
        "RANGE_DAMAGE",
        "RANGE_MISSED",
        "SPELL_BUILDING_DAMAGE",
        "SPELL_DAMAGE",
        "SPELL_MISSED",
        "SPELL_PERIODIC_DAMAGE",
        "SWING_DAMAGE",
        "SWING_MISSED",
        "UNIT_DIED",
    }
)


def detect_damage_activity(
    rows: Iterable[str],
    *,
    parse_line_time: Callable[[str, datetime], datetime | None],
    recording_started_at: datetime,
    recording_duration_seconds: float,
    recording_offset_seconds: float,
    minimum_pull_seconds: float,
    merge_gap_seconds: float,
) -> list[PullCandidate]:
    """Cluster hostile events without asserting that an unknown cluster is a boss."""

    clusters: list[tuple[datetime, datetime, int]] = []
    start: datetime | None = None
    previous: datetime | None = None
    events = 0
    for row in rows:
        match = _EVENT.match(row)
        if match is None or match.group("event") not in _ACTIVE_EVENTS:
            continue
        occurred_at = parse_line_time(row, recording_started_at)
        if occurred_at is None:
            continue
        if previous is None or (occurred_at - previous).total_seconds() > merge_gap_seconds:
            if start is not None and previous is not None:
                clusters.append((start, previous, events))
            start = occurred_at
            events = 0
        previous = occurred_at
        events += 1
    if start is not None and previous is not None:
        clusters.append((start, previous, events))

    pulls: list[PullCandidate] = []
    for started_at, ended_at, event_count in clusters:
        start_seconds = (
            started_at - recording_started_at
        ).total_seconds() + recording_offset_seconds
        end_seconds = (ended_at - recording_started_at).total_seconds() + recording_offset_seconds
        start_seconds = max(0.0, start_seconds)
        end_seconds = min(recording_duration_seconds, end_seconds)
        if end_seconds - start_seconds < minimum_pull_seconds:
            continue
        confidence = 0.72 if event_count >= 100 else 0.58
        pulls.append(
            PullCandidate(
                id=f"legacy-{len(pulls) + 1:04d}",
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                type="unknown",
                result="unknown",
                confidence=confidence,
                evidence=[
                    "legacy_3.3.5_damage_activity",
                    f"hostile_event_count:{event_count}",
                ],
                include=True,
                title=f"Combat activity {len(pulls) + 1}",
                notes="Manual classification required; legacy log has no encounter boundary event.",
            )
        )
    return pulls
