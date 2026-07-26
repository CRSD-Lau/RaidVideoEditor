from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

import raid_editor.config.loader as config_loader
from raid_editor.cli import app
from raid_editor.ingestion.probe import probe_media
from raid_editor.util.paths import assert_within, quick_file_fingerprint

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_CONFIG = PROJECT_ROOT / "samples" / "synthetic-project.yaml"
SYNTHETIC_RECORDING = PROJECT_ROOT / "samples" / "generated" / "synthetic-raid.mkv"


def test_assert_within_rejects_sibling_and_parent_paths(tmp_path: Path) -> None:
    # Arrange
    managed_root = tmp_path / "managed"
    nested = managed_root / "review" / "artifact.json"
    sibling = tmp_path / "outside" / "artifact.json"

    # Act
    accepted = assert_within(nested, managed_root)

    # Assert
    assert accepted == nested.resolve()
    with pytest.raises(ValueError, match="Refusing path outside managed root"):
        assert_within(sibling, managed_root)


def test_render_preview_dry_run_cli_builds_safe_artifacts_without_changing_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    missing_tools = [
        executable for executable in ("ffmpeg", "ffprobe") if shutil.which(executable) is None
    ]
    if missing_tools:
        pytest.skip("Synthetic CLI journey requires: " + ", ".join(missing_tools))
    if not SYNTHETIC_RECORDING.is_file():
        pytest.skip("Generated synthetic raid fixture is not available")

    before = quick_file_fingerprint(SYNTHETIC_RECORDING)
    monkeypatch.setattr(config_loader, "PROJECT_ROOT", tmp_path)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        app,
        ["render-preview", str(SYNTHETIC_CONFIG), "--dry-run"],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert "Preview command prepared for:" in result.output
    project_output = tmp_path / "output" / "synthetic-pizza-warriors-raid"
    timeline_path = project_output / "timeline" / "timeline.json"
    sidecar = project_output / "generated-assets" / "source-microphone-free.mov"
    preview = project_output / "preview" / "synthetic-pizza-warriors-raid-review-720p.mp4"
    filter_script = preview.with_suffix(".filters.txt")
    bridge_payload = project_output / "resolve" / "create-project.json"

    assert timeline_path.is_file()
    assert sidecar.is_file()
    assert filter_script.is_file()
    assert bridge_payload.is_file()
    assert not preview.exists()
    assert not preview.with_suffix(".manifest.json").exists()
    assert quick_file_fingerprint(SYNTHETIC_RECORDING) == before

    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    graph = filter_script.read_text(encoding="utf-8")
    safety = json.loads(bridge_payload.read_text(encoding="utf-8"))["safety"]
    assert timeline["retained_audio_stream_indexes"] == [2, 3]
    assert timeline["excluded_microphone_stream_index"] == 4
    assert "[0:2]" in graph
    assert "[0:3]" in graph
    assert "[0:4]" not in graph
    assert len(probe_media(sidecar).audio_streams) == 2
    assert safety == {
        "create_unique_project_only": True,
        "add_render_job": False,
        "start_rendering": False,
        "upload": False,
    }
