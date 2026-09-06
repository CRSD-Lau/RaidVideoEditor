"""A real encoded legacy final remains reusable after its raw recording is removed."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from raid_editor import workflow
from raid_editor.config.loader import load_project_config
from raid_editor.growth.package import _validated_final
from raid_editor.rendering import validation
from raid_editor.util.paths import atomic_write_json, full_file_sha256

ROOT = Path(__file__).resolve().parents[2]


def test_recover_real_legacy_final_without_source_or_rerender(tmp_path, monkeypatch):
    fixture = ROOT / "samples/generated/synthetic-raid.mkv"
    if not fixture.is_file() or not all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe")):
        pytest.skip("Generate the synthetic fixture and provide FFmpeg to run real final recovery")
    config = load_project_config(ROOT / "samples/synthetic-project.yaml")
    raw = tmp_path / "raw.mkv"
    shutil.copyfile(fixture, raw)
    config = config.model_copy(
        update={
            "input": config.input.model_copy(update={"recording": raw}),
            "preview": config.preview.model_copy(update={"resolution": "640x360"}),
            "final": config.final.model_copy(update={"hardware_encoding": False}),
        }
    )
    monkeypatch.setattr(workflow, "project_output_dir", lambda _: tmp_path / "project")
    workflow.render_preview_project(config)
    final, paths, rendered_validation = workflow.render_final_project(config, approved=True)
    assert rendered_validation["status"] == "passed"
    sidecar = final.with_suffix(".manifest.json")
    legacy_render = json.loads(sidecar.read_text())
    del legacy_render["artifact"]
    atomic_write_json(sidecar, legacy_render)
    legacy_validation = dict(rendered_validation)
    del legacy_validation["artifact"]
    atomic_write_json(paths.reports / "final-validation.json", legacy_validation)
    before_hash, before_mtime = full_file_sha256(final), final.stat().st_mtime_ns
    raw.unlink()

    def forbidden(*args, **kwargs):
        raise AssertionError("Existing-final recovery must not render or rebuild from raw media")

    monkeypatch.setattr(workflow, "build_timeline_project", forbidden)
    monkeypatch.setattr(workflow, "render_final", forbidden)
    monkeypatch.setattr(workflow, "upload_youtube_video", forbidden)
    inspection = validation.validate_existing_final(config, paths.root, final)
    assert inspection["source_available"] is False
    accepted = validation.validate_existing_final(
        config, paths.root, final, approved=True, expected_sha256=inspection["artifact"]["sha256"]
    )
    assert accepted["status"] == "passed"
    assert _validated_final(paths.root) == (final, before_hash)
    package, uploaded, _ = workflow.upload_youtube_project(config, dry_run=True)
    assert package.video == final and uploaded is None
    assert package.metadata.is_file()
    assert full_file_sha256(final) == before_hash
    assert final.stat().st_mtime_ns == before_mtime
    assert not (paths.root / "youtube/upload-manifest.json").exists()
    assert json.loads(sidecar.read_text())["signature"] == legacy_render["signature"]
