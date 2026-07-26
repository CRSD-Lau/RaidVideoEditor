from __future__ import annotations

import math

import pytest

from raid_editor.detection.combat_log import Encounter, PullResult, PullType
from raid_editor.timeline.merge import TimelineWindow, merge_timeline_windows


def _trash(
    start_seconds: float,
    end_seconds: float,
    *,
    evidence: tuple[str, ...] = ("trash",),
    confidence: float = 1.0,
    include: bool = True,
) -> TimelineWindow:
    return TimelineWindow(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        type=PullType.TRASH,
        result=PullResult.UNKNOWN,
        encounter=None,
        evidence=evidence,
        confidence=confidence,
        include=include,
    )


def _boss(
    start_seconds: float,
    end_seconds: float,
    *,
    encounter_id: int = 10,
    attempt_number: int = 1,
    result: PullResult = PullResult.WIPE,
) -> TimelineWindow:
    return TimelineWindow(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        type=PullType.BOSS,
        result=result,
        encounter=Encounter(encounter_id, "Boss"),
        evidence=("encounter",),
        confidence=1.0,
        include=True,
        attempt_number=attempt_number,
    )


def test_applies_lead_in_and_out_then_clamps_to_recording() -> None:
    result = merge_timeline_windows(
        [_trash(2.0, 98.0)],
        recording_duration_seconds=100.0,
        lead_in_seconds=5.0,
        lead_out_seconds=10.0,
    )

    assert [(window.start_seconds, window.end_seconds) for window in result] == [(0.0, 100.0)]


def test_drops_windows_that_are_wholly_outside_recording_after_clamping() -> None:
    result = merge_timeline_windows(
        [_trash(-20.0, -5.0), _trash(105.0, 110.0)],
        recording_duration_seconds=100.0,
    )

    assert result == ()


def test_merges_trash_at_exact_short_gap_boundary() -> None:
    result = merge_timeline_windows(
        [
            _trash(0.0, 10.0, evidence=("one",), confidence=0.7),
            _trash(12.0, 20.0, evidence=("two",), confidence=0.9),
        ],
        recording_duration_seconds=30.0,
        trash_merge_gap_seconds=2.0,
    )

    assert len(result) == 1
    assert result[0].start_seconds == 0.0
    assert result[0].end_seconds == 20.0
    assert result[0].evidence == ("one", "two")
    assert result[0].confidence == 0.9


def test_does_not_merge_trash_beyond_short_gap_boundary() -> None:
    result = merge_timeline_windows(
        [_trash(0.0, 10.0), _trash(12.001, 20.0)],
        recording_duration_seconds=30.0,
        trash_merge_gap_seconds=2.0,
    )

    assert len(result) == 2


def test_overlapping_trash_windows_collapse_without_duplicate_time() -> None:
    result = merge_timeline_windows(
        [_trash(0.0, 12.0), _trash(10.0, 20.0)],
        recording_duration_seconds=30.0,
    )

    assert [(window.start_seconds, window.end_seconds) for window in result] == [(0.0, 20.0)]


def test_never_merges_distinct_boss_attempts_even_for_same_encounter() -> None:
    result = merge_timeline_windows(
        [
            _boss(10.0, 20.0, attempt_number=1),
            _boss(
                19.0,
                30.0,
                attempt_number=2,
                result=PullResult.KILL,
            ),
        ],
        recording_duration_seconds=40.0,
        lead_in_seconds=2.0,
        lead_out_seconds=2.0,
        trash_merge_gap_seconds=100.0,
    )

    assert len(result) == 2
    assert [window.attempt_number for window in result] == [1, 2]
    assert [window.result for window in result] == [
        PullResult.WIPE,
        PullResult.KILL,
    ]
    assert result[0].end_seconds == pytest.approx(19.5)
    assert result[1].start_seconds == pytest.approx(19.5)
    assert result[0].end_seconds <= result[1].start_seconds


def test_identical_boss_ranges_are_split_not_coalesced() -> None:
    result = merge_timeline_windows(
        [
            _boss(10.0, 20.0, attempt_number=1),
            _boss(10.0, 20.0, attempt_number=2),
        ],
        recording_duration_seconds=30.0,
    )

    assert len(result) == 2
    assert result[0].end_seconds == pytest.approx(15.0)
    assert result[1].start_seconds == pytest.approx(15.0)
    assert [window.attempt_number for window in result] == [1, 2]


def test_nested_boss_range_keeps_both_distinct_attempts() -> None:
    result = merge_timeline_windows(
        [
            _boss(0.0, 100.0, attempt_number=1),
            _boss(10.0, 20.0, attempt_number=2),
        ],
        recording_duration_seconds=120.0,
    )

    assert len(result) == 2
    assert result[0].end_seconds == 15.0
    assert result[1].start_seconds == 15.0
    assert result[1].end_seconds == 20.0


def test_boss_takes_priority_and_splits_overlapping_trash() -> None:
    result = merge_timeline_windows(
        [_trash(0.0, 30.0), _boss(10.0, 20.0)],
        recording_duration_seconds=30.0,
    )

    assert [(window.type, window.start_seconds, window.end_seconds) for window in result] == [
        (PullType.TRASH, 0.0, 10.0),
        (PullType.BOSS, 10.0, 20.0),
        (PullType.TRASH, 20.0, 30.0),
    ]


def test_short_gap_merging_never_crosses_a_boss_attempt() -> None:
    result = merge_timeline_windows(
        [_trash(0.0, 10.0), _boss(10.0, 11.0), _trash(11.0, 20.0)],
        recording_duration_seconds=30.0,
        trash_merge_gap_seconds=100.0,
    )

    assert [window.type for window in result] == [
        PullType.TRASH,
        PullType.BOSS,
        PullType.TRASH,
    ]


def test_excluded_windows_are_not_added_to_timeline() -> None:
    result = merge_timeline_windows(
        [_trash(0.0, 10.0, include=False), _trash(20.0, 30.0)],
        recording_duration_seconds=40.0,
    )

    assert [(window.start_seconds, window.end_seconds) for window in result] == [(20.0, 30.0)]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("recording_duration_seconds", 0.0),
        ("recording_duration_seconds", math.inf),
        ("lead_in_seconds", -1.0),
        ("lead_out_seconds", -1.0),
        ("trash_merge_gap_seconds", -1.0),
    ],
)
def test_rejects_invalid_merge_options(name: str, value: float) -> None:
    options = {
        "recording_duration_seconds": 30.0,
        "lead_in_seconds": 0.0,
        "lead_out_seconds": 0.0,
        "trash_merge_gap_seconds": 0.0,
    }
    options[name] = value

    with pytest.raises(ValueError):
        merge_timeline_windows([_trash(1.0, 2.0)], **options)
