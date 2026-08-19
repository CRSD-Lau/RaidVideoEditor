from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml

from raid_editor.classification.difficulty import ICC_BOSSES
from raid_editor.config.loader import load_project_config
from raid_editor.ingestion.probe import AudioStream, MediaProbe, VideoStream
from raid_editor.models import PullCandidate
from raid_editor.weekly import (
    create_weekly_project_config,
    find_latest_recording,
    verify_completed_recording,
)
from raid_editor.workflow import _resolved_presentation


def _probe(recording: Path) -> MediaProbe:
    return MediaProbe(
        source={"path": str(recording), "size_bytes": recording.stat().st_size},
        format_name="mov,mp4",
        duration_seconds=3600,
        size_bytes=recording.stat().st_size,
        video_streams=[
            VideoStream(
                index=0,
                codec="h264",
                width=2560,
                height=1440,
                frame_rate=60,
            )
        ],
        audio_streams=[
            AudioStream(index=1, audio_ordinal=0, codec="aac", title="Full Mix"),
            AudioStream(index=2, audio_ordinal=1, codec="aac", title="WoW Game"),
            AudioStream(index=3, audio_ordinal=2, codec="aac", title="Discord"),
            AudioStream(index=4, audio_ordinal=3, codec="aac", title="Microphone"),
        ],
    )


def _template(path: Path, recording: Path) -> None:
    payload = {
        "project": {
            "name": "Earlier raid",
            "game": "World of Warcraft",
            "raid": "Icecrown Citadel",
        },
        "input": {"recording": str(recording)},
        "audio": {
            "microphone_track": 4,
            "game_track": 2,
            "discord_track": 3,
            "mixed_track": 1,
            "keep_game_audio": True,
            "keep_discord_audio": False,
            "remove_microphone": True,
        },
        "music": {"library": str(path.parent / "music-library.json")},
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_find_latest_recording_skips_smoke_files(tmp_path: Path) -> None:
    earlier = tmp_path / "2026-08-21 21-00-00.mp4"
    smoke = tmp_path / "Friday smoke test.mp4"
    earlier.write_bytes(b"raid")
    smoke.write_bytes(b"smoke")
    now = time.time() - 300
    earlier.touch()
    smoke.touch()
    earlier_time = now - 30
    smoke_time = now
    os.utime(earlier, (earlier_time, earlier_time))
    os.utime(smoke, (smoke_time, smoke_time))

    result = find_latest_recording(
        tmp_path,
        minimum_age_minutes=2,
        stability_seconds=0,
    )

    assert result == earlier


def test_explicit_recording_must_be_old_enough_to_be_complete(tmp_path: Path) -> None:
    recording = tmp_path / "2026-08-21 22-05-30.mp4"
    recording.write_bytes(b"active")

    with pytest.raises(ValueError, match="may still be active"):
        verify_completed_recording(
            recording,
            minimum_age_minutes=2,
            stability_seconds=0,
        )


def test_create_weekly_config_locks_approved_visual_and_audio_defaults(tmp_path: Path) -> None:
    project_root = tmp_path / "RaidVideoEditor"
    config_dir = project_root / "config"
    assets = project_root / "assets"
    config_dir.mkdir(parents=True)
    assets.mkdir()
    (assets / "pizza-warriors-lausudo-camera-cover-v1.png").write_bytes(b"cover")
    (assets / "pizza-warriors-raid-presentation-v2-clean-1920x1080.png").write_bytes(
        b"presentation"
    )
    old_recording = tmp_path / "2026-08-14 22-13-11.mp4"
    old_recording.write_bytes(b"old")
    template = config_dir / "pizza-warriors-2026-08-14.local.yaml"
    _template(template, old_recording)
    recording = tmp_path / "2026-08-21 22-05-30.mp4"
    recording.write_bytes(b"new raid")

    setup = create_weekly_project_config(
        recording,
        template_path=template,
        config_directory=config_dir,
        project_root=project_root,
        probe=_probe(recording),
    )
    config = load_project_config(setup.config_path)

    assert setup.created is True
    assert setup.config_path.name == "pizza-warriors-2026-08-21.local.yaml"
    assert config.audio.retained_stream_indexes() == [2]
    assert config.audio.microphone_track == 4
    assert config.highlights.keep_microphone_audio is True
    assert config.editing.include_trash_pulls is False
    assert config.editing.include_boss_wipes is False
    assert config.preview.review_clip_mode == "full"
    assert config.preview.watermark is not None
    assert config.preview.watermark.width_fraction == 0.258
    assert config.preview.watermark.height_fraction == 0.258
    assert config.preview.presentation is not None
    assert config.preview.presentation.theme == "icecrown_v2"
    assert config.preview.presentation.intro_seconds == 5
    assert config.preview.presentation.outro_seconds == 5
    assert config.preview.presentation.outro_subtitle is None
    assert config.youtube.title is None
    assert config.youtube.privacy_status == "public"

    reviewed_kills = [
        PullCandidate(
            id=f"pull-{number:03d}",
            start_seconds=number * 100.0,
            end_seconds=number * 100.0 + 80.0,
            type="boss_kill",
            encounter=boss,
            result="kill",
            difficulty="25H" if number <= 7 else "25N",
            difficulty_confidence="high",
        )
        for number, boss in enumerate(ICC_BOSSES, start=1)
    ]
    resolved = _resolved_presentation(config, reviewed_kills)

    assert resolved is not None
    assert resolved.outro_subtitle == "ICC 25M 12/12 7HC / AUGUST 21, 2026"


def test_create_weekly_config_reuses_same_recording_without_overwrite(tmp_path: Path) -> None:
    project_root = tmp_path / "RaidVideoEditor"
    config_dir = project_root / "config"
    assets = project_root / "assets"
    config_dir.mkdir(parents=True)
    assets.mkdir()
    (assets / "pizza-warriors-lausudo-camera-cover-v1.png").write_bytes(b"cover")
    (assets / "pizza-warriors-raid-presentation-v2-clean-1920x1080.png").write_bytes(
        b"presentation"
    )
    old_recording = tmp_path / "2026-08-14 22-13-11.mp4"
    old_recording.write_bytes(b"old")
    template = config_dir / "pizza-warriors-2026-08-14.local.yaml"
    _template(template, old_recording)
    recording = tmp_path / "2026-08-21 22-05-30.mp4"
    recording.write_bytes(b"new raid")
    first = create_weekly_project_config(
        recording,
        template_path=template,
        config_directory=config_dir,
        project_root=project_root,
        probe=_probe(recording),
    )
    before = first.config_path.read_bytes()

    second = create_weekly_project_config(
        recording,
        template_path=template,
        config_directory=config_dir,
        project_root=project_root,
        probe=_probe(recording),
    )

    assert second.created is False
    assert second.config_path.read_bytes() == before
