"""Approved playlist management and read-only post-publish analytics reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]

from raid_editor.config.models import ProjectConfig
from raid_editor.util.paths import atomic_write_json, atomic_write_text
from raid_editor.youtube.upload import (
    YOUTUBE_ANALYTICS_SCOPE,
    YOUTUBE_MANAGE_SCOPE,
    YouTubeUploadError,
    youtube_credentials,
)


@dataclass(frozen=True, slots=True)
class PlaylistResult:
    """Record whether a playlist was created and a video inserted or reused."""

    playlist_id: str
    playlist_title: str
    created: bool
    added: bool
    already_present: bool


def _derived_token(config: ProjectConfig, purpose: str) -> Path:
    configured = (
        config.youtube.management_token
        if purpose == "management"
        else config.youtube.analytics_token
    )
    if configured is not None:
        return configured
    base = config.youtube.token
    if base is None:
        raise YouTubeUploadError("A YouTube token path is required")
    return base.with_name(f"{base.stem}-{purpose}{base.suffix}")


def _find_playlist(youtube: Any, title: str) -> str | None:
    page_token: str | None = None
    while True:
        response = (
            youtube.playlists()
            .list(
                part="snippet",
                mine=True,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("items", []):
            if item.get("snippet", {}).get("title") == title:
                return str(item["id"])
        page_token = response.get("nextPageToken")
        if not page_token:
            return None


def add_video_to_weekly_playlist(
    config: ProjectConfig,
    *,
    video_id: str,
    approved: bool,
    report_destination: Path,
) -> PlaylistResult:
    """Find or create the exact weekly playlist, then idempotently add one video.

    Args:
        config: Validated YouTube and playlist settings.
        video_id: Approved YouTube video ID.
        approved: Explicit operator approval for the mutation.
        report_destination: Local Markdown result destination.

    Returns:
        Playlist identity and create/add/already-present state.

    Raises:
        YouTubeUploadError: If approval or configuration is missing, OAuth fails,
            or a YouTube API request fails.
    """

    if not approved:
        raise YouTubeUploadError("Playlist changes require explicit approval")
    if not config.youtube.enabled:
        raise YouTubeUploadError("YouTube integration is disabled in the project configuration")
    if not config.youtube.playlist_auto_add:
        raise YouTubeUploadError("Automatic playlist addition is disabled")
    title = config.youtube.playlist_title
    if not title and not config.youtube.playlist_id:
        raise YouTubeUploadError("Configure youtube.playlist_id or youtube.playlist_title")
    credentials = youtube_credentials(
        config,
        scopes=[YOUTUBE_MANAGE_SCOPE],
        token=_derived_token(config, "management"),
    )
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    playlist_id = config.youtube.playlist_id
    created = False
    try:
        if playlist_id is None:
            playlist_id = _find_playlist(youtube, title or "")
        if playlist_id is None:
            response = (
                youtube.playlists()
                .insert(
                    part="snippet,status",
                    body={
                        "snippet": {
                            "title": title,
                            "description": "Weekly Pizza Warriors World of Warcraft raid clears.",
                        },
                        "status": {"privacyStatus": config.youtube.playlist_privacy_status},
                    },
                )
                .execute()
            )
            playlist_id = str(response["id"])
            created = True
        existing = (
            youtube.playlistItems()
            .list(
                part="id",
                playlistId=playlist_id,
                videoId=video_id,
                maxResults=1,
            )
            .execute()
        )
        already_present = bool(existing.get("items"))
        added = False
        if not already_present:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        "position": 0,
                    }
                },
            ).execute()
            added = True
    except HttpError as exc:
        raise YouTubeUploadError(
            f"YouTube playlist request failed with HTTP {exc.resp.status}"
        ) from exc
    result = PlaylistResult(
        playlist_id=playlist_id,
        playlist_title=title or playlist_id,
        created=created,
        added=added,
        already_present=already_present,
    )
    atomic_write_text(
        report_destination,
        "# YouTube Playlist\n\n"
        f"- Playlist: {result.playlist_title}\n"
        f"- Playlist ID: `{result.playlist_id}`\n"
        f"- Created: {'yes' if result.created else 'no'}\n"
        f"- Video added: {'yes' if result.added else 'no'}\n"
        f"- Already present: {'yes' if result.already_present else 'no'}\n",
    )
    return result


def _rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    names = [str(header["name"]) for header in response.get("columnHeaders", [])]
    return [dict(zip(names, row, strict=False)) for row in response.get("rows", [])]


def _nearest_retention(
    rows: list[dict[str, Any]],
    target_ratio: float,
) -> float | None:
    candidates = [
        row for row in rows if "elapsedVideoTimeRatio" in row and "audienceWatchRatio" in row
    ]
    if not candidates:
        return None
    row = min(
        candidates,
        key=lambda item: abs(float(item["elapsedVideoTimeRatio"]) - target_ratio),
    )
    return float(row["audienceWatchRatio"])


def _retention_changes(
    rows: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    ordered = sorted(rows, key=lambda row: float(row.get("elapsedVideoTimeRatio", 0)))
    changes: list[dict[str, float]] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if "audienceWatchRatio" not in previous or "audienceWatchRatio" not in current:
            continue
        changes.append(
            {
                "elapsed_ratio": float(current["elapsedVideoTimeRatio"]),
                "change": float(current["audienceWatchRatio"])
                - float(previous["audienceWatchRatio"]),
            }
        )
    dips = sorted(
        (item for item in changes if item["change"] < 0),
        key=lambda item: item["change"],
    )[:limit]
    spikes = sorted(
        (item for item in changes if item["change"] > 0),
        key=lambda item: item["change"],
        reverse=True,
    )[:limit]
    return dips, spikes


def fetch_video_analytics(
    config: ProjectConfig,
    *,
    video_id: str,
    start_date: date,
    end_date: date,
    video_duration_seconds: float,
    label: str,
    json_destination: Path,
    markdown_destination: Path,
    studio_impressions: int | None = None,
    studio_ctr_percent: float | None = None,
) -> dict[str, object]:
    """Fetch owner summary and retention data and accept Studio-only CTR fields.

    Args:
        config: Validated YouTube analytics settings.
        video_id: YouTube video to query.
        start_date: Inclusive analytics start date.
        end_date: Inclusive analytics end date.
        video_duration_seconds: Duration used to locate the first-30-second point.
        label: Human-readable report window such as ``48-hour`` or ``7-day``.
        json_destination: Machine-readable report destination.
        markdown_destination: Human-readable report destination.
        studio_impressions: Optional impressions copied from YouTube Studio.
        studio_ctr_percent: Optional thumbnail CTR copied from YouTube Studio.

    Returns:
        Summary metrics, retention checkpoints, dips, spikes, and optional CTR.

    Raises:
        YouTubeUploadError: If analytics is disabled, OAuth fails, or the API
            request is rejected.
    """

    if not config.youtube.enabled:
        raise YouTubeUploadError("YouTube integration is disabled in the project configuration")
    if not config.youtube.analytics_enabled:
        raise YouTubeUploadError("YouTube analytics are disabled in the project configuration")
    credentials = youtube_credentials(
        config,
        scopes=[YOUTUBE_ANALYTICS_SCOPE],
        token=_derived_token(config, "analytics"),
    )
    analytics = build(
        "youtubeAnalytics",
        "v2",
        credentials=credentials,
        cache_discovery=False,
    )
    common = {
        "ids": "channel==MINE",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "filters": f"video=={video_id}",
    }
    try:
        summary_response = (
            analytics.reports()
            .query(
                **common,
                metrics=(
                    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
                    "likes,comments,shares,subscribersGained"
                ),
            )
            .execute()
        )
        retention_response = (
            analytics.reports()
            .query(
                **common,
                dimensions="elapsedVideoTimeRatio",
                metrics="audienceWatchRatio,relativeRetentionPerformance",
                sort="elapsedVideoTimeRatio",
            )
            .execute()
        )
    except HttpError as exc:
        raise YouTubeUploadError(
            f"YouTube Analytics request failed with HTTP {exc.resp.status}"
        ) from exc
    summary_rows = _rows(summary_response)
    summary = summary_rows[0] if summary_rows else {}
    retention = _rows(retention_response)
    first_30_ratio = min(1.0, 30.0 / max(1.0, video_duration_seconds))
    first_30_retention = _nearest_retention(retention, first_30_ratio)
    dips, spikes = _retention_changes(retention)
    payload: dict[str, object] = {
        "label": label,
        "video_id": video_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summary": summary,
        "first_30_seconds_retention": first_30_retention,
        "retention_dips": dips,
        "retention_spikes": spikes,
        "studio_impressions": studio_impressions,
        "studio_ctr_percent": studio_ctr_percent,
        "retention_rows": retention,
    }
    atomic_write_json(json_destination, payload)
    impressions_text = (
        str(studio_impressions) if studio_impressions is not None else "enter manually"
    )
    ctr_text = f"{studio_ctr_percent:.2f}%" if studio_ctr_percent is not None else "enter manually"
    lines = [
        f"# YouTube Analytics: {label}",
        "",
        f"- Video: https://youtu.be/{video_id}",
        f"- Period: {start_date.isoformat()} through {end_date.isoformat()}",
        f"- Views: {summary.get('views', 'unavailable')}",
        f"- Average view duration: {summary.get('averageViewDuration', 'unavailable')} seconds",
        f"- Average viewed: {summary.get('averageViewPercentage', 'unavailable')}%",
        "- First 30-second retention: "
        + (f"{first_30_retention * 100:.1f}%" if first_30_retention is not None else "unavailable"),
        f"- Studio impressions: {impressions_text}",
        f"- Studio thumbnail CTR: {ctr_text}",
        "",
        "## Largest retention dips",
        "",
        *[
            f"- {item['elapsed_ratio'] * 100:.1f}% through video: {item['change'] * 100:.1f} points"
            for item in dips
        ],
        "",
        "## Largest retention spikes",
        "",
        *[
            f"- {item['elapsed_ratio'] * 100:.1f}% through video: "
            f"+{item['change'] * 100:.1f} points"
            for item in spikes
        ],
    ]
    atomic_write_text(markdown_destination, "\n".join(lines) + "\n")
    return payload
