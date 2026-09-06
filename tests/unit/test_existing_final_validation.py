"""Existing-final adoption must preserve bytes and bind explicit current-file review."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from raid_editor import workflow
from raid_editor.cli import app
from raid_editor.config.models import PresentationConfig, ProjectConfig
from raid_editor.growth.package import _validated_final
from raid_editor.ingestion.probe import AudioStream, MediaProbe, VideoStream
from raid_editor.rendering import validation as module
from raid_editor.util.paths import atomic_write_json, full_file_sha256
from raid_editor.youtube.upload import YouTubeUploadError, _chapter_lines

REAL_DECODE = module._decode


@pytest.fixture
def legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    root = tmp_path / "project"
    video = root / "final" / "original-final.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"existing approved movie bytes")
    source = tmp_path / "deleted-raw.mkv"
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "Existing final"},
            "input": {"recording": source},
            "audio": {"game_track": 1, "microphone_track": 3},
            "music": {"library": tmp_path / "missing-music.json"},
        }
    )
    timeline = {
        "timeline_name": "Saved approved edit",
        "source": str(source),
        "source_duration_seconds": 30,
        "source_fps": 30,
        "retained_audio_stream_indexes": [1],
        "excluded_microphone_stream_index": 3,
        "clips": [
            {
                "source_in": 2,
                "source_out": 12,
                "timeline_in": 0,
                "label": "Saved kill",
                "type": "boss_kill",
                "result": "kill",
                "pull_ids": ["kill-1"],
            }
        ],
    }
    atomic_write_json(root / "timeline" / "timeline.json", timeline)
    source_probe = MediaProbe(
        source={"path": str(source)},
        format_name="matroska",
        duration_seconds=30,
        size_bytes=1,
        video_streams=[VideoStream(index=0, codec="h264", width=640, height=360, frame_rate=30)],
        audio_streams=[AudioStream(index=i, audio_ordinal=i - 1, codec="aac") for i in (1, 2, 3)],
    )
    atomic_write_json(root / "analysis" / "media-probe.json", source_probe.model_dump(mode="json"))
    atomic_write_json(
        root / "analysis" / "pull-candidates.json",
        [
            {
                "id": "kill-1",
                "start_seconds": 2,
                "end_seconds": 12,
                "type": "boss_kill",
                "result": "kill",
            }
        ],
    )
    presentation = PresentationConfig(intro_seconds=2, outro_seconds=3)
    render = {
        "approved": True,
        "signature": "a" * 64,
        "resolution": "640x360",
        "fps": 30,
        "codec": "h264",
        "timeline_duration_seconds": 10,
        "output_duration_seconds": 15,
        "presentation": presentation.model_dump(mode="json"),
        "music_track_id": None,
    }
    sidecar = video.with_suffix(".manifest.json")
    atomic_write_json(sidecar, render)
    report = root / "reports" / "final-validation.json"
    atomic_write_json(report, {"status": "passed", "checks": [{"name": "old", "passed": True}]})
    report.with_suffix(".md").write_bytes(b"original report\r\n")
    final_probe = source_probe.model_copy(
        update={
            "duration_seconds": 15,
            "audio_streams": [
                AudioStream(index=1, audio_ordinal=0, codec="aac", channels=2, sample_rate=48000)
            ],
        }
    )
    probe = Mock(return_value=final_probe)
    decode = Mock()
    monkeypatch.setattr(module, "probe_media", probe)
    monkeypatch.setattr(module, "_decode", decode)
    monkeypatch.setattr(workflow, "project_output_dir", lambda _: root)
    monkeypatch.setattr(
        workflow, "build_timeline_project", Mock(side_effect=AssertionError("raw builder called"))
    )
    return SimpleNamespace(
        root=root,
        video=video,
        source=source,
        config=config,
        render=render,
        sidecar=sidecar,
        report=report,
        probe=probe,
        decode=decode,
        timeline=timeline,
        sha=full_file_sha256(video),
    )


def inspect(state):
    return module.validate_existing_final(state.config, state.root, state.video)


def approve(state):
    return module.validate_existing_final(
        state.config, state.root, state.video, approved=True, expected_sha256=state.sha
    )


@pytest.mark.parametrize("source_exists", [False, True])
def test_legacy_adoption_preserves_media_and_old_reports_and_is_idempotent(legacy, source_exists):
    if source_exists:
        legacy.source.write_bytes(b"source not read")
    originals = {
        p: p.read_bytes() for p in (legacy.sidecar, legacy.report, legacy.report.with_suffix(".md"))
    }
    stat = legacy.video.stat()
    result = inspect(legacy)
    assert result["approved"] is False
    assert result["source_available"] is source_exists
    assert result["source_preservation_reverified"] is False
    assert all(p.read_bytes() == data for p, data in originals.items())
    accepted = approve(legacy)
    assert accepted["validation_origin"] == module.ORIGIN
    assert full_file_sha256(legacy.video) == legacy.sha
    assert legacy.video.stat().st_mtime_ns == stat.st_mtime_ns
    backup = Path(accepted["existing_final_validation"]["review"]["backup"])
    assert all((backup / p.name).read_bytes() == data for p, data in originals.items())
    saved_render = json.loads(legacy.sidecar.read_text())
    assert saved_render["signature"] == legacy.render["signature"]
    assert saved_render["artifact"] == accepted["artifact"]
    assert _validated_final(legacy.root) == (legacy.video, legacy.sha)
    after = {p: p.read_bytes() for p in originals}
    assert approve(legacy) == accepted
    assert all(p.read_bytes() == data for p, data in after.items())
    assert len(list(backup.parent.iterdir())) == 1


@pytest.mark.parametrize("bad", [None, "", "bad-hash", "b" * 64])
def test_approval_requires_exact_inspected_hash(legacy, bad):
    inspect(legacy)
    before = legacy.report.read_bytes()
    with pytest.raises(module.FinalValidationError):
        module.validate_existing_final(
            legacy.config, legacy.root, legacy.video, approved=True, expected_sha256=bad
        )
    assert legacy.report.read_bytes() == before


def test_approval_requires_a_prior_inspection(legacy):
    with pytest.raises(module.FinalValidationError, match="saved evidence"):
        approve(legacy)


@pytest.mark.parametrize("artifact", [None, {}, {"path": "relative.mp4", "sha256": "a" * 64}])
@pytest.mark.parametrize("target", ["sidecar", "report"])
def test_present_malformed_binding_is_not_treated_as_legacy(legacy, artifact, target):
    path = getattr(legacy, target)
    value = json.loads(path.read_text())
    value["artifact"] = artifact
    atomic_write_json(path, value)
    before = path.read_bytes()
    with pytest.raises(module.FinalValidationError, match="binding conflicts"):
        inspect(legacy)
    assert path.read_bytes() == before


@pytest.mark.parametrize("target", ["sidecar", "report"])
def test_changed_modern_bytes_cannot_be_reapproved(legacy, target):
    path = getattr(legacy, target)
    value = json.loads(path.read_text())
    value["artifact"] = {"path": str(legacy.video), "sha256": legacy.sha}
    atomic_write_json(path, value)
    legacy.video.write_bytes(b"replacement movie")
    with pytest.raises(module.FinalValidationError, match="binding conflicts"):
        inspect(legacy)


@pytest.mark.parametrize(
    "failure",
    [
        "no_sidecar",
        "unapproved",
        "string_approval",
        "invalid_signature",
        "missing_timeline",
        "source_mismatch",
        "invalid_window",
        "source_duration",
        "audio_mapping",
        "render_duration",
        "failed_report",
        "invalid_report",
        "missing_probe",
        "config_mismatch",
    ],
)
def test_inconsistent_saved_evidence_is_rejected(legacy, failure):
    if failure == "no_sidecar":
        legacy.sidecar.unlink()
    elif failure in {"unapproved", "string_approval", "invalid_signature", "render_duration"}:
        value = legacy.render.copy()
        if failure == "unapproved":
            value["approved"] = False
        elif failure == "string_approval":
            value["approved"] = "true"
        elif failure == "invalid_signature":
            value["signature"] = "legacy"
        else:
            value["output_duration_seconds"] = 99
        atomic_write_json(legacy.sidecar, value)
    elif failure == "missing_timeline":
        (legacy.root / "timeline/timeline.json").unlink()
    elif failure == "missing_probe":
        (legacy.root / "analysis/media-probe.json").unlink()
    elif failure in {"failed_report", "invalid_report"}:
        atomic_write_json(legacy.report, {"status": "failed"} if failure == "failed_report" else [])
    elif failure == "config_mismatch":
        legacy.config.input.recording = legacy.source.with_name("other.mkv")
    else:
        value = legacy.timeline.copy()
        if failure == "source_mismatch":
            value["source"] = str(legacy.source.with_name("other.mkv"))
        elif failure == "source_duration":
            value["source_duration_seconds"] = 90
        elif failure == "audio_mapping":
            value["retained_audio_stream_indexes"] = [3]
        else:
            value["clips"] = [{**value["clips"][0], "source_out": 1}]
        atomic_write_json(legacy.root / "timeline/timeline.json", value)
    before = legacy.report.read_bytes()
    with pytest.raises(module.FinalValidationError):
        inspect(legacy)
    assert legacy.report.read_bytes() == before


@pytest.mark.parametrize(
    "failure",
    ["geometry", "fps", "codec", "duration", "audio_count", "audio_codec", "audio_channels"],
)
def test_observed_final_must_match_saved_render(legacy, failure):
    probe = legacy.probe.return_value.model_copy(deep=True)
    if failure == "geometry":
        probe.video_streams[0].width = 1280
    elif failure == "fps":
        probe.video_streams[0].frame_rate = 25
    elif failure == "codec":
        probe.video_streams[0].codec = "hevc"
    elif failure == "duration":
        probe.duration_seconds = 5
    elif failure == "audio_count":
        probe.audio_streams.append(probe.audio_streams[0])
    elif failure == "audio_codec":
        probe.audio_streams[0].codec = "opus"
    else:
        probe.audio_streams[0].channels = 1
    legacy.probe.return_value = probe
    with pytest.raises(module.FinalValidationError, match="shape differs"):
        inspect(legacy)
    legacy.decode.assert_not_called()


def test_decode_failure_never_mutates_canonical_evidence(legacy):
    before = legacy.report.read_bytes(), legacy.sidecar.read_bytes()
    legacy.decode.side_effect = module.FinalValidationError("decode failed")
    with pytest.raises(module.FinalValidationError, match="decode failed"):
        inspect(legacy)
    assert (legacy.report.read_bytes(), legacy.sidecar.read_bytes()) == before


@pytest.mark.parametrize("target", ["video", "timeline", "sidecar", "report"])
def test_changes_during_inspection_block_acceptance(legacy, target):
    inspect(legacy)
    path = (
        legacy.root / "timeline/timeline.json" if target == "timeline" else getattr(legacy, target)
    )
    original = path.read_bytes()
    legacy.decode.side_effect = lambda _: path.write_bytes(original + b" ")
    with pytest.raises(module.FinalValidationError, match="changed during inspection"):
        approve(legacy)
    assert not (legacy.root / "reports/final-validation-history").exists()


@pytest.mark.parametrize("target", ["pulls", "report", "sidecar"])
def test_changed_evidence_between_inspection_and_approval_requires_new_inspection(legacy, target):
    inspect(legacy)
    path = (
        (legacy.root / "analysis/pull-candidates.json")
        if target == "pulls"
        else getattr(legacy, target)
    )
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(module.FinalValidationError, match="run a new inspection"):
        approve(legacy)


def test_migration_write_failure_restores_exact_receipts_and_retry_works(legacy, monkeypatch):
    inspect(legacy)
    paths = (legacy.sidecar, legacy.report, legacy.report.with_suffix(".md"))
    before = {p: p.read_bytes() for p in paths}
    original_writer = module.atomic_write_json

    def fail_report(path, value):
        if path == legacy.report:
            raise OSError("simulated interrupted report commit")
        original_writer(path, value)

    monkeypatch.setattr(module, "atomic_write_json", fail_report)
    with pytest.raises(OSError, match="interrupted"):
        approve(legacy)
    assert all(p.read_bytes() == b for p, b in before.items())
    monkeypatch.setattr(module, "atomic_write_json", original_writer)
    assert approve(legacy)["status"] == "passed"


def test_outside_project_master_is_not_adopted(legacy, tmp_path):
    outside = tmp_path / "another-final.mp4"
    outside.write_bytes(legacy.video.read_bytes())
    with pytest.raises(module.FinalValidationError, match="project's final directory"):
        module.validate_existing_final(legacy.config, legacy.root, outside)


def test_source_free_upload_uses_recorded_presentation_and_keeps_upload_gates(legacy, monkeypatch):
    inspect(legacy)
    approve(legacy)
    assert not legacy.source.exists()
    legacy.config.preview.presentation = PresentationConfig(intro_seconds=12, outro_seconds=8)
    builder = Mock(return_value=object())
    uploader = Mock()
    monkeypatch.setattr(workflow, "write_youtube_package", builder)
    monkeypatch.setattr(workflow, "upload_youtube_video", uploader)
    _, result, _ = workflow.upload_youtube_project(legacy.config, dry_run=True)
    assert result is None
    used_config, timeline, video, _ = builder.call_args.args
    assert video == legacy.video
    assert used_config.preview.presentation.intro_seconds == 2
    assert used_config.preview.presentation.outro_seconds == 3
    chapters = _chapter_lines(used_config, timeline, builder.call_args.kwargs["pulls"])
    assert any(line.startswith("00:02 ") for line in chapters)
    assert any(line.startswith("00:12 ") for line in chapters)
    uploader.assert_not_called()
    with pytest.raises(YouTubeUploadError, match="explicit approval"):
        workflow.upload_youtube_project(legacy.config)
    legacy.config.youtube.privacy_status = "public"
    with pytest.raises(YouTubeUploadError, match="public-approved"):
        workflow.upload_youtube_project(legacy.config, approved=True)


@pytest.mark.parametrize("target", ["video", "sidecar", "timeline"])
def test_accepted_context_changes_block_growth_and_upload(legacy, monkeypatch, target):
    inspect(legacy)
    approve(legacy)
    path = (
        legacy.root / "timeline/timeline.json" if target == "timeline" else getattr(legacy, target)
    )
    path.write_bytes(path.read_bytes() + b" ")
    assert _validated_final(legacy.root) is None
    builder = Mock()
    monkeypatch.setattr(workflow, "write_youtube_package", builder)
    with pytest.raises(module.FinalValidationError):
        workflow.upload_youtube_project(legacy.config, dry_run=True)
    builder.assert_not_called()


def test_missing_pulls_allows_inspection_but_blocks_approval_for_downstream_claims(legacy):
    (legacy.root / "analysis/pull-candidates.json").unlink()
    inspect(legacy)
    with pytest.raises(module.FinalValidationError, match="Restore saved"):
        approve(legacy)
    assert "artifact" not in json.loads(legacy.report.read_text())


def test_cli_prints_exact_inspection_approval_command(legacy, monkeypatch):
    import raid_editor.cli as cli

    monkeypatch.setattr(cli, "load_project_config", lambda _: legacy.config)
    result = CliRunner().invoke(
        app, ["validate-final", "project.yaml", "--video", str(legacy.video)]
    )
    assert result.exit_code == 0, result.output
    assert legacy.sha in result.output
    assert "--approved --expected-sha256" in result.output
    assert "Inspection passed" in result.output
    assert "artifact" not in json.loads(legacy.report.read_text())


def test_evidence_replacement_while_loading_accepted_context_is_rejected(legacy, monkeypatch):
    inspect(legacy)
    approved = approve(legacy)
    original = module._load_context

    def replace_evidence(*args):
        context = original(*args)
        path = legacy.root / "timeline/timeline.json"
        path.write_bytes(path.read_bytes() + b" ")
        return context

    monkeypatch.setattr(module, "_load_context", replace_evidence)
    with pytest.raises(module.FinalValidationError, match="changed during verification"):
        module.accepted_final_context(legacy.root, approved)


def test_media_changed_during_youtube_packaging_is_not_uploaded(legacy, monkeypatch):
    inspect(legacy)
    approve(legacy)

    def mutate(*args, **kwargs):
        legacy.video.write_bytes(b"changed during thumbnail preparation")
        return object()

    uploader = Mock()
    monkeypatch.setattr(workflow, "write_youtube_package", mutate)
    monkeypatch.setattr(workflow, "upload_youtube_video", uploader)
    with pytest.raises(YouTubeUploadError, match="changed while"):
        workflow.upload_youtube_project(legacy.config, approved=True)
    uploader.assert_not_called()


def test_interrupted_migration_can_be_reinspected_without_losing_original_backup(
    legacy, monkeypatch
):
    inspect(legacy)
    original_sidecar = legacy.sidecar.read_bytes()
    original_writer = module.atomic_write_json

    def interrupt(path, value):
        if path == legacy.report:
            raise KeyboardInterrupt("simulated process interruption")
        original_writer(path, value)

    monkeypatch.setattr(module, "atomic_write_json", interrupt)
    with pytest.raises(KeyboardInterrupt):
        approve(legacy)
    assert _validated_final(legacy.root) is None
    backup_root = legacy.root / "reports/final-validation-history"
    first_backup = next(backup_root.iterdir())
    assert (first_backup / legacy.sidecar.name).read_bytes() == original_sidecar
    monkeypatch.setattr(module, "atomic_write_json", original_writer)
    inspect(legacy)
    assert approve(legacy)["status"] == "passed"
    assert (first_backup / legacy.sidecar.name).read_bytes() == original_sidecar
    assert _validated_final(legacy.root) == (legacy.video, legacy.sha)


def test_failed_write_does_not_rollback_an_unrelated_concurrent_edit(legacy, monkeypatch):
    inspect(legacy)
    original_writer = module.atomic_write_json

    def interrupt(path, value):
        if path == legacy.report:
            legacy.sidecar.write_bytes(b"unrelated concurrent edit")
            raise OSError("simulated write failure")
        original_writer(path, value)

    monkeypatch.setattr(module, "atomic_write_json", interrupt)
    with pytest.raises(OSError):
        approve(legacy)
    assert legacy.sidecar.read_bytes() == b"unrelated concurrent edit"
    assert "artifact" not in json.loads(legacy.report.read_text())


def test_media_change_during_metadata_commit_blocks_the_canonical_receipt(legacy, monkeypatch):
    inspect(legacy)
    before = legacy.sidecar.read_bytes(), legacy.report.read_bytes()
    original_writer = module.atomic_write_text

    def replace(path, value):
        original_writer(path, value)
        if path == legacy.report.with_suffix(".md"):
            legacy.video.write_bytes(b"changed while metadata was being saved")

    monkeypatch.setattr(module, "atomic_write_text", replace)
    with pytest.raises(module.FinalValidationError, match="while approval was being saved"):
        approve(legacy)
    assert (legacy.sidecar.read_bytes(), legacy.report.read_bytes()) == before


@pytest.mark.parametrize("failure", [False, True])
def test_decode_checks_complete_video_and_audio_without_an_output_file(
    legacy, monkeypatch, failure
):
    runner = Mock()
    if failure:
        runner.side_effect = subprocess.CalledProcessError(1, ["ffmpeg"])
    monkeypatch.setattr(module.shutil, "which", lambda _: "C:/ffmpeg/bin/ffmpeg.exe")
    monkeypatch.setattr(module.subprocess, "run", runner)
    if failure:
        with pytest.raises(module.FinalValidationError, match="Full video/audio decode failed"):
            REAL_DECODE(legacy.video)
    else:
        REAL_DECODE(legacy.video)
    command = runner.call_args.args[0]
    assert command[-3:] == ["-f", "null", "-"]
    assert "-xerror" in command and "explode" in command
    assert "0:v:0" in command and "0:a:0" in command
    assert runner.call_args.kwargs["check"] is True
