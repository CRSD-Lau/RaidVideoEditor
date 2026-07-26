from __future__ import annotations

from datetime import UTC, datetime

import pytest

from raid_editor.detection.combat_log import (
    PullResult,
    PullType,
    parse_combat_log,
)


def test_extracts_quoted_encounter_metadata_result_and_explicit_offset() -> None:
    recording_started_at = datetime(2026, 7, 26, 20, 0, tzinfo=UTC)
    rows = [
        '7/26 20:00:05.250  ENCOUNTER_START,3016,"Cauldron of Carnage, Redux",16,20',
        '7/26 20:00:35.750  ENCOUNTER_END,3016,"Cauldron of Carnage, Redux",16,20,1',
    ]

    result = parse_combat_log(
        rows,
        recording_started_at=recording_started_at,
        recording_offset_seconds=2.5,
    )

    assert result.issues == ()
    assert result.events[0].fields[1] == "Cauldron of Carnage, Redux"
    assert len(result.pulls) == 1
    pull = result.pulls[0]
    assert pull.start_seconds == pytest.approx(7.75)
    assert pull.end_seconds == pytest.approx(38.25)
    assert pull.type is PullType.BOSS
    assert pull.result is PullResult.KILL
    assert pull.encounter is not None
    assert pull.encounter.id == 3016
    assert pull.encounter.name == "Cauldron of Carnage, Redux"
    assert pull.attempt_number == 1
    assert pull.evidence == ("ENCOUNTER_START", "ENCOUNTER_END")


def test_resolves_new_year_rollover_relative_to_recording_anchor() -> None:
    recording_started_at = datetime(2026, 12, 31, 23, 59, 50, tzinfo=UTC)
    rows = [
        '12/31 23:59:59.900  ENCOUNTER_START,42,"Midnight Boss",16,20',
        '1/1 00:00:05.100  ENCOUNTER_END,42,"Midnight Boss",16,20,0',
    ]

    result = parse_combat_log(rows, recording_started_at=recording_started_at)

    assert result.issues == ()
    assert result.events[0].occurred_at.year == 2026
    assert result.events[1].occurred_at.year == 2027
    assert result.pulls[0].start_seconds == pytest.approx(9.9)
    assert result.pulls[0].end_seconds == pytest.approx(15.1)
    assert result.pulls[0].result is PullResult.WIPE


def test_supports_explicit_years_and_subsecond_precision() -> None:
    recording_started_at = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
    rows = [
        "7/26/2026 10:00:01.123456789  PLAYER_REGEN_DISABLED",
        "7/26/2026 10:00:02.5  PLAYER_REGEN_ENABLED",
    ]

    result = parse_combat_log(rows, recording_started_at=recording_started_at)

    assert result.events[0].occurred_at.microsecond == 123456
    assert result.events[1].occurred_at.microsecond == 500000
    assert result.pulls[0].start_seconds == pytest.approx(1.123456)
    assert result.pulls[0].end_seconds == pytest.approx(2.5)


def test_resolves_yearless_leap_day_without_failing_on_adjacent_years() -> None:
    recording_started_at = datetime(2024, 2, 28, 23, 59, tzinfo=UTC)
    rows = [
        "2/29 00:00:01.000  PLAYER_REGEN_DISABLED",
        "2/29 00:00:03.000  PLAYER_REGEN_ENABLED",
    ]

    result = parse_combat_log(rows, recording_started_at=recording_started_at)

    assert result.issues == ()
    assert result.events[0].occurred_at.year == 2024
    assert result.pulls[0].start_seconds == 61.0
    assert result.pulls[0].end_seconds == 63.0


def test_reports_malformed_rows_and_continues_with_valid_boundaries() -> None:
    recording_started_at = datetime(2026, 7, 26, 20, 0, tzinfo=UTC)
    rows = [
        "COMBAT_LOG_VERSION,20,ADVANCED_LOG_ENABLED,1",
        "",
        "7/26 not-a-timestamp",
        '7/26 20:00:01.000  ENCOUNTER_START,abc,"Bad id",16,20',
        '7/26 20:00:02.000  ENCOUNTER_START,10,"unterminated,16,20',
        '13/40 20:00:03.000  ENCOUNTER_START,10,"Bad date",16,20',
        '7/26 20:00:04.000  ENCOUNTER_START,10,"Valid Boss",16,20',
        '7/26 20:00:09.000  ENCOUNTER_END,10,"Valid Boss",16,20,1',
    ]

    result = parse_combat_log(rows, recording_started_at=recording_started_at)

    assert len(result.issues) == 4
    assert {issue.line_number for issue in result.issues} == {3, 4, 5, 6}
    assert len(result.pulls) == 1
    assert result.pulls[0].encounter is not None
    assert result.pulls[0].encounter.name == "Valid Boss"


def test_parses_unrelated_event_rows_without_treating_them_as_boundaries() -> None:
    recording_started_at = datetime(2026, 7, 26, 20, 0, tzinfo=UTC)
    rows = [
        (
            '7/26 20:00:01.000  SPELL_CAST_START,"Player-1","Mage, Neil",'
            '0x511,12345,"Arcane Spell",0x40'
        ),
        "7/26 20:00:02.000  PLAYER_REGEN_DISABLED",
        "7/26 20:00:04.000  PLAYER_REGEN_ENABLED",
    ]

    result = parse_combat_log(rows, recording_started_at=recording_started_at)

    assert [event.event for event in result.events] == [
        "SPELL_CAST_START",
        "PLAYER_REGEN_DISABLED",
        "PLAYER_REGEN_ENABLED",
    ]
    assert result.events[0].fields[1] == "Mage, Neil"
    assert len(result.pulls) == 1
    assert result.pulls[0].type is PullType.TRASH


def test_accepts_multiline_text_and_a_utf8_bom() -> None:
    recording_started_at = datetime(2026, 7, 26, 20, 0, tzinfo=UTC)
    text = (
        "\ufeff7/26 20:00:01.000  PLAYER_REGEN_DISABLED\n7/26 20:00:03.000  PLAYER_REGEN_ENABLED\n"
    )

    result = parse_combat_log(text, recording_started_at=recording_started_at)

    assert result.issues == ()
    assert len(result.pulls) == 1
    assert (result.pulls[0].start_seconds, result.pulls[0].end_seconds) == (
        1.0,
        3.0,
    )


def test_regen_windows_overlapping_an_encounter_do_not_duplicate_the_boss() -> None:
    recording_started_at = datetime(2026, 7, 26, 20, 0, tzinfo=UTC)
    rows = [
        "7/26 20:00:01.000  PLAYER_REGEN_DISABLED",
        "7/26 20:00:05.000  PLAYER_REGEN_ENABLED",
        "7/26 20:00:09.000  PLAYER_REGEN_DISABLED",
        '7/26 20:00:10.000  ENCOUNTER_START,22,"Boss",16,20',
        '7/26 20:00:20.000  ENCOUNTER_END,22,"Boss",16,20,0',
        "7/26 20:00:21.000  PLAYER_REGEN_ENABLED",
        "7/26 20:00:30.000  PLAYER_REGEN_DISABLED",
        "7/26 20:00:35.000  PLAYER_REGEN_ENABLED",
    ]

    result = parse_combat_log(rows, recording_started_at=recording_started_at)

    assert [(pull.type, pull.start_seconds, pull.end_seconds) for pull in result.pulls] == [
        (PullType.TRASH, 1.0, 5.0),
        (PullType.BOSS, 10.0, 20.0),
        (PullType.TRASH, 30.0, 35.0),
    ]


def test_repeated_encounters_remain_separate_attempts_when_an_end_is_missing() -> None:
    recording_started_at = datetime(2026, 7, 26, 20, 0, tzinfo=UTC)
    rows = [
        '7/26 20:00:01.000  ENCOUNTER_START,22,"Boss",16,20',
        '7/26 20:00:10.000  ENCOUNTER_START,22,"Boss",16,20',
        '7/26 20:00:20.000  ENCOUNTER_END,22,"Boss",16,20,0',
    ]

    result = parse_combat_log(rows, recording_started_at=recording_started_at)

    assert [pull.attempt_number for pull in result.pulls] == [1, 2]
    assert [pull.result for pull in result.pulls] == [
        PullResult.UNKNOWN,
        PullResult.WIPE,
    ]
    assert [(pull.start_seconds, pull.end_seconds) for pull in result.pulls] == [
        (1.0, 10.0),
        (10.0, 20.0),
    ]


def test_clamps_pull_windows_to_recording_bounds() -> None:
    recording_started_at = datetime(2026, 7, 26, 20, 0, 5, tzinfo=UTC)
    rows = [
        '7/26 20:00:00.000  ENCOUNTER_START,22,"Boss",16,20',
        '7/26 20:00:20.000  ENCOUNTER_END,22,"Boss",16,20,1',
    ]

    result = parse_combat_log(
        rows,
        recording_started_at=recording_started_at,
        recording_duration_seconds=10.0,
    )

    assert len(result.pulls) == 1
    assert result.pulls[0].start_seconds == 0.0
    assert result.pulls[0].end_seconds == 10.0


@pytest.mark.parametrize(
    ("offset", "duration"),
    [
        (float("nan"), None),
        (float("inf"), None),
        (0.0, 0.0),
        (0.0, -1.0),
        (0.0, float("inf")),
    ],
)
def test_rejects_invalid_recording_mapping_options(offset: float, duration: float | None) -> None:
    with pytest.raises(ValueError):
        parse_combat_log(
            [],
            recording_started_at=datetime(2026, 7, 26, tzinfo=UTC),
            recording_offset_seconds=offset,
            recording_duration_seconds=duration,
        )
