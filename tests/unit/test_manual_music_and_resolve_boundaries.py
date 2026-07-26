from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import raid_editor.resolve.bridge as resolve_bridge
from raid_editor.detection.manual import ManualPullError, load_manual_pulls
from raid_editor.models import TimelineClip, TimelineDocument
from raid_editor.music.library import (
    MusicLibrary,
    MusicLibraryError,
    MusicTrack,
    approved_tracks,
    load_music_library,
)
from raid_editor.resolve.bridge import (
    ResolveIntegrationError,
    build_bridge_payload,
    run_resolve_bridge,
)


def _manual_pull() -> dict[str, object]:
    return {
        "id": "boss-1",
        "start_seconds": 10.0,
        "end_seconds": 20.0,
        "type": "boss_wipe",
        "result": "wipe",
        "confidence": 0.95,
        "include": True,
        "title": "Synthetic Boss — Wipe",
    }


def _music_record(local_file: str, sha256: str, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": "approved-bed",
        "title": "Approved Bed",
        "artist": "Test Artist",
        "source": "Artist download",
        "source_page": "https://example.com/licence",
        "licence": "CC BY",
        "licence_version": "4.0",
        "attribution_required": True,
        "date_obtained": "2026-07-26",
        "local_file": local_file,
        "sha256": sha256,
        "youtube_use_permitted": True,
        "monetized_youtube_permitted": True,
        "derivative_synchronization_permitted": True,
        "required_description_text": "Approved Bed by Test Artist, CC BY 4.0.",
        "tags": ["instrumental"],
    }
    record.update(overrides)
    return record


def _music_track(path: Path, sha256: str, **overrides: object) -> MusicTrack:
    return MusicTrack.model_validate(_music_record(str(path), sha256, **overrides))


def _timeline(source: Path) -> TimelineDocument:
    return TimelineDocument(
        timeline_name="Bridge Safety Review",
        source=source,
        source_duration_seconds=60.0,
        source_fps=30.0,
        retained_audio_stream_indexes=[2, 3],
        excluded_microphone_stream_index=4,
        clips=[
            TimelineClip(
                source_in=1.0,
                source_out=2.5,
                timeline_in=0.0,
                label="Safe Clip",
                type="boss_wipe",
                result="wipe",
                pull_ids=["boss-1"],
            )
        ],
    )


def test_load_manual_pulls_accepts_wrapped_json_and_derives_duration(
    tmp_path: Path,
) -> None:
    # Arrange
    source = tmp_path / "manual-pulls.json"
    source.write_text(
        json.dumps({"pulls": [_manual_pull()]}),
        encoding="utf-8",
    )

    # Act
    pulls = load_manual_pulls(source)

    # Assert
    assert len(pulls) == 1
    assert pulls[0].id == "boss-1"
    assert pulls[0].duration_seconds == 10.0
    assert pulls[0].include is True


def test_load_manual_pulls_accepts_utf8_bom_csv(tmp_path: Path) -> None:
    # Arrange
    source = tmp_path / "manual-pulls.csv"
    source.write_text(
        "id,start_seconds,end_seconds,type,result,confidence,include,title\n"
        "trash-1,1.5,4.0,trash_pull,unknown,0.8,false,Optional Trash\n",
        encoding="utf-8-sig",
    )

    # Act
    pulls = load_manual_pulls(source)

    # Assert
    assert len(pulls) == 1
    assert pulls[0].id == "trash-1"
    assert pulls[0].duration_seconds == 2.5
    assert pulls[0].include is False


def test_load_manual_pulls_wraps_invalid_window_as_user_actionable_error(
    tmp_path: Path,
) -> None:
    # Arrange
    invalid = _manual_pull()
    invalid["end_seconds"] = invalid["start_seconds"]
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps([invalid]), encoding="utf-8")

    # Act / Assert
    with pytest.raises(ManualPullError, match="end_seconds must be greater"):
        load_manual_pulls(source)


def test_approved_music_loads_relative_file_and_verifies_licence_hash(
    tmp_path: Path,
) -> None:
    # Arrange
    music_dir = tmp_path / "music"
    audio_file = music_dir / "files" / "approved-bed.bin"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"deterministic licensed music")
    expected_hash = hashlib.sha256(audio_file.read_bytes()).hexdigest()
    library_path = music_dir / "library.json"
    library_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tracks": [_music_record("files/approved-bed.bin", expected_hash)],
            }
        ),
        encoding="utf-8",
    )

    # Act
    library = load_music_library(library_path)
    selected = approved_tracks(library, ["approved-bed"])

    # Assert
    assert len(selected) == 1
    assert selected[0].local_file == audio_file.resolve()
    assert selected[0].sha256 == expected_hash


@pytest.mark.parametrize(
    "permission",
    [
        "youtube_use_permitted",
        "monetized_youtube_permitted",
        "derivative_synchronization_permitted",
    ],
)
def test_approved_music_rejects_any_missing_usage_permission(
    permission: str,
    tmp_path: Path,
) -> None:
    # Arrange
    audio_file = tmp_path / "music.bin"
    audio_file.write_bytes(b"licensed")
    expected_hash = hashlib.sha256(audio_file.read_bytes()).hexdigest()
    track = _music_track(audio_file, expected_hash, **{permission: False})
    library = MusicLibrary(tracks=[track])

    # Act / Assert
    with pytest.raises(MusicLibraryError, match="lacks explicit"):
        approved_tracks(library, [track.id])


def test_approved_music_rejects_file_hash_mismatch(tmp_path: Path) -> None:
    # Arrange
    audio_file = tmp_path / "music.bin"
    audio_file.write_bytes(b"modified after approval")
    recorded_hash = hashlib.sha256(b"original approved bytes").hexdigest()
    track = _music_track(audio_file, recorded_hash)
    library = MusicLibrary(tracks=[track])

    # Act / Assert
    with pytest.raises(MusicLibraryError, match="hash does not match"):
        approved_tracks(library, [track.id])


def test_approved_music_requires_attribution_text_when_marked_required(
    tmp_path: Path,
) -> None:
    # Arrange
    audio_file = tmp_path / "music.bin"
    audio_file.write_bytes(b"licensed")
    expected_hash = hashlib.sha256(audio_file.read_bytes()).hexdigest()
    track = _music_track(
        audio_file,
        expected_hash,
        attribution_required=True,
        required_description_text="   ",
    )
    library = MusicLibrary(tracks=[track])

    # Act / Assert
    with pytest.raises(MusicLibraryError, match="requires attribution text"):
        approved_tracks(library, [track.id])


def test_approved_music_rejects_ids_absent_from_the_licence_registry() -> None:
    # Arrange
    library = MusicLibrary(tracks=[])

    # Act / Assert
    with pytest.raises(MusicLibraryError, match="absent from the library"):
        approved_tracks(library, ["unregistered-track"])


def test_resolve_payload_uses_inclusive_frames_and_disables_render_upload(
    tmp_path: Path,
) -> None:
    # Arrange
    timeline = _timeline(tmp_path / "source.mkv")
    sidecar = tmp_path / "source-microphone-free.mov"

    # Act
    payload = build_bridge_payload(
        timeline,
        project_name="Unique Test Project",
        media_path=sidecar,
    )

    # Assert
    assert payload["project_name"] == "Unique Test Project"
    assert payload["media_path"] == str(sidecar.resolve())
    assert payload["clips"] == [
        {
            "start_frame": 30,
            "end_frame": 74,
            "record_frame": 0,
            "label": "Safe Clip",
            "type": "boss_wipe",
            "pull_ids": ["boss-1"],
        }
    ]
    assert payload["safety"] == {
        "create_unique_project_only": True,
        "add_render_job": False,
        "start_rendering": False,
        "upload": False,
    }


def test_resolve_bridge_invokes_isolated_python_and_accepts_created_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    sdk = tmp_path / "Resolve SDK"
    sdk.mkdir()
    library = tmp_path / "fusionscript.dll"
    library.write_bytes(b"mock library")
    payload = tmp_path / "create-project.json"
    payload.write_text("{}", encoding="utf-8")
    observed: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"status": "created"}',
            stderr="",
        )

    monkeypatch.setattr(resolve_bridge, "RESOLVE_API_ROOT", sdk)
    monkeypatch.setattr(resolve_bridge, "RESOLVE_LIBRARY", library)
    monkeypatch.setattr(resolve_bridge.subprocess, "run", fake_run)

    # Act
    command = run_resolve_bridge(payload)

    # Assert
    assert command == observed["command"]
    assert command[:2] == ["py", "-3.13"]
    assert command[-1] == str(payload.resolve())
    assert observed["kwargs"] == {
        "check": True,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }


def test_resolve_bridge_surfaces_safe_helper_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    sdk = tmp_path / "sdk"
    sdk.mkdir()
    library = tmp_path / "fusionscript.dll"
    library.write_bytes(b"mock library")
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"status": "error", "error": "project name already exists"}',
            stderr="",
        )

    monkeypatch.setattr(resolve_bridge, "RESOLVE_API_ROOT", sdk)
    monkeypatch.setattr(resolve_bridge, "RESOLVE_LIBRARY", library)
    monkeypatch.setattr(resolve_bridge.subprocess, "run", fake_run)

    # Act / Assert
    with pytest.raises(ResolveIntegrationError, match="project name already exists"):
        run_resolve_bridge(payload)
