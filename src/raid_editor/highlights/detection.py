"""Fuse bounded audio, motion, combat, and kill signals into review candidates."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median

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
    relative_energy: bool = True,
) -> list[Signal]:
    """Extract and suppress audio-energy peaks from one absolute stream index.

    Args:
        recording: Source media file.
        stream_index: Absolute FFprobe stream index to analyze.
        threshold_db: Minimum one-second RMS level in decibels.
        kind: Distinct game, Discord, or microphone signal role.
        relative_energy: Require a rise above the surrounding local background.

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
    eligible: list[Signal] = []
    left = right = 0
    for seconds, rms in points:
        if rms < threshold_db:
            continue
        while left < len(points) and points[left][0] < seconds - 30:
            left += 1
        while right < len(points) and points[right][0] <= seconds + 30:
            right += 1
        # Exclude the immediate event when estimating its local background. A
        # short or mostly silent neighborhood falls back to the absolute floor.
        background = [level for time, level in points[left:right] if abs(time - seconds) >= 5]
        baseline = max(threshold_db - 12.0, median(background)) if background else threshold_db
        rise = rms - baseline
        if relative_energy and len(background) >= 5 and rise < 3.0:
            continue
        excess = rise if relative_energy else rms - threshold_db
        eligible.append(
            Signal(
                seconds=seconds,
                kind=kind,
                strength=min(1.0, 0.35 + max(0.0, excess) / 18.0),
                detail=(
                    f"{kind}_rms:{rms:.1f}dB;local_rise:{rise:.1f}dB"
                    if relative_energy
                    else f"{kind}_rms:{rms:.1f}dB"
                ),
            )
        )
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


def _player_death_destination(fields: tuple[str, ...]) -> str | None:
    """Accept only known CLEU layouts with matching destination type evidence.

    The legacy layout is event, source GUID/name/flags, destination GUID/name/flags.
    Modern CLEU adds hideCaster and source/destination raid flags. In particular,
    the legacy null SOURCE GUID must never be interpreted as the dead player.
    """

    if len(fields) == 7 and fields[0] == "UNIT_DIED":
        guid, flags_text = fields[4], fields[6]
    elif len(fields) == 10 and fields[0] == "UNIT_DIED" and fields[1] in {"true", "false"}:
        guid, flags_text = fields[6], fields[8]
        try:
            for index in (4, 5, 9):
                int(fields[index], 16)
        except ValueError:
            return None
    else:
        return None
    try:
        flags = int(flags_text, 16)
    except ValueError:
        return None
    # COMBATLOG_OBJECT_TYPE_PLAYER, excluding conflicting NPC/pet/guardian/object types.
    if not flags & 0x400 or flags & 0xF800:
        return None
    if re.fullmatch(r"Player-\d+-[0-9a-fA-F]+", guid):
        return guid
    if re.fullmatch(r"0[xX](?:06[0-9a-fA-F]{14}|0000000000[0-9a-fA-F]{6})", guid):
        return guid if int(guid, 16) != 0 else None
    return None


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
    deaths: dict[int, list[float]] = defaultdict(list)
    for event in iter_timed_log_events(
        combat_log,
        recording_started_at=recording_started_at,
        recording_duration_seconds=recording_duration_seconds,
        recording_offset_seconds=recording_offset_seconds,
        margin_seconds=0.0,
    ):
        if event.event != "UNIT_DIED":
            continue
        if not 0 <= event.video_seconds <= recording_duration_seconds:
            continue
        if _player_death_destination(event.fields) is not None:
            deaths[math.floor(event.video_seconds / 8.0)].append(event.video_seconds)
    return [
        Signal(
            seconds=sum(times) / len(times),
            kind="raid_deaths",
            strength=min(1.0, 0.35 + len(times) * 0.13),
            detail=f"player_deaths_8s:{len(times)}",
        )
        for _, times in sorted(deaths.items())
        if len(times) >= 2
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
    """Use provisional categories without claiming humor or a successful rescue."""

    kinds = {signal.kind for signal in group}
    if "raid_deaths" in kinds or "kill_climax" in kinds:
        return "intense"
    if kinds & {"speech_clip_command", "discord", "microphone"}:
        return "reaction"
    if "motion" in kinds:
        return "movement"
    return "reaction"


def _score(group: list[Signal]) -> float:
    """Return a monotonic ranking value, not a probability or ASR confidence."""

    weights = {
        "discord": 0.36,
        "microphone": 0.36,
        "game": 0.16,
        "motion": 0.22,
        "raid_deaths": 0.36,
        "kill_climax": 0.22,
        "speech_clip_command": 1.0,
    }
    strongest: dict[str, float] = {}
    for signal in group:
        strongest[signal.kind] = max(strongest.get(signal.kind, 0.0), signal.strength)
    raw = sum(weights.get(kind, 0.1) * strength for kind, strength in strongest.items())
    diversity_bonus = max(0, len(strongest) - 1) * 0.08
    return -math.expm1(-(raw + diversity_bonus))


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


def _is_spoken(candidate: HighlightCandidate) -> bool:
    return candidate.origin == "speech" or any(
        signal.startswith("speech_clip_command:") for signal in candidate.signals
    )


def _routine_kill(candidate: HighlightCandidate) -> bool:
    """A heuristic finish with no separate voice or player-pressure evidence."""

    return (
        candidate.origin == "heuristic"
        and any(signal.startswith("boss_kill:") for signal in candidate.signals)
        and not any(
            signal.startswith(("player_deaths_8s:", "discord_rms:", "microphone_rms:"))
            for signal in candidate.signals
        )
    )


def _overlapping_event(
    candidate: HighlightCandidate, kept: HighlightCandidate, *, spacing_seconds: float
) -> bool:
    overlap = min(candidate.end_seconds, kept.end_seconds) - max(
        candidate.start_seconds, kept.start_seconds
    )
    shorter = min(
        candidate.end_seconds - candidate.start_seconds,
        kept.end_seconds - kept.start_seconds,
    )
    return (
        abs(candidate.peak_seconds - kept.peak_seconds) < spacing_seconds
        or overlap / shorter >= 0.65
    )


def _number_candidates(candidates: list[HighlightCandidate]) -> list[HighlightCandidate]:
    """Assign display order while retaining each original moment's feedback identity."""

    def identity(candidate: HighlightCandidate) -> str:
        if candidate.review_identity is not None:
            return candidate.review_identity
        material = {
            "origin": candidate.origin,
            "start_seconds": candidate.start_seconds,
            "end_seconds": candidate.end_seconds,
            "peak_seconds": candidate.peak_seconds,
            "semantic_kinds": sorted(
                {signal for signal in candidate.signals if signal.startswith("semantic_kind:")}
            ),
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    return [
        candidate.model_copy(
            update={
                "id": f"highlight-{index:03d}",
                "review_identity": identity(candidate),
            }
        )
        for index, candidate in enumerate(
            sorted(candidates, key=lambda item: item.peak_seconds), start=1
        )
    ]


def _enrich_spoken_candidates(
    candidates: list[HighlightCandidate], *, settings: HighlightConfig
) -> tuple[list[HighlightCandidate], set[int]]:
    """Attach supported story context without replacing an explicit voice anchor."""

    semantic = sorted(
        (
            (index, candidate)
            for index, candidate in enumerate(candidates)
            if candidate.origin == "semantic"
            and candidate.score >= settings.intelligence.minimum_score
            and candidate.confidence is not None
            and candidate.confidence >= settings.intelligence.minimum_score
            and candidate.setup_seconds is not None
            and candidate.payoff_seconds is not None
            and candidate.rationale
            and candidate.signals
            and candidate.review_rating != "reject"
            and candidate.rejection_reason == "unset"
        ),
        key=lambda pair: pair[1].score,
        reverse=True,
    )
    spoken: list[HighlightCandidate] = []
    consumed: set[int] = set()
    for command in (candidate for candidate in candidates if _is_spoken(candidate)):
        enriched = command
        for index, context in semantic:
            if not _overlapping_event(context, command, spacing_seconds=0):
                continue
            start = min(command.start_seconds, context.start_seconds)
            end = max(command.end_seconds, context.end_seconds)
            maximum = max(
                command.end_seconds - command.start_seconds,
                settings.intelligence.maximum_clip_seconds,
            )
            if end - start > maximum:
                continue
            rationale = " ".join(item for item in (command.rationale, context.rationale) if item)[
                :1200
            ]
            provenance = (
                f"semantic_context:{context.setup_seconds:.3f}-{context.payoff_seconds:.3f}:"
                f"score:{context.score:.3f}"
            )
            enriched = HighlightCandidate.model_validate(
                {
                    **command.model_dump(),
                    "origin": "speech",
                    "start_seconds": start,
                    "end_seconds": end,
                    "setup_seconds": context.setup_seconds,
                    "payoff_seconds": context.payoff_seconds,
                    "rationale": rationale,
                    "signals": list(
                        dict.fromkeys([*command.signals, provenance, *context.signals])
                    ),
                    "include": False,
                }
            )
            consumed.add(index)
            break
        spoken.append(enriched)
    return spoken, consumed


def select_highlight_candidates(
    candidates: list[HighlightCandidate], *, settings: HighlightConfig
) -> list[HighlightCandidate]:
    """Shortlist a combined discovery pool without discarding spoken intent.

    Explicit command anchors remain review-only proposals outside all heuristic
    limits, even when two commands overlap. Other proposals must meet the score
    floor; no quota is filled with weak or duplicate material.
    """

    spoken, consumed = _enrich_spoken_candidates(candidates, settings=settings)
    ranked = sorted(
        (
            candidate
            for index, candidate in enumerate(candidates)
            if not _is_spoken(candidate)
            and index not in consumed
            and candidate.score >= settings.minimum_score
            and candidate.review_rating != "reject"
            and candidate.rejection_reason == "unset"
        ),
        key=lambda item: (item.score, item.origin == "semantic"),
        reverse=True,
    )
    selected = list(spoken)
    routine_kills = 0
    selected_other = 0
    for candidate in ranked:
        is_routine = _routine_kill(candidate)
        if is_routine and routine_kills >= settings.maximum_routine_kills:
            continue
        if any(
            _overlapping_event(candidate, kept, spacing_seconds=settings.minimum_spacing_seconds)
            for kept in selected
            # If a semantic story could not fit the command's context bound,
            # retain it separately instead of discarding the richer evidence.
            if not (candidate.origin == "semantic" and _is_spoken(kept))
        ):
            continue
        selected.append(candidate)
        selected_other += 1
        routine_kills += is_routine
        if selected_other >= settings.maximum_candidates:
            break
    return _number_candidates(selected)


def _event_window(
    group: list[Signal], *, duration: float, settings: HighlightConfig
) -> tuple[float, float, float]:
    # Event anchors take priority over a centroid that can drift into nearby
    # unrelated movement. Timing is still provisional until semantic review.
    priorities = {"speech_clip_command": 4, "kill_climax": 3, "raid_deaths": 2}
    anchor = max(group, key=lambda item: (priorities.get(item.kind, 1), item.strength))
    peak = anchor.seconds
    first, last = min(item.seconds for item in group), max(item.seconds for item in group)
    start = max(0.0, first - settings.lead_in_seconds)
    end = min(duration, last + settings.lead_out_seconds)
    maximum = min(duration, settings.review_clip_seconds)
    if end - start > maximum:
        if last - first <= maximum:
            # Preserve every observed signal when the evidence span fits.
            start = max(last - maximum, min(start, first))
        else:
            start = peak - min(settings.lead_in_seconds, maximum * 0.65)
        start = max(0.0, min(start, duration - maximum))
        end = start + maximum
    if end <= start:
        start = max(0.0, peak - min(1.0, maximum))
        end = min(duration, start + min(1.0, maximum))
    return peak, start, end


def build_highlight_candidates(
    signals: list[Signal],
    pulls: list[PullCandidate],
    *,
    recording_duration_seconds: float,
    settings: HighlightConfig,
    limit_candidates: bool = True,
) -> list[HighlightCandidate]:
    """Fuse raw signals into bounded, spaced, unapproved review candidates.

    Args:
        signals: Extracted audio, motion, death, and kill signals.
        pulls: Reviewed pulls used for encounter context.
        recording_duration_seconds: Upper bound for candidate windows.
        settings: Fusion, duration, spacing, score, and count policy.
        limit_candidates: False retains a larger discovery pool for semantic review.

    Returns:
        Chronological candidates with stable sequential IDs and ``include=false``.
    """

    if not math.isfinite(recording_duration_seconds) or recording_duration_seconds <= 0:
        raise HighlightAnalysisError("Highlight recording duration must be positive and finite")
    invalid_commands = [
        signal
        for signal in signals
        if signal.kind == "speech_clip_command"
        and not 0 <= signal.seconds <= recording_duration_seconds
    ]
    if invalid_commands:
        raise HighlightAnalysisError("A spoken command falls outside the recording; check timing")
    signals = [
        signal
        for signal in signals
        if 0 <= signal.seconds <= recording_duration_seconds
        and math.isfinite(signal.strength)
        and signal.strength > 0
    ]
    candidates: list[HighlightCandidate] = []
    spoken_groups = [[signal] for signal in signals if signal.kind == "speech_clip_command"]
    heuristic_groups = _fuse(
        [signal for signal in signals if signal.kind != "speech_clip_command"],
        window_seconds=settings.fusion_window_seconds,
    )
    for group in [*spoken_groups, *heuristic_groups]:
        spoken_command = next(
            (signal for signal in group if signal.kind == "speech_clip_command"),
            None,
        )
        score = _score(group)
        if limit_candidates and spoken_command is None and score < settings.minimum_score:
            continue
        peak, start, end = _event_window(
            group, duration=recording_duration_seconds, settings=settings
        )
        encounter = _encounter_at(peak, pulls)
        category = _category(group)
        if spoken_command is not None:
            title = f"{encounter or 'Raid'} Clip-It Moment"
            notes = (
                "Exact spoken 'clip it' command detected locally; review the preceding "
                "moment and audio before approval."
            )
        else:
            kinds = {signal.kind for signal in group}
            label = (
                "Player Death Cluster"
                if "raid_deaths" in kinds
                else "Boss Finish"
                if "kill_climax" in kinds
                else "Voice Activity"
                if kinds & {"discord", "microphone"}
                else "Scene Change"
                if "motion" in kinds
                else "Game Audio Activity"
            )
            title = f"{encounter or 'Raid'} {label} Candidate"
            notes = "Provisional signal-based suggestion; review the event and its context."
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
                notes=notes,
                origin="speech" if spoken_command is not None else "heuristic",
                confidence=spoken_command.strength if spoken_command is not None else None,
                rationale=(
                    "An explicit spoken command marks this moment for review."
                    if spoken_command is not None
                    else "Timing comes from the listed audio, visual, and combat signals."
                ),
            )
        )
    if limit_candidates:
        return select_highlight_candidates(candidates, settings=settings)
    spoken = [candidate for candidate in candidates if _is_spoken(candidate)]
    ranked = sorted(
        (candidate for candidate in candidates if not _is_spoken(candidate)),
        key=lambda item: item.score,
        reverse=True,
    )
    budget = max(0, settings.candidate_pool_size - len(spoken))
    return _number_candidates([*spoken, *ranked[:budget]])


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
    spoken_command_signals: list[Signal] | None = None,
    limit_candidates: bool = True,
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
        spoken_command_signals: Exact, locally detected spoken commands. Each
            command is reserved outside heuristic score, spacing, and count limits.

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
    if spoken_command_signals:
        signals.extend(spoken_command_signals)
    if settings.keep_discord_audio and discord_stream_index is not None:
        signals.extend(
            audio_energy_signals(
                recording,
                stream_index=discord_stream_index,
                threshold_db=settings.discord_rms_threshold_db,
                kind="discord",
                relative_energy=settings.relative_audio_energy,
            )
        )
    if settings.keep_game_audio and game_stream_index is not None:
        signals.extend(
            audio_energy_signals(
                recording,
                stream_index=game_stream_index,
                threshold_db=settings.game_rms_threshold_db,
                kind="game",
                relative_energy=settings.relative_audio_energy,
            )
        )
    if settings.keep_microphone_audio and microphone_stream_index is not None:
        signals.extend(
            audio_energy_signals(
                recording,
                stream_index=microphone_stream_index,
                threshold_db=settings.microphone_rms_threshold_db,
                kind="microphone",
                relative_energy=settings.relative_audio_energy,
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
        limit_candidates=limit_candidates,
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
