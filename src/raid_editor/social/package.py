"""Build deterministic platform packages from approved portrait highlights."""

from __future__ import annotations

import hashlib
import html
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from raid_editor.config.loader import project_output_dir
from raid_editor.config.models import ProjectConfig
from raid_editor.ingestion.probe import probe_media
from raid_editor.social.models import (
    ContentPillar,
    DistributionManifest,
    SocialAsset,
    SocialAudioPolicy,
    SocialMediaDetails,
    SocialPost,
    json_payload,
)
from raid_editor.util.paths import (
    atomic_write_json,
    atomic_write_text,
    ensure_directory,
    full_file_sha256,
    quick_file_fingerprint,
    slugify,
)


class SocialPackageError(RuntimeError):
    """Expected validation or package-construction failure."""


PLATFORM_ORDER = ("facebook", "instagram", "tiktok", "twitch")
DEFAULT_HANDLES = {
    "facebook": "Pizza Warriors",
    "instagram": "pizzawarriorswow",
    "tiktok": "lausudo",
    "twitch": "lausudo",
}


def _clean_title(title: str) -> str:
    clean = title.split("|")[0].replace("#Shorts", "").strip()
    return clean.rstrip(" .") or "Pizza Warriors Raid Moment"


def _content_pillar(title: str, category: str | None = None) -> ContentPillar:
    lowered = f"{title} {category or ''}".casefold()
    if "shadowmourne" in lowered:
        return "guild_milestone"
    if "enraged" in lowered:
        return "intensity"
    if "dfo" in lowered or "wtf" in lowered or "hmm" in lowered:
        return "reaction"
    if "ping pong" in lowered:
        return "raid_comms"
    if "tank" in lowered:
        return "personality"
    if category in {
        "guild_milestone",
        "intensity",
        "comedy",
        "reaction",
        "raid_comms",
        "loot",
        "personality",
    }:
        return cast(ContentPillar, category)
    return "other"


def _copy_for(title: str, platform: str) -> tuple[str, list[str]]:
    clean = _clean_title(title)
    lowered = clean.casefold()
    if "shadowmourne" in lowered:
        hook = "Pizza Warriors' first Shadowmourne. A massive guild milestone."
    elif "enraged" in lowered:
        hook = "An enraged Lich King moment from Pizza Warriors raid night."
    elif "ping pong" in lowered:
        hook = "One raid callout somehow turned into a full game of ping pong."
    elif "double dfo" in lowered:
        hook = "Double DFO. The raid-night reaction says the rest."
    elif "tank" in lowered:
        hook = "Does Lau even know how to tank? You decide."
    elif "wtf" in lowered:
        hook = "One of those raid moments where all you can say is WTF."
    elif "hmm" in lowered:
        hook = "Sometimes all you can do is stop and think about what just happened."
    else:
        hook = f"{clean}. A Pizza Warriors raid-night moment."
    tags = ["#WorldOfWarcraft", "#WotLK", "#PizzaWarriors"]
    if platform == "facebook":
        return f"{hook}\n\nWatch our complete raids through the YouTube link on our Page.", tags
    if platform == "instagram":
        return f"{hook}\n\nFull raids on YouTube. Live raid nights on Twitch. Links in bio.", tags
    if platform == "tiktok":
        return f"{hook}\n\nFull raids on YouTube. Link in bio.", tags
    return (
        f"{hook} Full weekly raids are on YouTube, and raid nights are live on Twitch.",
        [],
    )


def _copy_hash(title: str, caption: str, hashtags: list[str]) -> str:
    raw = json.dumps(
        {"title": title, "caption": caption, "hashtags": hashtags},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _idempotency_key(platform: str, handle: str, media_sha256: str, version: str) -> str:
    raw = f"{platform}\0{handle.casefold()}\0{media_sha256}\0{version}".encode()
    return hashlib.sha256(raw).hexdigest()


def _next_release_start(value: datetime | None, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    if value is not None:
        if value.tzinfo is None:
            return value.replace(tzinfo=zone)
        return value.astimezone(zone)
    now = datetime.now(zone)
    today = datetime.combine(now.date(), time(hour=19), tzinfo=zone)
    return today if now < today else today + timedelta(days=1)


def _priority(asset: SocialAsset) -> tuple[int, str]:
    lowered = asset.title.casefold()
    words = ("shadowmourne", "enraged", "wtf", "hmm", "ping pong", "double dfo", "tank")
    for index, word in enumerate(words):
        if word in lowered:
            return index, asset.content_id
    return len(words), asset.content_id


def _media_details(
    path: Path,
    *,
    provenance: str,
    quality_class: str,
    quality_exception: str | None,
) -> SocialMediaDetails:
    resolved = path.expanduser().resolve()
    probe = probe_media(resolved)
    if len(probe.video_streams) != 1 or len(probe.audio_streams) != 1:
        raise SocialPackageError(
            f"Social media requires one video and one audio stream: {resolved}"
        )
    video = probe.video_streams[0]
    audio = probe.audio_streams[0]
    if video.codec.casefold() != "h264" or audio.codec.casefold() != "aac":
        raise SocialPackageError(f"Social media must use H.264 video and AAC audio: {resolved}")
    if video.width >= video.height:
        raise SocialPackageError(f"Social media must be portrait: {resolved}")
    if quality_class == "canonical_master" and (video.width < 1080 or video.height < 1920):
        raise SocialPackageError(f"Canonical social master must be at least 1080x1920: {resolved}")
    if quality_class == "owner_recovered_derivative" and (video.width < 720 or video.height < 1280):
        raise SocialPackageError(f"Recovered derivative is below 720x1280: {resolved}")
    return SocialMediaDetails(
        path=resolved,
        sha256=full_file_sha256(resolved),
        width=video.width,
        height=video.height,
        frame_rate=video.frame_rate or 0,
        duration_seconds=probe.duration_seconds,
        video_codec=video.codec,
        pixel_format=video.pixel_format,
        audio_codec=audio.codec,
        audio_sample_rate=audio.sample_rate,
        audio_channels=audio.channels,
        provenance=provenance,  # type: ignore[arg-type]
        quality_class=quality_class,  # type: ignore[arg-type]
        quality_exception=quality_exception,
    )


def assets_from_project(config: ProjectConfig) -> list[SocialAsset]:
    """Load and revalidate the explicitly approved project highlight renders."""

    root = project_output_dir(config)
    manifest_path = root / "highlights" / "vertical" / "manifest.json"
    if not manifest_path.is_file():
        raise SocialPackageError(f"Approved highlight manifest is missing: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("approved") is not True:
        raise SocialPackageError("Highlight manifest does not record approval")
    assets: list[SocialAsset] = []
    for row in payload.get("clips", []):
        if not isinstance(row, dict) or row.get("rendered") is not True:
            raise SocialPackageError("Social packaging requires completed highlight renders")
        fingerprint = row.get("output_fingerprint")
        if not isinstance(fingerprint, dict):
            raise SocialPackageError(
                "Highlight render lacks its output fingerprint. Re-render the reviewed clip, "
                "or import legacy media through an explicit approved owned-assets JSON."
            )
        output = Path(str(row["output"]))
        if not output.is_file():
            raise SocialPackageError(f"Approved highlight output is missing: {output}")
        current_fingerprint = quick_file_fingerprint(output)
        expected_path = Path(str(fingerprint.get("path", ""))).resolve()
        if expected_path != output.resolve() or any(
            fingerprint.get(key) != value
            for key, value in current_fingerprint.items()
            if key != "path"
        ):
            raise SocialPackageError(f"Approved highlight output fingerprint has changed: {output}")
        media = _media_details(
            output,
            provenance="raid_editor_render",
            quality_class="canonical_master",
            quality_exception=None,
        )
        assets.append(
            SocialAsset(
                content_id=str(row["id"]),
                title=_clean_title(str(row["title"])),
                content_pillar=_content_pillar(str(row["title"]), str(row.get("category", ""))),
                source_highlight_id=str(row["id"]),
                media=media,
                audio_policy=SocialAudioPolicy(
                    game=True,
                    discord=True,
                    microphone=bool(row.get("microphone_included")),
                    added_music=False,
                    reviewed=True,
                ),
                approved=True,
            )
        )
    if not assets:
        raise SocialPackageError("Approved highlight manifest contains no clips")
    return assets


def assets_from_owned_manifest(path: Path) -> tuple[str, Path, list[SocialAsset]]:
    """Load an explicit local owner-recovery manifest for archive backfills."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    campaign_id = str(payload["campaign_id"])
    output_root = Path(str(payload["output_root"])).expanduser().resolve()
    assets: list[SocialAsset] = []
    for row in payload.get("assets", []):
        quality_class = str(row.get("quality_class", "owner_recovered_derivative"))
        exception = row.get("quality_exception")
        media = _media_details(
            Path(str(row["path"])),
            provenance=str(row.get("provenance", "manual_owned_import")),
            quality_class=quality_class,
            quality_exception=str(exception) if exception else None,
        )
        assets.append(
            SocialAsset(
                content_id=str(row["content_id"]),
                title=_clean_title(str(row["title"])),
                content_pillar=_content_pillar(str(row["title"]), str(row.get("category", ""))),
                source_highlight_id=row.get("source_highlight_id"),
                youtube_video_id=row.get("youtube_video_id"),
                youtube_url=row.get("youtube_url"),
                media=media,
                audio_policy=SocialAudioPolicy.model_validate(row.get("audio_policy", {})),
                approved=row.get("approved") is True,
            )
        )
    if not assets or any(not asset.approved for asset in assets):
        raise SocialPackageError("Every owned backfill asset must exist and be approved")
    return campaign_id, output_root, assets


def build_distribution_manifest(
    campaign_id: str,
    assets: list[SocialAsset],
    *,
    handles: dict[str, str] | None = None,
    start_at: datetime | None = None,
    cadence_days: int = 2,
    timezone_name: str = "America/Halifax",
    package_version: str = "1",
) -> DistributionManifest:
    """Create four platform packages per approved asset with stable identities."""

    target_handles = DEFAULT_HANDLES | (handles or {})
    start = _next_release_start(start_at, timezone_name)
    ordered = sorted(assets, key=_priority)
    release_for = {
        asset.content_id: start + timedelta(days=index * cadence_days)
        for index, asset in enumerate(ordered)
    }
    posts: list[SocialPost] = []
    for asset in ordered:
        for platform in PLATFORM_ORDER:
            handle = target_handles[platform]
            caption, hashtags = _copy_for(asset.title, platform)
            if "—" in caption or "—" in asset.title:
                raise SocialPackageError("Social copy must not contain em dashes")
            posts.append(
                SocialPost(
                    content_id=asset.content_id,
                    platform=platform,  # type: ignore[arg-type]
                    content_type=(
                        "reel"
                        if platform in {"facebook", "instagram"}
                        else "short_video"
                        if platform == "tiktok"
                        else "upload"
                    ),
                    target_handle=handle,
                    media_path=asset.media.path,
                    media_sha256=asset.media.sha256,
                    title=asset.title,
                    caption=caption,
                    hashtags=hashtags,
                    cover_frame_seconds=min(2.0, max(0.0, asset.media.duration_seconds / 5)),
                    scheduled_for=release_for[asset.content_id],
                    status="prepared",
                    idempotency_key=_idempotency_key(
                        platform, handle, asset.media.sha256, package_version
                    ),
                    copy_sha256=_copy_hash(asset.title, caption, hashtags),
                    comments_enabled=True,
                    remix_enabled=False,
                    duet_enabled=False,
                    stitch_enabled=False,
                    download_enabled=False,
                )
            )
    return DistributionManifest(
        campaign_id=campaign_id,
        created_at=datetime.now(UTC),
        package_version=package_version,
        release_timezone=timezone_name,
        cadence_days=cadence_days,
        assets=ordered,
        posts=posts,
        review_approved=False,
        notes=[
            "Shadowmourne is the cross-platform QA pilot when present.",
            "Twitch destinations are Video Producer Uploads, never Twitch Clips.",
            "Owner-recovered derivatives retain their disclosed quality exception.",
            "No credentials, browser cookies, webhook URLs, or OAuth tokens are stored here.",
        ],
    )


def _review_html(manifest: DistributionManifest, destination: Path) -> None:
    cards: list[str] = []
    assets = {asset.content_id: asset for asset in manifest.assets}
    for asset in manifest.assets:
        posts = [post for post in manifest.posts if post.content_id == asset.content_id]
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(post.platform.title())}</td>"
            f"<td>{html.escape(post.target_handle)}</td>"
            f"<td>{html.escape(post.scheduled_for.isoformat())}</td>"
            f"<td><pre>{html.escape(post.caption)}\n\n"
            f"{html.escape(' '.join(post.hashtags))}</pre></td>"
            "</tr>"
            for post in posts
        )
        exception = (
            f"<p class='warning'>{html.escape(asset.media.quality_exception or '')}</p>"
            if asset.media.quality_exception
            else ""
        )
        cards.append(
            "<article>"
            f"<h2>{html.escape(asset.title)}</h2>"
            "<video controls preload='metadata' "
            f"src='{html.escape(asset.media.path.as_uri())}'></video>"
            f"<p><code>{html.escape(asset.content_id)}</code> · "
            f"{asset.media.width}x{asset.media.height} · "
            f"{asset.media.frame_rate:.2f} fps · {asset.media.duration_seconds:.1f}s</p>"
            f"{exception}<table><thead><tr><th>Platform</th><th>Account</th><th>Release</th>"
            f"<th>Copy</th></tr></thead><tbody>{rows}</tbody></table></article>"
        )
    asset_count = len(assets)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(manifest.campaign_id)} social review</title><style>
:root{{color-scheme:dark;font-family:Segoe UI,Arial,sans-serif;
background:#07131c;color:#eef9ff}}
body{{max-width:1500px;margin:auto;padding:28px}}
h1,h2{{color:#9be8ff}}
article{{background:#102431;border:1px solid #2a7896;border-radius:18px;
padding:22px;margin:24px 0}}
video{{display:block;width:min(340px,100%);max-height:72vh;background:#000;
border-radius:14px;margin:0 auto 20px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border-top:1px solid #315365;padding:12px;text-align:left;vertical-align:top}}
pre{{white-space:pre-wrap;font:inherit;margin:0}}
code{{color:#f2c45a}}
.warning{{color:#ffd27a;font-weight:600}}
@media(max-width:800px){{table,thead,tbody,tr,th,td{{display:block}}
th{{display:none}}td{{border:0;padding:8px 0}}}}
</style></head><body><h1>Pizza Warriors social review</h1>
<p>{asset_count} approved clips, {len(manifest.posts)} destination packages.
Nothing on this page publishes remotely.</p>
{"".join(cards)}</body></html>"""
    atomic_write_text(destination, page)


def _analytics_plan(manifest: DistributionManifest, destination: Path) -> None:
    lines = [
        "# Cross-platform analytics plan",
        "",
        "Capture raw platform metrics at publication verification, 24 hours, 7 days, and 28 days.",
        "Use 48 hours only when a first-day anomaly needs a closer look.",
        "Unavailable metrics stay unavailable; never record them as zero.",
        "",
        "| Clip | Platform | Release | 24h | 7d | 28d |",
        "|---|---|---|---|---|---|",
    ]
    for post in manifest.posts:
        released = post.scheduled_for
        lines.append(
            f"| {post.title} | {post.platform} | {released.isoformat()} | "
            f"{(released + timedelta(hours=24)).isoformat()} | "
            f"{(released + timedelta(days=7)).isoformat()} | "
            f"{(released + timedelta(days=28)).isoformat()} |"
        )
    lines.extend(
        [
            "",
            "Track views/plays, reach, average watch time, completion, shares, saves, "
            "comments, profile visits, follows, link clicks, and any mute or restriction.",
            "Compare a platform with its own prior posts at the same content age.",
        ]
    )
    atomic_write_text(destination, "\n".join(lines) + "\n")


def write_social_package(manifest: DistributionManifest, root: Path) -> Path:
    """Write the manifest, review dashboard, captions, and analytics plan."""

    social_root = root.resolve()
    manifest_path = social_root / "distribution-manifest.json"
    if manifest_path.is_file():
        try:
            existing = DistributionManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise SocialPackageError(
                "Existing social manifest cannot be validated; preserve it and resolve "
                "its recorded campaign state before preparing this destination again."
            ) from exc
        if _package_content(existing) == _package_content(manifest):
            # A fresh default release date is only a proposal. Keep the exact
            # accepted dates, approvals, remote IDs and receipts already stored.
            return manifest_path
        if (
            existing.review_approved
            or any(
                post.status != "prepared"
                or post.remote_content_id is not None
                or post.public_url is not None
                for post in existing.posts
            )
            or any(
                receipt.stat().st_size > 0 for receipt in (social_root / "receipts").glob("*.jsonl")
            )
        ):
            raise SocialPackageError(
                "This social destination already contains an approved campaign or publication "
                "receipts. Preserve it. For a separate package, use an approved owned-assets "
                "JSON with a new campaign_id and an unused output_root."
            )
    ensure_directory(social_root)
    atomic_write_json(manifest_path, json_payload(manifest))
    captions = ensure_directory(social_root / "captions")
    for platform in PLATFORM_ORDER:
        atomic_write_json(
            captions / f"{platform}.json",
            [json_payload(post) for post in manifest.posts if post.platform == platform],
        )
    _review_html(manifest, ensure_directory(social_root / "review") / "index.html")
    _analytics_plan(manifest, social_root / "analytics-plan.md")
    ensure_directory(social_root / "receipts")
    ensure_directory(social_root / "analytics")
    return manifest_path


def _package_content(manifest: DistributionManifest) -> dict[str, object]:
    """Compare publication content without replacing observed state or release locks."""

    return {
        "campaign_id": manifest.campaign_id,
        "package_version": manifest.package_version,
        "release_timezone": manifest.release_timezone,
        "cadence_days": manifest.cadence_days,
        "assets": [
            asset.model_dump(mode="json")
            for asset in sorted(manifest.assets, key=lambda asset: asset.content_id)
        ],
        "posts": [
            post.model_dump(
                mode="json",
                exclude={
                    "scheduled_for",
                    "status",
                    "target_account_id",
                    "remote_content_id",
                    "public_url",
                    "published_at",
                    "verified_at",
                    "verification_checks",
                    "last_error",
                },
            )
            for post in sorted(manifest.posts, key=lambda post: (post.platform, post.content_id))
        ],
    }


def prepare_social_source(
    source: Path,
    *,
    handles: dict[str, str] | None = None,
    start_at: datetime | None = None,
    cadence_days: int = 2,
) -> tuple[DistributionManifest, Path]:
    """Prepare a weekly config or explicit owned-backfill source."""

    resolved = source.expanduser().resolve()
    if resolved.suffix.casefold() in {".yaml", ".yml"}:
        from raid_editor.config.loader import load_project_config

        config = load_project_config(resolved)
        assets = assets_from_project(config)
        campaign_id = slugify(config.project.name)
        output_root = project_output_dir(config) / "social"
    elif resolved.suffix.casefold() == ".json":
        campaign_id, output_root, assets = assets_from_owned_manifest(resolved)
    else:
        raise SocialPackageError("Social source must be a project YAML or owned-assets JSON")
    manifest = build_distribution_manifest(
        campaign_id,
        assets,
        handles=handles,
        start_at=start_at,
        cadence_days=cadence_days,
    )
    destination = write_social_package(manifest, output_root)
    retained = DistributionManifest.model_validate_json(destination.read_text(encoding="utf-8"))
    return retained, destination
