"""Build a non-overlapping, editor-independent timeline from approved pulls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from raid_editor.config.models import DetectionConfig, EditingConfig
from raid_editor.models import PullCandidate, TimelineClip, TimelineDocument


@dataclass
class _Window:
    start: float
    end: float
    raw_start: float
    raw_end: float
    pulls: list[PullCandidate]


def _policy_includes(pull: PullCandidate, editing: EditingConfig) -> bool:
    if not pull.include:
        return False
    if pull.type == "trash_pull":
        return editing.include_trash_pulls
    if pull.type in {"boss_attempt", "boss_wipe"} or pull.result == "wipe":
        return editing.include_boss_wipes
    if pull.type == "boss_kill" or pull.result in {"kill", "success"}:
        return editing.include_boss_kills
    if pull.type == "run_back":
        return editing.include_run_backs
    if pull.type == "loot":
        return editing.include_loot
    return pull.type not in {"downtime", "recovery"}


def _is_trash(window: _Window) -> bool:
    return all(pull.type == "trash_pull" for pull in window.pulls)


def _merge_windows(
    pulls: list[PullCandidate],
    duration: float,
    detection: DetectionConfig,
    editing: EditingConfig,
) -> list[_Window]:
    windows = [
        _Window(
            start=max(0.0, pull.start_seconds - detection.pre_roll_seconds),
            end=min(duration, pull.end_seconds + detection.post_roll_seconds),
            raw_start=pull.start_seconds,
            raw_end=pull.end_seconds,
            pulls=[pull],
        )
        for pull in sorted(pulls, key=lambda item: (item.start_seconds, item.end_seconds))
        if _policy_includes(pull, editing)
    ]
    merged: list[_Window] = []
    for current in windows:
        if not merged:
            merged.append(current)
            continue
        previous = merged[-1]
        raw_gap = current.raw_start - previous.raw_end
        if _is_trash(previous) and _is_trash(current) and raw_gap <= detection.merge_gap_seconds:
            previous.end = max(previous.end, current.end)
            previous.raw_end = max(previous.raw_end, current.raw_end)
            previous.pulls.extend(current.pulls)
            continue
        if current.start < previous.end:
            if current.raw_start >= previous.raw_end:
                boundary = (previous.raw_end + current.raw_start) / 2
            else:
                boundary = current.raw_start
            previous.end = max(previous.start, min(previous.end, boundary))
            current.start = min(current.end, max(current.start, boundary))
        if previous.end > previous.start:
            merged.append(current)
    return [window for window in merged if window.end > window.start]


def _label(window: _Window) -> str:
    first = window.pulls[0]
    if len(window.pulls) > 1:
        return f"Trash clearing — {len(window.pulls)} pulls"
    if first.title:
        return first.title
    if first.encounter:
        result = f" — {first.result.title()}" if first.result != "unknown" else ""
        return f"{first.encounter}{result}"
    return first.id


def build_timeline(
    *,
    name: str,
    source: str,
    source_duration_seconds: float,
    source_fps: float,
    retained_audio_stream_indexes: list[int],
    excluded_microphone_stream_index: int | None,
    pulls: list[PullCandidate],
    detection: DetectionConfig,
    editing: EditingConfig,
) -> TimelineDocument:
    windows = _merge_windows(pulls, source_duration_seconds, detection, editing)
    timeline_position = 0.0
    clips: list[TimelineClip] = []
    for index, window in enumerate(windows):
        transition = "fade" if editing.transition_duration_seconds > 0 else None
        clips.append(
            TimelineClip(
                source_in=window.start,
                source_out=window.end,
                timeline_in=timeline_position,
                label=_label(window),
                type=window.pulls[0].type,
                result=window.pulls[0].result,
                encounter=window.pulls[0].encounter,
                difficulty=(window.pulls[0].difficulty if len(window.pulls) == 1 else "UNKNOWN"),
                transition_in=transition if index > 0 else None,
                transition_out=transition if index < len(windows) - 1 else None,
                pull_ids=[pull.id for pull in window.pulls],
            )
        )
        timeline_position += window.end - window.start
    return TimelineDocument(
        timeline_name=name,
        source=Path(source),
        source_duration_seconds=source_duration_seconds,
        source_fps=source_fps,
        retained_audio_stream_indexes=retained_audio_stream_indexes,
        excluded_microphone_stream_index=excluded_microphone_stream_index,
        clips=clips,
    )
