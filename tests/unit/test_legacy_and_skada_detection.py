from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from raid_editor.config.models import DetectionConfig
from raid_editor.detection.pipeline import detect_from_combat_log
from raid_editor.detection.skada import parse_skada_storage


def _legacy_row(occurred_at: datetime, event: str = "SPELL_DAMAGE") -> str:
    return (
        f"{occurred_at.month}/{occurred_at.day} {occurred_at:%H:%M:%S}.000  "
        f'{event},0x1,"Raider",0x514,0x2,"Target",0x10,1,"Test",0x1,10,0,0,0,0,0\n'
    )


def test_skada_parser_reads_only_top_level_boss_metadata(tmp_path: Path) -> None:
    # Arrange
    source = tmp_path / "SkadaStorage.lua"
    source.write_text(
        """
SkadaStorageDB = {
  {
    ["starttime"] = 100,
    ["actors"] = {
      ["Fake nested mobname"] = {
        ["mobname"] = "Must Not Escape",
      },
    },
    ["success"] = true,
    ["mobname"] = "The Lich King",
    ["type"] = "raid",
    ["endtime"] = 200,
  }, -- [1]
  [0] = {
    ["starttime"] = 1,
    ["mobname"] = "Aggregate",
    ["endtime"] = 999,
  },
}
""",
        encoding="utf-8",
    )

    # Act
    segments = parse_skada_storage(source)

    # Assert
    assert len(segments) == 1
    assert segments[0].mob_name == "The Lich King"
    assert segments[0].success is True
    assert segments[0].duration_seconds == 100


def test_legacy_pipeline_overlays_skada_and_excludes_short_duplicate(
    tmp_path: Path,
) -> None:
    # Arrange
    recording_start = datetime(2026, 7, 24, 23, 0, tzinfo=UTC)
    recording = tmp_path / "raid.mov"
    recording.write_bytes(b"media identity placeholder")
    combat_log = tmp_path / "WoWCombatLog.txt"
    rows = [
        *(_legacy_row(recording_start + timedelta(seconds=second)) for second in range(100, 201)),
        *(_legacy_row(recording_start + timedelta(seconds=second)) for second in range(250, 281)),
    ]
    combat_log.write_text("".join(rows), encoding="utf-8")
    first_start = round(recording_start.timestamp()) + 100
    first_end = round(recording_start.timestamp()) + 200
    second_start = round(recording_start.timestamp()) + 250
    second_end = round(recording_start.timestamp()) + 280
    skada = tmp_path / "SkadaStorage.lua"
    skada.write_text(
        f"""
SkadaStorageDB = {{
  {{
    ["starttime"] = {first_start},
    ["success"] = true,
    ["mobname"] = "The Lich King",
    ["type"] = "raid",
    ["endtime"] = {first_end},
  }},
  {{
    ["starttime"] = {second_start},
    ["success"] = true,
    ["mobname"] = "The Lich King",
    ["type"] = "raid",
    ["endtime"] = {second_end},
  }},
}}
""",
        encoding="utf-8",
    )
    settings = DetectionConfig(
        minimum_pull_seconds=2,
        merge_gap_seconds=2,
        recording_started_at=recording_start,
    )

    # Act
    pulls = detect_from_combat_log(
        combat_log,
        recording,
        recording_duration_seconds=400,
        settings=settings,
        skada_export=skada,
    )

    # Assert
    assert [(pull.type, pull.result, pull.include) for pull in pulls] == [
        ("boss_kill", "kill", True),
        ("unknown", "unknown", False),
    ]
    assert any("possible_duplicate" in evidence for evidence in pulls[1].evidence)


def test_repeated_skada_encounters_without_success_are_wipes(tmp_path: Path) -> None:
    recording_start = datetime(2026, 7, 24, 23, 0, tzinfo=UTC)
    recording = tmp_path / "raid.mov"
    recording.write_bytes(b"media identity placeholder")
    combat_log = tmp_path / "WoWCombatLog.txt"
    combat_log.write_text(
        "".join(
            _legacy_row(recording_start + timedelta(seconds=second)) for second in range(100, 321)
        ),
        encoding="utf-8",
    )
    epoch = round(recording_start.timestamp())
    skada = tmp_path / "SkadaStorage.lua"
    skada.write_text(
        f"""
SkadaStorageDB = {{
  {{
    ["starttime"] = {epoch + 100},
    ["mobname"] = "Professor Putricide",
    ["type"] = "raid",
    ["endtime"] = {epoch + 180},
  }},
  {{
    ["starttime"] = {epoch + 220},
    ["success"] = true,
    ["mobname"] = "Professor Putricide",
    ["type"] = "raid",
    ["endtime"] = {epoch + 320},
  }},
}}
""",
        encoding="utf-8",
    )

    pulls = detect_from_combat_log(
        combat_log,
        recording,
        recording_duration_seconds=400,
        settings=DetectionConfig(
            minimum_pull_seconds=2,
            merge_gap_seconds=2,
            recording_started_at=recording_start,
        ),
        skada_export=skada,
    )

    assert [(pull.type, pull.result) for pull in pulls] == [
        ("boss_wipe", "wipe"),
        ("boss_kill", "kill"),
    ]
    assert pulls[0].title == "Professor Putricide — Attempt 1 (Wipe)"
