"""Actual-age, surface-aware analytics snapshots for cross-platform comparisons."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from raid_editor.growth.models import AnalyticsSnapshot
from raid_editor.util.paths import atomic_write_json, ensure_directory


class GrowthAnalyticsError(RuntimeError):
    """Expected publication-time or metrics validation error."""


def record_growth_analytics(
    destination_root: Path,
    *,
    campaign_id: str,
    content_id: str,
    destination: str,
    published_at: datetime,
    requested_checkpoint_hours: int,
    metrics_path: Path,
    source: str,
    content_lane: str | None = None,
    cohort: str | None = None,
    surface: str | None = None,
    metric_definitions_path: Path | None = None,
    event_alignment_path: Path | None = None,
    captured_at: datetime | None = None,
    tolerance_seconds: float = 3600,
) -> tuple[AnalyticsSnapshot, Path]:
    """Record measured age from observed publication time, never from a label alone."""

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(metrics, dict):
        raise GrowthAnalyticsError("Metrics JSON must contain one object")
    if published_at.tzinfo is None:
        raise GrowthAnalyticsError("published_at must include a timezone")
    captured = captured_at or datetime.now(UTC)
    if captured.tzinfo is None:
        raise GrowthAnalyticsError("captured_at must include a timezone")
    actual_age = (captured - published_at).total_seconds()
    if actual_age < 0:
        raise GrowthAnalyticsError("captured_at cannot precede published_at")
    requested_seconds = requested_checkpoint_hours * 3600
    event_alignment: dict[str, int | float | str | None] | None = None
    metric_definitions: dict[str, str] = {}
    if metric_definitions_path is not None:
        definitions = json.loads(metric_definitions_path.read_text(encoding="utf-8"))
        if not isinstance(definitions, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in definitions.items()
        ):
            raise GrowthAnalyticsError("Metric definitions JSON must map strings to strings")
        metric_definitions = definitions
    if event_alignment_path is not None:
        loaded = json.loads(event_alignment_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise GrowthAnalyticsError("Event-alignment JSON must contain one object")
        event_alignment = {
            str(key): value
            for key, value in loaded.items()
            if isinstance(value, (int, float, str)) or value is None
        }
    snapshot_id = (
        f"{destination}-{content_id}-{requested_checkpoint_hours}h-"
        f"{captured.astimezone(UTC):%Y%m%dT%H%M%SZ}"
    )
    snapshot = AnalyticsSnapshot(
        snapshot_id=snapshot_id,
        campaign_id=campaign_id,
        content_id=content_id,
        destination=destination,  # type: ignore[arg-type]
        content_lane=content_lane,  # type: ignore[arg-type]
        cohort=cohort,
        published_at=published_at,
        captured_at=captured,
        requested_checkpoint_hours=requested_checkpoint_hours,
        actual_age_seconds=actual_age,
        tolerance_seconds=tolerance_seconds,
        outside_tolerance=abs(actual_age - requested_seconds) > tolerance_seconds,
        surface=surface,
        metrics=metrics,
        metric_definitions=metric_definitions,
        event_alignment=event_alignment,
        source=source,  # type: ignore[arg-type]
        notes=[
            "Compare within the same platform, surface, and actual content age.",
            "CTR must be interpreted with watch duration and traffic intent.",
        ],
    )
    root = ensure_directory(destination_root / destination / content_id)
    path = root / f"{requested_checkpoint_hours}h-{captured.astimezone(UTC):%Y%m%dT%H%M%SZ}.json"
    atomic_write_json(path, snapshot.model_dump(mode="json"))
    return snapshot, path
