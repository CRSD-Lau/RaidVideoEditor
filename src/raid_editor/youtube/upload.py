"""Build a YouTube package and perform an explicitly approved resumable upload."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from googleapiclient.http import MediaFileUpload  # type: ignore[import-untyped]

from raid_editor.config.models import ProjectConfig
from raid_editor.models import TimelineDocument
from raid_editor.util.paths import atomic_write_json, atomic_write_text, ensure_directory

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
_RETRIABLE_STATUS_CODES = {500, 502, 503, 504}
_MAX_RETRIES = 8
_MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024


class YouTubeUploadError(RuntimeError):
    """Expected, user-actionable YouTube package or upload failure."""


@dataclass(frozen=True)
class YouTubePackage:
    root: Path
    video: Path
    metadata: Path
    description: Path
    chapters: Path
    thumbnail: Path
    manifest: Path


@dataclass(frozen=True)
class YouTubeUploadResult:
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


def _default_title(config: ProjectConfig) -> str:
    raid = config.project.raid or config.project.name
    raid_date = _display_date(config.project.raid_date)
    suffix = f" | {raid_date}" if raid_date else ""
    return f"Pizza Warriors — {raid} Full Raid Clear{suffix}"[:100]


def _chapter_lines(config: ProjectConfig, timeline: TimelineDocument) -> list[str]:
    intro = (
        config.preview.presentation.intro_seconds
        if config.preview.presentation is not None
        else 0.0
    )
    rows: list[tuple[float, str]] = []
    if intro > 0:
        rows.append((0.0, "Intro"))
    rows.extend((intro + clip.timeline_in, clip.label) for clip in timeline.clips)
    outro = (
        config.preview.presentation.outro_seconds
        if config.preview.presentation is not None
        else 0.0
    )
    if outro > 0:
        rows.append((intro + timeline.duration_seconds, "Outro"))
    return [f"{_timestamp(start)} {label}" for start, label in rows]


def _default_description(config: ProjectConfig, chapters: list[str]) -> str:
    raid = config.project.raid or "the raid"
    raid_date = _display_date(config.project.raid_date)
    date_line = f"Recorded {raid_date}." if raid_date else ""
    lines = [
        f"Pizza Warriors clear {raid} with the winning boss takes in chronological order.",
        date_line,
        "",
        "Chapters",
        *chapters,
        "",
        "Final edit uses game audio only; Discord and microphone tracks are excluded.",
        "",
        "#WorldofWarcraft #WrathoftheLichKing #IcecrownCitadel",
    ]
    return "\n".join(lines).strip()


def _create_thumbnail(video: Path, destination: Path) -> None:
    for quality in (2, 4, 6, 8):
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "1.2",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black",
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
) -> YouTubePackage:
    if not video.is_file():
        raise YouTubeUploadError(f"Approved final master does not exist: {video}")
    root = ensure_directory(destination)
    chapters = _chapter_lines(config, timeline)
    title = config.youtube.title or _default_title(config)
    description = config.youtube.description or _default_description(config, chapters)
    attribution_report = destination.parent / "reports" / "youtube-attribution.txt"
    attribution = (
        attribution_report.read_text(encoding="utf-8").strip()
        if attribution_report.is_file()
        else ""
    )
    if attribution:
        description = f"{description}\n\nMusic attribution\n{attribution}"
    if len(description) > 5000:
        raise YouTubeUploadError(
            "YouTube description exceeds 5,000 characters after required attribution"
        )
    metadata_payload = {
        "title": title,
        "description": description,
        "tags": config.youtube.tags,
        "category_id": config.youtube.category_id,
        "privacy_status": config.youtube.privacy_status,
        "made_for_kids": config.youtube.made_for_kids,
    }
    metadata = root / "metadata.json"
    description_path = root / "description.md"
    chapters_path = root / "chapters.txt"
    thumbnail = root / "thumbnail-source.jpg"
    manifest = root / "upload-manifest.json"
    atomic_write_json(metadata, metadata_payload)
    atomic_write_text(description_path, description + "\n")
    atomic_write_text(chapters_path, "\n".join(chapters) + "\n")
    atomic_write_text(
        root / "title-options.md",
        "# YouTube Title\n\n"
        f"1. {title}\n"
        f"2. Pizza Warriors ICC Full Clear — {_display_date(config.project.raid_date)}\n",
    )
    atomic_write_text(root / "video-source.txt", str(video.resolve()) + "\n")
    atomic_write_text(
        root / "attribution.txt",
        (attribution + "\n") if attribution else "No third-party music is used in this edit.\n",
    )
    atomic_write_text(
        root / "upload-checklist.md",
        "# YouTube Upload Checklist\n\n"
        "- [x] Final render approved and validated\n"
        "- [ ] Full source-file hash will be checked immediately before upload\n"
        "- [x] Microphone and Discord tracks excluded\n"
        f"- [x] Requested upload visibility: {config.youtube.privacy_status.title()}\n"
        "- [ ] Review title, description, chapters, and thumbnail\n"
        "- [ ] Confirm YouTube processing completed before changing visibility\n",
    )
    if not thumbnail.is_file():
        _create_thumbnail(video, thumbnail)
    _validate_thumbnail(thumbnail)
    return YouTubePackage(
        root=root,
        video=video,
        metadata=metadata,
        description=description_path,
        chapters=chapters_path,
        thumbnail=thumbnail,
        manifest=manifest,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _credentials(config: ProjectConfig) -> Any:
    client_secrets = config.youtube.client_secrets
    token = config.youtube.token
    if client_secrets is None or token is None:
        raise YouTubeUploadError("YouTube client_secrets and token paths are not configured")
    if not client_secrets.is_file():
        raise YouTubeUploadError(
            f"YouTube OAuth client file is missing: {client_secrets}. "
            "Create a Desktop app client in Google Auth Platform first."
        )
    credentials: Any | None = None
    if token.is_file():
        try:
            credentials = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                str(token), [YOUTUBE_UPLOAD_SCOPE]
            )
        except (OSError, ValueError, json.JSONDecodeError):
            credentials = None
    if credentials is not None and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except GoogleAuthError:
            credentials = None
    if credentials is None or not credentials.valid:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets), [YOUTUBE_UPLOAD_SCOPE]
            )
            credentials = flow.run_local_server(
                host="localhost",
                port=0,
                open_browser=True,
                authorization_prompt_message="Opening Google authorization in your browser...",
                success_message="YouTube authorization complete. You may close this window.",
            )
        except (GoogleAuthError, OSError, ValueError) as exc:
            raise YouTubeUploadError("YouTube authorization could not be completed") from exc
    ensure_directory(token.parent)
    atomic_write_text(token, credentials.to_json())
    return credentials


def upload_youtube_video(
    config: ProjectConfig,
    package: YouTubePackage,
    *,
    approved: bool,
    progress: Callable[[int], None] | None = None,
) -> YouTubeUploadResult:
    if not approved:
        raise YouTubeUploadError(
            "YouTube upload requires explicit approval; rerun with --approved after reviewing "
            "the generated metadata package"
        )
    if not config.youtube.enabled:
        raise YouTubeUploadError("YouTube upload is disabled in the project configuration")
    metadata = json.loads(package.metadata.read_text(encoding="utf-8"))
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
        notifySubscribers=False,
        body={
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": metadata["category_id"],
            },
            "status": {
                "privacyStatus": metadata["privacy_status"],
                "selfDeclaredMadeForKids": metadata["made_for_kids"],
                "embeddable": True,
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
