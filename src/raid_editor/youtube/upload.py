"""Build a YouTube package and perform an explicitly approved resumable upload."""

from __future__ import annotations

import hashlib
import html
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from googleapiclient.http import MediaFileUpload  # type: ignore[import-untyped]

from raid_editor.classification.difficulty import (
    RaidProgress,
    scoreline_title_prefix,
    summarize_raid_progress,
)
from raid_editor.config.models import ProjectConfig
from raid_editor.models import DifficultyMode, PullCandidate, TimelineDocument
from raid_editor.util.paths import atomic_write_json, atomic_write_text, ensure_directory

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
_RETRIABLE_STATUS_CODES = {500, 502, 503, 504}
_MAX_RETRIES = 8
_MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
_ThumbnailLayout = Literal[
    "lower_third",
    "compact_badge",
    "boss_action",
    "guild_story",
]


class YouTubeUploadError(RuntimeError):
    """Expected, user-actionable YouTube package or upload failure."""


@dataclass(frozen=True)
class YouTubePackage:
    """Reference all locally generated, reviewable upload-package artifacts."""

    root: Path
    video: Path
    metadata: Path
    description: Path
    chapters: Path
    thumbnail: Path
    thumbnail_candidates: tuple[Path, ...]
    thumbnail_mobile_preview: Path
    studio_details: Path
    manifest: Path
    analytics_plan: Path
    playlist_plan: Path


@dataclass(frozen=True)
class YouTubeUploadResult:
    """Record the uploaded video identity, visibility, and thumbnail outcome."""

    video_id: str
    url: str
    privacy_status: str
    skipped_existing: bool = False
    thumbnail_applied: bool = False
    thumbnail_error: str | None = None


def _timestamp(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _display_date(value: date | None) -> str:
    if value is None:
        return ""
    return f"{value:%B} {value.day}, {value.year}"


def _youtube_text(value: str) -> str:
    """Normalize punctuation for the user's plain, human YouTube house style."""

    return value.replace("—", "-").replace("–", "-")


def _default_title(config: ProjectConfig, progress: RaidProgress) -> str:
    expansion = config.project.expansion
    if expansion is None:
        game_terms = "World of Warcraft"
    elif "wrath" in expansion.casefold():
        game_terms = "WoW WotLK"
    else:
        game_terms = f"WoW {expansion}"
    prefix = scoreline_title_prefix(
        progress,
        raid_name=config.project.raid,
        settings=config.difficulty,
    )
    raid_date = config.project.raid_date
    date_suffix = f" | {raid_date:%b} {raid_date.day}" if raid_date is not None else ""
    return _youtube_text(f"{prefix} | Pizza Warriors {game_terms}{date_suffix}")[:100]


def _chapter_lines(
    config: ProjectConfig,
    timeline: TimelineDocument,
    pulls: list[PullCandidate],
) -> list[str]:
    intro = (
        config.preview.presentation.intro_seconds
        if config.preview.presentation is not None
        else 0.0
    )
    rows: list[tuple[float, str]] = []
    if intro > 0:
        rows.append((0.0, "Intro"))
    difficulty_by_encounter = {
        (pull.encounter or "").casefold(): pull.difficulty
        for pull in pulls
        if pull.encounter is not None
    }
    for clip in timeline.clips:
        label = _youtube_text(clip.label)
        difficulty: DifficultyMode | None = (
            clip.difficulty if clip.difficulty != "UNKNOWN" else None
        )
        encounter_key = (clip.encounter or label).casefold()
        difficulty = difficulty or difficulty_by_encounter.get(encounter_key)
        if difficulty is None and "gunship" in label.casefold():
            difficulty = next(
                (
                    pull.difficulty
                    for pull in pulls
                    if pull.encounter and "gunship" in pull.encounter.casefold()
                ),
                None,
            )
        if difficulty and difficulty != "UNKNOWN":
            label += " (Heroic)" if difficulty.endswith("H") else " (Normal)"
        rows.append((intro + clip.timeline_in, label))
    outro = (
        config.preview.presentation.outro_seconds
        if config.preview.presentation is not None
        else 0.0
    )
    if outro > 0:
        rows.append((intro + timeline.duration_seconds, "Outro"))
    return [f"{_timestamp(start)} {label}" for start, label in rows]


def _default_description(
    config: ProjectConfig,
    chapters: list[str],
    progress: RaidProgress,
) -> str:
    raid = config.project.raid or "the raid"
    game = config.project.game or config.youtube.game_title or "World of Warcraft"
    expansion = config.project.expansion
    game_line = f"{game}: {expansion}" if expansion else game
    raid_date = _display_date(config.project.raid_date)
    hashtags = " ".join(config.youtube.hashtags)
    edit_summary = (
        "This full clear keeps the clean kills and cuts the wipes and long resets, "
        "so you can watch the run from start to finish without the downtime."
        if progress.full_clear
        else "This raid edit keeps the clean kills and cuts the wipes and long resets, "
        "so you can watch the confirmed progress without the downtime."
    )
    lines = [
        f"Pizza Warriors take on {raid} in {game_line}, with every winning boss pull "
        "from our raid night.",
        "",
        edit_summary,
        "",
        f"Raid progress: {progress.bosses_killed}/{progress.expected_bosses} bosses, "
        f"including {progress.heroic_kills} confirmed Heroic kills.",
        "",
        "Thanks for watching. Subscribe for more Pizza Warriors Friday raid nights, "
        "full boss clears, and World of Warcraft videos.",
        "",
        "Boss chapters",
        *chapters,
        "",
        f"Recorded: {raid_date}" if raid_date else "",
        f"Game: {config.youtube.game_title or game}",
        f"Raid: {raid}",
        "",
        hashtags,
    ]
    return _youtube_text("\n".join(lines).strip())


def _escape_drawtext(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def _create_thumbnail(
    video: Path,
    destination: Path,
    *,
    timestamp: float = 1.2,
    headline: str = "",
    subheadline: str = "",
    layout: _ThumbnailLayout = "lower_third",
) -> None:
    filters = [
        "scale=1280:720:force_original_aspect_ratio=decrease",
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black",
    ]
    if layout == "compact_badge" and headline:
        filters.extend(
            [
                "drawbox=x=902:y=46:w=330:h=158:color=0x06131F@0.90:t=fill",
                "drawbox=x=902:y=46:w=330:h=158:color=0x55DFF8@0.95:t=3",
                "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
                f"text='{_escape_drawtext(headline)}':fontcolor=white:fontsize=42:"
                "x=902+(330-text_w)/2:y=68",
            ]
        )
        if subheadline:
            filters.append(
                "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
                f"text='{_escape_drawtext(subheadline)}':fontcolor=0x55DFF8:fontsize=34:"
                "x=902+(330-text_w)/2:y=132"
            )
    elif layout == "boss_action" and headline:
        filters.extend(
            [
                "drawbox=x=42:y=42:w=560:h=132:color=0x06131F@0.88:t=fill",
                "drawbox=x=42:y=42:w=8:h=132:color=0x55DFF8@1.0:t=fill",
                "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
                f"text='{_escape_drawtext(headline)}':fontcolor=white:fontsize=42:"
                "x=74:y=62",
            ]
        )
        if subheadline:
            filters.append(
                "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
                f"text='{_escape_drawtext(subheadline)}':fontcolor=0x55DFF8:fontsize=28:"
                "x=74:y=120"
            )
    elif layout == "guild_story" and headline:
        filters.extend(
            [
                "drawbox=x=46:y=472:w=500:h=178:color=0x06131F@0.88:t=fill",
                "drawbox=x=46:y=472:w=500:h=178:color=0x55DFF8@0.95:t=3",
                "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
                f"text='{_escape_drawtext(headline)}':fontcolor=0x55DFF8:fontsize=32:"
                "x=74:y=500",
            ]
        )
        if subheadline:
            filters.append(
                "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
                f"text='{_escape_drawtext(subheadline)}':fontcolor=white:fontsize=44:"
                "x=74:y=554"
            )
    elif headline:
        filters.extend(
            [
                "drawbox=x=0:y=500:w=1280:h=220:color=black@0.72:t=fill",
                "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
                f"text='{_escape_drawtext(headline)}':fontcolor=white:fontsize=58:"
                "x=(w-text_w)/2:y=530",
            ]
        )
    if subheadline and layout == "lower_third":
        filters.append(
            "drawtext=fontfile='C\\:/Windows/Fonts/segoeui.ttf':"
            f"text='{_escape_drawtext(subheadline)}':fontcolor=0xF2C45A:fontsize=30:"
            "x=(w-text_w)/2:y=615"
        )
    for quality in (2, 4, 6, 8):
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, timestamp):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            ",".join(filters),
            "-q:v",
            str(quality),
            "-y",
            str(destination),
        ]
        try:
            subprocess.run(command, check=True)
        except FileNotFoundError as exc:
            raise YouTubeUploadError("ffmpeg is required to create the thumbnail source") from exc
        except subprocess.CalledProcessError as exc:
            raise YouTubeUploadError("Could not create the YouTube thumbnail source") from exc
        if destination.stat().st_size <= _MAX_THUMBNAIL_BYTES:
            return
    raise YouTubeUploadError("Generated YouTube thumbnail exceeds the 2 MB API limit")


def _create_thumbnail_variants(
    config: ProjectConfig,
    timeline: TimelineDocument,
    pulls: list[PullCandidate],
    progress: RaidProgress,
    video: Path,
    destination: Path,
) -> tuple[Path, ...]:
    intro = config.preview.presentation.intro_seconds if config.preview.presentation else 0.0
    raid_label = config.difficulty.title_raid_abbreviation or "ICC"
    raid_size = f" {progress.raid_size}M" if progress.raid_size is not None else ""
    clean_headline = f"{raid_label}{raid_size}"
    clean_scoreline = (
        f"{progress.bosses_killed}/{progress.expected_bosses} | {progress.heroic_kills}HC"
    )
    first_heroic = next(
        (
            clip
            for clip in timeline.clips
            if clip.difficulty.endswith("H")
            or any(
                pull.encounter
                and pull.difficulty.endswith("H")
                and (
                    pull.encounter.casefold() == (clip.encounter or clip.label).casefold()
                    or (
                        "gunship" in pull.encounter.casefold()
                        and "gunship" in (clip.encounter or clip.label).casefold()
                    )
                )
                for pull in pulls
            )
        ),
        None,
    )
    feature_clip = first_heroic or timeline.clips[0]
    feature_headline = feature_clip.label.upper()[:25]
    feature_subheadline = "HEROIC BOSS FIGHT" if first_heroic is not None else "RAID HIGHLIGHT"
    final_clip = timeline.clips[-1]
    final_duration = final_clip.source_out - final_clip.source_in
    final_thumbnail_offset = min(
        max(final_duration * 0.75, final_duration - 30.0),
        max(0.2, final_duration - 0.5),
    )
    variants: tuple[tuple[float, str, str, _ThumbnailLayout], ...] = (
        (
            max(0.2, min(1.5, intro / 2 if intro else 1.2)),
            clean_headline,
            clean_scoreline,
            "compact_badge",
        ),
        (
            intro
            + feature_clip.timeline_in
            + (feature_clip.source_out - feature_clip.source_in) / 2,
            feature_headline,
            feature_subheadline,
            "boss_action",
        ),
        (
            intro + final_clip.timeline_in + final_thumbnail_offset,
            "PIZZA WARRIORS",
            final_clip.label.upper()[:25],
            "guild_story",
        ),
    )
    paths: list[Path] = []
    for number, (timestamp, headline, subheadline, layout) in enumerate(
        variants[: config.youtube.thumbnail_variants], start=1
    ):
        path = destination / f"thumbnail-{number:02d}.jpg"
        _create_thumbnail(
            video,
            path,
            timestamp=timestamp,
            headline=headline,
            subheadline=subheadline,
            layout=layout,
        )
        _validate_thumbnail(path)
        paths.append(path)
    return tuple(paths)


def _write_thumbnail_mobile_preview(candidates: tuple[Path, ...], destination: Path) -> None:
    """Show each candidate at phone-feed sizes without altering source artwork."""

    cards = "".join(
        "<article>"
        f"<h2>Variant {index}</h2>"
        f"<img src='{html.escape(path.as_uri())}' alt='Thumbnail variant {index}'>"
        "<div class='phone-row'><img class='feed-320' src='"
        f"{html.escape(path.as_uri())}' alt='320 by 180 mobile preview {index}'>"
        "<span>320x180 preview<br><small>Mobile feed</small></span></div>"
        "<div class='phone-row'><img class='feed-160' src='"
        f"{html.escape(path.as_uri())}' alt='160 by 90 compact preview {index}'>"
        "<span>160x90 preview<br><small>Compact feed simulation</small></span></div>"
        "</article>"
        for index, path in enumerate(candidates, start=1)
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Mobile thumbnail review</title><style>
:root{{color-scheme:dark;font-family:Segoe UI,Arial,sans-serif;background:#08131c;color:#eef9ff}}
body{{max-width:1100px;margin:auto;padding:24px}}main{{display:grid;gap:24px;
grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}}
article{{background:#102431;border:1px solid #2a7896;border-radius:16px;padding:16px}}
article>img{{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:10px}}
.phone-row{{display:flex;gap:10px;align-items:center;margin-top:18px;font-size:13px}}
.phone-row img{{object-fit:cover;border-radius:6px;flex:none}}
.feed-320{{width:320px;height:180px;max-width:62%}}.feed-160{{width:160px;height:90px}}
small{{color:#9cb8c6}}p{{color:#b9d6e2}}</style></head><body>
<h1>Mobile thumbnail review</h1>
<p>Judge legibility, subject clarity, and curiosity at feed size. Variants intentionally test
different packaging ideas, not near-identical decorations.</p><main>{cards}</main></body></html>"""
    atomic_write_text(destination, page)


def _validate_thumbnail(path: Path) -> None:
    if not path.is_file():
        raise YouTubeUploadError(f"YouTube thumbnail does not exist: {path}")
    if path.stat().st_size > _MAX_THUMBNAIL_BYTES:
        raise YouTubeUploadError("YouTube thumbnail exceeds the 2 MB API limit")


def write_youtube_package(
    config: ProjectConfig,
    timeline: TimelineDocument,
    video: Path,
    destination: Path,
    *,
    pulls: list[PullCandidate] | None = None,
) -> YouTubePackage:
    """Generate reviewable metadata, chapters, thumbnails, and upload plans.

    Args:
        config: Validated project and YouTube settings.
        timeline: Approved edit timeline used for chapters and thumbnails.
        video: Validated final master that would be transmitted.
        destination: Managed YouTube package directory.
        pulls: Optional difficulty-labelled pulls used for scorelines and chapters.

    Returns:
        Paths and metadata required by a later approved upload.

    Raises:
        YouTubeUploadError: If the master/timeline is missing, automatic title
            evidence is unresolved, metadata is invalid, or thumbnails fail.
    """

    if not video.is_file():
        raise YouTubeUploadError(f"Approved final master does not exist: {video}")
    if not timeline.clips:
        raise YouTubeUploadError("The approved timeline contains no clips")
    root = ensure_directory(destination)
    pull_list = pulls or []
    progress = summarize_raid_progress(
        pull_list,
        raid_name=config.project.raid,
        settings=config.difficulty,
    )
    chapters = _chapter_lines(config, timeline, pull_list)
    automatic_title = config.youtube.title is None
    title = _youtube_text(config.youtube.title or _default_title(config, progress))
    description = _youtube_text(
        config.youtube.description or _default_description(config, chapters, progress)
    )
    attribution_report = destination.parent / "reports" / "youtube-attribution.txt"
    attribution = (
        attribution_report.read_text(encoding="utf-8").strip()
        if attribution_report.is_file()
        else ""
    )
    if attribution:
        description = _youtube_text(f"{description}\n\nMusic attribution\n{attribution}")
    if len(description) > 5000:
        raise YouTubeUploadError(
            "YouTube description exceeds 5,000 characters after required attribution"
        )
    metadata_payload = {
        "title": title,
        "description": description,
        "tags": config.youtube.tags,
        "hashtags": config.youtube.hashtags,
        "category_id": config.youtube.category_id,
        "category_name": config.youtube.category_name,
        "game_title": config.youtube.game_title,
        "game_rating": config.youtube.game_rating,
        "default_language": config.youtube.default_language,
        "privacy_status": config.youtube.privacy_status,
        "made_for_kids": config.youtube.made_for_kids,
        "age_restricted": config.youtube.age_restricted,
        "contains_synthetic_media": config.youtube.contains_synthetic_media,
        "license": config.youtube.license,
        "allow_embedding": config.youtube.allow_embedding,
        "notify_subscribers": config.youtube.notify_subscribers,
        "api_project_verified_for_public": (config.youtube.api_project_verified_for_public),
        "recording_date": (
            config.project.raid_date.isoformat() if config.project.raid_date is not None else None
        ),
        "raid_progress": {
            "bosses_killed": progress.bosses_killed,
            "expected_bosses": progress.expected_bosses,
            "raid_size": progress.raid_size,
            "heroic_kills": progress.heroic_kills,
            "unknown_difficulty": list(progress.unknown_difficulty),
        },
        "automatic_title": automatic_title,
        "title_ready": (
            not automatic_title
            or not config.difficulty.require_confirmed_for_auto_title
            or progress.title_ready
        ),
    }
    metadata = root / "metadata.json"
    description_path = root / "description.md"
    chapters_path = root / "chapters.txt"
    thumbnail = root / "thumbnail-source.jpg"
    analytics_plan = root / "analytics-plan.md"
    playlist_plan = root / "playlist-plan.md"
    studio_details = root / "studio-details.md"
    manifest = root / "upload-manifest.json"
    atomic_write_json(metadata, metadata_payload)
    atomic_write_text(description_path, description + "\n")
    atomic_write_text(chapters_path, "\n".join(chapters) + "\n")
    title_prefix = scoreline_title_prefix(
        progress,
        raid_name=config.project.raid,
        settings=config.difficulty,
    )
    atomic_write_text(
        root / "title-options.md",
        "# YouTube Title\n\n"
        f"1. {title}\n"
        f"2. {title_prefix} | Pizza Warriors ICC Raid\n"
        f"3. {title_prefix} | Full WoW WotLK Raid\n",
    )
    atomic_write_text(root / "video-source.txt", str(video.resolve()) + "\n")
    atomic_write_text(
        root / "attribution.txt",
        (attribution + "\n") if attribution else "No third-party music is used in this edit.\n",
    )
    atomic_write_text(
        studio_details,
        "# YouTube Studio Details\n\n"
        f"- Visibility: {config.youtube.privacy_status.title()}\n"
        f"- Category: {config.youtube.category_name} ({config.youtube.category_id})\n"
        f"- Game title: {config.youtube.game_title or 'Not set'}\n"
        f"- Requested game rating: {config.youtube.game_rating or 'Not set'}\n"
        "- Game rating note: Current YouTube Studio may not expose a separate editable "
        "game-rating field; never substitute a different rating.\n"
        f"- Video language: {config.youtube.default_language}\n"
        f"- Made for kids: {'Yes' if config.youtube.made_for_kids else 'No'}\n"
        f"- Age restricted: {'Yes' if config.youtube.age_restricted else 'No'}\n"
        "- Altered or synthetic content: "
        f"{'Yes' if config.youtube.contains_synthetic_media else 'No'}\n"
        f"- License: {config.youtube.license}\n"
        f"- Allow embedding: {'Yes' if config.youtube.allow_embedding else 'No'}\n"
        "- Comments: Use channel default\n"
        "- Chapters: Use the manual boss chapters in the description\n"
        f"- Weekly playlist: {config.youtube.playlist_title or 'Not configured'}\n"
        f"- Thumbnail candidates: {config.youtube.thumbnail_variants}\n"
        "- End screen: Add Subscribe plus Best for viewer over the 5-second outro\n",
    )
    public_route = (
        "YouTube Studio"
        if config.youtube.privacy_status == "public"
        and not config.youtube.api_project_verified_for_public
        else "YouTube Data API"
    )
    atomic_write_text(
        root / "upload-checklist.md",
        "# YouTube Upload Checklist\n\n"
        "- [x] Final render approved and validated\n"
        "- [ ] Full source-file hash will be checked immediately before upload\n"
        "- [x] Microphone and Discord tracks excluded\n"
        f"- [x] Requested upload visibility: {config.youtube.privacy_status.title()}\n"
        f"- [x] Required publishing route: {public_route}\n"
        f"- [x] Category: {config.youtube.category_name}\n"
        f"- [x] Game title: {config.youtube.game_title or 'Not set'}\n"
        f"- [x] Requested game rating: {config.youtube.game_rating or 'Not set'}\n"
        "- [x] If Studio has no separate game-rating field, leave it unset\n"
        "- [x] Human copy contains no em dash\n"
        f"- [{'x' if metadata_payload['title_ready'] else ' '}] "
        "Difficulty scoreline is fully confirmed\n"
        f"- [x] Generated {config.youtube.thumbnail_variants} thumbnail candidates\n"
        f"- [ ] Add to playlist: {config.youtube.playlist_title or 'Not configured'}\n"
        "- [ ] Start Thumbnail Test & Compare when the video has enough impressions\n"
        "- [ ] Review title, description, chapters, and thumbnail\n"
        "- [ ] Confirm SD checks finish before publishing\n"
        "- [ ] Confirm 1440p processing completes after publishing\n"
        "- [ ] Add Subscribe plus Best for viewer to the 5-second outro\n",
    )
    candidates = _create_thumbnail_variants(
        config,
        timeline,
        pull_list,
        progress,
        video,
        root,
    )
    selected = candidates[config.youtube.selected_thumbnail_variant - 1]
    mobile_preview = root / "thumbnail-mobile-preview.html"
    _write_thumbnail_mobile_preview(candidates, mobile_preview)
    concept_names = (
        ("clean_scoreline", "Instant raid size, progress, and Heroic context"),
        ("boss_action", "Encounter curiosity from a strong in-fight frame"),
        ("guild_story", "Pizza Warriors identity and final-boss payoff"),
    )
    concepts = [
        {
            "variant": index,
            "file": path.name,
            "concept": concept_names[index - 1][0],
            "hypothesis": concept_names[index - 1][1],
            "provenance": "frame extracted from the validated final master",
            "sha256": _file_sha256(path),
        }
        for index, path in enumerate(candidates, start=1)
    ]
    atomic_write_json(root / "thumbnail-concepts.json", concepts)
    shutil.copy2(selected, thumbnail)
    _validate_thumbnail(thumbnail)
    atomic_write_text(
        root / "thumbnail-test-plan.md",
        "# Thumbnail Test Plan\n\n"
        + f"- Variant 1: `{candidates[0].name}` - clean scoreline badge; tests instant "
        + f"context; SHA-256 `{concepts[0]['sha256']}`.\n"
        + (
            f"- Variant 2: `{candidates[1].name}` - boss action; tests encounter "
            f"curiosity; SHA-256 `{concepts[1]['sha256']}`.\n"
            if len(candidates) > 1
            else ""
        )
        + (
            f"- Variant 3: `{candidates[2].name}` - guild story; tests Pizza Warriors "
            f"identity; SHA-256 `{concepts[2]['sha256']}`.\n"
            if len(candidates) > 2
            else ""
        )
        + "\nReview `thumbnail-mobile-preview.html` before selecting a candidate.\n"
        + "Run one interpretable YouTube Studio Test & Compare only after the "
        + "public-upload gate.\n",
    )
    atomic_write_text(
        playlist_plan,
        "# Playlist Plan\n\n"
        f"- Automatic add requested: {'yes' if config.youtube.playlist_auto_add else 'no'}\n"
        f"- Playlist ID: {config.youtube.playlist_id or 'resolve by exact title'}\n"
        f"- Playlist title: {config.youtube.playlist_title or 'not configured'}\n"
        "- Add only after the upload gate is approved.\n",
    )
    atomic_write_text(
        analytics_plan,
        "# Analytics Follow-up\n\n"
        "- Run the 48-hour report after audience-retention data becomes available.\n"
        "- Run the 7-day report for a more stable comparison.\n"
        "- Record Studio impressions and thumbnail CTR manually because the lightweight "
        "Analytics API report does not expose those thumbnail metrics.\n",
    )
    return YouTubePackage(
        root=root,
        video=video,
        metadata=metadata,
        description=description_path,
        chapters=chapters_path,
        thumbnail=thumbnail,
        thumbnail_candidates=candidates,
        thumbnail_mobile_preview=mobile_preview,
        studio_details=studio_details,
        manifest=manifest,
        analytics_plan=analytics_plan,
        playlist_plan=playlist_plan,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def record_publication_confirmation(
    config: ProjectConfig,
    package: YouTubePackage,
    *,
    video_id: str,
    maximum_quality: str,
    approved: bool,
    report_destination: Path,
) -> dict[str, object]:
    """Record an operator-verified public watch page for Studio publishing."""

    if not approved:
        raise YouTubeUploadError("Publication confirmation requires explicit approval")
    if not config.youtube.enabled:
        raise YouTubeUploadError("YouTube integration is disabled in the project configuration")
    if config.youtube.privacy_status != "public":
        raise YouTubeUploadError(
            "Set youtube.privacy_status to public before recording public verification"
        )
    normalized_id = video_id.strip()
    if not normalized_id or any(character.isspace() for character in normalized_id):
        raise YouTubeUploadError("A valid whitespace-free YouTube video ID is required")
    quality = maximum_quality.strip().casefold()
    if quality not in {"1440p", "1440p60"}:
        raise YouTubeUploadError("Confirm the final 1440p or 1440p60 playback quality")
    metadata = json.loads(package.metadata.read_text(encoding="utf-8"))
    if metadata.get("privacy_status") != "public":
        raise YouTubeUploadError("Regenerate the YouTube package with public visibility first")
    if metadata.get("title_ready") is False:
        raise YouTubeUploadError("Cannot confirm publication while the title scoreline is blocked")
    source_sha256 = _file_sha256(package.video)
    metadata_sha256 = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    existing: dict[str, object] = {}
    if package.manifest.is_file():
        try:
            loaded = json.loads(package.manifest.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing_id = existing.get("video_id")
    if existing_id is not None and str(existing_id) != normalized_id:
        raise YouTubeUploadError(
            f"Upload manifest already belongs to YouTube video {existing_id}; "
            f"refusing {normalized_id}"
        )
    payload: dict[str, object] = {
        **existing,
        "video_id": normalized_id,
        "url": f"https://youtu.be/{normalized_id}",
        "privacy_status": "public",
        "source": str(package.video.resolve()),
        "source_sha256": source_sha256,
        "metadata_sha256": metadata_sha256,
        "title": metadata["title"],
        "upload_complete": True,
        "publication_confirmation_method": "operator_verified_watch_page",
        "public_playback_confirmed": True,
        "maximum_quality_confirmed": quality,
        "publication_verified_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_json(package.manifest, payload)
    atomic_write_text(
        report_destination,
        "# YouTube Publication Verification\n\n"
        f"- Video: {payload['url']}\n"
        "- Public playback: operator confirmed\n"
        f"- Maximum quality: {quality}\n"
        f"- Source SHA-256: `{source_sha256}`\n"
        f"- Metadata SHA-256: `{metadata_sha256}`\n"
        "- Remote verification: not performed by the CLI\n",
    )
    return payload


def youtube_credentials(
    config: ProjectConfig,
    *,
    scopes: list[str],
    token: Path | None = None,
) -> Any:
    """Load or authorize a purpose-specific desktop OAuth credential.

    Args:
        config: Validated YouTube OAuth paths.
        scopes: Exact least-purpose scopes requested by the caller.
        token: Optional purpose-specific token path override.

    Returns:
        Refreshed or newly authorized Google credentials.

    Raises:
        YouTubeUploadError: If OAuth paths are absent or invalid, or authorization fails.
    """

    client_secrets = config.youtube.client_secrets
    token_path = token or config.youtube.token
    if client_secrets is None or token_path is None:
        raise YouTubeUploadError("YouTube client_secrets and token paths are not configured")
    if not client_secrets.is_file():
        raise YouTubeUploadError(
            f"YouTube OAuth client file is missing: {client_secrets}. "
            "Create a Desktop app client in Google Auth Platform first."
        )
    credentials: Any | None = None
    if token_path.is_file():
        try:
            credentials = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                str(token_path), scopes
            )
        except (OSError, ValueError, json.JSONDecodeError):
            credentials = None
    if credentials is not None and not credentials.has_scopes(scopes):
        credentials = None
    if credentials is not None and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except GoogleAuthError:
            credentials = None
    if credentials is None or not credentials.valid:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes)
            credentials = flow.run_local_server(
                host="localhost",
                port=0,
                open_browser=True,
                authorization_prompt_message="Opening Google authorization in your browser...",
                success_message="YouTube authorization complete. You may close this window.",
            )
        except (GoogleAuthError, OSError, ValueError) as exc:
            raise YouTubeUploadError("YouTube authorization could not be completed") from exc
    ensure_directory(token_path.parent)
    atomic_write_text(token_path, credentials.to_json())
    return credentials


def _credentials(config: ProjectConfig) -> Any:
    return youtube_credentials(config, scopes=[YOUTUBE_UPLOAD_SCOPE])


def upload_youtube_video(
    config: ProjectConfig,
    package: YouTubePackage,
    *,
    approved: bool,
    progress: Callable[[int], None] | None = None,
) -> YouTubeUploadResult:
    """Resumably upload an explicitly approved final master to YouTube.

    Args:
        config: Validated upload, visibility, and OAuth settings.
        package: Reviewed local upload package.
        approved: Explicit operator approval for transmission.
        progress: Optional callback receiving cumulative uploaded bytes.

    Returns:
        YouTube identity and thumbnail result. A prior matching manifest is
        returned without duplicate transmission.

    Raises:
        YouTubeUploadError: If approval, validation, visibility, OAuth, upload,
            thumbnail, or duplicate-safety requirements fail.
    """

    if not approved:
        raise YouTubeUploadError(
            "YouTube upload requires explicit approval; rerun with --approved after reviewing "
            "the generated metadata package"
        )
    if not config.youtube.enabled:
        raise YouTubeUploadError("YouTube upload is disabled in the project configuration")
    if (
        config.youtube.privacy_status == "public"
        and not config.youtube.api_project_verified_for_public
    ):
        raise YouTubeUploadError(
            "This Google API project is not verified for Public uploads. Use the generated "
            "YouTube Studio package so YouTube can publish the video publicly."
        )
    metadata = json.loads(package.metadata.read_text(encoding="utf-8"))
    if metadata.get("title_ready") is False:
        unknown = metadata.get("raid_progress", {}).get("unknown_difficulty", [])
        raise YouTubeUploadError(
            "Automatic title is blocked until every winning pull difficulty is confirmed: "
            + ", ".join(str(item) for item in unknown)
        )
    source_sha256 = _file_sha256(package.video)
    metadata_sha256 = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if package.manifest.is_file():
        try:
            existing = json.loads(package.manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if existing.get("source_sha256") == source_sha256 and existing.get("video_id"):
            if existing.get("metadata_sha256") != metadata_sha256:
                raise YouTubeUploadError(
                    "This exact final master is already recorded as uploaded, but the local "
                    "metadata has changed. Refusing to create a duplicate; update the existing "
                    f"YouTube video {existing['video_id']} deliberately instead."
                )
            video_id = str(existing["video_id"])
            return YouTubeUploadResult(
                video_id=video_id,
                url=f"https://youtu.be/{video_id}",
                privacy_status=str(existing.get("privacy_status", "private")),
                skipped_existing=True,
                thumbnail_applied=bool(existing.get("thumbnail_applied", False)),
                thumbnail_error=(
                    str(existing["thumbnail_error"]) if existing.get("thumbnail_error") else None
                ),
            )

    youtube = build(
        "youtube",
        "v3",
        credentials=_credentials(config),
        cache_discovery=False,
    )
    media = MediaFileUpload(
        str(package.video),
        chunksize=config.youtube.chunk_size_mib * 1024 * 1024,
        resumable=True,
        mimetype="video/mp4",
    )
    request = youtube.videos().insert(
        part="snippet,status",
        notifySubscribers=metadata["notify_subscribers"],
        body={
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": metadata["category_id"],
                "defaultLanguage": metadata["default_language"],
            },
            "status": {
                "privacyStatus": metadata["privacy_status"],
                "selfDeclaredMadeForKids": metadata["made_for_kids"],
                "containsSyntheticMedia": metadata["contains_synthetic_media"],
                "license": metadata["license"],
                "embeddable": metadata["allow_embedding"],
            },
        },
        media_body=media,
    )
    response: dict[str, Any] | None = None
    retries = 0
    last_percent = -1
    while response is None:
        try:
            upload_status, response = request.next_chunk()
            if upload_status is not None:
                percent = int(upload_status.progress() * 100)
                if percent > last_percent:
                    last_percent = percent
                    if progress is not None:
                        progress(percent)
            retries = 0
        except HttpError as exc:
            if exc.resp.status not in _RETRIABLE_STATUS_CODES:
                raise YouTubeUploadError(
                    f"YouTube rejected the upload with HTTP {exc.resp.status}"
                ) from exc
            retries += 1
        except OSError as exc:
            retries += 1
            if retries > _MAX_RETRIES:
                raise YouTubeUploadError("YouTube upload failed after network retries") from exc
        if retries:
            if retries > _MAX_RETRIES:
                raise YouTubeUploadError("YouTube upload failed after server retries")
            time.sleep(min(32, 2**retries))
    video_id = str(response["id"])
    response_status = response.get("status")
    actual_privacy = metadata["privacy_status"]
    if isinstance(response_status, dict) and response_status.get("privacyStatus"):
        actual_privacy = str(response_status["privacyStatus"])
    payload = {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "privacy_status": actual_privacy,
        "source": str(package.video.resolve()),
        "source_sha256": source_sha256,
        "metadata_sha256": metadata_sha256,
        "title": metadata["title"],
        "upload_complete": True,
        "thumbnail_applied": False,
    }
    atomic_write_json(package.manifest, payload)
    thumbnail_error: str | None = None
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(
                str(package.thumbnail),
                resumable=False,
                mimetype="image/jpeg",
            ),
        ).execute()
        payload["thumbnail_applied"] = True
    except HttpError as exc:
        thumbnail_error = f"YouTube thumbnail request failed with HTTP {exc.resp.status}"
    except OSError as exc:
        thumbnail_error = f"YouTube thumbnail request failed: {exc}"
    if thumbnail_error is not None:
        payload["thumbnail_error"] = thumbnail_error
    atomic_write_json(package.manifest, payload)
    return YouTubeUploadResult(
        video_id=video_id,
        url=payload["url"],
        privacy_status=payload["privacy_status"],
        thumbnail_applied=bool(payload["thumbnail_applied"]),
        thumbnail_error=thumbnail_error,
    )
