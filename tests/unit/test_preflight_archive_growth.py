from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raid_editor import preflight as preflight_module
from raid_editor.archive import ArchiveError, create_verified_archive
from raid_editor.config.models import ProjectConfig
from raid_editor.preflight import run_preflight
from raid_editor.youtube import growth


def _config(
    tmp_path: Path,
    *,
    archive_enabled: bool = False,
    playlist_id: str | None = "PL-weekly",
) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "project": {
                "name": "Pizza Warriors ICC",
                "raid": "Icecrown Citadel",
                "raid_date": "2026-08-14",
            },
            "input": {
                "recording": str(tmp_path / "raid.mp4"),
                "combat_log": str(tmp_path / "WoWCombatLog.txt"),
            },
            "audio": {
                "game_track": 2,
                "discord_track": 3,
                "microphone_track": 4,
            },
            "music": {"library": str(tmp_path / "music.json")},
            "preflight": {
                "obs_profile_dir": "Profile",
                "obs_scene_collection_file": "Collection.json",
                "minimum_free_space_gib": 1,
            },
            "youtube": {
                "enabled": True,
                "client_secrets": str(tmp_path / "client.json"),
                "token": str(tmp_path / "token.json"),
                "playlist_id": playlist_id,
                "playlist_title": "Pizza Warriors Weekly ICC Clears",
            },
            "archive": {
                "enabled": archive_enabled,
                "destination": str(tmp_path / "archive"),
                "include_project_artifacts": False,
                "require_public_1440p_verified": False,
            },
        }
    )


def test_preflight_accepts_verified_tracks_including_top_level_mic_aux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    obs = tmp_path / "obs-studio"
    profile = obs / "basic" / "profiles" / "Profile"
    scenes = obs / "basic" / "scenes"
    recordings = tmp_path / "recordings"
    profile.mkdir(parents=True)
    scenes.mkdir(parents=True)
    recordings.mkdir()
    config.input.combat_log.write_text("fresh event\n", encoding="utf-8")
    smoke = tmp_path / "smoke.mp4"
    smoke.write_bytes(b"fresh-smoke")
    obs.joinpath("user.ini").write_text(
        "[Basic]\nProfileDir=Profile\nSceneCollectionFile=Collection.json\n",
        encoding="utf-8",
    )
    profile.joinpath("basic.ini").write_text(
        "[Video]\nOutputCX=2560\nOutputCY=1440\nFPSCommon=60\n"
        "[AdvOut]\n"
        f"RecFilePath={recordings}\n"
        "RecFormat2=hybrid_mp4\nRecTracks=15\n"
        "Track1Name=Full Mix\nTrack2Name=WoW Game\n"
        "Track3Name=Discord\nTrack4Name=Microphone\n",
        encoding="utf-8",
    )
    collection = {
        "current_program_scene": "WoW Raid",
        "sources": [
            {"name": "WoW Audio", "mixers": 3},
            {"name": "Discord Audio", "mixers": 5},
            {
                "name": "WoW Raid",
                "settings": {
                    "items": [
                        {"name": "WoW"},
                        {"name": "WebCam"},
                        {"name": "WebCam Border"},
                    ]
                },
            },
        ],
        "AuxAudioDevice1": {"name": "Mic/Aux", "mixers": 9},
    }
    scenes.joinpath("Collection.json").write_text(
        json.dumps(collection),
        encoding="utf-8",
    )
    obs.joinpath("service.json").write_text(
        '{"stream_key":"must-never-be-read"}',
        encoding="utf-8",
    )
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path.name == "service.json":
            raise AssertionError("preflight must not read OBS service credentials")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(
        preflight_module,
        "probe_media",
        lambda *_args, **_kwargs: SimpleNamespace(
            duration_seconds=10.0,
            video_streams=[SimpleNamespace(width=2560, height=1440, frame_rate=60.0)],
            audio_streams=[
                SimpleNamespace(title="Full Mix"),
                SimpleNamespace(title="WoW Game"),
                SimpleNamespace(title="Discord"),
                SimpleNamespace(title="Microphone"),
            ],
        ),
    )
    markdown = tmp_path / "preflight.md"
    report = run_preflight(
        config,
        destination_json=tmp_path / "preflight.json",
        destination_markdown=markdown,
        obs_root=obs,
        smoke_recording=smoke,
    )

    statuses = {check.name: check.status for check in report.checks}
    assert report.status == "passed"
    assert statuses["audio_route_WoW Audio"] == "passed"
    assert statuses["audio_route_Discord Audio"] == "passed"
    assert statuses["audio_route_Mic/Aux"] == "passed"
    assert statuses["smoke_recording_fresh"] == "passed"
    assert statuses["smoke_recording_duration"] == "passed"
    assert statuses["smoke_recording_geometry"] == "passed"
    assert statuses["smoke_recording_audio_tracks"] == "passed"
    assert "must-never-be-read" not in markdown.read_text(encoding="utf-8")


def test_archive_is_approval_gated_copy_only_and_hash_verified(tmp_path: Path) -> None:
    config = _config(tmp_path, archive_enabled=True)
    config.input.recording.write_bytes(b"raw-recording")
    config_path = tmp_path / "project.yaml"
    config_path.write_text("project: test\n", encoding="utf-8")
    project_root = tmp_path / "weekly-output"
    final = project_root / "final"
    final.mkdir(parents=True)
    final.joinpath("raid-final.mp4").write_bytes(b"approved-final")

    with pytest.raises(ArchiveError, match="explicit approval"):
        create_verified_archive(
            config,
            config_path=config_path,
            project_root=project_root,
            approved=False,
        )

    destination = create_verified_archive(
        config,
        config_path=config_path,
        project_root=project_root,
        approved=True,
    )
    manifest = json.loads(destination.joinpath("archive-manifest.json").read_text(encoding="utf-8"))
    copied_raw = destination / "raw" / "raid.mp4"

    assert config.input.recording.is_file()
    assert copied_raw.read_bytes() == b"raw-recording"
    assert manifest["verified"] is True
    assert manifest["copy_only"] is True
    assert manifest["source_deleted"] is False
    assert manifest["items"][0]["sha256"] == hashlib.sha256(b"raw-recording").hexdigest()


def test_playlist_addition_is_approval_gated_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    calls = {"insert": 0}

    class Request:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def execute(self) -> dict[str, Any]:
            return self.payload

    class PlaylistItems:
        def list(self, **_kwargs: object) -> Request:
            return Request({"items": [{"id": "existing-entry"}]})

        def insert(self, **_kwargs: object) -> Request:
            calls["insert"] += 1
            return Request({"id": "new-entry"})

    class YouTube:
        def playlistItems(self) -> PlaylistItems:
            return PlaylistItems()

    monkeypatch.setattr(growth, "youtube_credentials", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(growth, "build", lambda *_args, **_kwargs: YouTube())

    with pytest.raises(growth.YouTubeUploadError, match="explicit approval"):
        growth.add_video_to_weekly_playlist(
            config,
            video_id="video123",
            approved=False,
            report_destination=tmp_path / "playlist.md",
        )

    result = growth.add_video_to_weekly_playlist(
        config,
        video_id="video123",
        approved=True,
        report_destination=tmp_path / "playlist.md",
    )

    assert result.already_present is True
    assert result.added is False
    assert calls["insert"] == 0


def test_analytics_report_calculates_first_30_seconds_and_retention_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)

    class Request:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def execute(self) -> dict[str, Any]:
            return self.payload

    class Reports:
        def query(self, **kwargs: object) -> Request:
            if "dimensions" not in kwargs:
                return Request(
                    {
                        "columnHeaders": [
                            {"name": "views"},
                            {"name": "averageViewDuration"},
                            {"name": "averageViewPercentage"},
                        ],
                        "rows": [[450, 620, 41.2]],
                    }
                )
            return Request(
                {
                    "columnHeaders": [
                        {"name": "elapsedVideoTimeRatio"},
                        {"name": "audienceWatchRatio"},
                        {"name": "relativeRetentionPerformance"},
                    ],
                    "rows": [
                        [0.0, 1.0, 0.1],
                        [0.3, 0.76, 0.0],
                        [0.5, 0.58, -0.1],
                        [0.7, 0.63, 0.1],
                    ],
                }
            )

    class Analytics:
        def reports(self) -> Reports:
            return Reports()

    monkeypatch.setattr(growth, "youtube_credentials", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(growth, "build", lambda *_args, **_kwargs: Analytics())
    markdown = tmp_path / "analytics.md"
    payload = growth.fetch_video_analytics(
        config,
        video_id="video123",
        start_date=date(2026, 8, 14),
        end_date=date(2026, 8, 21),
        video_duration_seconds=100,
        label="7-day",
        json_destination=tmp_path / "analytics.json",
        markdown_destination=markdown,
        studio_impressions=12000,
        studio_ctr_percent=5.25,
    )

    assert payload["first_30_seconds_retention"] == pytest.approx(0.76)
    assert payload["retention_dips"][0]["change"] == pytest.approx(-0.24)
    assert payload["retention_spikes"][0]["change"] == pytest.approx(0.05)
    report = markdown.read_text(encoding="utf-8")
    assert "First 30-second retention: 76.0%" in report
    assert "Studio thumbnail CTR: 5.25%" in report
