"""Deterministic pull analysis with explicit combat-log synchronization."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from raid_editor.config.models import DetectionConfig
from raid_editor.detection.combat_log import (
    PullResult as LogPullResult,
)
from raid_editor.detection.combat_log import (
    PullType as LogPullType,
)
from raid_editor.detection.combat_log import (
    parse_combat_log,
)
from raid_editor.detection.legacy import detect_damage_activity
from raid_editor.detection.manual import load_manual_pulls
from raid_editor.detection.skada import parse_skada_storage
from raid_editor.models import PullCandidate, PullResult, PullType
from raid_editor.util.paths import atomic_write_json


class PullDetectionError(ValueError):
    """Expected missing evidence or timestamp-alignment failure."""


_RECORDING_NAME_TIME = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[ _]"
    r"(?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})"
)
_LOG_TIME = re.compile(
    r"^\s*(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{2}|\d{4}))?\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d{1,9}))?"
)


def _inferred_recording_start(recording: Path, duration: float) -> tuple[datetime, str]:
    match = _RECORDING_NAME_TIME.search(recording.stem)
    timezone = datetime.fromtimestamp(recording.stat().st_mtime).astimezone().tzinfo
    if match:
        parts = {key: int(value) for key, value in match.groupdict().items()}
        return datetime(**parts, tzinfo=timezone), "recording_filename_timestamp"
    completed_at = datetime.fromtimestamp(recording.stat().st_mtime).astimezone()
    return completed_at - timedelta(seconds=duration), "filesystem_end_time_estimate"


def _line_datetime(line: str, anchor: datetime) -> datetime | None:
    match = _LOG_TIME.match(line)
    if not match:
        return None
    year_text = match.group("year")
    if year_text:
        year = int(year_text)
        year = year + 2000 if year < 100 else year
    else:
        candidates: list[datetime] = []
        for year in range(anchor.year - 1, anchor.year + 2):
            try:
                candidates.append(
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
        return min(candidates, key=lambda value: abs(value - anchor)) if candidates else None
    try:
        return datetime(
            year,
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            int(((match.group("fraction") or "") + "000000")[:6]),
            tzinfo=anchor.tzinfo,
        )
    except ValueError:
        return None


def _iter_recording_window(
    source: Path,
    recording_start: datetime,
    recording_duration_seconds: float,
    offset_seconds: float,
) -> Iterator[str]:
    """Stream only rows that could map to this recording, plus a small boundary margin."""

    margin = timedelta(seconds=60)
    earliest = recording_start - timedelta(seconds=offset_seconds) - margin
    latest = (
        recording_start
        - timedelta(seconds=offset_seconds)
        + timedelta(seconds=recording_duration_seconds)
        + margin
    )
    with source.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            occurred_at = _line_datetime(line, recording_start)
            if occurred_at is not None and earliest <= occurred_at <= latest:
                yield line


def detect_from_combat_log(
    combat_log: Path,
    recording: Path,
    *,
    recording_duration_seconds: float,
    settings: DetectionConfig,
    skada_export: Path | None = None,
    issues_destination: Path | None = None,
) -> list[PullCandidate]:
    """Parse combat events and map them to the recording.

    The large, accumulated legacy log is streamed and filtered to the recording
    window. The recording start is explicit when configured, otherwise inferred
    from OBS's timestamped filename, with filesystem end time as a last resort.
    """

    source = combat_log.expanduser().resolve()
    if not source.is_file():
        raise PullDetectionError(f"Combat log does not exist: {source}")
    if settings.recording_started_at is not None:
        recording_start = settings.recording_started_at
        offset = settings.combat_log_offset_seconds
        sync_mode = "explicit_recording_start_plus_offset"
    else:
        recording_start, sync_mode = _inferred_recording_start(
            recording, recording_duration_seconds
        )
        offset = settings.combat_log_offset_seconds
    result = parse_combat_log(
        _iter_recording_window(
            source,
            recording_start,
            recording_duration_seconds,
            offset,
        ),
        recording_started_at=recording_start,
        recording_offset_seconds=offset,
        recording_duration_seconds=recording_duration_seconds,
    )

    pulls: list[PullCandidate] = []
    for raw in result.pulls:
        duration = raw.end_seconds - raw.start_seconds
        is_boss = raw.type is LogPullType.BOSS
        if not is_boss and duration < settings.minimum_pull_seconds:
            continue
        if is_boss and raw.result is LogPullResult.KILL:
            pull_type: PullType = "boss_kill"
            result_name: PullResult = "kill"
        elif is_boss and raw.result is LogPullResult.WIPE:
            pull_type = "boss_wipe"
            result_name = "wipe"
        elif is_boss:
            pull_type = "boss_attempt"
            result_name = "unknown"
        else:
            pull_type = "trash_pull"
            result_name = "not_applicable"
        encounter = raw.encounter.name if raw.encounter else None
        title = None
        if encounter:
            title = f"{encounter} — Attempt {raw.attempt_number}"
        pulls.append(
            PullCandidate(
                id=f"pull-{len(pulls) + 1:04d}",
                start_seconds=raw.start_seconds,
                end_seconds=raw.end_seconds,
                type=pull_type,
                encounter=encounter,
                result=result_name,
                confidence=raw.confidence,
                evidence=[*raw.evidence, f"sync:{sync_mode}"],
                include=raw.include,
                title=title,
            )
        )
    if not pulls:
        pulls = detect_damage_activity(
            _iter_recording_window(
                source,
                recording_start,
                recording_duration_seconds,
                offset,
            ),
            parse_line_time=_line_datetime,
            recording_started_at=recording_start,
            recording_duration_seconds=recording_duration_seconds,
            recording_offset_seconds=offset,
            minimum_pull_seconds=settings.minimum_pull_seconds,
            merge_gap_seconds=settings.merge_gap_seconds,
        )
        sync_mode += "+legacy_damage_activity"

    if skada_export is not None:
        boss_pulls: list[PullCandidate] = []
        last_success_by_name: dict[str, tuple[int, int]] = {}
        skada_segments = parse_skada_storage(skada_export)
        encounter_counts = Counter(segment.mob_name.casefold() for segment in skada_segments)
        attempt_numbers: Counter[str] = Counter()
        for segment in skada_segments:
            encounter_key = segment.mob_name.casefold()
            attempt_numbers[encounter_key] += 1
            attempt_number = attempt_numbers[encounter_key]
            start_seconds = segment.start_epoch - recording_start.timestamp() + offset
            end_seconds = segment.end_epoch - recording_start.timestamp() + offset
            start_seconds = max(0.0, start_seconds)
            end_seconds = min(recording_duration_seconds, end_seconds)
            if end_seconds <= start_seconds:
                continue
            previous_success = last_success_by_name.get(segment.mob_name.casefold())
            possible_duplicate = (
                segment.success is True
                and previous_success is not None
                and segment.start_epoch - previous_success[0] < 600
                and segment.duration_seconds < 60
            )
            skada_type: PullType
            skada_result: PullResult
            if possible_duplicate:
                skada_type = "unknown"
                skada_result = "unknown"
                confidence = 0.45
                include = False
                title = f"Possible duplicate {segment.mob_name} segment"
                notes = (
                    "Short successful Skada segment near a prior kill; "
                    "excluded pending manual review."
                )
            elif segment.success is True:
                skada_type = "boss_kill"
                skada_result = "kill"
                confidence = 0.96
                include = True
                title = segment.mob_name
                notes = ""
                last_success_by_name[segment.mob_name.casefold()] = (
                    segment.end_epoch,
                    segment.duration_seconds,
                )
            elif segment.success is False or encounter_counts[encounter_key] > 1:
                skada_type = "boss_wipe"
                skada_result = "wipe"
                confidence = 0.96 if segment.success is False else 0.90
                include = True
                title = f"{segment.mob_name} — Attempt {attempt_number} (Wipe)"
                notes = ""
            else:
                skada_type = "boss_attempt"
                skada_result = "unknown"
                confidence = 0.82
                include = True
                title = segment.mob_name
                notes = ""
            boss_pulls.append(
                PullCandidate(
                    id=f"skada-{len(boss_pulls) + 1:04d}",
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    type=skada_type,
                    encounter=segment.mob_name,
                    result=skada_result,
                    confidence=confidence,
                    evidence=[
                        "SkadaStorage:starttime",
                        "SkadaStorage:endtime",
                        "SkadaStorage:mobname",
                        *(
                            ["SkadaStorage:inferred_wipe_from_repeated_encounter"]
                            if segment.success is None and encounter_counts[encounter_key] > 1
                            else []
                        ),
                        *(["SkadaStorage:possible_duplicate"] if possible_duplicate else []),
                        f"sync:{sync_mode}",
                    ],
                    include=include,
                    title=title,
                    notes=notes,
                )
            )
        if boss_pulls:
            pulls = [
                pull
                for pull in pulls
                if not any(
                    pull.start_seconds < boss.end_seconds and boss.start_seconds < pull.end_seconds
                    for boss in boss_pulls
                )
            ]
            pulls.extend(boss_pulls)

    pulls.sort(key=lambda item: (item.start_seconds, item.end_seconds))
    pulls = [
        pull.model_copy(update={"id": f"pull-{index:04d}"})
        for index, pull in enumerate(pulls, start=1)
    ]
    if issues_destination:
        atomic_write_json(
            issues_destination,
            {
                "sync_mode": sync_mode,
                "recording_started_at": recording_start.isoformat(),
                "recording_offset_seconds": offset,
                "issues": [
                    {
                        "line_number": issue.line_number,
                        "reason": issue.reason,
                        "raw_line": issue.raw_line,
                    }
                    for issue in result.issues
                ],
            },
        )
    return pulls


def analyse_pulls(
    *,
    recording: Path,
    recording_duration_seconds: float,
    settings: DetectionConfig,
    combat_log: Path | None,
    skada_export: Path | None,
    manual_pulls: Path | None,
    issues_destination: Path | None = None,
) -> list[PullCandidate]:
    if manual_pulls is not None:
        return load_manual_pulls(manual_pulls)
    if combat_log is None:
        raise PullDetectionError(
            "No combat log or manual pull file is configured. "
            "Set input.combat_log or input.manual_pulls."
        )
    return detect_from_combat_log(
        combat_log,
        recording,
        recording_duration_seconds=recording_duration_seconds,
        settings=settings,
        skada_export=skada_export,
        issues_destination=issues_destination,
    )
