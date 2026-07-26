"""Deterministic construction of a non-overlapping edit timeline."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from raid_editor.detection.combat_log import (
    CombatPull,
    Encounter,
    PullResult,
    PullType,
)


@dataclass(frozen=True, slots=True)
class TimelineWindow:
    """A clamped, padded, non-overlapping section of the final timeline."""

    start_seconds: float
    end_seconds: float
    type: PullType
    result: PullResult
    encounter: Encounter | None
    evidence: tuple[str, ...]
    confidence: float
    include: bool
    attempt_number: int | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.start_seconds) or not math.isfinite(self.end_seconds):
            raise ValueError("timeline boundaries must be finite")
        if self.end_seconds < self.start_seconds:
            raise ValueError("timeline end must not precede timeline start")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.attempt_number is not None and self.attempt_number <= 0:
            raise ValueError("attempt number must be positive")

    @property
    def duration_seconds(self) -> float:
        """Return this window's duration."""

        return self.end_seconds - self.start_seconds


@dataclass(slots=True)
class _WorkingWindow:
    start_seconds: float
    end_seconds: float
    core_start_seconds: float
    core_end_seconds: float
    type: PullType
    result: PullResult
    encounter: Encounter | None
    evidence: tuple[str, ...]
    confidence: float
    include: bool
    attempt_number: int | None
    source_index: int

    def clone(self, *, start_seconds: float, end_seconds: float) -> _WorkingWindow:
        return _WorkingWindow(
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            core_start_seconds=self.core_start_seconds,
            core_end_seconds=self.core_end_seconds,
            type=self.type,
            result=self.result,
            encounter=self.encounter,
            evidence=self.evidence,
            confidence=self.confidence,
            include=self.include,
            attempt_number=self.attempt_number,
            source_index=self.source_index,
        )

    def freeze(self) -> TimelineWindow:
        return TimelineWindow(
            start_seconds=self.start_seconds,
            end_seconds=self.end_seconds,
            type=self.type,
            result=self.result,
            encounter=self.encounter,
            evidence=self.evidence,
            confidence=self.confidence,
            include=self.include,
            attempt_number=self.attempt_number,
        )


def merge_timeline_windows(
    windows: Iterable[CombatPull | TimelineWindow],
    *,
    recording_duration_seconds: float,
    lead_in_seconds: float = 0.0,
    lead_out_seconds: float = 0.0,
    trash_merge_gap_seconds: float = 0.0,
) -> tuple[TimelineWindow, ...]:
    """Pad, clamp, de-duplicate, and merge candidate timeline windows.

    Boss windows are always treated as distinct attempts, including repeated
    attempts against the same encounter.  When their padded ranges overlap,
    the overlap is split at a deterministic boundary; the attempts are never
    coalesced.  Boss windows also take priority over trash, so overlapping
    trash is trimmed or split around them.

    Only adjacent trash windows may merge, and only when their gap is at most
    ``trash_merge_gap_seconds`` and no boss attempt occupies that gap.
    """

    _validate_options(
        recording_duration_seconds=recording_duration_seconds,
        lead_in_seconds=lead_in_seconds,
        lead_out_seconds=lead_out_seconds,
        trash_merge_gap_seconds=trash_merge_gap_seconds,
    )

    prepared: list[_WorkingWindow] = []
    for source_index, window in enumerate(windows):
        if not window.include:
            continue
        _validate_input_window(window)
        start_seconds = _clamp(
            window.start_seconds - lead_in_seconds,
            maximum=recording_duration_seconds,
        )
        end_seconds = _clamp(
            window.end_seconds + lead_out_seconds,
            maximum=recording_duration_seconds,
        )
        if end_seconds <= start_seconds:
            continue

        prepared.append(
            _WorkingWindow(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                core_start_seconds=_clamp(window.start_seconds, maximum=recording_duration_seconds),
                core_end_seconds=_clamp(window.end_seconds, maximum=recording_duration_seconds),
                type=PullType(window.type),
                result=PullResult(window.result),
                encounter=window.encounter,
                evidence=tuple(window.evidence),
                confidence=window.confidence,
                include=True,
                attempt_number=window.attempt_number,
                source_index=source_index,
            )
        )

    bosses = _remove_boss_overlaps([window for window in prepared if window.type is PullType.BOSS])
    trash_fragments: list[_WorkingWindow] = []
    for trash in (window for window in prepared if window.type is PullType.TRASH):
        trash_fragments.extend(_subtract_bosses(trash, bosses))
    merged_trash = _merge_trash(
        trash_fragments,
        bosses=bosses,
        maximum_gap_seconds=trash_merge_gap_seconds,
    )

    result = [*bosses, *merged_trash]
    result.sort(
        key=lambda window: (
            window.start_seconds,
            0 if window.type is PullType.BOSS else 1,
            window.end_seconds,
            window.source_index,
        )
    )
    return tuple(window.freeze() for window in result)


def _remove_boss_overlaps(
    bosses: list[_WorkingWindow],
) -> list[_WorkingWindow]:
    bosses.sort(
        key=lambda window: (
            window.start_seconds,
            window.end_seconds,
            window.source_index,
        )
    )
    resolved: list[_WorkingWindow] = []

    for boss in bosses:
        current = boss
        if resolved and current.start_seconds < resolved[-1].end_seconds:
            previous = resolved[-1]
            overlap_start = current.start_seconds
            overlap_end = min(previous.end_seconds, current.end_seconds)
            preferred_boundary = (previous.core_end_seconds + current.core_start_seconds) / 2.0
            if overlap_start < preferred_boundary < overlap_end:
                boundary = preferred_boundary
            else:
                # A fully nested or identical range can put the preferred
                # boundary at an endpoint.  The midpoint preserves both
                # distinct attempts while still removing their overlap.
                boundary = (overlap_start + overlap_end) / 2.0

            previous.end_seconds = boundary
            current.start_seconds = boundary
            if previous.end_seconds <= previous.start_seconds:
                resolved.pop()

        if current.end_seconds > current.start_seconds:
            resolved.append(current)

    return resolved


def _subtract_bosses(
    trash: _WorkingWindow,
    bosses: list[_WorkingWindow],
) -> list[_WorkingWindow]:
    fragments = [trash]
    for boss in bosses:
        next_fragments: list[_WorkingWindow] = []
        for fragment in fragments:
            if (
                boss.end_seconds <= fragment.start_seconds
                or boss.start_seconds >= fragment.end_seconds
            ):
                next_fragments.append(fragment)
                continue

            if fragment.start_seconds < boss.start_seconds:
                next_fragments.append(
                    fragment.clone(
                        start_seconds=fragment.start_seconds,
                        end_seconds=boss.start_seconds,
                    )
                )
            if boss.end_seconds < fragment.end_seconds:
                next_fragments.append(
                    fragment.clone(
                        start_seconds=boss.end_seconds,
                        end_seconds=fragment.end_seconds,
                    )
                )
        fragments = next_fragments
        if not fragments:
            break
    return fragments


def _merge_trash(
    trash_windows: list[_WorkingWindow],
    *,
    bosses: list[_WorkingWindow],
    maximum_gap_seconds: float,
) -> list[_WorkingWindow]:
    trash_windows.sort(
        key=lambda window: (
            window.start_seconds,
            window.end_seconds,
            window.source_index,
        )
    )
    merged: list[_WorkingWindow] = []

    for current in trash_windows:
        if not merged:
            merged.append(current)
            continue

        previous = merged[-1]
        gap = current.start_seconds - previous.end_seconds
        boss_in_gap = any(
            boss.start_seconds < current.start_seconds and boss.end_seconds > previous.end_seconds
            for boss in bosses
        )
        if gap <= maximum_gap_seconds and not boss_in_gap:
            previous.end_seconds = max(previous.end_seconds, current.end_seconds)
            previous.core_start_seconds = min(
                previous.core_start_seconds, current.core_start_seconds
            )
            previous.core_end_seconds = max(previous.core_end_seconds, current.core_end_seconds)
            previous.evidence = _ordered_union(previous.evidence, current.evidence)
            previous.confidence = max(previous.confidence, current.confidence)
            previous.encounter = (
                previous.encounter if previous.encounter == current.encounter else None
            )
            previous.source_index = min(previous.source_index, current.source_index)
        else:
            merged.append(current)

    return merged


def _ordered_union(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in (*left, *right):
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def _clamp(value: float, *, maximum: float) -> float:
    return min(maximum, max(0.0, value))


def _validate_input_window(window: CombatPull | TimelineWindow) -> None:
    if not math.isfinite(window.start_seconds) or not math.isfinite(window.end_seconds):
        raise ValueError("window boundaries must be finite")
    if window.end_seconds < window.start_seconds:
        raise ValueError("window end must not precede window start")
    if not math.isfinite(window.confidence) or not 0.0 <= window.confidence <= 1.0:
        raise ValueError("window confidence must be between 0 and 1")
    try:
        PullType(window.type)
    except ValueError as exc:
        raise ValueError(f"unsupported window type: {window.type!r}") from exc
    try:
        PullResult(window.result)
    except ValueError as exc:
        raise ValueError(f"unsupported pull result: {window.result!r}") from exc


def _validate_options(
    *,
    recording_duration_seconds: float,
    lead_in_seconds: float,
    lead_out_seconds: float,
    trash_merge_gap_seconds: float,
) -> None:
    values = {
        "recording duration": recording_duration_seconds,
        "lead in": lead_in_seconds,
        "lead out": lead_out_seconds,
        "trash merge gap": trash_merge_gap_seconds,
    }
    for name, value in values.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if recording_duration_seconds <= 0:
        raise ValueError("recording duration must be greater than zero")
    if lead_in_seconds < 0:
        raise ValueError("lead in must not be negative")
    if lead_out_seconds < 0:
        raise ValueError("lead out must not be negative")
    if trash_merge_gap_seconds < 0:
        raise ValueError("trash merge gap must not be negative")
