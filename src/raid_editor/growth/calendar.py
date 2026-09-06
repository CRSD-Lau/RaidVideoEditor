"""Build one local calendar while preserving remotely scheduled posts as locked."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from raid_editor.growth.models import GlobalContentCalendar, ScheduleEntry, ScheduleLock
from raid_editor.social.ledger import load_distribution_manifest
from raid_editor.util.paths import atomic_write_json

_TERMINAL = {"verified_public", "waived_by_neil"}
_LOCKED = {"scheduled", "published"}


def _schedule_id(campaign_id: str, content_id: str, destination: str, value: datetime) -> str:
    raw = f"{campaign_id}\0{content_id}\0{destination}\0{value.isoformat()}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def import_social_schedule(manifest_paths: list[Path]) -> list[ScheduleEntry]:
    """Read social manifests and import observed schedules without changing them."""

    rows: list[ScheduleEntry] = []
    for path in sorted({item.resolve() for item in manifest_paths}):
        manifest = load_distribution_manifest(path)
        for post in manifest.posts:
            if post.status in _TERMINAL:
                lock: ScheduleLock = "terminal"
            elif post.status in _LOCKED:
                lock = "locked_remote"
            else:
                continue
            status = "waived_by_neil" if post.status == "waived_by_neil" else post.status
            rows.append(
                ScheduleEntry(
                    schedule_id=_schedule_id(
                        manifest.campaign_id,
                        post.content_id,
                        post.platform,
                        post.scheduled_for,
                    ),
                    campaign_id=manifest.campaign_id,
                    content_id=post.content_id,
                    destination=post.platform,
                    target_handle=post.target_handle,
                    scheduled_for=post.scheduled_for,
                    lock=lock,
                    status=status,  # type: ignore[arg-type]
                    source_manifest=path,
                    approved=manifest.review_approved,
                )
            )
    return sorted(rows, key=lambda row: (row.scheduled_for, row.destination, row.content_id))


def propose_open_slots(
    *,
    campaign_id: str,
    content_ids: list[str],
    destinations: list[str],
    existing: list[ScheduleEntry],
    timezone_name: str = "America/Halifax",
    cadence_days: int = 2,
    hour: int = 19,
) -> list[ScheduleEntry]:
    """Place fresh proposals after the last locked release, never displacing it."""

    if not content_ids or not destinations:
        return []
    zone = ZoneInfo(timezone_name)
    now = datetime.now(zone)
    selected_destinations = set(destinations)
    future = [
        row.scheduled_for.astimezone(zone)
        for row in existing
        if row.destination in selected_destinations and row.scheduled_for >= now
    ]
    if future:
        next_day = max(future).date() + timedelta(days=cadence_days)
    else:
        next_day = now.date() + timedelta(days=1)
    proposals: list[ScheduleEntry] = []
    for index, content_id in enumerate(content_ids):
        release = datetime.combine(
            next_day + timedelta(days=index * cadence_days),
            time(hour=hour),
            tzinfo=zone,
        )
        for destination in destinations:
            proposals.append(
                ScheduleEntry(
                    schedule_id=_schedule_id(campaign_id, content_id, destination, release),
                    campaign_id=campaign_id,
                    content_id=content_id,
                    destination=destination,  # type: ignore[arg-type]
                    scheduled_for=release,
                    lock="proposed",
                    status="prepared",
                    approved=False,
                )
            )
    return proposals


def write_global_calendar(
    destination: Path,
    *,
    existing: list[ScheduleEntry],
    proposals: list[ScheduleEntry],
    timezone_name: str = "America/Halifax",
    displacement_proposal: ScheduleEntry | None = None,
) -> GlobalContentCalendar:
    """Write the reviewable local calendar; this function has no platform client."""

    all_entries = [*existing, *proposals]
    buckets: dict[tuple[str, str, datetime], list[str]] = {}
    for row in all_entries:
        key = (
            row.destination,
            row.target_handle or "configured-default-account",
            row.scheduled_for,
        )
        buckets.setdefault(key, []).append(row.content_id)
    collisions = [
        (f"{destination}:{handle}:{scheduled_for.isoformat()} contains {', '.join(content_ids)}")
        for (destination, handle, scheduled_for), content_ids in sorted(
            buckets.items(), key=lambda item: item[0]
        )
        if len(content_ids) > 1
    ]
    calendar = GlobalContentCalendar(
        generated_at=datetime.now(UTC),
        timezone=timezone_name,
        entries=all_entries,
        displacement_proposals=(
            [displacement_proposal] if displacement_proposal is not None else []
        ),
        notes=[
            "Existing remote schedules are imported as locked and never changed here.",
            "New rows are proposals only until separately reviewed and approved per platform.",
        ],
        collision_warnings=collisions,
    )
    atomic_write_json(destination, calendar.model_dump(mode="json"))
    return calendar
