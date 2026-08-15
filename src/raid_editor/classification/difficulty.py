"""Auditable Icecrown Citadel difficulty detection and title scorelines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from raid_editor.config.models import DifficultyConfig
from raid_editor.detection.log_stream import TimedLogEvent, iter_timed_log_events
from raid_editor.models import DifficultyConfidence, DifficultyMode, PullCandidate
from raid_editor.util.paths import atomic_write_json, atomic_write_text

DETECTOR_VERSION = "raid-editor-icc-difficulty-v2"
VALID_MODES: tuple[DifficultyMode, ...] = ("10N", "10H", "25N", "25H")
_ENCOUNTER_DIFFICULTY_IDS: dict[int, DifficultyMode] = {
    1: "10N",
    2: "25N",
    3: "10N",
    4: "25N",
    5: "10H",
    6: "25H",
}


def _modes(*families: tuple[str, str, str, str]) -> dict[DifficultyMode, frozenset[int]]:
    result: dict[DifficultyMode, set[int]] = {mode: set() for mode in VALID_MODES}
    for family in families:
        for mode, spell_id in zip(VALID_MODES, family, strict=True):
            if spell_id:
                result[mode].add(int(spell_id))
    return {mode: frozenset(ids) for mode, ids in result.items() if ids}


ICC_DIFFICULTY_SPELLS: dict[str, dict[DifficultyMode, frozenset[int]]] = {
    "Lord Marrowgar": _modes(("69146", "70824", "70823", "70825")),
    "Lady Deathwhisper": _modes(
        ("71254", "72503", "72008", "72504"),
        ("71001", "72109", "72108", "72110"),
    ),
    "Gunship Battle": _modes(
        ("70162", "72567", "72566", "72568"),
        ("70161", "72540", "72539", "72541"),
    ),
    "Deathbringer Saurfang": _modes(
        ("72380", "72439", "72438", "72440"),
        ("72385", "72442", "72441", "72443"),
    ),
    "Festergut": _modes(("72219", "72552", "72551", "72553")),
    "Rotface": _modes(("69674", "73022", "71224", "73023")),
    "Professor Putricide": _modes(
        ("70402", "72512", "72511", "72513"),
        ("70351", "71967", "71966", "71968"),
    ),
    "Blood Prince Council": _modes(("71405", "72805", "72804", "72806")),
    "Blood-Queen Lana'thel": _modes(("70985", "71699", "71698", "71700")),
    "Valithria Dreamwalker": _modes(("70759", "72015", "71889", "72016")),
    "Sindragosa": _modes(("70084", "71051", "71050", "71052")),
    "The Lich King": _modes(("70541", "73780", "73779", "73781")),
}

_ALIASES = {
    "icecrown gunship battle": "Gunship Battle",
    "gunship battle": "Gunship Battle",
}
_SIZE_SCOPED_HEROIC_MARKERS = {
    "Deathbringer Saurfang": frozenset({72769, 72771}),
    "Valithria Dreamwalker": frozenset({71940, 71941}),
}
ICC_BOSSES = frozenset(ICC_DIFFICULTY_SPELLS)


@dataclass(frozen=True, slots=True)
class DifficultyDetection:
    """Hold one evidence-backed encounter difficulty decision."""

    mode: DifficultyMode
    confidence: DifficultyConfidence
    evidence: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class RaidProgress:
    """Summarize unique winning bosses for scoreline and title generation."""

    raid_size: int | None
    bosses_killed: int
    expected_bosses: int
    heroic_kills: int
    unknown_difficulty: tuple[str, ...]
    duplicate_encounters: tuple[str, ...]

    @property
    def full_clear(self) -> bool:
        return self.bosses_killed == self.expected_bosses

    @property
    def title_ready(self) -> bool:
        return self.raid_size in {10, 25} and not self.unknown_difficulty


def _canonical_boss(name: str | None) -> str | None:
    if name is None:
        return None
    return _ALIASES.get(name.casefold(), name)


def _detection(
    mode: DifficultyMode,
    confidence: DifficultyConfidence,
    evidence: list[str],
    reason: str,
) -> DifficultyDetection:
    return DifficultyDetection(mode, confidence, tuple(sorted(set(evidence))), reason)


def detect_icc_difficulty(
    boss_name: str,
    spell_ids: set[int],
    *,
    configured_raid_size: int | None,
    encounter_modes: set[DifficultyMode] | None = None,
    raid_size_evidence: str = "configured_raid_size",
) -> DifficultyDetection:
    """Classify one already-segmented encounter from aggregate evidence."""

    canonical = _canonical_boss(boss_name) or boss_name
    spell_map = ICC_DIFFICULTY_SPELLS.get(canonical)
    if spell_map is None:
        return _detection(
            "UNKNOWN",
            "none",
            [],
            f"{boss_name} has no supported Icecrown difficulty rule",
        )

    heroic_markers = spell_ids & _SIZE_SCOPED_HEROIC_MARKERS.get(canonical, frozenset())
    if heroic_markers:
        if configured_raid_size not in {10, 25}:
            return _detection(
                "UNKNOWN",
                "none",
                [*(f"spell:{spell_id}" for spell_id in heroic_markers)],
                "A heroic-only marker is present but raid size is unknown",
            )
        mode: DifficultyMode = "25H" if configured_raid_size == 25 else "10H"
        return _detection(
            mode,
            "high",
            [
                f"{raid_size_evidence}:{configured_raid_size}",
                *(f"spell:{spell_id}" for spell_id in heroic_markers),
            ],
            f"{canonical} heroic-only marker is present",
        )

    matched: dict[DifficultyMode, frozenset[int]] = {}
    for mode, identifiers in spell_map.items():
        overlap = identifiers & spell_ids
        if overlap:
            matched[mode] = overlap
    evidence = [
        f"spell:{spell_id}=>{mode}"
        for mode, identifiers in matched.items()
        for spell_id in sorted(identifiers)
    ]
    if len(matched) > 1:
        return _detection(
            "UNKNOWN",
            "none",
            evidence,
            "Conflicting difficulty-specific spell IDs occur in the pull window",
        )
    if len(matched) == 1:
        mode = next(iter(matched))
        if configured_raid_size is not None and int(mode[:2]) != configured_raid_size:
            return _detection(
                "UNKNOWN",
                "none",
                [*evidence, f"{raid_size_evidence}:{configured_raid_size}"],
                "Spell evidence conflicts with the configured raid size",
            )
        return _detection(
            mode,
            "high",
            evidence,
            "One unambiguous boss-specific spell mode matched",
        )

    supported_encounter_modes = {mode for mode in (encounter_modes or set()) if mode in spell_map}
    if len(supported_encounter_modes) == 1:
        mode = next(iter(supported_encounter_modes))
        if configured_raid_size is not None and int(mode[:2]) != configured_raid_size:
            return _detection(
                "UNKNOWN",
                "none",
                [f"encounter_start:{mode}", f"{raid_size_evidence}:{configured_raid_size}"],
                "Encounter marker conflicts with the configured raid size",
            )
        return _detection(
            mode,
            "medium",
            [f"encounter_start:{mode}"],
            "Supported encounter mode used because no rank evidence was present",
        )
    if len(supported_encounter_modes) > 1:
        return _detection(
            "UNKNOWN",
            "none",
            [*(f"encounter_start:{mode}" for mode in supported_encounter_modes)],
            "Conflicting encounter modes occur in the pull window",
        )
    return _detection(
        "UNKNOWN",
        "none",
        [f"{raid_size_evidence}:{configured_raid_size}"] if configured_raid_size else [],
        "No supported difficulty evidence was found",
    )


def _encounter_mode(event: TimedLogEvent) -> DifficultyMode | None:
    if event.event != "ENCOUNTER_START" or len(event.fields) < 4:
        return None
    try:
        return _ENCOUNTER_DIFFICULTY_IDS.get(int(event.fields[3]))
    except ValueError:
        return None


def classify_pull_difficulties(
    pulls: list[PullCandidate],
    *,
    combat_log: Path | None,
    recording_started_at: datetime | None,
    recording_duration_seconds: float,
    recording_offset_seconds: float,
    settings: DifficultyConfig,
) -> list[PullCandidate]:
    """Enrich pulls in one streaming pass while preserving confirmed overrides."""

    if not settings.enabled or combat_log is None or recording_started_at is None:
        return pulls
    windows = [
        (index, pull)
        for index, pull in enumerate(pulls)
        if pull.encounter is not None and pull.type.startswith("boss")
    ]
    if not windows:
        return pulls
    spell_ids: dict[int, set[int]] = {index: set() for index, _ in windows}
    encounter_modes: dict[int, set[DifficultyMode]] = {index: set() for index, _ in windows}
    for event in iter_timed_log_events(
        combat_log,
        recording_started_at=recording_started_at,
        recording_duration_seconds=recording_duration_seconds,
        recording_offset_seconds=recording_offset_seconds,
    ):
        for index, pull in windows:
            if event.video_seconds < pull.start_seconds:
                continue
            if event.video_seconds > pull.end_seconds:
                continue
            if event.spell_id is not None:
                spell_ids[index].add(event.spell_id)
            mode = _encounter_mode(event)
            if mode is not None:
                encounter_modes[index].add(mode)

    effective_raid_size = settings.raid_size
    raid_size_evidence = "configured_raid_size"
    if effective_raid_size is None:
        preliminary = [
            detect_icc_difficulty(
                pull.encounter or "",
                spell_ids[index],
                configured_raid_size=None,
                encounter_modes=encounter_modes[index],
            )
            for index, pull in windows
        ]
        observed_sizes = {
            int(result.mode[:2]) for result in preliminary if result.mode != "UNKNOWN"
        }
        if len(observed_sizes) == 1:
            effective_raid_size = cast(Literal[10, 25], next(iter(observed_sizes)))
            raid_size_evidence = "inferred_consensus_raid_size"

    enriched = list(pulls)
    for index, pull in windows:
        if pull.difficulty != "UNKNOWN" and "user_confirmed" in pull.difficulty_evidence:
            continue
        detection = detect_icc_difficulty(
            pull.encounter or "",
            spell_ids[index],
            configured_raid_size=effective_raid_size,
            encounter_modes=encounter_modes[index],
            raid_size_evidence=raid_size_evidence,
        )
        enriched[index] = pull.model_copy(
            update={
                "difficulty": detection.mode,
                "difficulty_confidence": detection.confidence,
                "difficulty_evidence": [*detection.evidence, f"detector:{DETECTOR_VERSION}"],
                "difficulty_reason": detection.reason,
            }
        )
    return enriched


def summarize_raid_progress(
    pulls: list[PullCandidate],
    *,
    raid_name: str | None,
    settings: DifficultyConfig,
) -> RaidProgress:
    """Summarize included winning pulls without counting duplicate encounters.

    Args:
        pulls: Classified pull candidates.
        raid_name: Display raid name used to infer an ICC boss count.
        settings: Difficulty and title policy.

    Returns:
        Unique-boss progress, Heroic count, inferred size, and unresolved names.
    """

    expected = settings.expected_bosses
    if expected is None:
        expected = 12 if raid_name and "icecrown" in raid_name.casefold() else len(ICC_BOSSES)
    winners = [
        pull
        for pull in pulls
        if pull.include
        and pull.encounter
        and (pull.type == "boss_kill" or pull.result in {"kill", "success"})
    ]
    by_encounter: dict[str, PullCandidate] = {}
    duplicates: set[str] = set()
    for pull in winners:
        encounter = pull.encounter
        if encounter is None:
            continue
        canonical = _canonical_boss(encounter) or encounter
        if canonical in by_encounter:
            duplicates.add(canonical)
        by_encounter[canonical] = pull
    unknown = tuple(
        sorted(name for name, pull in by_encounter.items() if pull.difficulty == "UNKNOWN")
    )
    modes = {pull.difficulty for pull in by_encounter.values() if pull.difficulty != "UNKNOWN"}
    sizes = {int(mode[:2]) for mode in modes}
    raid_size = (
        settings.raid_size
        if settings.raid_size is not None
        else (next(iter(sizes)) if len(sizes) == 1 else None)
    )
    return RaidProgress(
        raid_size=raid_size,
        bosses_killed=len(by_encounter),
        expected_bosses=expected,
        heroic_kills=sum(pull.difficulty.endswith("H") for pull in by_encounter.values()),
        unknown_difficulty=unknown,
        duplicate_encounters=tuple(sorted(duplicates)),
    )


def scoreline_title_prefix(
    progress: RaidProgress,
    *,
    raid_name: str | None,
    settings: DifficultyConfig,
) -> str:
    """Format the compact raid scoreline used by titles and thumbnails.

    Args:
        progress: Summarized winning-pull progress.
        raid_name: Display raid name used for the default abbreviation.
        settings: Difficulty and title policy.

    Returns:
        A scoreline such as ``ICC 25M 12/12 7HC Full Clear``. Unknown evidence
        is shown explicitly instead of guessed.
    """

    abbreviation = settings.title_raid_abbreviation
    if abbreviation is None:
        abbreviation = "ICC" if raid_name and "icecrown" in raid_name.casefold() else raid_name
    abbreviation = abbreviation or "Raid"
    size = f"{progress.raid_size}M" if progress.raid_size else "?M"
    heroic = f"{progress.heroic_kills}HC" if not progress.unknown_difficulty else "?HC"
    full_clear = " Full Clear" if progress.full_clear else ""
    return (
        f"{abbreviation} {size} {progress.bosses_killed}/{progress.expected_bosses} "
        f"{heroic}{full_clear}"
    )


def write_difficulty_report(
    pulls: list[PullCandidate],
    progress: RaidProgress,
    *,
    json_destination: Path,
    markdown_destination: Path,
) -> None:
    """Write auditable JSON and Markdown difficulty reports.

    Args:
        pulls: Classified pull candidates to serialize.
        progress: Aggregate raid progress derived from those pulls.
        json_destination: Machine-readable report destination.
        markdown_destination: Human-readable report destination.

    Raises:
        OSError: If either report cannot be written.
    """

    rows = [
        {
            "id": pull.id,
            "encounter": pull.encounter,
            "result": pull.result,
            "difficulty": pull.difficulty,
            "confidence": pull.difficulty_confidence,
            "evidence": pull.difficulty_evidence,
            "reason": pull.difficulty_reason,
        }
        for pull in pulls
        if pull.encounter is not None and pull.type.startswith("boss")
    ]
    atomic_write_json(
        json_destination,
        {
            "detector_version": DETECTOR_VERSION,
            "progress": {
                "raid_size": progress.raid_size,
                "bosses_killed": progress.bosses_killed,
                "expected_bosses": progress.expected_bosses,
                "heroic_kills": progress.heroic_kills,
                "unknown_difficulty": list(progress.unknown_difficulty),
                "duplicate_encounters": list(progress.duplicate_encounters),
                "title_ready": progress.title_ready,
            },
            "pulls": rows,
        },
    )
    lines = [
        "# Raid Difficulty",
        "",
        f"- Progress: {progress.bosses_killed}/{progress.expected_bosses}",
        f"- Raid size: {progress.raid_size or 'unknown'}",
        f"- Confirmed Heroic kills: {progress.heroic_kills}",
        f"- Automatic title ready: {'yes' if progress.title_ready else 'no'}",
        "",
        "| Boss | Mode | Confidence | Reason |",
        "|---|---:|---:|---|",
    ]
    lines.extend(
        f"| {row['encounter']} | {row['difficulty']} | {row['confidence']} | {row['reason']} |"
        for row in rows
    )
    if progress.unknown_difficulty:
        lines.extend(
            [
                "",
                "## Needs confirmation",
                "",
                *[f"- {boss}" for boss in progress.unknown_difficulty],
            ]
        )
    atomic_write_text(markdown_destination, "\n".join(lines) + "\n")
