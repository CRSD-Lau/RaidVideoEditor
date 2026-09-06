from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from raid_editor.social.analytics import record_analytics_snapshot
from raid_editor.social.ledger import (
    SocialLedgerError,
    approve_social_review,
    cleanup_eligibility,
    load_distribution_manifest,
    record_publication_status,
)
from raid_editor.social.models import SocialAsset, SocialAudioPolicy, SocialMediaDetails
from raid_editor.social.package import build_distribution_manifest, write_social_package


def _asset(tmp_path: Path, *, content_id: str = "shadowmourne") -> SocialAsset:
    media = tmp_path / f"{content_id}.mp4"
    media.write_bytes(f"approved portrait media {content_id}".encode())
    return SocialAsset(
        content_id=content_id,
        title="Pizza Warriors' FIRST Shadowmourne!",
        content_pillar="guild_milestone",
        media=SocialMediaDetails(
            path=media,
            sha256=sha256(media.read_bytes()).hexdigest(),
            width=1080,
            height=1920,
            frame_rate=60,
            duration_seconds=40,
            video_codec="h264",
            pixel_format="yuv420p",
            audio_codec="aac",
            audio_sample_rate=48_000,
            audio_channels=2,
            provenance="raid_editor_render",
            quality_class="canonical_master",
        ),
        audio_policy=SocialAudioPolicy(),
        approved=True,
    )


def test_distribution_package_has_four_unique_posts_and_two_day_cadence(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 8, 30, 19, tzinfo=ZoneInfo("America/Halifax"))
    manifest = build_distribution_manifest(
        "campaign",
        [_asset(tmp_path), _asset(tmp_path, content_id="second")],
        start_at=start,
        cadence_days=2,
    )

    assert len(manifest.posts) == 8
    assert {post.platform for post in manifest.posts} == {
        "facebook",
        "instagram",
        "tiktok",
        "twitch",
    }
    assert len({post.idempotency_key for post in manifest.posts}) == 8
    assert all("—" not in post.caption for post in manifest.posts)
    release_times = sorted({post.scheduled_for for post in manifest.posts})
    assert (release_times[1] - release_times[0]).days == 2
    twitch = next(post for post in manifest.posts if post.platform == "twitch")
    assert twitch.content_type == "upload"
    assert twitch.hashtags == []


def test_recovered_derivative_requires_disclosed_quality_exception(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="quality_exception"):
        SocialMediaDetails(
            path=tmp_path / "recovered.mp4",
            sha256="b" * 64,
            width=720,
            height=1280,
            frame_rate=30,
            duration_seconds=40,
            video_codec="h264",
            audio_codec="aac",
            provenance="youtube_studio_owner_download",
            quality_class="owner_recovered_derivative",
        )


def test_publication_receipts_are_gated_idempotent_and_hold_cleanup(tmp_path: Path) -> None:
    manifest = build_distribution_manifest("campaign", [_asset(tmp_path)])
    path = write_social_package(manifest, tmp_path / "social")
    with pytest.raises(SocialLedgerError, match="requires --approved"):
        approve_social_review(path, approved=False)
    approve_social_review(path, approved=True)

    record_publication_status(
        path,
        platform="facebook",
        content_id="shadowmourne",
        status="scheduled",
        approved=True,
        remote_content_id="scheduled-1",
    )
    record_publication_status(
        path,
        platform="facebook",
        content_id="shadowmourne",
        status="verified_public",
        approved=True,
        remote_content_id="facebook-1",
        public_url="https://www.facebook.com/reel/1",
        verification_checks={"public_playback": True, "audio": True},
    )
    replay = record_publication_status(
        path,
        platform="facebook",
        content_id="shadowmourne",
        status="verified_public",
        approved=True,
        remote_content_id="facebook-1",
        public_url="https://www.facebook.com/reel/1",
    )

    facebook = next(post for post in replay.posts if post.platform == "facebook")
    assert facebook.verified_at is not None
    cleanup = cleanup_eligibility(load_distribution_manifest(path))
    assert cleanup["eligible"] is False
    assert any("instagram:reviewed" in hold for hold in cleanup["assets"][0]["holds"])
    receipts = path.parent / "receipts" / "facebook.jsonl"
    assert len(receipts.read_text(encoding="utf-8").splitlines()) == 2


def test_manual_publication_can_be_waived_without_claiming_an_account_failure(
    tmp_path: Path,
) -> None:
    manifest = build_distribution_manifest("campaign", [_asset(tmp_path)])
    path = write_social_package(manifest, tmp_path / "social")
    approve_social_review(path, approved=True)

    updated = record_publication_status(
        path,
        platform="tiktok",
        content_id="shadowmourne",
        status="waived_by_neil",
        approved=True,
        verification_checks={
            "manual_publication_by_neil": True,
            "automation_skipped_to_prevent_duplicate": True,
        },
    )

    tiktok = next(post for post in updated.posts if post.platform == "tiktok")
    assert tiktok.status == "waived_by_neil"
    cleanup = cleanup_eligibility(updated)
    assert all("tiktok:" not in hold for hold in cleanup["assets"][0]["holds"])
    receipts = path.parent / "receipts" / "tiktok.jsonl"
    assert len(receipts.read_text(encoding="utf-8").splitlines()) == 1


def test_social_analytics_preserves_raw_metrics_and_rates(tmp_path: Path) -> None:
    manifest_path = write_social_package(
        build_distribution_manifest("campaign", [_asset(tmp_path)]),
        tmp_path / "social",
    )
    metrics = tmp_path / "metrics.json"
    metrics.write_text(
        json.dumps({"views": 200, "likes": 20, "shares": 4, "saves": None}),
        encoding="utf-8",
    )

    snapshot, destination = record_analytics_snapshot(
        manifest_path,
        platform="instagram",
        content_id="shadowmourne",
        label="24h",
        metrics_path=metrics,
        source="manual_studio_entry",
    )

    assert snapshot.normalized["likes_per_1000_views"] == 100
    assert snapshot.normalized["shares_per_1000_views"] == 20
    assert snapshot.normalized["saves_per_1000_views"] is None
    assert destination.is_file()
