"""Publication receipts and review approvals belong to exact media bytes."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from raid_editor.config.models import ProjectConfig
from raid_editor.growth import package
from raid_editor.growth.models import (
    CampaignAsset,
    CampaignManifest,
    CoverageRecord,
    DistributionEntry,
    GrowthDistributionManifest,
    RelatedTarget,
    ScheduleEntry,
    SourceFingerprint,
    SourceLineage,
)
from raid_editor.social.models import SocialAsset, SocialMediaDetails
from raid_editor.social.package import build_distribution_manifest
from raid_editor.util.paths import atomic_write_json, full_file_sha256


def _render(root: Path, *, name: str = "reviewed.mp4", content: bytes = b"reviewed master") -> Path:
    path = root / "final" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    artifact = {"path": str(path), "sha256": full_file_sha256(path)}
    atomic_write_json(
        path.with_suffix(".manifest.json"),
        {"approved": True, "signature": "a" * 64, "artifact": artifact},
    )
    atomic_write_json(
        root / "reports" / "final-validation.json",
        {"status": "passed", "artifact": artifact},
    )
    return path


def _upload(root: Path, path: Path, *, complete: bool = True) -> None:
    atomic_write_json(
        root / "youtube" / "upload-manifest.json",
        {
            "upload_complete": complete,
            "source": str(path),
            "source_sha256": full_file_sha256(path),
            "video_id": "owned-archive",
            "url": "https://youtu.be/owned-archive",
            "privacy_status": "public",
            "public_playback_confirmed": True,
            "publication_verified_at": "2026-09-06T12:00:00Z",
        },
    )


def _source(path: Path) -> SourceLineage:
    return SourceLineage(
        source_id="landscape-authoritative",
        path=path,
        kind="landscape_authoritative_source",
        fingerprint=SourceFingerprint(
            algorithm="sha256",
            digest=full_file_sha256(path),
            size_bytes=path.stat().st_size,
        ),
        authoritative_for=["archive", "audio"],
    )


def test_newer_unvalidated_mp4_never_inherits_another_files_validation(tmp_path: Path) -> None:
    reviewed = _render(tmp_path)
    other = reviewed.with_name("newer-unvalidated.mp4")
    other.write_bytes(b"different master")
    os.utime(other, (2_000_000_000, 2_000_000_000))
    assert package._validated_final(tmp_path) == (reviewed, full_file_sha256(reviewed))


@pytest.mark.parametrize(
    "change",
    ["replace_bytes", "missing", "unapproved_sidecar", "wrong_sidecar", "legacy_sidecar"],
)
def test_final_receipt_rejects_replaced_missing_or_unapproved_output(
    tmp_path: Path,
    change: str,
) -> None:
    reviewed = _render(tmp_path)
    if change == "replace_bytes":
        reviewed.write_bytes(b"different master")
    elif change == "missing":
        reviewed.unlink()
    elif change == "unapproved_sidecar":
        atomic_write_json(reviewed.with_suffix(".manifest.json"), {"approved": False})
    elif change == "wrong_sidecar":
        atomic_write_json(
            reviewed.with_suffix(".manifest.json"),
            {"approved": True, "artifact": {"path": str(reviewed), "sha256": "f" * 64}},
        )
    else:
        atomic_write_json(reviewed.with_suffix(".manifest.json"), {"approved": True})
    assert package._validated_final(tmp_path) is None


def test_legacy_report_requires_completed_receipt_with_exact_path_and_hash(tmp_path: Path) -> None:
    reviewed = _render(tmp_path)
    report = tmp_path / "reports" / "final-validation.json"
    atomic_write_json(report, {"status": "passed", "checks": [{"passed": True}]})
    atomic_write_json(reviewed.with_suffix(".manifest.json"), {"approved": True})
    assert package._validated_final(tmp_path) is None
    _upload(tmp_path, reviewed, complete=False)
    assert package._validated_final(tmp_path) is None
    _upload(tmp_path, reviewed)
    assert package._validated_final(tmp_path) == (reviewed, full_file_sha256(reviewed))
    reviewed.write_bytes(b"replacement at the same path")
    assert package._validated_final(tmp_path) is None


def test_publication_receipt_requires_completed_upload_of_selected_archive(tmp_path: Path) -> None:
    old = _render(tmp_path)
    _upload(tmp_path, old)
    selected = _render(tmp_path, name="new-master.mp4", content=b"new reviewed master")
    assert package._publication_state(tmp_path, media_sha256=full_file_sha256(selected)) == (
        "prepared",
        None,
        None,
    )
    assert package._publication_state(tmp_path, media_sha256=None) == ("prepared", None, None)
    _upload(tmp_path, selected, complete=False)
    assert package._publication_state(tmp_path, media_sha256=full_file_sha256(selected)) == (
        "prepared",
        None,
        None,
    )
    _upload(tmp_path, selected)
    assert package._publication_state(tmp_path, media_sha256=full_file_sha256(selected)) == (
        "verified_public",
        "owned-archive",
        "https://youtu.be/owned-archive",
    )


def test_regeneration_keeps_cleanup_hold_after_published_master_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "raw.mkv"
    raw.write_bytes(b"unmodified original raid")
    root = tmp_path / "raid-night"
    reviewed = _render(root)
    _upload(root, reviewed)
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "Identity fixture", "raid": "Icecrown Citadel"},
            "input": {"recording": raw},
            "audio": {"game_track": 1},
            "music": {"library": tmp_path / "unused.json"},
        }
    )
    monkeypatch.setattr(package, "project_output_dir", lambda _config: root)
    monkeypatch.setattr(package, "_lineage", lambda path, **_kwargs: _source(path))
    _first, old_distribution, _page = package.prepare_growth_package(
        config, social_manifest_paths=[]
    )
    assert old_distribution.entries[-1].status == "verified_public"
    reviewed.write_bytes(b"new master without validation or publication")
    campaign, distribution, _page = package.prepare_growth_package(config, social_manifest_paths=[])
    assert campaign.assets[0].media_path is None
    youtube = next(row for row in distribution.entries if row.destination == "youtube")
    assert youtube.status == "prepared" and not youtube.review_approved
    assert youtube.public_url is None and youtube.remote_content_id is None
    source_hold = next(row for row in distribution.cleanup_dependencies if row.path == raw)
    assert not source_hold.cleanup_eligible and source_hold.holds


def _approval_files(tmp_path: Path) -> tuple[Path, list[Path]]:
    raw = tmp_path / "raw.mkv"
    raw.write_bytes(b"original source")
    media = [tmp_path / "archive.mp4", tmp_path / "feature.mp4"]
    for index, path in enumerate(media):
        path.write_bytes(f"reviewed synthetic media {index}".encode())
    now = datetime.now(UTC)
    campaign_path = tmp_path / "growth" / "campaign-manifest.json"
    assets = [
        CampaignAsset(
            content_id=f"asset-{index}",
            lane="archive" if index == 0 else "boss_feature",
            title="Reviewed synthetic media",
            state="rendered",
            media_path=path,
            media_sha256=full_file_sha256(path),
            source_lineage_ids=["landscape-authoritative"],
            coverage=CoverageRecord(),
        )
        for index, path in enumerate(media)
    ]
    campaign = CampaignManifest(
        campaign_id="synthetic-campaign",
        created_at=now,
        updated_at=now,
        raid_name="Synthetic raid",
        sources=[_source(raw)],
        assets=assets,
    )
    distribution = GrowthDistributionManifest(
        campaign_id=campaign.campaign_id,
        generated_at=now,
        campaign_manifest=campaign_path,
        entries=[
            DistributionEntry(
                content_id=asset.content_id,
                lane=asset.lane,
                destination="youtube",
                status="prepared",
                media_sha256=asset.media_sha256,
            )
            for asset in assets
        ],
    )
    atomic_write_json(campaign_path, campaign.model_dump(mode="json"))
    atomic_write_json(
        campaign_path.with_name("distribution-manifest.json"), distribution.model_dump(mode="json")
    )
    return campaign_path, media


@pytest.mark.parametrize("change", ["missing", "replace", "distribution_mismatch"])
def test_approval_rejects_stale_media_before_any_manifest_or_ledger_write(
    tmp_path: Path,
    change: str,
) -> None:
    campaign_path, media = _approval_files(tmp_path)
    distribution_path = campaign_path.with_name("distribution-manifest.json")
    if change == "missing":
        media[1].unlink()
    elif change == "replace":
        media[1].write_bytes(b"replacement")
    else:
        payload = json.loads(distribution_path.read_text())
        payload["entries"][0]["media_sha256"] = "f" * 64
        atomic_write_json(distribution_path, payload)
    before = campaign_path.read_bytes(), distribution_path.read_bytes()
    with pytest.raises(package.GrowthPackageError):
        package.approve_growth_package(campaign_path, approved=True)
    assert before == (campaign_path.read_bytes(), distribution_path.read_bytes())
    assert not campaign_path.with_name("growth-ledger.jsonl").exists()


def test_approval_detects_earlier_asset_changed_while_hashing_later_asset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_path, media = _approval_files(tmp_path)
    original = package.full_file_sha256

    def replace(path: Path, **kwargs: Any) -> str:
        result = original(path, **kwargs)
        if path == media[1]:
            media[0].write_bytes(b"changed during review")
        return result

    monkeypatch.setattr(package, "full_file_sha256", replace)
    before = campaign_path.read_bytes()
    with pytest.raises(package.GrowthPackageError, match="during approval"):
        package.approve_growth_package(campaign_path, approved=True)
    assert before == campaign_path.read_bytes()
    assert not campaign_path.with_name("growth-ledger.jsonl").exists()


def test_same_identity_approval_succeeds_and_records_only_local_review(tmp_path: Path) -> None:
    campaign_path, _media = _approval_files(tmp_path)
    campaign = package.approve_growth_package(campaign_path, approved=True)
    assert all(asset.state == "upload_ready" for asset in campaign.assets)
    distribution = GrowthDistributionManifest.model_validate_json(
        campaign_path.with_name("distribution-manifest.json").read_text()
    )
    assert all(
        entry.review_approved and entry.status == "reviewed" for entry in distribution.entries
    )
    assert all("growth_package_review" in entry.named_approvals for entry in distribution.entries)


def test_completed_historical_assets_do_not_need_deleted_media_reapproved(tmp_path: Path) -> None:
    campaign_path, media = _approval_files(tmp_path)
    payload = json.loads(campaign_path.read_text())
    payload["assets"][0]["state"] = "verified_public"
    atomic_write_json(campaign_path, payload)
    media[0].unlink()
    result = package.approve_growth_package(campaign_path, approved=True)
    assert result.assets[0].state == "verified_public"
    assert result.assets[1].state == "upload_ready"


@pytest.mark.parametrize("mismatch", [None, "campaign", "post", "asset"])
def test_social_publication_import_requires_campaign_asset_and_post_media_sha_agreement(
    tmp_path: Path,
    mismatch: str | None,
) -> None:
    campaign_path, media = _approval_files(tmp_path)
    campaign = CampaignManifest.model_validate_json(campaign_path.read_text())
    selected = campaign.assets[1]
    selected.lane = "short"
    selected.related_target = RelatedTarget(target_content_id="asset-0", target_lane="archive")
    current_sha256 = full_file_sha256(media[1])
    social_asset = SocialAsset(
        content_id="owned-social-short",
        source_highlight_id=selected.content_id,
        title="Owned synthetic short",
        youtube_video_id="abcdefghijk",
        youtube_url="https://youtu.be/abcdefghijk",
        media=SocialMediaDetails(
            path=media[1],
            sha256=current_sha256,
            width=1080,
            height=1920,
            frame_rate=30,
            duration_seconds=30,
            video_codec="h264",
            audio_codec="aac",
            provenance="manual_owned_import",
            quality_class="canonical_master",
        ),
    )
    social = build_distribution_manifest("owned-social-campaign", [social_asset])
    social.review_approved = True
    now = datetime.now(UTC)
    for post in social.posts:
        post.status = "verified_public"
        post.remote_content_id = f"{post.platform}-owned"
        post.public_url = f"https://example.com/{post.platform}-owned"
        post.verified_at = now
    if mismatch == "campaign":
        # The same content ID is now a different actual rendered file.
        media[1].write_bytes(b"newer edited short")
        selected.media_sha256 = full_file_sha256(media[1])
    elif mismatch == "post":
        for post in social.posts:
            post.media_sha256 = "f" * 64
    elif mismatch == "asset":
        social.assets[0].media.sha256 = "f" * 64
    manifest_path = tmp_path / "social" / "distribution-manifest.json"
    atomic_write_json(manifest_path, social.model_dump(mode="json"))
    aliases = package._social_aliases(
        [manifest_path],
        current_media={asset.content_id: asset.media_sha256 for asset in campaign.assets},
    )
    assert (selected.content_id in aliases) == (mismatch is None)
    assert ("owned-social-short" in aliases) == (mismatch is None)
    entries = package._distribution(campaign, campaign_path, [manifest_path], tmp_path)
    short_entries = [entry for entry in entries if entry.content_id == selected.content_id]
    expected_status = "verified_public" if mismatch is None else "prepared"
    assert all(
        entry.status == expected_status for entry in short_entries if entry.destination != "discord"
    )
    if mismatch is not None:
        assert all(
            not entry.review_approved and entry.public_url is None for entry in short_entries
        )
    cleanup = package._cleanup_dependencies(campaign, entries)
    short_dependency = next(row for row in cleanup if row.dependency_id == "asset:asset-1")
    assert short_dependency.cleanup_eligible == (mismatch is None)


def test_old_social_alias_cannot_suppress_new_slots_or_modify_old_package(tmp_path: Path) -> None:
    # Old package uses exactly the same display content ID as a replacement.
    media = tmp_path / "short.mp4"
    media.write_bytes(b"old reviewed edit")
    asset = SocialAsset(
        content_id="highlight-001",
        title="Old synthetic edit",
        media=SocialMediaDetails(
            path=media,
            sha256=full_file_sha256(media),
            width=1080,
            height=1920,
            frame_rate=30,
            duration_seconds=20,
            video_codec="h264",
            audio_codec="aac",
            provenance="manual_owned_import",
            quality_class="canonical_master",
        ),
    )
    social = build_distribution_manifest("old-package", [asset])
    manifest = tmp_path / "social" / "distribution-manifest.json"
    atomic_write_json(manifest, social.model_dump(mode="json"))
    media.write_bytes(b"new replacement edit")
    aliases = package._social_aliases(
        [manifest],
        current_media={"highlight-001": full_file_sha256(media)},
    )
    assert "highlight-001" not in aliases
    new_short_ids = [content_id for content_id in ["highlight-001"] if content_id not in aliases]
    assert new_short_ids == ["highlight-001"]
    before = manifest.read_bytes()
    package._align_unreviewed_social_schedules(
        [manifest],
        [
            ScheduleEntry(
                schedule_id="new-synthetic-slot",
                campaign_id="new-campaign",
                content_id="highlight-001",
                destination="facebook",
                scheduled_for=datetime(2030, 1, 1, tzinfo=UTC),
                lock="proposed",
                status="prepared",
            )
        ],
        aliases,
    )
    assert manifest.read_bytes() == before
