"""Stream timestamped combat-log events onto a recording timeline."""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

_LOG_TIME = re.compile(
    r"^\s*(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{2}|\d{4}))?\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?\s+(?P<payload>.+?)\s*$"
)


@dataclass(frozen=True, slots=True)
class TimedLogEvent:
    """One well-formed event with its position on the source recording."""

    video_seconds: float
    occurred_at: datetime
    event: str
    fields: tuple[str, ...]
    line_number: int

    @property
    def spell_id(self) -> int | None:
        """Return the spell identifier from legacy CLEU rows when present."""

        if self.event in {"SWING_DAMAGE", "SWING_MISSED", "UNIT_DIED"}:
            return None
        if len(self.fields) <= 7:
            return None
        try:
            return int(self.fields[7])
        except (TypeError, ValueError):
            return None


def _line_datetime(line: str, anchor: datetime) -> tuple[datetime, str] | None:
    match = _LOG_TIME.match(line.removeprefix("\ufeff"))
    if match is None:
        return None
    year_text = match.group("year")
    if year_text:
        year = int(year_text)
        year = year + 2000 if year < 100 else year
        candidates = [year]
    else:
        candidates = [anchor.year - 1, anchor.year, anchor.year + 1]
    parsed: list[datetime] = []
    for year in candidates:
        try:
            parsed.append(
                datetime(
                    year,
                    int(match.group("month")),
                    int(match.group("day")),
                    int(match.group("hour")),
                    int(match.group("minute")),
                    int(match.group("second")),
                    int(((match.group("fraction") or "") + "000000")[:6]),
                    tzinfo=anchor.tzinfo,
                )
            )
        except ValueError:
            continue
    if not parsed:
        return None
    return min(parsed, key=lambda value: abs(value - anchor)), match.group("payload")


def iter_timed_log_events(
    source: Path,
    *,
    recording_started_at: datetime,
    recording_duration_seconds: float,
    recording_offset_seconds: float = 0.0,
    margin_seconds: float = 60.0,
) -> Iterator[TimedLogEvent]:
    """Yield valid events in the recording window without loading the log."""

    path = source.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Combat log does not exist: {path}")
    margin = timedelta(seconds=margin_seconds)
    earliest = recording_started_at - timedelta(seconds=recording_offset_seconds) - margin
    latest = (
        recording_started_at
        - timedelta(seconds=recording_offset_seconds)
        + timedelta(seconds=recording_duration_seconds)
        + margin
    )
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw in enumerate(handle, start=1):
            parsed = _line_datetime(raw.rstrip("\r\n"), recording_started_at)
            if parsed is None:
                continue
            occurred_at, payload = parsed
            if occurred_at < earliest or occurred_at > latest:
                continue
            try:
                fields = tuple(
                    item.strip()
                    for item in next(csv.reader([payload], skipinitialspace=True, strict=True))
                )
            except (csv.Error, StopIteration):
                continue
            if not fields:
                continue
            video_seconds = (
                occurred_at - recording_started_at
            ).total_seconds() + recording_offset_seconds
            if not -margin_seconds <= video_seconds <= recording_duration_seconds + margin_seconds:
                continue
            yield TimedLogEvent(
                video_seconds=video_seconds,
                occurred_at=occurred_at,
                event=fields[0],
                fields=fields,
                line_number=line_number,
            )
