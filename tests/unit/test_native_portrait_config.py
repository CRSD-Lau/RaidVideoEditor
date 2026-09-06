from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from raid_editor.config.loader import load_project_config
from raid_editor.config.models import HighlightConfig, InputConfig


def test_legacy_config_defaults_to_landscape_without_companion() -> None:
    assert InputConfig(recording=Path("raid.mp4")).vertical_recording is None
    config = HighlightConfig()
    assert config.video_source == "landscape"
    assert config.vertical_offset_hint_seconds is None
    assert config.vertical_sync_audio_role == "game"


def test_relative_vertical_path_is_relative_to_config_directory(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "raid.yaml"
    path.write_text(
        "project:\n  name: Raid\ninput:\n  recording: ../recordings/raid.mp4\n"
        "  vertical_recording: ../recordings/Vertical/raid.mp4\n"
        "audio: {}\nmusic:\n  library: ../music/library.json\n"
        "highlights:\n  video_source: native_vertical\n"
        "  vertical_offset_hint_seconds: -2.5\n  vertical_sync_audio_role: discord\n",
        encoding="utf-8",
    )

    config = load_project_config(path)

    assert config.input.recording == (tmp_path / "recordings" / "raid.mp4").resolve()
    assert (
        config.input.vertical_recording
        == (tmp_path / "recordings" / "Vertical" / "raid.mp4").resolve()
    )
    assert config.highlights.video_source == "native_vertical"
    assert config.highlights.vertical_offset_hint_seconds == -2.5
    assert config.highlights.vertical_sync_audio_role == "discord"


@pytest.mark.parametrize("offset", [float("inf"), float("-inf"), float("nan")])
def test_vertical_offset_hint_must_be_finite(offset: float) -> None:
    with pytest.raises(ValidationError):
        HighlightConfig(vertical_offset_hint_seconds=offset)


@pytest.mark.parametrize(
    "field,value",
    [("video_source", "auto"), ("video_source", "portrait"), ("vertical_sync_audio_role", "mixed")],
)
def test_native_source_configuration_rejects_unsupported_choices(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        HighlightConfig.model_validate({field: value})
