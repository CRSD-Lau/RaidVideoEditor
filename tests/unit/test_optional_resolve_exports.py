from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path

import pytest

import raid_editor.cli as cli
import raid_editor.workflow as workflow
from raid_editor.config.models import ProjectConfig
from raid_editor.ingestion.probe import AudioStream, MediaProbe, VideoStream
from raid_editor.models import PullCandidate
from raid_editor.reporting.summary import validate_artifacts


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unchanged original recording")
    stat = source.stat()
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "Storage Regression"},
            "input": {"recording": source},
            "audio": {"game_track": 2, "microphone_track": 4},
            "music": {"library": tmp_path / "music.json", "approved_track_ids": []},
        }
    )
    probe = MediaProbe(
        source={"path": str(source), "size_bytes": stat.st_size, "modified_ns": stat.st_mtime_ns},
        format_name="mp4",
        duration_seconds=30,
        size_bytes=stat.st_size,
        video_streams=[VideoStream(index=0, codec="h264", width=2560, height=1440, frame_rate=60)],
        audio_streams=[
            AudioStream(index=index, audio_ordinal=index - 1, codec="aac") for index in range(1, 5)
        ],
    )
    pulls = [
        PullCandidate(id="boss", start_seconds=5, end_seconds=20, type="boss_kill", result="kill")
    ]
    monkeypatch.setattr(workflow, "project_output_dir", lambda _: tmp_path / "output")
    monkeypatch.setattr(workflow, "probe_media", lambda *args, **kwargs: probe)
    monkeypatch.setattr(workflow, "_load_saved_pulls", lambda *args: pulls)
    monkeypatch.setattr(workflow, "selected_music", lambda _: [])

    def forbidden_remux(*args, **kwargs):
        raise AssertionError("FFmpeg workflows must not generate a Resolve sidecar")

    monkeypatch.setattr(workflow, "create_mic_free_remux", forbidden_remux)
    return config, probe, pulls


def test_ffmpeg_timeline_and_validation_do_not_require_or_create_sidecar(project):
    config, _, _ = project
    _, _, timeline, sidecar, paths = workflow.build_timeline_project(config, resolve_exports=False)
    assert sidecar is None
    assert timeline.source == config.input.recording
    assert timeline.retained_audio_stream_indexes == [2]
    assert not (paths.generated_assets / "source-microphone-free.mov").exists()
    assert not (paths.timeline / "timeline.fcpxml").exists()
    assert not (paths.resolve / "create-project.json").exists()
    preview = paths.preview / f"{paths.root.name}-review-720p.mp4"
    preview.write_bytes(b"existing review fixture")
    result, _ = workflow.validate_project_artifacts(config)
    assert result["status"] == "passed"
    checks = {row["name"]: row for row in result["checks"]}
    assert checks["microphone_stream_excluded"]["passed"]
    assert "structural" in checks["microphone_stream_excluded"]["detail"]
    assert "resolve_sidecar_retained_audio_count" not in checks


def test_explicit_resolve_export_preserves_safe_source_mapping(project, monkeypatch):
    config, _, _ = project
    calls = []

    def remux(source, retained, microphone, destination):
        calls.append((source, retained, microphone))
        destination.write_bytes(b"safe remux fixture")
        return destination

    monkeypatch.setattr(workflow, "create_mic_free_remux", remux)
    _, _, _, sidecar, paths = workflow.build_timeline_project(config)
    assert calls == [(config.input.recording, [2], 4)]
    assert sidecar is not None and sidecar.is_file()
    payload = json.loads((paths.resolve / "create-project.json").read_text(encoding="utf-8"))
    assert payload["media_path"] == str(sidecar.resolve())
    assert payload["safety"]["start_rendering"] is False
    assert payload["safety"]["upload"] is False


@pytest.mark.parametrize("retained", [[4], [99], []])
def test_direct_validation_rejects_microphone_unknown_or_empty_audio(project, retained):
    config, probe, pulls = project
    _, _, timeline, _, _ = workflow.build_timeline_project(config, resolve_exports=False)
    unsafe = timeline.model_copy(update={"retained_audio_stream_indexes": retained})
    result = validate_artifacts(
        probe=probe,
        pulls=pulls,
        timeline=unsafe,
        microphone_free_probe=None,
        expected_audio_stream_indexes=retained,
        preview_probe=probe,
        preview_exists=True,
    )
    assert result["status"] == "failed"
    checks = {row["name"]: row for row in result["checks"]}
    assert checks["microphone_stream_excluded"]["passed"] is False


def test_validation_rejects_timeline_mapping_that_differs_from_config(project):
    config, probe, pulls = project
    _, _, timeline, _, _ = workflow.build_timeline_project(config, resolve_exports=False)
    result = validate_artifacts(
        probe=probe,
        pulls=pulls,
        timeline=timeline,
        microphone_free_probe=None,
        expected_audio_stream_indexes=[3],
        preview_probe=probe,
        preview_exists=True,
    )
    checks = {row["name"]: row for row in result["checks"]}
    assert checks["retained_audio_mapping_valid"]["passed"] is False


def test_source_missing_configured_audio_is_rejected_before_any_export(project, monkeypatch):
    config, probe, _ = project
    missing = probe.model_copy(update={"audio_streams": [probe.audio_streams[-1]]})
    monkeypatch.setattr(workflow, "probe_media", lambda *args, **kwargs: missing)
    with pytest.raises(ValueError, match="game_track references stream 2"):
        workflow.build_timeline_project(config, resolve_exports=False)


@pytest.mark.parametrize("lane", ["preview", "final", "upload"])
def test_ffmpeg_dry_run_lanes_never_remux(project, monkeypatch, lane):
    config, _, _ = project
    paths = workflow.ProjectPaths.for_config(config).create()
    (paths.preview / f"{paths.root.name}-review-720p.mp4").write_bytes(b"review fixture")
    monkeypatch.setattr(workflow, "render_preview", lambda *args, **kwargs: None)
    monkeypatch.setattr(workflow, "render_final", lambda *args, **kwargs: None)
    if lane == "preview":
        workflow.render_preview_project(config, dry_run=True)
    elif lane == "final":
        workflow.render_final_project(config, dry_run=True)
    else:
        _, probe, _ = project
        _, _, _, _, final = workflow._final_output_settings(config, probe, paths)
        final.write_bytes(b"validated final fixture")
        (paths.reports / "final-validation.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "artifact": {
                        "path": str(final.resolve()),
                        "sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
                    },
                }
            )
        )
        monkeypatch.setattr(workflow, "write_youtube_package", lambda *args, **kwargs: object())
        workflow.upload_youtube_project(config, dry_run=True)
    assert not (paths.generated_assets / "source-microphone-free.mov").exists()


def test_fresh_final_validation_binds_rendered_bytes_and_absolute_path(project, monkeypatch):
    config, source_probe, _ = project
    _, _, timeline, _, paths = workflow.build_timeline_project(config, resolve_exports=False)
    (paths.preview / f"{paths.root.name}-review-720p.mp4").write_bytes(b"review fixture")
    _, _, _, _, final = workflow._final_output_settings(config, source_probe, paths)
    rendered_bytes = b"newly rendered final, distinct from all previous fixtures"

    def render(_timeline, destination, **kwargs):
        assert kwargs["approved"] is True
        destination.write_bytes(rendered_bytes)
        destination.with_suffix(".manifest.json").write_text(
            json.dumps(
                {
                    "approved": True,
                    "artifact": {
                        "path": str(destination.resolve()),
                        "sha256": hashlib.sha256(rendered_bytes).hexdigest(),
                    },
                }
            )
        )

    def probe(path, *_args, **_kwargs):
        if path != final:
            return source_probe
        return source_probe.model_copy(
            update={
                "duration_seconds": timeline.duration_seconds,
                "size_bytes": len(rendered_bytes),
                "audio_streams": [AudioStream(index=1, audio_ordinal=0, codec="aac")],
            }
        )

    monkeypatch.setattr(workflow, "render_final", render)
    monkeypatch.setattr(workflow, "probe_media", probe)
    destination, actual_paths, report = workflow.render_final_project(config, approved=True)

    expected_binding = {
        "path": str(final.resolve()),
        "sha256": hashlib.sha256(rendered_bytes).hexdigest(),
    }
    assert destination == final
    assert actual_paths == paths
    assert report is not None and report["status"] == "passed"
    assert report["artifact"] == expected_binding
    persisted = json.loads((paths.reports / "final-validation.json").read_text(encoding="utf-8"))
    assert persisted["artifact"] == expected_binding


def test_only_explicit_resolve_cli_commands_allow_full_size_remux():
    # Keep newly added indirect callers from silently restoring the large duplicate.
    callsites = []
    for module in (workflow, cli):
        tree = ast.parse(inspect.getsource(module))
        for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
            for node in ast.walk(function):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "build_timeline_project"
                ):
                    continue
                callsites.append(function.name)
                if function.name in {"build_timeline_command", "create_resolve_project"}:
                    continue
                option = next(
                    (kw.value for kw in node.keywords if kw.arg == "resolve_exports"), None
                )
                assert isinstance(option, ast.Constant) and option.value is False, function.name
    assert set(callsites) == {
        "build_timeline_command",
        "create_resolve_project",
        "youtube_analytics_command",
        "render_preview_project",
        "render_final_project",
        "upload_youtube_project",
        "validate_project_artifacts",
    }
