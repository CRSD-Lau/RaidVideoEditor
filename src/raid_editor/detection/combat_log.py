"""Deterministic pull detection from World of Warcraft combat logs.

WoW combat-log rows do not contain a year.  The parser therefore requires the
recording start as an anchor and resolves each row to the closest sensible
calendar year, including recordings that cross New Year's Eve.

``recording_offset_seconds`` is deliberately explicit: a positive value moves
combat-log events later on the recording timeline, and a negative value moves
them earlier.
"""

from __future__ import annotations

import csv
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

ENCOUNTER_START = "ENCOUNTER_START"
ENCOUNTER_END = "ENCOUNTER_END"
PLAYER_REGEN_DISABLED = "PLAYER_REGEN_DISABLED"
PLAYER_REGEN_ENABLED = "PLAYER_REGEN_ENABLED"

PULL_BOUNDARY_EVENTS = frozenset(
    {
        ENCOUNTER_START,
        ENCOUNTER_END,
        PLAYER_REGEN_DISABLED,
        PLAYER_REGEN_ENABLED,
    }
)

_TIMESTAMPED_ROW = re.compile(
    r"""
    ^\s*
    (?P<month>\d{1,2})/
    (?P<day>\d{1,2})
    (?:/(?P<year>\d{2}|\d{4}))?
    \s+
    (?P<hour>\d{1,2}):
    (?P<minute>\d{2}):
    (?P<second>\d{2})
    (?:\.(?P<fraction>\d{1,9}))?
    \s+
    (?P<payload>.+?)
    \s*$
    """,
    re.VERBOSE,
)
_EVENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_TIMESTAMP_LIKE_PREFIX = re.compile(r"^\s*\d{1,2}/")


class PullType(StrEnum):
    """The broad kind of combat window."""

    BOSS = "boss"
    TRASH = "trash"


class PullResult(StrEnum):
    """The deterministic result available from the combat log."""

    KILL = "kill"
    WIPE = "wipe"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Encounter:
    """Stable encounter identity from ENCOUNTER_START/ENCOUNTER_END."""

    id: int
    name: str

    def __post_init__(self) -> None:
        if self.id <= 0:
            raise ValueError("encounter id must be positive")
        if not self.name.strip():
            raise ValueError("encounter name must not be blank")


@dataclass(frozen=True, slots=True)
class CombatLogEvent:
    """One timestamped combat-log event."""

    occurred_at: datetime
    event: str
    fields: tuple[str, ...]
    line_number: int
    raw_line: str


@dataclass(frozen=True, slots=True)
class CombatLogIssue:
    """A row that looked relevant but could not safely be interpreted."""

    line_number: int
    raw_line: str
    reason: str


@dataclass(frozen=True, slots=True)
class CombatPull:
    """A deterministic boss attempt or non-boss combat window."""

    start_seconds: float
    end_seconds: float
    type: PullType
    result: PullResult
    encounter: Encounter | None
    evidence: tuple[str, ...]
    confidence: float
    include: bool
    attempt_number: int | None
    started_at: datetime
    ended_at: datetime

    def __post_init__(self) -> None:
        if not math.isfinite(self.start_seconds) or not math.isfinite(self.end_seconds):
            raise ValueError("pull boundaries must be finite")
        if self.end_seconds < self.start_seconds:
            raise ValueError("pull end must not precede pull start")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.type is PullType.BOSS:
            if self.encounter is None:
                raise ValueError("boss pulls require encounter metadata")
            if self.attempt_number is None or self.attempt_number <= 0:
                raise ValueError("boss pulls require a positive attempt number")
        elif self.encounter is not None or self.attempt_number is not None:
            raise ValueError("trash pulls cannot have encounter attempt metadata")

    @property
    def duration_seconds(self) -> float:
        """Return the clamped duration on the recording timeline."""

        return self.end_seconds - self.start_seconds


@dataclass(frozen=True, slots=True)
class CombatLogParseResult:
    """Events, derived pulls, and non-fatal parse problems."""

    events: tuple[CombatLogEvent, ...]
    pulls: tuple[CombatPull, ...]
    issues: tuple[CombatLogIssue, ...]


@dataclass(frozen=True, slots=True)
class _RawWindow:
    started_at: datetime
    ended_at: datetime
    type: PullType
    result: PullResult
    encounter: Encounter | None
    evidence: tuple[str, ...]
    attempt_number: int | None


@dataclass(slots=True)
class _OpenEncounter:
    started_at: datetime
    encounter: Encounter
    attempt_number: int


class _YearResolver:
    """Resolve yearless log timestamps while retaining chronological context."""

    def __init__(self, anchor: datetime) -> None:
        self._anchor = anchor
        self._previous: datetime | None = None

    def resolve(
        self,
        *,
        month: int,
        day: int,
        year_text: str | None,
        hour: int,
        minute: int,
        second: int,
        microsecond: int,
    ) -> datetime:
        if year_text is not None:
            year = int(year_text)
            if len(year_text) == 2:
                year += 2000
            resolved = self._make_datetime(year, month, day, hour, minute, second, microsecond)
        else:
            reference = self._previous or self._anchor
            candidates: list[datetime] = []
            for year in range(reference.year - 1, reference.year + 2):
                try:
                    candidates.append(
                        self._make_datetime(
                            year,
                            month,
                            day,
                            hour,
                            minute,
                            second,
                            microsecond,
                        )
                    )
                except ValueError:
                    # February 29 is invalid in two of most three adjacent
                    # years.  Keep the valid candidate instead of rejecting it
                    # because an earlier candidate was not a leap year.
                    continue
            if not candidates:
                raise ValueError("date is not valid in a nearby calendar year")
            resolved = min(
                candidates,
                key=lambda candidate: (
                    abs((candidate - reference).total_seconds()),
                    abs(candidate.year - reference.year),
                    candidate,
                ),
            )

        self._previous = resolved
        return resolved

    def _make_datetime(
        self,
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        second: int,
        microsecond: int,
    ) -> datetime:
        return datetime(
            year,
            month,
            day,
            hour,
            minute,
            second,
            microsecond,
            tzinfo=self._anchor.tzinfo,
            fold=self._anchor.fold,
        )


def parse_combat_log(
    rows: Iterable[str],
    *,
    recording_started_at: datetime,
    recording_offset_seconds: float = 0.0,
    recording_duration_seconds: float | None = None,
) -> CombatLogParseResult:
    """Parse rows and derive deterministic boss and trash pull windows.

    Non-timestamp header rows and blank lines are ignored.  Timestamp-like rows
    that are malformed, as well as malformed pull-boundary events, are retained
    as :class:`CombatLogIssue` values without aborting the rest of the log.
    """

    _validate_mapping_options(
        recording_offset_seconds=recording_offset_seconds,
        recording_duration_seconds=recording_duration_seconds,
    )
    resolver = _YearResolver(recording_started_at)
    events: list[CombatLogEvent] = []
    issues: list[CombatLogIssue] = []

    source_rows = rows.splitlines() if isinstance(rows, str) else rows
    for line_number, row in enumerate(source_rows, start=1):
        raw_line = row.rstrip("\r\n")
        if not raw_line.strip():
            continue

        parseable_line = raw_line.removeprefix("\ufeff")
        match = _TIMESTAMPED_ROW.match(parseable_line)
        if match is None:
            if _TIMESTAMP_LIKE_PREFIX.match(parseable_line):
                issues.append(CombatLogIssue(line_number, raw_line, "malformed timestamped row"))
            continue

        try:
            occurred_at = _timestamp_from_match(match, resolver)
        except ValueError as exc:
            issues.append(CombatLogIssue(line_number, raw_line, f"invalid timestamp: {exc}"))
            continue

        try:
            parsed_fields = next(
                csv.reader(
                    [match.group("payload")],
                    skipinitialspace=True,
                    strict=True,
                )
            )
        except (csv.Error, StopIteration) as exc:
            issues.append(CombatLogIssue(line_number, raw_line, f"malformed CSV payload: {exc}"))
            continue

        if not parsed_fields:
            issues.append(CombatLogIssue(line_number, raw_line, "missing event name"))
            continue

        event_name = parsed_fields[0].strip()
        if not _EVENT_NAME.fullmatch(event_name):
            issues.append(CombatLogIssue(line_number, raw_line, "invalid event name"))
            continue

        event = CombatLogEvent(
            occurred_at=occurred_at,
            event=event_name,
            fields=tuple(field.strip() for field in parsed_fields[1:]),
            line_number=line_number,
            raw_line=raw_line,
        )
        semantic_problem = _boundary_event_problem(event)
        if semantic_problem is not None:
            issues.append(CombatLogIssue(line_number, raw_line, semantic_problem))
            continue
        events.append(event)

    ordered_events = tuple(sorted(events, key=lambda event: (event.occurred_at, event.line_number)))
    pulls = build_pull_windows(
        ordered_events,
        recording_started_at=recording_started_at,
        recording_offset_seconds=recording_offset_seconds,
        recording_duration_seconds=recording_duration_seconds,
    )
    return CombatLogParseResult(ordered_events, pulls, tuple(issues))


def build_pull_windows(
    events: Sequence[CombatLogEvent] | Iterable[CombatLogEvent],
    *,
    recording_started_at: datetime,
    recording_offset_seconds: float = 0.0,
    recording_duration_seconds: float | None = None,
) -> tuple[CombatPull, ...]:
    """Build non-overlapping deterministic pull windows from parsed events.

    Encounter markers take precedence over the broader player combat-state
    markers.  A PLAYER_REGEN window that overlaps a boss attempt is considered
    part of that attempt, preventing duplicate trash windows around a boss.
    Missing encounter ends are closed by combat ending, the next encounter
    starting, or the final parsed event, in that order.
    """

    _validate_mapping_options(
        recording_offset_seconds=recording_offset_seconds,
        recording_duration_seconds=recording_duration_seconds,
    )
    ordered_events = sorted(events, key=lambda event: (event.occurred_at, event.line_number))
    if not ordered_events:
        return ()

    raw_boss_windows: list[_RawWindow] = []
    raw_regen_windows: list[_RawWindow] = []
    active_encounter: _OpenEncounter | None = None
    active_regen_at: datetime | None = None
    attempts_by_encounter: dict[int, int] = {}

    for event in ordered_events:
        if event.event == ENCOUNTER_START:
            encounter = _encounter_from_event(event)
            if encounter is None:
                continue
            if active_encounter is not None:
                _append_boss_window(
                    raw_boss_windows,
                    active_encounter,
                    event.occurred_at,
                    PullResult.UNKNOWN,
                    (ENCOUNTER_START, "next encounter start"),
                )
            attempt_number = attempts_by_encounter.get(encounter.id, 0) + 1
            attempts_by_encounter[encounter.id] = attempt_number
            active_encounter = _OpenEncounter(event.occurred_at, encounter, attempt_number)
            continue

        if event.event == ENCOUNTER_END:
            end_encounter = _encounter_from_event(event)
            if (
                active_encounter is not None
                and end_encounter is not None
                and end_encounter.id == active_encounter.encounter.id
            ):
                _append_boss_window(
                    raw_boss_windows,
                    active_encounter,
                    event.occurred_at,
                    _result_from_end_event(event),
                    (ENCOUNTER_START, ENCOUNTER_END),
                )
                active_encounter = None
            continue

        if event.event == PLAYER_REGEN_DISABLED:
            if active_regen_at is None:
                active_regen_at = event.occurred_at
            continue

        if event.event == PLAYER_REGEN_ENABLED:
            if active_regen_at is not None and event.occurred_at > active_regen_at:
                raw_regen_windows.append(
                    _RawWindow(
                        started_at=active_regen_at,
                        ended_at=event.occurred_at,
                        type=PullType.TRASH,
                        result=PullResult.UNKNOWN,
                        encounter=None,
                        evidence=(PLAYER_REGEN_DISABLED, PLAYER_REGEN_ENABLED),
                        attempt_number=None,
                    )
                )
            active_regen_at = None
            if active_encounter is not None:
                _append_boss_window(
                    raw_boss_windows,
                    active_encounter,
                    event.occurred_at,
                    PullResult.UNKNOWN,
                    (ENCOUNTER_START, PLAYER_REGEN_ENABLED),
                )
                active_encounter = None

    final_event_at = ordered_events[-1].occurred_at
    if active_encounter is not None:
        _append_boss_window(
            raw_boss_windows,
            active_encounter,
            final_event_at,
            PullResult.UNKNOWN,
            (ENCOUNTER_START, "end of parsed log"),
        )
    if active_regen_at is not None and final_event_at > active_regen_at:
        raw_regen_windows.append(
            _RawWindow(
                started_at=active_regen_at,
                ended_at=final_event_at,
                type=PullType.TRASH,
                result=PullResult.UNKNOWN,
                encounter=None,
                evidence=(PLAYER_REGEN_DISABLED, "end of parsed log"),
                attempt_number=None,
            )
        )

    non_boss_regen_windows = [
        window
        for window in raw_regen_windows
        if not any(_windows_overlap(window, boss) for boss in raw_boss_windows)
    ]
    raw_windows = sorted(
        [*raw_boss_windows, *non_boss_regen_windows],
        key=lambda window: (
            window.started_at,
            0 if window.type is PullType.BOSS else 1,
            window.ended_at,
        ),
    )

    pulls: list[CombatPull] = []
    for window in raw_windows:
        pull = _map_window_to_recording(
            window,
            recording_started_at=recording_started_at,
            recording_offset_seconds=recording_offset_seconds,
            recording_duration_seconds=recording_duration_seconds,
        )
        if pull is not None:
            pulls.append(pull)
    return tuple(pulls)


def _timestamp_from_match(match: re.Match[str], resolver: _YearResolver) -> datetime:
    fraction = match.group("fraction") or ""
    microsecond = int((fraction[:6]).ljust(6, "0")) if fraction else 0
    return resolver.resolve(
        month=int(match.group("month")),
        day=int(match.group("day")),
        year_text=match.group("year"),
        hour=int(match.group("hour")),
        minute=int(match.group("minute")),
        second=int(match.group("second")),
        microsecond=microsecond,
    )


def _boundary_event_problem(event: CombatLogEvent) -> str | None:
    if event.event not in {ENCOUNTER_START, ENCOUNTER_END}:
        return None
    required_fields = 5 if event.event == ENCOUNTER_END else 2
    if len(event.fields) < required_fields:
        return f"{event.event} requires at least {required_fields} fields"
    try:
        encounter_id = int(event.fields[0])
    except ValueError:
        return f"{event.event} has an invalid encounter id"
    if encounter_id <= 0:
        return f"{event.event} has an invalid encounter id"
    if not event.fields[1].strip():
        return f"{event.event} has a blank encounter name"
    if event.event == ENCOUNTER_END and event.fields[4] not in {"0", "1"}:
        return "ENCOUNTER_END result must be 0 or 1"
    return None


def _encounter_from_event(event: CombatLogEvent) -> Encounter | None:
    if len(event.fields) < 2:
        return None
    try:
        return Encounter(id=int(event.fields[0]), name=event.fields[1])
    except (ValueError, TypeError):
        return None


def _result_from_end_event(event: CombatLogEvent) -> PullResult:
    if len(event.fields) < 5:
        return PullResult.UNKNOWN
    if event.fields[4] == "1":
        return PullResult.KILL
    if event.fields[4] == "0":
        return PullResult.WIPE
    return PullResult.UNKNOWN


def _append_boss_window(
    windows: list[_RawWindow],
    active: _OpenEncounter,
    ended_at: datetime,
    result: PullResult,
    evidence: tuple[str, ...],
) -> None:
    if ended_at <= active.started_at:
        return
    windows.append(
        _RawWindow(
            started_at=active.started_at,
            ended_at=ended_at,
            type=PullType.BOSS,
            result=result,
            encounter=active.encounter,
            evidence=evidence,
            attempt_number=active.attempt_number,
        )
    )


def _windows_overlap(left: _RawWindow, right: _RawWindow) -> bool:
    return left.started_at < right.ended_at and right.started_at < left.ended_at


def _map_window_to_recording(
    window: _RawWindow,
    *,
    recording_started_at: datetime,
    recording_offset_seconds: float,
    recording_duration_seconds: float | None,
) -> CombatPull | None:
    try:
        start_seconds = (
            window.started_at - recording_started_at
        ).total_seconds() + recording_offset_seconds
        end_seconds = (
            window.ended_at - recording_started_at
        ).total_seconds() + recording_offset_seconds
    except TypeError as exc:
        raise ValueError(
            "event timestamps and recording_started_at must use compatible timezone awareness"
        ) from exc

    start_seconds = max(0.0, start_seconds)
    end_seconds = max(0.0, end_seconds)
    if recording_duration_seconds is not None:
        start_seconds = min(recording_duration_seconds, start_seconds)
        end_seconds = min(recording_duration_seconds, end_seconds)
    if end_seconds <= start_seconds:
        return None

    return CombatPull(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        type=window.type,
        result=window.result,
        encounter=window.encounter,
        evidence=window.evidence,
        confidence=1.0,
        include=True,
        attempt_number=window.attempt_number,
        started_at=window.started_at,
        ended_at=window.ended_at,
    )


def _validate_mapping_options(
    *,
    recording_offset_seconds: float,
    recording_duration_seconds: float | None,
) -> None:
    if not math.isfinite(recording_offset_seconds):
        raise ValueError("recording offset must be finite")
    if recording_duration_seconds is not None:
        if not math.isfinite(recording_duration_seconds):
            raise ValueError("recording duration must be finite")
        if recording_duration_seconds <= 0:
            raise ValueError("recording duration must be greater than zero")
