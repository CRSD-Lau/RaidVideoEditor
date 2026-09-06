"""Publication packages retain observed state and reject unverified replacement media."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from raid_editor.config.models import ProjectConfig
from raid_editor.ingestion.probe import AudioStream, MediaProbe, VideoStream
from raid_editor.social import package
from raid_editor.social.ledger import (
    SocialLedgerError,
    approve_social_review,
    record_publication_status,
)
from raid_editor.util.paths import atomic_write_json, quick_file_fingerprint


def _probe(path: Path) -> MediaProbe:
    return MediaProbe(
        source=quick_file_fingerprint(path),
        format_name="mp4",
        duration_seconds=30,
        size_bytes=path.stat().st_size,
        video_streams=[
            VideoStream(
                index=0,
                codec="h264",
                width=1080,
                height=1920,
                frame_rate=60,
            )
        ],
        audio_streams=[AudioStream(index=1, audio_ordinal=0, codec="aac")],
    )


@pytest.fixture
def owned_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    media = tmp_path / "owned.mp4"
    media.write_bytes(b"approved owned portrait")
    source = tmp_path / "owned-assets.json"
    atomic_write_json(
        source,
        {
            "campaign_id": "test-campaign",
            "output_root": str(tmp_path / "social"),
            "assets": [
                {
                    "content_id": "moment-1",
                    "title": "Reviewed raid reaction",
                    "path": str(media),
                    "quality_class": "canonical_master",
                    "approved": True,
                }
            ],
        },
    )
    monkeypatch.setattr(package, "probe_media", _probe)
    return source


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "approval",
    [False, "false", "true", 0, 1, None, "missing"],
    ids=["false", "string-false", "string-true", "zero", "one", "null", "missing"],
)
def test_owned_import_requires_literal_true_before_packaging(
    owned_source: Path,
    approval: object,
) -> None:
    payload = json.loads(owned_source.read_text(encoding="utf-8"))
    if approval == "missing":
        payload["assets"][0].pop("approved")
    else:
        payload["assets"][0]["approved"] = approval
    atomic_write_json(owned_source, payload)

    with pytest.raises(package.SocialPackageError, match="Every owned backfill asset"):
        package.prepare_social_source(owned_source)

    assert not Path(payload["output_root"]).exists()


@pytest.mark.parametrize("approval", [False, "false", 1])
def test_owned_import_checks_later_assets_before_packaging(
    owned_source: Path,
    approval: object,
) -> None:
    payload = json.loads(owned_source.read_text(encoding="utf-8"))
    payload["assets"].append(
        payload["assets"][0] | {"content_id": "moment-2", "approved": approval}
    )
    atomic_write_json(owned_source, payload)

    with pytest.raises(package.SocialPackageError, match="Every owned backfill asset"):
        package.prepare_social_source(owned_source)

    assert not Path(payload["output_root"]).exists()


@pytest.mark.parametrize("status", ["reviewed", "scheduled", "verified_public"])
def test_reprepare_retains_approval_remote_identity_and_locked_release(
    owned_source: Path,
    status: str,
) -> None:
    start = datetime(2026, 9, 10, 19, tzinfo=ZoneInfo("America/Halifax"))
    _, path = package.prepare_social_source(owned_source, start_at=start)
    approved = approve_social_review(path, approved=True)
    if status != "reviewed":
        approved = record_publication_status(
            path,
            platform="facebook",
            content_id="moment-1",
            status="scheduled",
            approved=True,
            remote_content_id="remote-1",
            target_account_id="account-1",
        )
    if status == "verified_public":
        approved = record_publication_status(
            path,
            platform="facebook",
            content_id="moment-1",
            status="verified_public",
            approved=True,
            remote_content_id="remote-1",
            public_url="https://www.facebook.com/reel/1",
            verification_checks={"public_playback": True, "audio": True},
        )
    before = _files(path.parent)
    retained, destination = package.prepare_social_source(
        owned_source,
        start_at=start + timedelta(days=20),
    )
    assert retained == approved
    assert destination == path
    assert retained.review_approved
    assert next(post for post in retained.posts if post.platform == "facebook").status == status
    assert all(post.scheduled_for == start for post in retained.posts)
    assert _files(path.parent) == before


@pytest.mark.parametrize("change", ["media", "copy", "target", "campaign"])
def test_reprepare_cannot_overwrite_accepted_campaign(
    owned_source: Path,
    change: str,
) -> None:
    _, path = package.prepare_social_source(owned_source)
    approve_social_review(path, approved=True)
    before = _files(path.parent)
    payload = json.loads(owned_source.read_text(encoding="utf-8"))
    handles = None
    if change == "media":
        Path(payload["assets"][0]["path"]).write_bytes(b"unrelated replacement portrait")
    elif change == "copy":
        payload["assets"][0]["title"] = "Different reviewed title"
    elif change == "target":
        handles = {"facebook": "another-account"}
    else:
        payload["campaign_id"] = "different-campaign"
    atomic_write_json(owned_source, payload)
    with pytest.raises(package.SocialPackageError, match="unused output_root"):
        package.prepare_social_source(owned_source, handles=handles)
    assert _files(path.parent) == before


def test_unapproved_package_can_be_revised_before_any_receipt(owned_source: Path) -> None:
    _, path = package.prepare_social_source(owned_source)
    payload = json.loads(owned_source.read_text(encoding="utf-8"))
    payload["assets"][0]["title"] = "Updated proposal title"
    atomic_write_json(owned_source, payload)
    updated, destination = package.prepare_social_source(owned_source)
    assert destination == path
    assert updated.assets[0].title == "Updated proposal title"
    assert not updated.review_approved
    assert all(post.status == "prepared" for post in updated.posts)


def test_existing_receipt_prevents_replacing_unapproved_projection(owned_source: Path) -> None:
    _, path = package.prepare_social_source(owned_source)
    (path.parent / "receipts" / "facebook.jsonl").write_text('{"status":"scheduled"}\n')
    payload = json.loads(owned_source.read_text(encoding="utf-8"))
    payload["assets"][0]["title"] = "Conflicting proposal title"
    atomic_write_json(owned_source, payload)
    before = _files(path.parent)
    with pytest.raises(package.SocialPackageError, match="publication receipts"):
        package.prepare_social_source(owned_source)
    assert _files(path.parent) == before


def test_invalid_existing_manifest_is_preserved(owned_source: Path) -> None:
    _, path = package.prepare_social_source(owned_source)
    path.write_text('{"incomplete":')
    before = _files(path.parent)
    with pytest.raises(package.SocialPackageError, match="cannot be validated"):
        package.prepare_social_source(owned_source)
    assert _files(path.parent) == before


@pytest.mark.parametrize("change", ["deleted", "replaced", "post_path", "post_hash", "unapproved"])
def test_approval_requires_exact_present_approved_media_before_any_mutation(
    owned_source: Path,
    change: str,
) -> None:
    manifest, path = package.prepare_social_source(owned_source)
    media = manifest.assets[0].media.path
    if change == "deleted":
        media.unlink()
    elif change == "replaced":
        media.write_bytes(b"an unreviewed replacement")
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if change == "post_path":
            payload["posts"][0]["media_path"] = str(media.with_name("another.mp4"))
        elif change == "post_hash":
            payload["posts"][0]["media_sha256"] = "f" * 64
        else:
            payload["assets"][0]["approved"] = False
        atomic_write_json(path, payload)
    before = _files(path.parent)
    with pytest.raises(SocialLedgerError):
        approve_social_review(path, approved=True)
    assert _files(path.parent) == before
    assert json.loads(path.read_text(encoding="utf-8"))["review_approved"] is False


def _project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProjectConfig, Path, Path, dict[str, object]]:
    media = tmp_path / "rendered.mp4"
    media.write_bytes(b"approved completed render")
    root = tmp_path / "project-output"
    manifest = root / "highlights" / "vertical" / "manifest.json"
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "Source test"},
            "input": {"recording": tmp_path / "raw.mkv"},
            "audio": {},
            "music": {"library": tmp_path / "unused.json"},
        }
    )
    row: dict[str, object] = {
        "id": "highlight-001",
        "title": "Reviewed reaction",
        "output": str(media),
        "rendered": True,
        "video_source": "native_vertical",
        "output_fingerprint": quick_file_fingerprint(media),
    }
    monkeypatch.setattr(package, "project_output_dir", lambda _config: root)
    monkeypatch.setattr(package, "probe_media", _probe)
    return config, manifest, media, row


@pytest.mark.parametrize("mode", ["native_vertical", "landscape"])
def test_completed_project_render_requires_matching_output_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    config, manifest, media, row = _project(tmp_path, monkeypatch)
    row["video_source"] = mode
    atomic_write_json(manifest, {"approved": True, "clips": [row]})
    assets = package.assets_from_project(config)
    assert len(assets) == 1 and assets[0].approved
    assert assets[0].media.path == media


@pytest.mark.parametrize(
    "change",
    [
        "unapproved",
        "dry_run",
        "missing_rendered",
        "missing_fingerprint",
        "wrong_path",
        "changed_file",
        "missing_file",
    ],
)
def test_project_import_rejects_stale_or_incomplete_render_without_packaging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    config, manifest, media, row = _project(tmp_path, monkeypatch)
    if change == "dry_run":
        row["rendered"] = False
    elif change == "missing_rendered":
        row.pop("rendered")
    elif change == "missing_fingerprint":
        row.pop("output_fingerprint")
    elif change == "wrong_path":
        fingerprint = dict(quick_file_fingerprint(media))
        fingerprint["path"] = str(tmp_path / "another.mp4")
        row["output_fingerprint"] = fingerprint
    elif change == "changed_file":
        media.write_bytes(b"different output reusing the same file name")
    elif change == "missing_file":
        media.unlink()
    atomic_write_json(manifest, {"approved": change != "unapproved", "clips": [row]})
    with pytest.raises(package.SocialPackageError):
        package.assets_from_project(config)
    assert not (manifest.parents[2] / "social").exists()
