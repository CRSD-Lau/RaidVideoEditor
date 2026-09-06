"""Local fixed-age analytics snapshots with honest unavailable values."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from raid_editor.social.ledger import load_distribution_manifest
from raid_editor.social.models import SocialAnalyticsSnapshot, json_payload
from raid_editor.util.paths import atomic_write_json, atomic_write_text, ensure_directory


class SocialAnalyticsError(RuntimeError):
    """Expected malformed metrics or unknown post identity."""


def _rate(value: int | float | None, views: int | float | None) -> float | None:
    if value is None or views in {None, 0}:
        return None
    return float(value) * 1000 / float(views)


def record_analytics_snapshot(
    manifest_path: Path,
    *,
    platform: str,
    content_id: str,
    label: str,
    metrics_path: Path,
    source: str,
) -> tuple[SocialAnalyticsSnapshot, Path]:
    """Validate and preserve one manual export or official API result."""

    manifest = load_distribution_manifest(manifest_path)
    if not any(
        post.platform == platform and post.content_id == content_id for post in manifest.posts
    ):
        raise SocialAnalyticsError("Unknown platform/content identity")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(metrics, dict):
        raise SocialAnalyticsError("Metrics JSON must contain one object")
    views = metrics.get("views", metrics.get("plays"))
    numeric_views = views if isinstance(views, (int, float)) else None
    normalized = {
        "likes_per_1000_views": _rate(
            metrics.get("likes") if isinstance(metrics.get("likes"), (int, float)) else None,
            numeric_views,
        ),
        "comments_per_1000_views": _rate(
            metrics.get("comments") if isinstance(metrics.get("comments"), (int, float)) else None,
            numeric_views,
        ),
        "shares_per_1000_views": _rate(
            metrics.get("shares") if isinstance(metrics.get("shares"), (int, float)) else None,
            numeric_views,
        ),
        "saves_per_1000_views": _rate(
            metrics.get("saves") if isinstance(metrics.get("saves"), (int, float)) else None,
            numeric_views,
        ),
        "follows_per_1000_views": _rate(
            metrics.get("follows") if isinstance(metrics.get("follows"), (int, float)) else None,
            numeric_views,
        ),
    }
    snapshot = SocialAnalyticsSnapshot(
        platform=platform,  # type: ignore[arg-type]
        content_id=content_id,
        label=label,
        captured_at=datetime.now(UTC),
        metrics=metrics,
        normalized=normalized,
        source=source,  # type: ignore[arg-type]
        notes=["Raw platform definitions are preserved; compare within a platform at fixed age."],
    )
    root = ensure_directory(manifest_path.parent / "analytics" / platform / content_id)
    destination = root / f"{label}.json"
    atomic_write_json(destination, json_payload(snapshot))
    lines = [
        f"# {platform.title()} analytics: {content_id} at {label}",
        "",
        f"- Captured: {snapshot.captured_at.isoformat()}",
        f"- Source: {snapshot.source}",
        "",
        "## Raw metrics",
        "",
        *[
            f"- {key}: {value if value is not None else 'unavailable'}"
            for key, value in metrics.items()
        ],
        "",
        "## Normalized",
        "",
        *[
            f"- {key}: {value:.2f}" if value is not None else f"- {key}: unavailable"
            for key, value in normalized.items()
        ],
    ]
    atomic_write_text(destination.with_suffix(".md"), "\n".join(lines) + "\n")
    return snapshot, destination
