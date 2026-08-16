"""Fuse bounded audio, motion, combat, and kill signals into review candidates."""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from raid_editor.config.models import HighlightConfig
from raid_editor.detection.log_stream import iter_timed_log_events
from raid_editor.models import HighlightCandidate, HighlightCategory, PullCandidate
from raid_editor.util.paths import atomic_write_json, atomic_write_text

_HIGHLIGHT_LIST = TypeAdapter(list[HighlightCandidate])
_PTS_TIME = re.compile(r"pts_time:(?P<time>-?\d+(?:\.\d+)?)")
_RMS = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(?P<rms>-?\d+(?:\.\d+)?|-inf)")
_SCENE = re.compile(r"lavfi\.scene_score=(?P<score>\d+(?:\.\d+)?)")


class HighlightAnalysisError(RuntimeError):
    """Expected signal extraction or selection failure."""


@dataclass(frozen=True, slots=True)
class Signal:
    """Represent one timestamped, normalized highlight signal."""

    seconds: float
    kind: str
    strength: float
    detail: str


def _run_ffmpeg(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise HighlightAnalysisError("ffmpeg is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown FFmpeg error").strip()
        raise HighlightAnalysisError(
            f"Highlight signal extraction failed: {detail[-2000:]}"
        ) from exc
    return completed.stdout


def _paired_metadata(output: str, value_pattern: re.Pattern[str]) -> list[tuple[float, float]]:
    timestamp: float | None = None
    values: list[tuple[float, float]] = []
    for line in output.splitlines():
        time_match = _PTS_TIME.search(line)
        if time_match:
            timestamp = float(time_match.group("time"))
            continue
        value_match = value_pattern.search(line)
        if timestamp is None or value_match is None:
            continue
        raw = next(value for value in value_match.groupdict().values() if value is not None)
        if raw == "-inf":
            timestamp = None
            continue
        values.append((timestamp, float(raw)))
        timestamp = None
    return values


def audio_energy_signals(
    recording: Path,
    *,
    stream_index: int,
    threshold_db: float,
    kind: str,
) -> list[Signal]:
    """Extract and suppress audio-energy peaks from one absolute stream index.

    Args:
        recording: Source media file.
        stream_index: Absolute FFprobe stream index to analyze.
        threshold_db: Minimum one-second RMS level in decibels.
        kind: Signal role, normally ``game`` or ``discord``.

    Returns:
        Chronological energy signals spaced at least four seconds apart.

    Raises:
        HighlightAnalysisError: If FFmpeg is missing or signal extraction fails.
    """

    output = _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostats",
            "-i",
            str(recording),
            "-map",
            f"0:{stream_index}",
            "-vn",
            "-af",
            "aresample=8000,asetnsamples=n=8000:p=1,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-f",
            "null",
            "-",
        ]
    )
    points = _paired_metadata(output, _RMS)
    eligible = [
        Signal(
            seconds=seconds,
            kind=kind,
            strength=min(1.0, 0.35 + max(0.0, rms - threshold_db) / 18.0),
            detail=f"{kind}_rms:{rms:.1f}dB",
        )
        for seconds, rms in points
        if rms >= threshold_db
    ]
    return _suppress_nearby(eligible, spacing_seconds=4.0)


def motion_signals(
    recording: Path,
    *,
    threshold: float,
    sample_fps: float,
    keyframes_only: bool,
) -> list[Signal]:
    """Extract visual scene-change signals from sampled video frames.

    Args:
        recording: Source media file.
        threshold: FFmpeg scene-score threshold.
        sample_fps: Sampling rate before scene comparison.
        keyframes_only: Limit decoding to keyframes for long-recording speed.

    Returns:
        Chronological motion signals spaced at least four seconds apart.

    Raises:
        HighlightAnalysisError: If FFmpeg is missing or signal extraction fails.
    """

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
    ]
    if keyframes_only:
        command.extend(["-skip_frame", "nokey"])
    command.extend(
        [
            "-i",
            str(recording),
            "-vf",
            f"fps={sample_fps:.3f},scale=320:-2,select='gt(scene,{threshold:.6f})',"
            "metadata=print:file=-",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    output = _run_ffmpeg(command)
    return _suppress_nearby(
        [
            Signal(
                seconds=seconds,
                kind="motion",
                strength=min(1.0, 0.3 + score / max(threshold * 3, 0.01)),
                detail=f"scene_score:{score:.3f}",
            )
            for seconds, score in _paired_metadata(output, _SCENE)
        ],
        spacing_seconds=4.0,
    )


def combat_pressure_signals(
    combat_log: Path | None,
    *,
    recording_started_at: datetime | None,
    recording_duration_seconds: float,
    recording_offset_seconds: float,
) -> list[Signal]:
    """Convert clustered player deaths into combat-pressure signals.

    Args:
        combat_log: Optional accumulated combat log.
        recording_started_at: Timestamp used to align log and video time.
        recording_duration_seconds: Source duration used to bound streaming.
        recording_offset_seconds: Explicit log-to-video synchronization offset.

    Returns:
        Death-cluster signals, or an empty list when timing evidence is absent.
    """

    if combat_log is None or recording_started_at is None:
        return []
    deaths: Counter[int] = Counter()
    for event in iter_timed_log_events(
        combat_log,
        recording_started_at=recording_started_at,
        recording_duration_seconds=recording_duration_seconds,
        recording_offset_seconds=recording_offset_seconds,
    ):
        if event.event != "UNIT_DIED":
            continue
        if any(
            field.upper().startswith(("PLAYER-", "0X06", "0X0000000000")) for field in event.fields
        ):
            deaths[math.floor(max(0.0, event.video_seconds) / 8.0)] += 1
    return [
        Signal(
            seconds=bucket * 8.0 + 4.0,
            kind="raid_deaths",
            strength=min(1.0, 0.35 + count * 0.13),
            detail=f"player_deaths_8s:{count}",
        )
        for bucket, count in deaths.items()
        if count >= 2
    ]


def kill_climax_signals(pulls: list[PullCandidate]) -> list[Signal]:
    """Create climax signals near included boss kills.

    Args:
        pulls: Reviewed pull candidates with difficulty labels.

    Returns:
        One weighted signal per included winning boss pull.
    """

    signals: list[Signal] = []
    for pull in pulls:
        if not pull.include or not (pull.type == "boss_kill" or pull.result in {"kill", "success"}):
            continue
        strength = 0.64
        if pull.difficulty.endswith("H"):
            strength += 0.14
        if pull.encounter and "lich king" in pull.encounter.casefold():
            strength += 0.12
        signals.append(
            Signal(
                seconds=max(pull.start_seconds, pull.end_seconds - 7.0),
                kind="kill_climax",
                strength=min(1.0, strength),
                detail=f"boss_kill:{pull.encounter or pull.id}:{pull.difficulty}",
            )
        )
    return signals


def _suppress_nearby(signals: list[Signal], *, spacing_seconds: float) -> list[Signal]:
    selected: list[Signal] = []
    for signal in sorted(signals, key=lambda item: item.strength, reverse=True):
        if all(abs(signal.seconds - kept.seconds) >= spacing_seconds for kept in selected):
            selected.append(signal)
    return sorted(selected, key=lambda item: item.seconds)


def _fuse(signals: list[Signal], *, window_seconds: float) -> list[list[Signal]]:
    groups: list[list[Signal]] = []
    for signal in sorted(signals, key=lambda item: item.seconds):
        if not groups:
            groups.append([signal])
            continue
        center = sum(item.seconds * item.strength for item in groups[-1]) / sum(
            item.strength for item in groups[-1]
        )
        if signal.seconds - center <= window_seconds:
            groups[-1].append(signal)
        else:
            groups.append([signal])
    return groups


def _category(group: list[Signal]) -> HighlightCategory:
    kinds = {signal.kind for signal in group}
    if "kill_climax" in kinds and "raid_deaths" in kinds:
        return "clutch"
    if "kill_climax" in kinds and "discord" in kinds:
        return "reaction"
    if "discord" in kinds and "motion" in kinds and "kill_climax" not in kinds:
        return "funny"
    if "raid_deaths" in kinds or "kill_climax" in kinds:
        return "intense"
    if "motion" in kinds:
        return "movement"
    return "reaction"


def _score(group: list[Signal]) -> float:
    weights = {
        "discord": 0.36,
        "game": 0.16,
        "motion": 0.22,
        "raid_deaths": 0.36,
        "kill_climax": 0.54,
    }
    strongest: dict[str, float] = {}
    for signal in group:
        strongest[signal.kind] = max(strongest.get(signal.kind, 0.0), signal.strength)
    raw = sum(weights.get(kind, 0.1) * strength for kind, strength in strongest.items())
    diversity_bonus = max(0, len(strongest) - 1) * 0.08
    return min(1.0, raw + diversity_bonus)


def _encounter_at(seconds: float, pulls: list[PullCandidate]) -> str | None:
    containing = next(
        (
            pull.encounter
            for pull in pulls
            if pull.encounter and pull.start_seconds <= seconds <= pull.end_seconds
        ),
        None,
    )
    if containing is not None:
        return containing
    nearby = [
        (abs(pull.end_seconds - seconds), pull.encounter)
        for pull in pulls
        if pull.encounter and abs(pull.end_seconds - seconds) <= 20
    ]
    return min(nearby)[1] if nearby else None


def build_highlight_candidates(
    signals: list[Signal],
    pulls: list[PullCandidate],
    *,
    recording_duration_seconds: float,
    settings: HighlightConfig,
) -> list[HighlightCandidate]:
    """Fuse raw signals into bounded, spaced, unapproved review candidates.

    Args:
        signals: Extracted audio, motion, death, and kill signals.
        pulls: Reviewed pulls used for encounter context.
        recording_duration_seconds: Upper bound for candidate windows.
        settings: Fusion, duration, spacing, score, and count policy.

    Returns:
        Chronological candidates with stable sequential IDs and ``include=false``.
    """

    candidates: list[HighlightCandidate] = []
    for group in _fuse(signals, window_seconds=settings.fusion_window_seconds):
        score = _score(group)
        if score < settings.minimum_score:
            continue
        peak = sum(item.seconds * item.strength for item in group) / sum(
            item.strength for item in group
        )
        start = max(0.0, peak - settings.lead_in_seconds)
        end = min(recording_duration_seconds, peak + settings.lead_out_seconds)
        if end - start > settings.review_clip_seconds:
            end = min(recording_duration_seconds, start + settings.review_clip_seconds)
        encounter = _encounter_at(peak, pulls)
        category = _category(group)
        title = (
            f"{encounter} {category.title()} Moment"
            if encounter
            else f"Raid {category.title()} Moment"
        )
        candidates.append(
            HighlightCandidate(
                id="pending",
                peak_seconds=peak,
                start_seconds=start,
                end_seconds=end,
                category=category,
                score=score,
                signals=[signal.detail for signal in group],
                encounter=encounter,
                include=False,
                title=title,
                notes="Automatically proposed; review reaction audio and framing before approval.",
            )
        )
    ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
    final_boss = [
        candidate
        for candidate in ranked
        if any(
            signal.startswith("boss_kill:") and "lich king" in signal.casefold()
            for signal in candidate.signals
        )
    ]
    ordered = [*final_boss, *(candidate for candidate in ranked if candidate not in final_boss)]
    selected: list[HighlightCandidate] = []
    for candidate in ordered:
        if all(
            abs(candidate.peak_seconds - kept.peak_seconds) >= settings.minimum_spacing_seconds
            for kept in selected
        ):
            selected.append(candidate)
        if len(selected) >= settings.maximum_candidates:
            break
    selected.sort(key=lambda item: item.peak_seconds)
    return [
        candidate.model_copy(update={"id": f"highlight-{index:03d}"})
        for index, candidate in enumerate(selected, start=1)
    ]


def analyse_highlights(
    recording: Path,
    pulls: list[PullCandidate],
    *,
    game_stream_index: int | None,
    discord_stream_index: int | None,
    microphone_stream_index: int | None,
    combat_log: Path | None,
    recording_started_at: datetime | None,
    recording_duration_seconds: float,
    recording_offset_seconds: float,
    settings: HighlightConfig,
) -> list[HighlightCandidate]:
    """Analyze a recording and propose review-only social highlights.

    Args:
        recording: Source media file.
        pulls: Reviewed and difficulty-labelled pull list.
        game_stream_index: Absolute game-audio stream index, when available.
        discord_stream_index: Absolute Discord-audio stream index, when available.
        microphone_stream_index: Absolute microphone stream kept distinct from
            game and Discord signal roles. It may be retained in review/export
            mixes when explicitly enabled by settings.
        combat_log: Optional combat log for death-pressure signals.
        recording_started_at: Timestamp used to align combat evidence.
        recording_duration_seconds: Source duration used for bounds.
        recording_offset_seconds: Explicit log-to-video synchronization offset.
        settings: Highlight extraction and review policy.

    Returns:
        Ranked, bounded, and unapproved highlight candidates.

    Raises:
        HighlightAnalysisError: If the microphone is misconfigured as a game or
            Discord signal source, or an FFmpeg signal pass fails.
    """

    if not settings.enabled:
        return []
    selected_audio = [
        stream
        for stream, enabled in (
            (game_stream_index, settings.keep_game_audio),
            (discord_stream_index, settings.keep_discord_audio),
        )
        if enabled and stream is not None
    ]
    if microphone_stream_index is not None and microphone_stream_index in selected_audio:
        raise HighlightAnalysisError(
            "Highlight game/Discord signal roles must not include the microphone"
        )
    signals: list[Signal] = []
    if settings.keep_discord_audio and discord_stream_index is not None:
        signals.extend(
            audio_energy_signals(
                recording,
                stream_index=discord_stream_index,
                threshold_db=settings.discord_rms_threshold_db,
                kind="discord",
            )
        )
    if settings.keep_game_audio and game_stream_index is not None:
        signals.extend(
            audio_energy_signals(
                recording,
                stream_index=game_stream_index,
                threshold_db=settings.game_rms_threshold_db,
                kind="game",
            )
        )
    signals.extend(
        motion_signals(
            recording,
            threshold=settings.motion_scene_threshold,
            sample_fps=settings.motion_sample_fps,
            keyframes_only=settings.motion_keyframes_only,
        )
    )
    signals.extend(
        combat_pressure_signals(
            combat_log,
            recording_started_at=recording_started_at,
            recording_duration_seconds=recording_duration_seconds,
            recording_offset_seconds=recording_offset_seconds,
        )
    )
    if settings.include_kill_climaxes:
        signals.extend(kill_climax_signals(pulls))
    return build_highlight_candidates(
        signals,
        pulls,
        recording_duration_seconds=recording_duration_seconds,
        settings=settings,
    )


def load_highlight_selection(path: Path) -> list[HighlightCandidate]:
    """Load and validate a reviewed highlight override file.

    Args:
        path: JSON list or object containing a ``highlights`` list.

    Returns:
        Validated highlight candidates, including explicit include decisions.

    Raises:
        HighlightAnalysisError: If the file is unreadable, malformed, or invalid.
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = raw.get("highlights", raw) if isinstance(raw, dict) else raw
        return _HIGHLIGHT_LIST.validate_python(rows)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise HighlightAnalysisError(f"Invalid highlight selection {path}: {exc}") from exc


def write_highlight_candidates(
    candidates: list[HighlightCandidate],
    *,
    json_destination: Path,
    markdown_destination: Path,
) -> None:
    """Write machine-readable and human-readable candidate reports.

    Args:
        candidates: Proposed highlight candidates.
        json_destination: JSON report destination.
        markdown_destination: Markdown report destination.

    Raises:
        OSError: If either report cannot be written.
    """

    atomic_write_json(
        json_destination,
        [candidate.model_dump(mode="json") for candidate in candidates],
    )
    lines = [
        "# Highlight Candidates",
        "",
        "Candidates combine heuristics and are never approved automatically.",
        "",
        "| Candidate | Time | Type | Score | Signals |",
        "|---|---:|---:|---:|---|",
        *[
            f"| {candidate.title} | {candidate.peak_seconds:.1f}s | {candidate.category} | "
            f"{candidate.score:.2f} | {', '.join(candidate.signals)} |"
            for candidate in candidates
        ],
    ]
    atomic_write_text(markdown_destination, "\n".join(lines) + "\n")
