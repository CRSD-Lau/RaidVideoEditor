from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from raid_editor.audio.tracks import infer_track_roles, validate_audio_mapping
from raid_editor.config.loader import load_project_config
from raid_editor.config.models import AudioConfig, PreviewConfig, ProjectConfig
from raid_editor.ingestion.probe import AudioStream, MediaProbe


def _project_payload() -> dict[str, object]:
    return {
        "project": {"name": "Deterministic Raid"},
        "input": {
            "recording": "media/raid.mkv",
            "combat_log": "logs/WoWCombatLog.txt",
            "details_export": "details/export.json",
            "manual_pulls": "review/manual-pulls.json",
        },
        "audio": {
            "microphone_track": 4,
            "game_track": 2,
            "discord_track": 3,
        },
        "music": {
            "library": "music/library.json",
            "approved_track_ids": [],
        },
    }


def _audio_stream(index: int, ordinal: int, title: str | None) -> AudioStream:
    return AudioStream(
        index=index,
        audio_ordinal=ordinal,
        codec="aac",
        channels=2,
        channel_layout="stereo",
        sample_rate=48_000,
        title=title,
    )


def _probe(*streams: AudioStream) -> MediaProbe:
    return MediaProbe(
        source={"path": "raid.mkv"},
        format_name="matroska",
        duration_seconds=30.0,
        size_bytes=1024,
        video_streams=[],
        audio_streams=list(streams),
    )


def test_project_config_rejects_misspelled_keys() -> None:
    # Arrange
    payload = _project_payload()
    payload["preveiw"] = {"resolution": "640x360"}

    # Act / Assert
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProjectConfig.model_validate(payload)


def test_audio_config_rejects_retaining_the_removed_microphone_stream() -> None:
    # Arrange
    values = {
        "microphone_track": 4,
        "game_track": 4,
        "keep_game_audio": True,
        "remove_microphone": True,
    }

    # Act / Assert
    with pytest.raises(
        ValidationError,
        match="microphone_track cannot also be a retained game/Discord track",
    ):
        AudioConfig.model_validate(values)


def test_preview_config_normalizes_valid_dimensions() -> None:
    # Arrange
    value = "1920X1080"

    # Act
    config = PreviewConfig(resolution=value)

    # Assert
    assert config.resolution == "1920x1080"


@pytest.mark.parametrize("value", ["1920", "0x1080", "-1x720", "wide x tall"])
def test_preview_config_rejects_malformed_dimensions(value: str) -> None:
    # Arrange / Act / Assert
    with pytest.raises(ValidationError, match="resolution must use WIDTHxHEIGHT"):
        PreviewConfig(resolution=value)


def test_load_project_config_resolves_paths_from_the_yaml_directory(
    tmp_path: Path,
) -> None:
    # Arrange
    config_dir = tmp_path / "portable-project"
    config_dir.mkdir()
    config_path = config_dir / "project.yaml"
    config_path.write_text(
        yaml.safe_dump(_project_payload(), sort_keys=False),
        encoding="utf-8",
    )

    # Act
    config = load_project_config(config_path)

    # Assert
    assert config.input.recording == (config_dir / "media/raid.mkv").resolve()
    assert config.input.combat_log == (config_dir / "logs/WoWCombatLog.txt").resolve()
    assert config.input.details_export == (config_dir / "details/export.json").resolve()
    assert config.input.manual_pulls == (config_dir / "review/manual-pulls.json").resolve()
    assert config.music.library == (config_dir / "music/library.json").resolve()


def test_infer_track_roles_uses_absolute_stream_indexes_from_titles() -> None:
    # Arrange
    streams = [
        _audio_stream(1, 0, "Full Stream Mix"),
        _audio_stream(2, 1, "World of Warcraft Game Audio"),
        _audio_stream(3, 2, "Discord Voice Chat"),
        _audio_stream(4, 3, "Rode Microphone"),
        _audio_stream(5, 4, None),
    ]

    # Act
    roles = infer_track_roles(streams)

    # Assert
    assert roles == {
        "microphone": 4,
        "game": 2,
        "discord": 3,
        "mixed": 1,
    }


def test_validate_audio_mapping_accepts_separate_retained_and_microphone_streams() -> None:
    # Arrange
    audio = AudioConfig(
        microphone_track=4,
        game_track=2,
        discord_track=3,
        mixed_track=1,
    )
    probe = _probe(
        _audio_stream(1, 0, "Full Stream Mix"),
        _audio_stream(2, 1, "Game"),
        _audio_stream(3, 2, "Discord"),
        _audio_stream(4, 3, "Microphone"),
    )

    # Act
    issues = validate_audio_mapping(audio, probe)

    # Assert
    assert issues == []
    assert audio.retained_stream_indexes() == [2, 3]
    assert audio.microphone_track not in audio.retained_stream_indexes()


def test_validate_audio_mapping_requires_manual_microphone_identification_for_multitrack() -> None:
    # Arrange
    audio = AudioConfig(
        microphone_track=None,
        game_track=2,
        discord_track=3,
        remove_microphone=True,
    )
    probe = _probe(
        _audio_stream(2, 0, "Game"),
        _audio_stream(3, 1, "Discord"),
        _audio_stream(4, 2, None),
    )

    # Act
    issues = validate_audio_mapping(audio, probe)

    # Assert
    assert any("microphone_track is not identified" in issue for issue in issues)


def test_validate_audio_mapping_reports_unknown_absolute_stream_index() -> None:
    # Arrange
    audio = AudioConfig(
        microphone_track=4,
        game_track=99,
        discord_track=3,
    )
    probe = _probe(
        _audio_stream(3, 0, "Discord"),
        _audio_stream(4, 1, "Microphone"),
    )

    # Act
    issues = validate_audio_mapping(audio, probe)

    # Assert
    assert issues == ["game_track references stream 99, but available audio streams are [3, 4]"]
