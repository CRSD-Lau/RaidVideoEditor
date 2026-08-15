from __future__ import annotations

from raid_editor.classification.difficulty import (
    ICC_BOSSES,
    detect_icc_difficulty,
    scoreline_title_prefix,
    summarize_raid_progress,
)
from raid_editor.config.models import DifficultyConfig
from raid_editor.models import PullCandidate


def _kill(number: int, encounter: str, difficulty: str) -> PullCandidate:
    return PullCandidate(
        id=f"pull-{number:03d}",
        start_seconds=number * 100.0,
        end_seconds=number * 100.0 + 80.0,
        type="boss_kill",
        encounter=encounter,
        result="kill",
        difficulty=difficulty,
        difficulty_confidence="high" if difficulty != "UNKNOWN" else "none",
    )


def test_icc_rank_spell_modes_and_conflicts_are_auditable() -> None:
    assert (
        detect_icc_difficulty(
            "Lord Marrowgar",
            {70825},
            configured_raid_size=25,
        ).mode
        == "25H"
    )
    assert (
        detect_icc_difficulty(
            "Lord Marrowgar",
            {70823},
            configured_raid_size=25,
        ).mode
        == "25N"
    )

    conflict = detect_icc_difficulty(
        "Lord Marrowgar",
        {70823, 70825},
        configured_raid_size=25,
    )

    assert conflict.mode == "UNKNOWN"
    assert conflict.confidence == "none"
    assert "Conflicting" in conflict.reason
    assert conflict.evidence == ("spell:70823=>25N", "spell:70825=>25H")


def test_size_scoped_heroic_marker_requires_raid_size_consensus() -> None:
    unresolved = detect_icc_difficulty(
        "Deathbringer Saurfang",
        {72769},
        configured_raid_size=None,
    )
    resolved = detect_icc_difficulty(
        "Deathbringer Saurfang",
        {72769},
        configured_raid_size=25,
        raid_size_evidence="inferred_consensus_raid_size",
    )

    assert unresolved.mode == "UNKNOWN"
    assert resolved.mode == "25H"
    assert "inferred_consensus_raid_size:25" in resolved.evidence


def test_full_clear_scoreline_is_exactly_12_of_12_7hc() -> None:
    bosses = sorted(ICC_BOSSES)
    pulls = [
        _kill(index, boss, "25H" if index <= 7 else "25N")
        for index, boss in enumerate(bosses, start=1)
    ]
    settings = DifficultyConfig(expected_bosses=12, title_raid_abbreviation="ICC")

    progress = summarize_raid_progress(
        pulls,
        raid_name="Icecrown Citadel",
        settings=settings,
    )

    assert progress.title_ready is True
    assert progress.full_clear is True
    assert (
        scoreline_title_prefix(
            progress,
            raid_name="Icecrown Citadel",
            settings=settings,
        )
        == "ICC 25M 12/12 7HC Full Clear"
    )


def test_unknown_boss_mode_blocks_claiming_a_heroic_count() -> None:
    pulls = [
        _kill(1, "Lord Marrowgar", "25H"),
        _kill(2, "Lady Deathwhisper", "UNKNOWN"),
    ]
    settings = DifficultyConfig(expected_bosses=12, title_raid_abbreviation="ICC")

    progress = summarize_raid_progress(
        pulls,
        raid_name="Icecrown Citadel",
        settings=settings,
    )

    assert progress.title_ready is False
    assert progress.unknown_difficulty == ("Lady Deathwhisper",)
    assert (
        scoreline_title_prefix(
            progress,
            raid_name="Icecrown Citadel",
            settings=settings,
        )
        == "ICC 25M 2/12 ?HC"
    )
