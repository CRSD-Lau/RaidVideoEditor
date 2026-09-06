"""Idempotent publication receipts, status reporting, and cleanup holds."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from raid_editor.social.models import DistributionManifest, PublicationStatus, json_payload
from raid_editor.util.paths import (
    atomic_write_json,
    atomic_write_text,
    ensure_directory,
    full_file_sha256,
)


class SocialLedgerError(RuntimeError):
    """Expected approval, transition, or receipt conflict."""


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "prepared": {"reviewed", "awaiting_confirmation", "blocked_account", "blocked_capability"},
    "reviewed": {
        "awaiting_confirmation",
        "uploaded",
        "scheduled",
        "blocked_account",
        "waived_by_neil",
    },
    "awaiting_confirmation": {
        "uploaded",
        "scheduled",
        "blocked_account",
        "failed",
        "waived_by_neil",
    },
    "uploaded": {"processing", "scheduled", "published", "failed", "remote_state_uncertain"},
    "processing": {"scheduled", "published", "failed", "remote_state_uncertain"},
    "scheduled": {"published", "verified_public", "failed", "remote_state_uncertain"},
    "published": {"verified_public", "failed", "remote_state_uncertain"},
    "failed": {"awaiting_confirmation", "uploaded", "blocked_account"},
    "blocked_account": {"awaiting_confirmation", "waived_by_neil"},
    "blocked_capability": {"waived_by_neil"},
    "remote_state_uncertain": {"published", "verified_public", "failed"},
    "verified_public": set(),
    "waived_by_neil": set(),
}


def load_distribution_manifest(path: Path) -> DistributionManifest:
    """Read and validate the complete manifest before any mutation."""

    return DistributionManifest.model_validate_json(path.read_text(encoding="utf-8"))


def approve_social_review(path: Path, *, approved: bool) -> DistributionManifest:
    """Record the package review gate without publishing remotely."""

    if not approved:
        raise SocialLedgerError("Social review approval requires --approved")
    manifest = load_distribution_manifest(path)
    assets = {asset.content_id: asset for asset in manifest.assets}
    for post in manifest.posts:
        asset = assets[post.content_id]
        if (
            post.media_path.resolve() != asset.media.path.resolve()
            or post.media_sha256 != asset.media.sha256
        ):
            raise SocialLedgerError(
                f"Social post media does not match its asset: {post.platform}/{post.content_id}"
            )
    for asset in manifest.assets:
        if not asset.approved:
            raise SocialLedgerError(f"Social asset lacks its clip approval: {asset.content_id}")
        if not asset.media.path.is_file():
            raise SocialLedgerError(f"Reviewed social media is missing: {asset.media.path}")
        if full_file_sha256(asset.media.path) != asset.media.sha256:
            raise SocialLedgerError(
                f"Reviewed social media has changed; prepare and review its new identity: "
                f"{asset.media.path}"
            )
    manifest.review_approved = True
    for post in manifest.posts:
        if post.status == "prepared":
            post.status = "reviewed"
    atomic_write_json(path, json_payload(manifest))
    return manifest


def _append_receipt(root: Path, platform: str, value: dict[str, Any]) -> None:
    destination = ensure_directory(root / "receipts") / f"{platform}.jsonl"
    existing = destination.read_text(encoding="utf-8") if destination.is_file() else ""
    atomic_write_text(destination, existing + json.dumps(value, ensure_ascii=False) + "\n")


def record_publication_status(
    path: Path,
    *,
    platform: str,
    content_id: str,
    status: PublicationStatus,
    approved: bool,
    remote_content_id: str | None = None,
    public_url: str | None = None,
    target_account_id: str | None = None,
    verification_checks: dict[str, bool | str | float | int | None] | None = None,
    error: str | None = None,
) -> DistributionManifest:
    """Record one confirmed remote state and append immutable receipt history."""

    if not approved:
        raise SocialLedgerError("Publication receipt mutation requires --approved")
    manifest = load_distribution_manifest(path)
    if not manifest.review_approved:
        raise SocialLedgerError("The social package review is not approved")
    matching = [
        post
        for post in manifest.posts
        if post.platform == platform and post.content_id == content_id
    ]
    if len(matching) != 1:
        raise SocialLedgerError("Expected exactly one platform/content package")
    post = matching[0]
    if post.status == status:
        if remote_content_id and post.remote_content_id not in {None, remote_content_id}:
            raise SocialLedgerError("Existing receipt belongs to a different remote content ID")
        if public_url and post.public_url not in {None, public_url}:
            raise SocialLedgerError("Existing receipt belongs to a different public URL")
        return manifest
    allowed = _ALLOWED_TRANSITIONS.get(post.status, set())
    if status not in allowed:
        raise SocialLedgerError(f"Invalid social transition: {post.status} -> {status}")
    if status in {"published", "verified_public"} and (not remote_content_id or not public_url):
        raise SocialLedgerError("Published states require remote content ID and public URL")
    now = datetime.now(UTC)
    post.status = status
    post.target_account_id = target_account_id or post.target_account_id
    post.remote_content_id = remote_content_id or post.remote_content_id
    post.public_url = public_url or post.public_url
    post.last_error = error
    if status in {"published", "verified_public"}:
        post.published_at = post.published_at or now
    if status == "verified_public":
        post.verified_at = now
    if verification_checks is not None:
        post.verification_checks = verification_checks
    payload = {
        "recorded_at": now.isoformat(),
        "campaign_id": manifest.campaign_id,
        "content_id": post.content_id,
        "platform": post.platform,
        "target_handle": post.target_handle,
        "target_account_id": post.target_account_id,
        "idempotency_key": post.idempotency_key,
        "media_sha256": post.media_sha256,
        "copy_sha256": post.copy_sha256,
        "status": post.status,
        "remote_content_id": post.remote_content_id,
        "public_url": post.public_url,
        "verification_checks": post.verification_checks,
        "error": post.last_error,
    }
    atomic_write_json(path, json_payload(manifest))
    _append_receipt(path.parent, platform, payload)
    return manifest


def cleanup_eligibility(manifest: DistributionManifest) -> dict[str, Any]:
    """Return per-asset social holds without deleting anything."""

    terminal = {"verified_public", "waived_by_neil"}
    rows: list[dict[str, Any]] = []
    for asset in manifest.assets:
        posts = [post for post in manifest.posts if post.content_id == asset.content_id]
        pending = [
            f"{post.platform}:{post.status}" for post in posts if post.status not in terminal
        ]
        rows.append(
            {
                "content_id": asset.content_id,
                "media_path": str(asset.media.path),
                "media_sha256": asset.media.sha256,
                "eligible": not pending,
                "holds": pending,
            }
        )
    return {
        "campaign_id": manifest.campaign_id,
        "eligible": all(row["eligible"] for row in rows),
        "terminal_states": sorted(terminal),
        "assets": rows,
    }


def write_social_status(path: Path) -> tuple[dict[str, Any], Path]:
    """Write a concise publication and cleanup report."""

    manifest = load_distribution_manifest(path)
    cleanup = cleanup_eligibility(manifest)
    counts: dict[str, int] = {}
    for post in manifest.posts:
        counts[post.status] = counts.get(post.status, 0) + 1
    report = {
        "campaign_id": manifest.campaign_id,
        "review_approved": manifest.review_approved,
        "post_status_counts": counts,
        "cleanup": cleanup,
    }
    destination = path.parent / "social-status.md"
    lines = [
        "# Social publication status",
        "",
        f"- Campaign: {manifest.campaign_id}",
        f"- Review approved: {'yes' if manifest.review_approved else 'no'}",
        f"- Social media cleanup eligible: {'yes' if cleanup['eligible'] else 'no'}",
        "",
        "## Post states",
        "",
        *[f"- {state}: {count}" for state, count in sorted(counts.items())],
        "",
        "## Media holds",
        "",
    ]
    for row in cleanup["assets"]:
        state = "eligible" if row["eligible"] else ", ".join(row["holds"])
        lines.append(f"- {row['content_id']}: {state}")
    atomic_write_text(destination, "\n".join(lines) + "\n")
    atomic_write_json(path.parent / "cleanup-eligibility.json", cleanup)
    return report, destination
