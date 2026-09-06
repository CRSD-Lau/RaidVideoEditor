"""Final-media identity gates before YouTube packaging or transmission."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from raid_editor import workflow
from raid_editor.config.models import ProjectConfig
from raid_editor.youtube.upload import YouTubeUploadError, YouTubeUploadResult


@pytest.fixture
def upload_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "Final binding test"},
            "input": {"recording": tmp_path / "source.mp4"},
            "audio": {"game_track": 2, "microphone_track": 4},
            "music": {"library": tmp_path / "library.json"},
            "youtube": {
                "enabled": True,
                "privacy_status": "private",
                "client_secrets": tmp_path / "unused-client.json",
                "token": tmp_path / "unused-token.json",
            },
        }
    )
    monkeypatch.setattr(workflow, "project_output_dir", lambda _: tmp_path / "output")
    paths = workflow.ProjectPaths.for_config(config).create()
    final = paths.final_master / "output-final-1440p60.mp4"
    final.write_bytes(b"verified master contents")
    validation = {
        "status": "passed",
        "artifact": {
            "path": str(final.resolve()),
            "sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        },
    }
    report = paths.reports / "final-validation.json"
    report.write_text(json.dumps(validation), encoding="utf-8")
    package = object()
    builder = Mock(return_value=package)
    uploader = Mock(
        return_value=YouTubeUploadResult(
            video_id="synthetic-id",
            url="https://example.invalid/synthetic",
            privacy_status="private",
        )
    )
    monkeypatch.setattr(
        workflow, "build_timeline_project", Mock(return_value=(None, [], None, None, paths))
    )
    monkeypatch.setattr(
        workflow, "_final_output_settings", Mock(return_value=("2560x1440", 60, 2560, 1440, final))
    )
    monkeypatch.setattr(workflow, "write_youtube_package", builder)
    monkeypatch.setattr(workflow, "upload_youtube_video", uploader)
    return SimpleNamespace(
        config=config,
        paths=paths,
        final=final,
        validation=validation,
        report=report,
        package=package,
        builder=builder,
        uploader=uploader,
    )


@pytest.mark.parametrize("dry_run", [True, False])
def test_unchanged_bound_final_can_be_packaged_and_upload_keeps_approval_gate(
    upload_state: SimpleNamespace, dry_run: bool
) -> None:
    state = upload_state
    before = state.final.read_bytes()

    package, result, paths = workflow.upload_youtube_project(
        state.config, approved=not dry_run, dry_run=dry_run
    )

    assert package is state.package and paths == state.paths
    state.builder.assert_called_once()
    assert state.builder.call_args.args[2] == state.final
    assert state.final.read_bytes() == before
    if dry_run:
        assert result is None
        state.uploader.assert_not_called()
    else:
        assert result is state.uploader.return_value
        state.uploader.assert_called_once_with(
            state.config, state.package, approved=True, progress=None
        )


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize(
    "failure",
    [
        "changed_bytes",
        "wrong_path",
        "relative_path",
        "missing_artifact",
        "invalid_sha",
        "missing_file",
    ],
)
def test_unbound_or_replaced_final_blocks_before_package_creation(
    upload_state: SimpleNamespace, dry_run: bool, failure: str
) -> None:
    state = upload_state
    if failure == "changed_bytes":
        # Keep the pathname and length: a full content hash must detect substitution.
        state.final.write_bytes(b"replaced master contents")
    elif failure == "wrong_path":
        other = state.final.with_name("different-final.mp4")
        other.write_bytes(state.final.read_bytes())
        state.validation["artifact"]["path"] = str(other.resolve())
    elif failure == "relative_path":
        state.validation["artifact"]["path"] = state.final.name
    elif failure == "missing_artifact":
        del state.validation["artifact"]
    elif failure == "invalid_sha":
        state.validation["artifact"]["sha256"] = "not-a-valid-full-hash"
    else:
        state.final.unlink()
    state.report.write_text(json.dumps(state.validation), encoding="utf-8")

    with pytest.raises(YouTubeUploadError, match="validate-final CONFIG --video PATH"):
        workflow.upload_youtube_project(state.config, approved=True, dry_run=dry_run)

    state.builder.assert_not_called()
    state.uploader.assert_not_called()
    assert not (state.paths.root / "youtube").exists()


@pytest.mark.parametrize("public", [False, True])
def test_validation_binding_does_not_replace_upload_or_public_approval(
    upload_state: SimpleNamespace, public: bool
) -> None:
    state = upload_state
    if public:
        state.config.youtube.privacy_status = "public"
    with pytest.raises(
        YouTubeUploadError, match="public-approved" if public else "explicit approval"
    ):
        workflow.upload_youtube_project(state.config, approved=public)
    state.builder.assert_not_called()
    state.uploader.assert_not_called()
