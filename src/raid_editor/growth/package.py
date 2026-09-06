"""Create one reviewable, local-only growth package for a weekly raid campaign."""

from __future__ import annotations

import hashlib
import html
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from raid_editor.classification.difficulty import ICC_BOSSES, summarize_raid_progress
from raid_editor.config.loader import project_output_dir
from raid_editor.config.models import ProjectConfig
from raid_editor.growth.calendar import (
    import_social_schedule,
    propose_open_slots,
    write_global_calendar,
)
from raid_editor.growth.claims import allowed_copy_tokens, validate_coverage_claims
from raid_editor.growth.events import build_timeline_events, write_timeline_events
from raid_editor.growth.ledger import (
    append_growth_event,
    append_packaging_experiment,
    load_growth_events,
    load_packaging_experiments,
)
from raid_editor.growth.models import (
    AssetState,
    AudioReview,
    CampaignAsset,
    CampaignManifest,
    ClaimKind,
    ClaimRecord,
    CleanupDependency,
    CoverageRecord,
    DistributionEntry,
    GlobalContentCalendar,
    GrowthDistributionManifest,
    GrowthLedgerEvent,
    PackagingExperiment,
    RecapDecision,
    RelatedTarget,
    RelatedTargetState,
    ScheduleEntry,
    SourceFingerprint,
    SourceLineage,
    SourceRange,
    SourceStream,
)
from raid_editor.ingestion.probe import probe_media
from raid_editor.models import PullCandidate, TimelineDocument
from raid_editor.rendering.validation import ORIGIN, FinalValidationError, accepted_final_context
from raid_editor.social.ledger import load_distribution_manifest
from raid_editor.util.paths import (
    atomic_write_json,
    atomic_write_text,
    ensure_directory,
    full_file_sha256,
    quick_file_fingerprint,
    slugify,
)

_PULLS = TypeAdapter(list[PullCandidate])


class GrowthPackageError(RuntimeError):
    """Expected campaign evidence, review, or selection error."""


def _source_id(prefix: str, path: Path) -> str:
    digest = hashlib.sha256(str(path.resolve()).casefold().encode()).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _lineage(
    path: Path,
    *,
    kind: str,
    authoritative_for: list[str],
    paired_source_id: str | None = None,
    source_id: str | None = None,
) -> SourceLineage:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise GrowthPackageError(f"Source media does not exist: {resolved}")
    fingerprint = quick_file_fingerprint(resolved)
    probe = probe_media(resolved)
    video = probe.video_streams[0] if probe.video_streams else None
    return SourceLineage(
        source_id=source_id or _source_id("source", resolved),
        path=resolved,
        kind=kind,  # type: ignore[arg-type]
        fingerprint=SourceFingerprint(
            algorithm="head_tail_sha256",
            digest=str(fingerprint["head_tail_sha256"]),
            size_bytes=int(fingerprint["size_bytes"]),
            modified_ns=int(fingerprint["modified_ns"]),
            sampled_bytes_per_end=int(fingerprint["sample_bytes_per_end"]),
        ),
        width=video.width if video else None,
        height=video.height if video else None,
        duration_seconds=probe.duration_seconds,
        streams=[
            *[
                SourceStream(
                    index=stream.index,
                    kind="video",
                    codec=stream.codec,
                    title=stream.title,
                    language=stream.language,
                )
                for stream in probe.video_streams
            ],
            *[
                SourceStream(
                    index=stream.index,
                    kind="audio",
                    codec=stream.codec,
                    audio_ordinal=stream.audio_ordinal,
                    title=stream.title,
                    language=stream.language,
                )
                for stream in probe.audio_streams
            ],
        ],
        paired_source_id=paired_source_id,
        authoritative_for=authoritative_for,  # type: ignore[arg-type]
    )


def _historical_landscape_lineage(root: Path, expected_path: Path) -> SourceLineage:
    """Recover immutable source identity from a saved probe after approved cleanup."""

    probe_path = root / "analysis" / "media-probe.json"
    if not probe_path.is_file():
        raise GrowthPackageError(
            f"Source media is gone and its saved media probe is missing: {expected_path}"
        )
    payload = json.loads(probe_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("source"), dict):
        raise GrowthPackageError("Saved media probe does not contain source identity")
    source = payload["source"]
    if Path(str(source.get("path", ""))).resolve() != expected_path.resolve():
        raise GrowthPackageError("Saved media probe belongs to a different source path")
    videos = payload.get("video_streams")
    audios = payload.get("audio_streams")
    video = videos[0] if isinstance(videos, list) and videos and isinstance(videos[0], dict) else {}
    return SourceLineage(
        source_id="landscape-authoritative",
        path=expected_path.resolve(),
        kind="landscape_authoritative_source",
        fingerprint=SourceFingerprint(
            algorithm="head_tail_sha256",
            digest=str(source["head_tail_sha256"]),
            size_bytes=int(source["size_bytes"]),
            modified_ns=int(source["modified_ns"]),
            sampled_bytes_per_end=int(source["sample_bytes_per_end"]),
        ),
        width=int(video["width"]) if video.get("width") else None,
        height=int(video["height"]) if video.get("height") else None,
        duration_seconds=(
            float(payload["duration_seconds"]) if payload.get("duration_seconds") else None
        ),
        streams=[
            *[
                SourceStream(
                    index=int(row["index"]),
                    kind="video",
                    codec=str(row.get("codec") or "unknown"),
                    title=str(row["title"]) if row.get("title") else None,
                    language=str(row["language"]) if row.get("language") else None,
                )
                for row in videos or []
                if isinstance(row, dict) and row.get("index") is not None
            ],
            *[
                SourceStream(
                    index=int(row["index"]),
                    kind="audio",
                    codec=str(row.get("codec") or "unknown"),
                    audio_ordinal=int(row.get("audio_ordinal", 0)),
                    title=str(row["title"]) if row.get("title") else None,
                    language=str(row["language"]) if row.get("language") else None,
                )
                for row in audios or []
                if isinstance(row, dict) and row.get("index") is not None
            ],
        ],
        authoritative_for=["archive", "audio"],
        notes=[
            "Original recording was already cleaned after verified publication.",
            "Identity and geometry were recovered from the immutable saved media probe.",
        ],
    )


def _load_pulls(root: Path) -> list[PullCandidate]:
    path = root / "analysis" / "pull-candidates.json"
    if not path.is_file():
        return []
    return _PULLS.validate_json(path.read_text(encoding="utf-8"))


def _load_timeline(root: Path) -> TimelineDocument | None:
    path = root / "timeline" / "timeline.json"
    if not path.is_file():
        return None
    return TimelineDocument.model_validate_json(path.read_text(encoding="utf-8"))


def _unique_bosses(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        compact = value.strip()
        if not compact or compact.casefold() in seen:
            continue
        seen.add(compact.casefold())
        result.append(compact)
    return result


def _coverage(
    config: ProjectConfig,
    pulls: list[PullCandidate],
    timeline: TimelineDocument | None,
) -> CoverageRecord:
    winners = [
        pull
        for pull in pulls
        if pull.include
        and pull.encounter
        and (pull.type == "boss_kill" or pull.result in {"kill", "success"})
    ]
    recorded = _unique_bosses([pull.encounter or "" for pull in winners])
    edited: list[str] = []
    timeline_pull_ids: list[str] = []
    if timeline is not None:
        edited = _unique_bosses(
            [
                clip.encounter or clip.label
                for clip in timeline.clips
                if clip.type in {"boss_attempt", "boss_kill"} and clip.result in {"kill", "success"}
            ]
        )
        timeline_pull_ids = [pull_id for clip in timeline.clips for pull_id in clip.pull_ids]
    by_name = {(pull.encounter or "").casefold(): pull for pull in winners}
    heroic = [
        name
        for name in recorded
        if by_name.get(name.casefold()) is not None
        and by_name[name.casefold()].difficulty.endswith("H")
    ]
    unknown = [
        name
        for name in recorded
        if by_name.get(name.casefold()) is not None
        and by_name[name.casefold()].difficulty == "UNKNOWN"
    ]
    progress = summarize_raid_progress(
        pulls,
        raid_name=config.project.raid,
        settings=config.difficulty,
    )
    overall_bosses_killed = (
        config.difficulty.overall_bosses_killed
        if config.difficulty.overall_bosses_killed is not None
        else progress.bosses_killed
    )
    ending_complete = bool(edited) and all(
        name.casefold() in by_name and by_name[name.casefold()].result in {"kill", "success"}
        for name in edited
    )
    source_ranges = [
        SourceRange(
            source_id="landscape-authoritative",
            start_seconds=pull.start_seconds,
            end_seconds=pull.end_seconds,
            purpose="recorded_coverage",
            evidence_ids=[pull.id],
        )
        for pull in winners
    ]
    if timeline is not None:
        source_ranges.extend(
            SourceRange(
                source_id="landscape-authoritative",
                start_seconds=clip.source_in,
                end_seconds=clip.source_out,
                purpose="edited_asset",
                evidence_ids=list(clip.pull_ids),
            )
            for clip in timeline.clips
        )
    known_missing = (
        sorted(set(ICC_BOSSES) - set(recorded))
        if progress.expected_bosses == len(ICC_BOSSES)
        else []
    )
    return CoverageRecord(
        expected_bosses=progress.expected_bosses,
        overall_bosses_killed=overall_bosses_killed,
        recorded_bosses=recorded,
        edited_bosses=edited,
        heroic_bosses=heroic,
        unknown_difficulty_bosses=unknown,
        winning_ending_bosses=edited if ending_complete else [],
        confirmed_raid_size=progress.raid_size,  # type: ignore[arg-type]
        recording_state=(
            "full"
            if len(recorded) == progress.expected_bosses
            else "partial"
            if recorded
            else "unknown"
        ),
        source_ranges=source_ranges,
        known_missing_segments=known_missing,
        ending_evidence_complete=ending_complete,
        evidence_ids=list(
            dict.fromkeys(
                [
                    *(pull.id for pull in winners),
                    *timeline_pull_ids,
                    *(
                        [
                            "configured-overall-result:"
                            f"{overall_bosses_killed}/{progress.expected_bosses}"
                        ]
                        if config.difficulty.overall_bosses_killed is not None
                        else []
                    ),
                ]
            )
        ),
        notes=[
            "Overall result, recorded coverage, and edited coverage are stored separately.",
            "Only included winning pulls count toward automatic public claims.",
        ],
    )


def _validated_final(root: Path) -> tuple[Path, str] | None:
    """Resolve the receipt's exact media identity, never the newest file in a folder."""
    validation_path = root / "reports" / "final-validation.json"
    if not validation_path.is_file():
        return None
    try:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        return None
    if validation.get("validation_origin") == ORIGIN:
        try:
            context = accepted_final_context(root, validation)
            return context.video, validation["artifact"]["sha256"]
        except (OSError, ValueError, FinalValidationError):
            return None
    artifact = validation.get("artifact")
    if "artifact" not in validation:
        # Historical validation reports contained checks only. A completed
        # upload receipt supplies an immutable binding for that legacy case;
        # filenames or timestamps alone cannot recover it.
        upload_path = root / "youtube" / "upload-manifest.json"
        try:
            upload = json.loads(upload_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(upload, dict) or upload.get("upload_complete") is not True:
            return None
        artifact = {"path": upload.get("source"), "sha256": upload.get("source_sha256")}
    if not isinstance(artifact, dict):
        return None
    raw_path, expected_sha256 = artifact.get("path"), artifact.get("sha256")
    if not isinstance(raw_path, str) or not isinstance(expected_sha256, str):
        return None
    path = Path(raw_path)
    if not path.is_absolute() or len(expected_sha256) != 64:
        return None
    try:
        render = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
        if not isinstance(render, dict) or render.get("approved") is not True:
            return None
        if "artifact" in validation or "artifact" in render:
            rendered_artifact = render.get("artifact")
            if (
                not isinstance(rendered_artifact, dict)
                or rendered_artifact.get("sha256") != expected_sha256
                or not isinstance(rendered_artifact.get("path"), str)
                or Path(rendered_artifact["path"]).resolve() != path.resolve()
            ):
                return None
        _verify_media_identity(path, expected_sha256)
    except (OSError, ValueError, GrowthPackageError):
        return None
    return path.resolve(), expected_sha256


def _file_stat_identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _verify_media_identity(path: Path, expected_sha256: str) -> tuple[int, int, int, int, int]:
    """Hash live bytes and reject a replacement or modification during the read."""
    try:
        if not path.is_file():
            raise GrowthPackageError(f"Reviewed media is missing: {path}")
        before = _file_stat_identity(path)
        current_sha256 = full_file_sha256(path)
        after = _file_stat_identity(path)
    except OSError as exc:
        raise GrowthPackageError(f"Could not verify reviewed media: {path}") from exc
    if current_sha256 != expected_sha256 or before != after:
        raise GrowthPackageError(f"Reviewed media changed; regenerate its growth package: {path}")
    return after


def _archive_title(
    config: ProjectConfig,
    coverage: CoverageRecord,
    claims: list[ClaimRecord],
) -> str:
    raid_name = config.project.raid or "Raid"
    abbreviation = config.difficulty.title_raid_abbreviation or (
        "ICC" if "icecrown" in raid_name.casefold() else raid_name
    )
    size = f"{coverage.confirmed_raid_size}M" if coverage.confirmed_raid_size else "?M"
    progress = next(claim.text for claim in claims if claim.kind == "progress")
    heroic = next(
        (
            claim.text
            for claim in claims
            if claim.kind == "heroic_count" and claim.state == "allowed"
        ),
        "?HC",
    )
    suffix = " Full Clear" if "Full Clear" in allowed_copy_tokens(claims) else ""
    date_suffix = (
        f" | {config.project.raid_date:%b} {config.project.raid_date.day}"
        if config.project.raid_date
        else ""
    )
    return (
        f"{abbreviation} {size} {progress} {heroic}{suffix} | Pizza Warriors WoW WotLK{date_suffix}"
    )


def _load_short_rows(root: Path, selected_short_ids: list[str] | None) -> list[dict[str, Any]]:
    path = root / "highlights" / "vertical" / "manifest.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("approved") is not True:
        return []
    rows = [
        row
        for row in payload.get("clips", [])
        if isinstance(row, dict) and row.get("rendered") is not False
    ]
    if selected_short_ids is None:
        if len(rows) > 2:
            raise GrowthPackageError(
                "More than two approved highlight renders exist. Rerun with zero to two "
                "--short-id values so the growth pilot stays intentional."
            )
        return rows
    requested = list(dict.fromkeys(selected_short_ids))
    if len(requested) > 2:
        raise GrowthPackageError("New raid campaigns may select at most two Shorts")
    by_id = {str(row.get("id")): row for row in rows}
    unknown = [content_id for content_id in requested if content_id not in by_id]
    if unknown:
        raise GrowthPackageError(f"Unknown approved Short IDs: {', '.join(unknown)}")
    return [by_id[content_id] for content_id in requested]


def _manifest_fingerprint(value: object, *, path: Path) -> SourceFingerprint:
    """Validate the bounded file identity retained in a completed render manifest."""

    if not isinstance(value, dict):
        raise GrowthPackageError("Native render is missing a source or output fingerprint")
    try:
        if Path(str(value["path"])).resolve() != path.resolve():
            raise ValueError("fingerprint path differs")
        fingerprint = SourceFingerprint(
            algorithm="head_tail_sha256",
            digest=value["head_tail_sha256"],
            size_bytes=value["size_bytes"],
            modified_ns=value["modified_ns"],
            sampled_bytes_per_end=value["sample_bytes_per_end"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise GrowthPackageError(f"Native render has invalid file identity: {path}") from exc
    if path.is_file():
        current = quick_file_fingerprint(path)
        if any(current.get(key) != value.get(key) for key in current if key != "path"):
            raise GrowthPackageError(f"Native render file identity has changed: {path}")
    return fingerprint


def _native_short_lineage(row: dict[str, Any], *, landscape: SourceLineage) -> SourceLineage | None:
    """Derive native provenance from a successful render, never a supplied raw path."""

    if row.get("video_source", "landscape") != "native_vertical":
        return None
    binding = row.get("source_binding")
    reference = row.get("presentation_reference")
    if (
        row.get("rendered") is not True
        or not isinstance(binding, dict)
        or binding.get("schema_version") != 1
        or binding.get("status") != "verified"
        or not isinstance(reference, str)
        or len(reference) != 64
        or any(character not in "0123456789abcdef" for character in reference)
    ):
        raise GrowthPackageError("Native Short requires a completed, source-bound approved render")
    try:
        video_path = Path(row["video_recording"]).resolve()
        audio_path = Path(row["audio_recording"]).resolve()
        output_path = Path(row["output"]).resolve()
        offset = float(binding["offset_seconds"])
        landscape_start = float(row["source_start_seconds"])
        landscape_end = float(row["source_end_seconds"])
        portrait_start = float(row["portrait_start_seconds"])
        portrait_end = float(row["portrait_end_seconds"])
        portrait_duration = float(binding["portrait_duration_seconds"])
        landscape_duration = float(binding["landscape_duration_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GrowthPackageError(
            "Native Short is missing its source paths or measured timing"
        ) from exc
    if not output_path.is_file():
        raise GrowthPackageError(f"Approved native Short output is missing: {output_path}")
    _manifest_fingerprint(row.get("output_fingerprint"), path=output_path)
    if audio_path != landscape.path.resolve() or video_path == audio_path:
        raise GrowthPackageError("Native Short source paths do not match the campaign recording")
    audio_fingerprint = _manifest_fingerprint(binding.get("landscape"), path=audio_path)
    if audio_fingerprint != landscape.fingerprint:
        raise GrowthPackageError("Native Short landscape identity differs from campaign evidence")
    portrait_fingerprint = _manifest_fingerprint(binding.get("portrait"), path=video_path)
    times = (
        offset,
        landscape_start,
        landscape_end,
        portrait_start,
        portrait_end,
        portrait_duration,
        landscape_duration,
    )
    if (
        not all(math.isfinite(value) for value in times)
        or not 0 <= landscape_start < landscape_end <= landscape_duration
        or not 0 <= portrait_start < portrait_end <= portrait_duration
        or abs(portrait_start - (landscape_start - offset)) > 0.001
        or abs(portrait_end - (landscape_end - offset)) > 0.001
    ):
        raise GrowthPackageError(
            "Native Short source intervals disagree with measured synchronization"
        )
    source_id = _source_id("portrait", video_path)
    if video_path.is_file():
        source = _lineage(
            video_path,
            kind="native_portrait_source",
            authoritative_for=["portrait"],
            paired_source_id=landscape.source_id,
            source_id=source_id,
        )
    else:
        source = SourceLineage(
            source_id=source_id,
            path=video_path,
            kind="native_portrait_source",
            fingerprint=portrait_fingerprint,
            duration_seconds=portrait_duration,
            paired_source_id=landscape.source_id,
            authoritative_for=["portrait"],
            notes=[
                "Raw recording is unavailable; identity is retained in the verified render binding."
            ],
        )
    source.notes.append(f"Native render presentation reference: {reference}")
    return source


def _existing_manifest_created_at(path: Path) -> datetime | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        existing = CampaignManifest.model_validate_json(raw)
        return existing.created_at
    except (OSError, ValueError):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            value = payload.get("created_at") if isinstance(payload, dict) else None
            return datetime.fromisoformat(str(value)) if value else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None


def _load_existing_campaign(path: Path) -> CampaignManifest | None:
    if not path.is_file():
        return None
    try:
        return CampaignManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _preserve_reviewed_projection(
    existing: CampaignManifest | None,
    *,
    sources: list[SourceLineage],
    assets: list[CampaignAsset],
) -> tuple[list[CampaignAsset], RecapDecision]:
    """Preserve reviewed local decisions only while their exact evidence still matches."""

    if existing is None:
        return assets, RecapDecision()
    old_sources = {source.source_id: source for source in existing.sources}
    new_sources = {source.source_id: source for source in sources}

    def sources_match(source_ids: list[str]) -> bool:
        return all(
            source_id in old_sources
            and source_id in new_sources
            and old_sources[source_id].fingerprint == new_sources[source_id].fingerprint
            for source_id in source_ids
        )

    state_rank = {
        "planned": 0,
        "review_required": 1,
        "reviewed": 2,
        "rendered": 3,
        "upload_ready": 4,
        "published": 5,
        "verified_public": 6,
        "skipped": -1,
    }
    old_assets = {asset.content_id: asset for asset in existing.assets}
    for asset in assets:
        old = old_assets.get(asset.content_id)
        if old is None or old.lane != asset.lane or not sources_match(asset.source_lineage_ids):
            continue
        media_matches = old.media_sha256 == asset.media_sha256
        if (
            asset.related_target is not None
            and old.related_target is not None
            and asset.related_target.target_content_id == old.related_target.target_content_id
            and asset.related_target.target_lane == old.related_target.target_lane
        ):
            asset.related_target = old.related_target
        existing_claim_ids = {claim.claim_id for claim in asset.claims}
        asset.claims.extend(
            claim
            for claim in old.claims
            if not claim.automatic
            and claim.manually_approved
            and claim.claim_id not in existing_claim_ids
        )
        if (
            media_matches
            and old.state != "skipped"
            and state_rank[old.state] > state_rank[asset.state]
        ):
            asset.state = old.state
        asset.packaging_experiment_id = old.packaging_experiment_id or asset.packaging_experiment_id
    primary_source_ids = [
        source.source_id for source in sources if "archive" in source.authoritative_for
    ]
    primary_source_matches = bool(primary_source_ids) and sources_match(primary_source_ids)
    recap = existing.recap if primary_source_matches else RecapDecision()
    return assets, recap


def _load_prior_campaign_proposals(
    calendar_path: Path,
    *,
    campaign_id: str,
    valid_keys: set[tuple[str, str]],
) -> list[ScheduleEntry]:
    if not calendar_path.is_file():
        return []
    try:
        calendar = GlobalContentCalendar.model_validate_json(
            calendar_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return []
    return [
        row
        for row in calendar.entries
        if row.campaign_id == campaign_id
        and row.lock == "proposed"
        and (row.destination, row.content_id) in valid_keys
    ]


def _merge_existing_distribution(
    path: Path,
    current: list[DistributionEntry],
) -> list[DistributionEntry]:
    """Carry observed receipts forward when regenerated media identity still matches."""

    if not path.is_file():
        return current
    try:
        existing = GrowthDistributionManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return current
    status_rank = {
        "not_planned": 0,
        "prepared": 1,
        "reviewed": 2,
        "awaiting_confirmation": 3,
        "uploaded": 4,
        "processing": 5,
        "scheduled": 6,
        "published": 7,
        "verified_public": 8,
        "failed": 3,
        "blocked": 3,
        "waived_by_neil": 8,
    }
    old_by_key = {(entry.destination, entry.content_id): entry for entry in existing.entries}
    for entry in current:
        old = old_by_key.get((entry.destination, entry.content_id))
        if old is None:
            continue
        if not old.media_sha256 or old.media_sha256 != entry.media_sha256:
            continue
        if status_rank[old.status] > status_rank[entry.status]:
            entry.status = old.status
            entry.scheduled_for = old.scheduled_for or entry.scheduled_for
            entry.published_at = old.published_at or entry.published_at
            entry.public_url = old.public_url
            entry.remote_content_id = old.remote_content_id
            entry.verification_notes = list(old.verification_notes)
        for key, value in old.mutation_intents.items():
            if value in {"pending", "applied", "verified", "blocked_capability", "waived_by_neil"}:
                entry.mutation_intents[key] = value
        entry.named_approvals = list(dict.fromkeys([*entry.named_approvals, *old.named_approvals]))
        entry.review_approved = entry.review_approved or old.review_approved
    return current


def _publication_state(
    root: Path,
    *,
    media_sha256: str | None,
) -> tuple[str, str | None, str | None]:
    path = root / "youtube" / "upload-manifest.json"
    if not path.is_file():
        return "prepared", None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "prepared", None, None
    if (
        not isinstance(payload, dict)
        or payload.get("upload_complete") is not True
        or not media_sha256
        or payload.get("source_sha256") != media_sha256
    ):
        return "prepared", None, None
    video_id = str(payload.get("video_id")) if payload.get("video_id") else None
    url = str(payload.get("url")) if payload.get("url") else None
    if not video_id:
        return "prepared", None, None
    if (
        payload.get("public_playback_confirmed") is True
        and payload.get("publication_verified_at")
        and url
    ):
        return "verified_public", video_id, url
    if payload.get("privacy_status") == "public":
        return "published", video_id, url
    return "uploaded", video_id, url


def _map_social_status(value: str) -> str:
    if value in {"blocked_account", "blocked_capability", "remote_state_uncertain"}:
        return "blocked"
    return value


def _social_aliases(
    social_manifest_paths: list[Path],
    *,
    current_media: dict[str, str | None],
) -> dict[str, tuple[Path, str, str | None, str | None]]:
    """Map only unchanged media to existing cross-platform package identities."""

    aliases: dict[str, tuple[Path, str, str | None, str | None]] = {}
    for path in social_manifest_paths:
        manifest = load_distribution_manifest(path)
        for asset in manifest.assets:
            selected_id = (
                asset.source_highlight_id
                if asset.source_highlight_id in current_media
                else asset.content_id
            )
            selected_sha256 = current_media.get(selected_id)
            posts = [post for post in manifest.posts if post.content_id == asset.content_id]
            if (
                not selected_sha256
                or asset.media.sha256 != selected_sha256
                or not posts
                or any(post.media_sha256 != selected_sha256 for post in posts)
            ):
                continue
            values = (path, asset.content_id, asset.youtube_video_id, asset.youtube_url)
            aliases[asset.content_id] = values
            if asset.source_highlight_id:
                aliases[asset.source_highlight_id] = values
    return aliases


def _align_unreviewed_social_schedules(
    social_manifest_paths: list[Path],
    proposals: list[Any],
    aliases: dict[str, tuple[Path, str, str | None, str | None]],
) -> None:
    """Make the global calendar authoritative for local, unreviewed social packages."""

    proposed_by_alias = {
        (row.destination, aliases.get(row.content_id, (None, row.content_id, None, None))[1]): (
            row.scheduled_for
        )
        for row in proposals
        if row.destination in {"facebook", "instagram", "tiktok", "twitch"}
        and row.content_id in aliases
    }
    matched_packages = {(path.resolve(), content_id) for path, content_id, _, _ in aliases.values()}
    for path in social_manifest_paths:
        manifest = load_distribution_manifest(path)
        if manifest.review_approved:
            continue
        changed = False
        for post in manifest.posts:
            if (path.resolve(), post.content_id) not in matched_packages:
                continue
            key = (post.platform, post.content_id)
            if post.status == "prepared" and key in proposed_by_alias:
                post.scheduled_for = proposed_by_alias[key]
                changed = True
        if changed:
            manifest.notes.append(
                "Prepared release times were aligned to the local global content calendar."
            )
            atomic_write_json(path, manifest.model_dump(mode="json"))


def _distribution(
    campaign: CampaignManifest,
    campaign_path: Path,
    social_manifest_paths: list[Path],
    root: Path,
) -> list[DistributionEntry]:
    entries: dict[tuple[str, str], DistributionEntry] = {}
    known_content_ids = {asset.content_id for asset in campaign.assets}
    for asset in campaign.assets:
        youtube_state, video_id, url = _publication_state(root, media_sha256=asset.media_sha256)
        destinations = ["youtube", "discord"]
        if asset.lane == "short":
            destinations.extend(["facebook", "instagram", "tiktok", "twitch"])
        for destination in destinations:
            state = (
                youtube_state
                if asset.lane == "archive" and destination == "youtube"
                else "prepared"
            )
            entries[(destination, asset.content_id)] = DistributionEntry(
                content_id=asset.content_id,
                lane=asset.lane,
                destination=destination,  # type: ignore[arg-type]
                media_sha256=asset.media_sha256,
                copy_sha256=hashlib.sha256(asset.title.encode("utf-8")).hexdigest(),
                status=state,  # type: ignore[arg-type]
                public_url=url if asset.lane == "archive" and destination == "youtube" else None,
                remote_content_id=(
                    video_id if asset.lane == "archive" and destination == "youtube" else None
                ),
                source_manifest=campaign_path,
                notify_subscribers=(
                    True if destination == "youtube" and asset.lane == "archive" else None
                ),
                mutation_intents={
                    "playlist": (
                        "planned"
                        if destination == "youtube" and asset.lane in {"archive", "recap"}
                        else "not_applicable"
                    ),
                    "related_video": (
                        "planned"
                        if destination == "youtube" and asset.lane == "short"
                        else "not_applicable"
                    ),
                    "end_screen": (
                        "planned"
                        if destination == "youtube" and asset.lane in {"archive", "recap"}
                        else "not_applicable"
                    ),
                    "card": "not_applicable",
                    "comments": "planned" if destination != "discord" else "not_applicable",
                    "community_post": "not_applicable",
                    "discord_post": "planned" if destination == "discord" else "not_applicable",
                },
                review_approved=asset.state
                in {
                    "reviewed",
                    "upload_ready",
                    "published",
                    "verified_public",
                },
            )
    for path in social_manifest_paths:
        manifest = load_distribution_manifest(path)
        assets_by_id = {asset.content_id: asset for asset in manifest.assets}
        for post in manifest.posts:
            social_asset = assets_by_id[post.content_id]
            campaign_content_id = (
                social_asset.source_highlight_id
                if social_asset.source_highlight_id in known_content_ids
                else social_asset.content_id
            )
            key = (post.platform, campaign_content_id)
            if key not in entries:
                continue
            selected_sha256 = entries[key].media_sha256
            if (
                not selected_sha256
                or post.media_sha256 != selected_sha256
                or social_asset.media.sha256 != selected_sha256
            ):
                # A content ID can survive a new edit. Its older publication
                # receipt must not make the replacement eligible for cleanup.
                continue
            entries[key] = DistributionEntry(
                content_id=campaign_content_id,
                lane=entries[key].lane,
                destination=post.platform,
                target_handle=post.target_handle,
                media_sha256=post.media_sha256,
                copy_sha256=post.copy_sha256,
                scheduled_for=post.scheduled_for,
                published_at=post.published_at,
                status=_map_social_status(post.status),  # type: ignore[arg-type]
                public_url=post.public_url,
                remote_content_id=post.remote_content_id,
                source_manifest=path,
                mutation_intents={"comments": "planned"},
                named_approvals=["social_package_review"] if manifest.review_approved else [],
                review_approved=manifest.review_approved,
            )
            if social_asset.youtube_video_id and social_asset.youtube_url:
                youtube_key = ("youtube", campaign_content_id)
                if youtube_key in entries:
                    entries[youtube_key] = DistributionEntry(
                        content_id=campaign_content_id,
                        lane=entries[youtube_key].lane,
                        destination="youtube",
                        media_sha256=social_asset.media.sha256,
                        status="verified_public",
                        public_url=social_asset.youtube_url,
                        remote_content_id=social_asset.youtube_video_id,
                        source_manifest=path,
                        review_approved=True,
                        verification_notes=[
                            "Owned YouTube Short identity imported from the verified "
                            "social backfill."
                        ],
                    )
    return sorted(entries.values(), key=lambda row: (row.content_id, row.destination))


def _cleanup_dependencies(
    campaign: CampaignManifest,
    distribution: list[DistributionEntry],
) -> list[CleanupDependency]:
    rows: list[CleanupDependency] = []
    terminal = {"verified_public", "waived_by_neil"}
    for source in campaign.sources:
        required_assets = [
            asset.content_id
            for asset in campaign.assets
            if source.source_id in asset.source_lineage_ids
        ]
        holds: list[str] = []
        if not source.path.is_file():
            rows.append(
                CleanupDependency(
                    dependency_id=f"source:{source.source_id}",
                    path=source.path,
                    required_by=required_assets,
                    holds=[],
                    cleanup_eligible=True,
                )
            )
            continue
        for content_id in required_assets:
            related = [entry for entry in distribution if entry.content_id == content_id]
            if not related:
                holds.append(f"{content_id}:no distribution record")
                continue
            for entry in related:
                if entry.destination == "discord" and entry.status == "prepared":
                    continue
                if entry.status not in terminal:
                    holds.append(f"{content_id}:{entry.destination}:{entry.status}")
        rows.append(
            CleanupDependency(
                dependency_id=f"source:{source.source_id}",
                path=source.path,
                sha256=(
                    source.fingerprint.digest if source.fingerprint.algorithm == "sha256" else None
                ),
                required_by=required_assets,
                holds=holds,
                cleanup_eligible=not holds,
            )
        )
    for asset in campaign.assets:
        if asset.media_path is None:
            continue
        holds = [
            f"{entry.destination}:{entry.status}"
            for entry in distribution
            if entry.content_id == asset.content_id
            and entry.destination != "discord"
            and entry.status not in terminal
        ]
        rows.append(
            CleanupDependency(
                dependency_id=f"asset:{asset.content_id}",
                path=asset.media_path,
                sha256=asset.media_sha256,
                required_by=[
                    f"{entry.destination}:{entry.content_id}"
                    for entry in distribution
                    if entry.content_id == asset.content_id
                ],
                holds=holds,
                cleanup_eligible=not holds,
            )
        )
    return rows


def _review_html(
    campaign: CampaignManifest,
    distribution: GrowthDistributionManifest,
    calendar_path: Path,
    destination: Path,
) -> None:
    source_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(source.source_id)}</code></td>"
        f"<td>{html.escape(source.kind)}</td>"
        f"<td>{html.escape(str(source.path))}</td>"
        f"<td>{source.width or '?'}x{source.height or '?'} / "
        f"{source.duration_seconds or 0:.1f}s / {len(source.streams)} stream(s)</td>"
        "</tr>"
        for source in campaign.sources
    )
    asset_cards: list[str] = []
    for asset in campaign.assets:
        claims = "".join(
            f"<li class='{claim.state}'><strong>{html.escape(claim.kind)}</strong>: "
            f"{html.escape(claim.text)} - {html.escape(claim.state)}<br>"
            f"<small>{html.escape(claim.reason)}</small></li>"
            for claim in asset.claims
        )
        related = (
            "<p>Related target: <code>"
            f"{html.escape(asset.related_target.target_content_id or '')}</code> "
            f"({html.escape(asset.related_target.state)})</p>"
            if asset.related_target
            else ""
        )
        asset_cards.append(
            "<article>"
            f"<h2>{html.escape(asset.title)}</h2>"
            f"<p><span class='pill'>{html.escape(asset.lane)}</span> "
            f"<span class='pill'>{html.escape(asset.state)}</span></p>"
            f"<p>Edited coverage: {len(asset.coverage.edited_bosses)}/"
            f"{asset.coverage.expected_bosses}; recorded: {len(asset.coverage.recorded_bosses)}/"
            f"{asset.coverage.expected_bosses}; overall: {asset.coverage.overall_bosses_killed}/"
            f"{asset.coverage.expected_bosses}</p>{related}<h3>Claims</h3><ul>{claims}</ul></article>"
        )
    schedule_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.destination)}</td>"
        f"<td>{html.escape(row.target_handle or 'configured default')}</td>"
        f"<td>{html.escape(row.content_id)}</td>"
        f"<td>{html.escape(row.scheduled_for.isoformat())}</td>"
        f"<td>{html.escape(row.lock)}</td><td>{html.escape(row.status)}</td></tr>"
        for row in distribution.schedule
    )
    cleanup_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.dependency_id)}</td><td>{html.escape(str(row.path))}</td>"
        f"<td>{'yes' if row.cleanup_eligible else 'no'}</td>"
        f"<td>{html.escape(', '.join(row.holds) or 'none')}</td></tr>"
        for row in distribution.cleanup_dependencies
    )
    calendar_payload = json.loads(calendar_path.read_text(encoding="utf-8"))
    raw_collisions = calendar_payload.get("collision_warnings", [])
    collision_warnings = (
        [str(item) for item in raw_collisions] if isinstance(raw_collisions, list) else []
    )
    collision_items = "".join(f"<li>{html.escape(item)}</li>" for item in collision_warnings)
    recap_ranges = "".join(
        f"<li>{html.escape(item.source_id)}: {item.start_seconds:.2f}-{item.end_seconds:.2f}s</li>"
        for item in campaign.recap.source_ranges
    )
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<title>{html.escape(campaign.campaign_id)} growth review</title>
<style>
:root{{color-scheme:dark;font-family:Segoe UI,Arial,sans-serif;
background:#07131c;color:#eef9ff}}
body{{max-width:1450px;margin:auto;padding:28px}}
article{{background:#102431;border:1px solid #2a7896;
border-radius:18px;padding:20px;margin:20px 0}}
h1,h2,h3{{color:#9be8ff}}table{{border-collapse:collapse;
width:100%;margin:14px 0 28px}}th,td{{border-top:1px solid #315365;padding:10px;text-align:left;
vertical-align:top}}code{{word-break:break-all}}.pill{{display:inline-block;background:#18445a;border-radius:999px;
padding:4px 10px}}.allowed{{color:#b9f7cf}}.rejected{{color:#ffb4b4}}.manual_review{{color:#ffe29b}}
.callout{{background:#14374a;border-left:5px solid #55dff8;padding:14px}}</style></head><body>
<h1>Growth package: {html.escape(campaign.campaign_id)}</h1>
<p class="callout">Local review artifact only. No upload, schedule, related-video, comment,
Community post, card, end screen, visibility, or platform mutation occurred.</p>
<p>Short selection: {sum(asset.lane == "short" for asset in campaign.assets)}/2 maximum.
Calendar: <code>{html.escape(str(calendar_path))}</code></p>
<h2>Source and footage lineage</h2><table><thead><tr><th>ID</th><th>Kind</th><th>Path</th>
<th>Media</th></tr></thead><tbody>{source_rows}</tbody></table>
{"".join(asset_cards)}
<h2>Recap decision</h2>
<p><strong>{html.escape(campaign.recap.decision)}</strong> -
{html.escape(campaign.recap.reason or "Not reviewed yet")}</p>
<p>Audio plan: {html.escape(campaign.recap.audio_plan)}; estimated active effort:
{campaign.recap.estimated_active_minutes or 0:.1f} minutes.</p>
<p>Candidate beats: {html.escape(", ".join(campaign.recap.candidate_beats) or "none")}</p>
<ul>{recap_ranges}</ul>
<h2>Global content calendar</h2><table><thead><tr><th>Platform</th><th>Account</th>
<th>Content</th><th>Release</th><th>Lock</th><th>Status</th></tr></thead><tbody>{schedule_rows}</tbody></table>
<h3>Collision warnings</h3><ul>{collision_items or "<li>none</li>"}</ul>
<h2>Cleanup holds</h2><table><thead><tr><th>Dependency</th><th>Path</th><th>Eligible</th>
<th>Holds</th></tr></thead><tbody>{cleanup_rows}</tbody></table>
</body></html>"""
    atomic_write_text(destination, page)


def prepare_growth_package(
    config: ProjectConfig,
    *,
    selected_short_ids: list[str] | None = None,
    portrait_source: Path | None = None,
    social_manifest_paths: list[Path] | None = None,
) -> tuple[CampaignManifest, GrowthDistributionManifest, Path]:
    """Generate the Phase 1 growth foundation with no remote side effects."""

    root = project_output_dir(config)
    growth_root = ensure_directory(root / "growth")
    review_root = ensure_directory(growth_root / "review")
    campaign_path = growth_root / "campaign-manifest.json"
    existing_campaign = _load_existing_campaign(campaign_path)
    created_at = (
        existing_campaign.created_at
        if existing_campaign is not None
        else _existing_manifest_created_at(campaign_path) or datetime.now(UTC)
    )
    now = datetime.now(UTC)
    landscape = (
        _lineage(
            config.input.recording,
            kind="landscape_authoritative_source",
            authoritative_for=["archive", "audio"],
            source_id="landscape-authoritative",
        )
        if config.input.recording.is_file()
        else _historical_landscape_lineage(root, config.input.recording)
    )
    sources = [landscape]
    if portrait_source is not None:
        registered_portrait = _lineage(
            portrait_source,
            kind="native_portrait_source",
            authoritative_for=["portrait"],
            source_id=_source_id("portrait", portrait_source),
        )
        registered_portrait.notes.append(
            "Raw portrait recording registered explicitly; "
            "this does not establish Short render lineage."
        )
        sources.append(registered_portrait)
    pulls = _load_pulls(root)
    timeline = _load_timeline(root)
    coverage = _coverage(config, pulls, timeline)
    archive_id = f"{root.name}-archive"
    claims = validate_coverage_claims(coverage, content_id=archive_id)
    final = _validated_final(root)
    archive_state: AssetState = "rendered" if final else "review_required"
    archive = CampaignAsset(
        content_id=archive_id,
        lane="archive",
        title=_archive_title(config, coverage, claims),
        state=archive_state,
        media_path=final[0] if final else None,
        media_sha256=final[1] if final else None,
        source_lineage_ids=[landscape.source_id],
        coverage=coverage,
        claims=claims,
        audio=AudioReview(
            game=True,
            discord=False,
            microphone=False,
            clip_level_reviewed=True,
            notes=["Archive retains game audio only."],
        ),
        review_notes=["Accurate archive is always created; recap remains optional."],
    )
    assets = [archive]
    short_rows = _load_short_rows(root, selected_short_ids)
    for row in short_rows:
        path = Path(str(row["output"])).resolve()
        content_id = str(row["id"])
        native_source = _native_short_lineage(row, landscape=landscape)
        if native_source is not None:
            registered = next(
                (source for source in sources if source.source_id == native_source.source_id), None
            )
            if registered is None:
                sources.append(native_source)
            elif registered.fingerprint != native_source.fingerprint:
                raise GrowthPackageError(
                    "Native Shorts disagree about their portrait source identity"
                )
            else:
                registered.paired_source_id = landscape.source_id
                registered.notes.extend(
                    note for note in native_source.notes if note not in registered.notes
                )
        short_source_id = f"short-media-{slugify(content_id)}"
        short_source = _lineage(
            path,
            kind=(
                "native_portrait_source"
                if native_source is not None
                else "landscape_derived_portrait"
            ),
            authoritative_for=["portrait"],
            paired_source_id=(native_source.source_id if native_source else landscape.source_id),
            source_id=short_source_id,
        )
        sources.append(short_source)
        source_lineage_ids = [landscape.source_id, short_source.source_id]
        if native_source is not None:
            source_lineage_ids.insert(1, native_source.source_id)
            short_source.notes.append(
                f"Native render presentation reference: {row['presentation_reference']}"
            )
        title = str(row.get("title") or content_id).replace("—", "-").replace("–", "-")
        short_coverage = CoverageRecord(
            expected_bosses=coverage.expected_bosses,
            overall_bosses_killed=coverage.overall_bosses_killed,
            recorded_bosses=coverage.recorded_bosses,
            edited_bosses=[],
            heroic_bosses=coverage.heroic_bosses,
            unknown_difficulty_bosses=coverage.unknown_difficulty_bosses,
            confirmed_raid_size=coverage.confirmed_raid_size,
            recording_state=coverage.recording_state,
            source_ranges=[
                SourceRange(
                    source_id=landscape.source_id,
                    start_seconds=float(row["source_start_seconds"]),
                    end_seconds=float(row["source_end_seconds"]),
                    purpose="highlight",
                    evidence_ids=[content_id],
                )
            ],
            ending_evidence_complete=False,
            evidence_ids=[content_id],
            notes=["Short coverage describes a moment, not the full raid result."],
        )
        if native_source is not None:
            short_coverage.source_ranges.append(
                SourceRange(
                    source_id=native_source.source_id,
                    start_seconds=float(row["portrait_start_seconds"]),
                    end_seconds=float(row["portrait_end_seconds"]),
                    purpose="highlight",
                    evidence_ids=[content_id],
                )
            )
        assets.append(
            CampaignAsset(
                content_id=content_id,
                lane="short",
                title=title,
                state="rendered",
                media_path=path,
                media_sha256=full_file_sha256(path),
                source_lineage_ids=source_lineage_ids,
                coverage=short_coverage,
                claims=[
                    ClaimRecord(
                        claim_id=f"{content_id}:moment",
                        kind="moment_description",
                        text=title,
                        state="allowed",
                        reason="The title was explicitly approved with the rendered highlight.",
                        evidence_ids=[content_id],
                    )
                ],
                audio=AudioReview(
                    game=True,
                    discord=True,
                    microphone=bool(row.get("microphone_included")),
                    clip_level_reviewed=True,
                    notes=["Approved vertical highlight render carries clip-level audio review."],
                ),
                related_target=RelatedTarget(
                    state="planned",
                    target_content_id=archive_id,
                    target_lane="archive",
                    notes=["Review this target before any Short becomes upload-ready."],
                ),
            )
        )
    assets, recap = _preserve_reviewed_projection(
        existing_campaign,
        sources=sources,
        assets=assets,
    )
    campaign = CampaignManifest(
        campaign_id=root.name,
        created_at=created_at,
        updated_at=now,
        raid_name=config.project.raid or "Raid",
        raid_date=config.project.raid_date.isoformat() if config.project.raid_date else None,
        expected_bosses=coverage.expected_bosses,
        sources=sources,
        assets=assets,
        recap=recap,
        notes=[
            "Content lanes, source lineage, allowed claims, and review gates are explicit.",
            "Recap creation is intentionally deferred until the raid has a coherent story.",
            "The current edit style and accurate archive are preserved.",
        ],
    )
    atomic_write_json(campaign_path, campaign.model_dump(mode="json"))

    discovered_social = social_manifest_paths
    if discovered_social is None:
        output_root = root.parent
        discovered_social = sorted(output_root.glob("*/social/distribution-manifest.json"))
    remote_schedule = import_social_schedule(discovered_social)
    aliases = _social_aliases(
        discovered_social,
        current_media={asset.content_id: asset.media_sha256 for asset in assets},
    )
    short_ids = [asset.content_id for asset in assets if asset.lane == "short"]
    unpublished_short_ids = [content_id for content_id in short_ids if content_id not in aliases]
    youtube_state, _, _ = _publication_state(root, media_sha256=archive.media_sha256)
    calendar_path = growth_root / "global-content-calendar.json"
    valid_proposal_keys: set[tuple[str, str]] = set()
    if youtube_state != "verified_public":
        valid_proposal_keys.add(("youtube", archive_id))
    for content_id in unpublished_short_ids:
        valid_proposal_keys.add(("youtube", content_id))
        valid_proposal_keys.update(
            (destination, content_id)
            for destination in ("facebook", "instagram", "tiktok", "twitch")
        )
    prior_proposals = _load_prior_campaign_proposals(
        calendar_path,
        campaign_id=campaign.campaign_id,
        valid_keys=valid_proposal_keys,
    )
    prior_keys = {(row.destination, row.content_id) for row in prior_proposals}
    scheduling_context = [*remote_schedule, *prior_proposals]
    youtube_archive = propose_open_slots(
        campaign_id=campaign.campaign_id,
        content_ids=(
            []
            if youtube_state == "verified_public" or ("youtube", archive_id) in prior_keys
            else [archive_id]
        ),
        destinations=["youtube"],
        existing=scheduling_context,
        cadence_days=1,
        hour=15,
    )
    youtube_shorts = propose_open_slots(
        campaign_id=campaign.campaign_id,
        content_ids=[
            content_id
            for content_id in unpublished_short_ids
            if ("youtube", content_id) not in prior_keys
        ],
        destinations=["youtube"],
        existing=[*scheduling_context, *youtube_archive],
        cadence_days=2,
        hour=19,
    )
    social_shorts: list[ScheduleEntry] = []
    for destination in ("facebook", "instagram", "tiktok", "twitch"):
        rows = propose_open_slots(
            campaign_id=campaign.campaign_id,
            content_ids=[
                content_id
                for content_id in unpublished_short_ids
                if (destination, content_id) not in prior_keys
            ],
            destinations=[destination],
            existing=[*scheduling_context, *social_shorts],
            cadence_days=2,
            hour=19,
        )
        social_shorts.extend(rows)
    proposals = [*prior_proposals, *youtube_archive, *youtube_shorts, *social_shorts]
    _align_unreviewed_social_schedules(discovered_social, proposals, aliases)
    calendar = write_global_calendar(
        calendar_path,
        existing=remote_schedule,
        proposals=proposals,
    )
    distribution_path = growth_root / "distribution-manifest.json"
    distribution_entries = _merge_existing_distribution(
        distribution_path,
        _distribution(campaign, campaign_path, discovered_social, root),
    )
    cleanup = _cleanup_dependencies(campaign, distribution_entries)
    distribution = GrowthDistributionManifest(
        campaign_id=campaign.campaign_id,
        generated_at=now,
        campaign_manifest=campaign_path,
        entries=distribution_entries,
        schedule=calendar.entries,
        cleanup_dependencies=cleanup,
        notes=[
            "YouTube is the canonical archive; social platforms receive reviewed portrait moments.",
            "Discord receives verified public links, never a speculative local path.",
            "Existing remote schedules remain locked; all fresh dates are proposals only.",
        ],
    )
    atomic_write_json(distribution_path, distribution.model_dump(mode="json"))
    if timeline is not None:
        presentation = config.preview.presentation
        write_timeline_events(
            build_timeline_events(
                timeline,
                intro_seconds=presentation.intro_seconds if presentation else 0,
                boss_card_seconds=1.5 if presentation else 0,
                outro_seconds=presentation.outro_seconds if presentation else 0,
            ),
            growth_root / "timeline-events.json",
        )
    append_growth_event(
        growth_root / "growth-ledger.jsonl",
        GrowthLedgerEvent(
            event_id=(
                f"campaign-prepared:{campaign.campaign_id}:{campaign.updated_at:%Y%m%dT%H%M%S%f}"
            ),
            recorded_at=now,
            campaign_id=campaign.campaign_id,
            event_type="campaign_prepared",
            subject_id=campaign.campaign_id,
            payload={
                "campaign_manifest": str(campaign_path),
                "distribution_manifest": str(distribution_path),
                "short_count": len(short_ids),
                "remote_mutations": 0,
            },
        ),
    )
    thumbnails = sorted((root / "youtube").glob("thumbnail-0*.jpg"))
    if len(thumbnails) >= 2:
        append_packaging_experiment(
            growth_root / "packaging-experiments.jsonl",
            PackagingExperiment(
                experiment_id=f"{campaign.campaign_id}-thumbnail-01",
                campaign_id=campaign.campaign_id,
                content_id=archive_id,
                created_at=created_at,
                factor="thumbnail",
                hypothesis=(
                    "A boss-action image will improve qualified clicks without lowering "
                    "watch duration per impression versus a clean scoreline badge."
                ),
                control=thumbnails[0].name,
                treatment=thumbnails[1].name,
                primary_metric="watch_time_per_impression",
                guardrail_metrics=["average_view_duration", "first_30_seconds_retention"],
                minimum_runtime_hours=168,
            ),
        )
    review_path = review_root / "index.html"
    _review_html(campaign, distribution, calendar_path, review_path)
    return campaign, distribution, review_path


def approve_growth_package(campaign_path: Path, *, approved: bool) -> CampaignManifest:
    """Record one local campaign review and related-target decision."""

    if not approved:
        raise GrowthPackageError("Growth package approval requires --approved")
    campaign = CampaignManifest.model_validate_json(campaign_path.read_text(encoding="utf-8"))
    distribution_path = campaign_path.parent / "distribution-manifest.json"
    distribution = (
        GrowthDistributionManifest.model_validate_json(
            distribution_path.read_text(encoding="utf-8")
        )
        if distribution_path.is_file()
        else None
    )
    reviewable = [
        asset
        for asset in campaign.assets
        if asset.state not in {"published", "verified_public", "skipped"}
    ]
    verified: dict[Path, tuple[int, int, int, int, int]] = {}
    for asset in reviewable:
        if asset.media_path is not None and asset.media_sha256 is not None:
            verified[asset.media_path] = _verify_media_identity(
                asset.media_path, asset.media_sha256
            )
        if distribution is not None:
            for entry in distribution.entries:
                if (
                    entry.content_id == asset.content_id
                    and entry.media_sha256 != asset.media_sha256
                ):
                    raise GrowthPackageError(
                        "Distribution media identity changed; regenerate the growth package: "
                        f"{asset.content_id}"
                    )
    # Check all identities again after hashing the final asset, so an earlier
    # asset cannot change unnoticed while the remainder of the review is checked.
    for path, identity in verified.items():
        try:
            unchanged = _file_stat_identity(path) == identity
        except OSError:
            unchanged = False
        if not unchanged:
            raise GrowthPackageError(f"Reviewed media changed during approval: {path}")
    now = datetime.now(UTC)
    for asset in reviewable:
        if asset.related_target is not None and asset.related_target.state == "planned":
            asset.related_target.state = "approved"
            asset.related_target.reviewed_at = now
        asset.state = "upload_ready" if asset.media_path is not None else "reviewed"
    campaign.updated_at = now
    atomic_write_json(campaign_path, campaign.model_dump(mode="json"))
    if distribution is not None:
        for entry in distribution.entries:
            if entry.status == "prepared":
                entry.status = "reviewed"
            entry.review_approved = True
            entry.named_approvals = list(
                dict.fromkeys([*entry.named_approvals, "growth_package_review"])
            )
        atomic_write_json(distribution_path, distribution.model_dump(mode="json"))
    append_growth_event(
        campaign_path.parent / "growth-ledger.jsonl",
        GrowthLedgerEvent(
            event_id=f"campaign-reviewed:{campaign.campaign_id}:{now:%Y%m%dT%H%M%S}",
            recorded_at=now,
            campaign_id=campaign.campaign_id,
            event_type="asset_reviewed",
            subject_id=campaign.campaign_id,
            actor="Neil Mitchell",
            payload={
                "asset_ids": [asset.content_id for asset in campaign.assets],
                "related_targets_reviewed": [
                    asset.content_id
                    for asset in campaign.assets
                    if asset.related_target is not None
                ],
                "remote_mutations": 0,
            },
        ),
    )
    return campaign


def record_manual_claim_approval(
    campaign_path: Path,
    *,
    content_id: str,
    claim_id: str,
    kind: ClaimKind,
    text: str,
    reason: str,
    evidence_ids: list[str],
    approved: bool,
) -> CampaignManifest:
    """Add or approve one evidence-backed custom claim without touching a platform."""

    if not approved:
        raise GrowthPackageError("Manual claim approval requires --approved")
    if kind in {
        "full_clear",
        "progress",
        "overall_result",
        "recorded_range",
        "heroic_count",
        "raid_size",
    }:
        raise GrowthPackageError(
            "Coverage and scoreline claims cannot bypass automatic footage validation"
        )
    compact_evidence = list(dict.fromkeys(item.strip() for item in evidence_ids if item.strip()))
    if not compact_evidence:
        raise GrowthPackageError("Manual factual claims require at least one evidence ID")
    campaign = CampaignManifest.model_validate_json(campaign_path.read_text(encoding="utf-8"))
    matches = [asset for asset in campaign.assets if asset.content_id == content_id]
    if len(matches) != 1:
        raise GrowthPackageError("content-id must match exactly one campaign asset")
    asset = matches[0]
    existing = [claim for claim in asset.claims if claim.claim_id == claim_id]
    if existing:
        claim = existing[0]
        if claim.state == "rejected":
            raise GrowthPackageError("Rejected claims cannot be manually overridden")
        if claim.automatic and claim.state != "manual_review":
            raise GrowthPackageError("Allowed automatic claims do not need manual approval")
        if claim.kind != kind or claim.text != text:
            raise GrowthPackageError("Existing claim identity has different kind or text")
        claim.state = "allowed"
        claim.reason = reason
        claim.evidence_ids = compact_evidence
        claim.automatic = False
        claim.manually_approved = True
    else:
        asset.claims.append(
            ClaimRecord(
                claim_id=claim_id,
                kind=kind,
                text=text,
                state="allowed",
                reason=reason,
                evidence_ids=compact_evidence,
                automatic=False,
                manually_approved=True,
            )
        )
    now = datetime.now(UTC)
    campaign.updated_at = now
    campaign = CampaignManifest.model_validate(campaign.model_dump(mode="json"))
    atomic_write_json(campaign_path, campaign.model_dump(mode="json"))
    distribution_path = campaign_path.parent / "distribution-manifest.json"
    if distribution_path.is_file():
        distribution = GrowthDistributionManifest.model_validate_json(
            distribution_path.read_text(encoding="utf-8")
        )
        for entry in distribution.entries:
            if entry.content_id == content_id:
                entry.named_approvals = list(
                    dict.fromkeys([*entry.named_approvals, f"claim:{claim_id}"])
                )
        atomic_write_json(distribution_path, distribution.model_dump(mode="json"))
    append_growth_event(
        campaign_path.parent / "growth-ledger.jsonl",
        GrowthLedgerEvent(
            event_id=f"claim-reviewed:{campaign.campaign_id}:{claim_id}:{now:%Y%m%dT%H%M%S%f}",
            recorded_at=now,
            campaign_id=campaign.campaign_id,
            event_type="claim_reviewed",
            subject_id=claim_id,
            actor="Neil Mitchell",
            reason=reason,
            evidence_ids=compact_evidence,
            payload={
                "content_id": content_id,
                "kind": kind,
                "text": text,
                "state": "allowed",
                "remote_mutations": 0,
            },
        ),
    )
    return campaign


def record_related_target_state(
    campaign_path: Path,
    *,
    content_id: str,
    state: RelatedTargetState,
    reason: str,
    approved: bool,
    short_remote_id: str | None = None,
    short_public_url: str | None = None,
    target_url: str | None = None,
    assignment_receipt: str | None = None,
) -> CampaignManifest:
    """Record the separately approved native related-video mutation receipt."""

    if not approved:
        raise GrowthPackageError("Related-video state changes require --approved")
    if state not in {
        "assignment_pending",
        "assigned_verified",
        "blocked_capability",
        "waived_by_neil",
    }:
        raise GrowthPackageError(
            "State must be assignment_pending, assigned_verified, blocked_capability, "
            "or waived_by_neil"
        )
    campaign = CampaignManifest.model_validate_json(campaign_path.read_text(encoding="utf-8"))
    matches = [
        asset
        for asset in campaign.assets
        if asset.content_id == content_id and asset.lane == "short"
    ]
    if len(matches) != 1 or matches[0].related_target is None:
        raise GrowthPackageError("content-id must match exactly one Short with a related target")
    asset = matches[0]
    current = asset.related_target
    if current is None:
        raise GrowthPackageError("Short related target unexpectedly disappeared")
    if state in {"assignment_pending", "assigned_verified"}:
        if current.state not in {"approved", "assignment_pending", "assigned_verified"}:
            raise GrowthPackageError("Approve the related target before recording assignment")
        if not short_remote_id or not short_public_url:
            raise GrowthPackageError(
                "Assignment receipts require the observed YouTube Short ID and public URL"
            )
    if state == "assigned_verified" and (not target_url or not assignment_receipt):
        raise GrowthPackageError(
            "Verified assignment requires the observed target URL and receipt note"
        )
    now = datetime.now(UTC)
    notes = list(
        dict.fromkeys(
            [
                *current.notes,
                reason,
                "This is a local observation receipt; no Studio mutation was performed here.",
            ]
        )
    )
    asset.related_target = RelatedTarget(
        state=state,
        target_content_id=current.target_content_id,
        target_lane=current.target_lane,
        target_url=target_url or current.target_url,
        reviewed_at=current.reviewed_at or now,
        assignment_observed_at=now if state == "assigned_verified" else None,
        assignment_receipt=(assignment_receipt if state == "assigned_verified" else None),
        notes=notes,
    )
    if state == "assignment_pending":
        asset.state = "published"
    elif state == "assigned_verified" or (
        state == "waived_by_neil" and short_remote_id and short_public_url
    ):
        asset.state = "verified_public"
    elif state == "blocked_capability" and short_remote_id and short_public_url:
        asset.state = "published"
    campaign.updated_at = now
    campaign = CampaignManifest.model_validate(campaign.model_dump(mode="json"))
    distribution_path = campaign_path.parent / "distribution-manifest.json"
    distribution = GrowthDistributionManifest.model_validate_json(
        distribution_path.read_text(encoding="utf-8")
    )
    youtube_entries = [
        entry
        for entry in distribution.entries
        if entry.destination == "youtube" and entry.content_id == content_id
    ]
    if len(youtube_entries) != 1:
        raise GrowthPackageError("Consolidated manifest lacks one YouTube Short destination")
    youtube_entry = youtube_entries[0]
    if short_remote_id:
        youtube_entry.remote_content_id = short_remote_id
    if short_public_url:
        youtube_entry.public_url = short_public_url
    intent_state = {
        "assignment_pending": "pending",
        "assigned_verified": "verified",
        "blocked_capability": "blocked_capability",
        "waived_by_neil": "waived_by_neil",
    }[state]
    youtube_entry.mutation_intents["related_video"] = intent_state  # type: ignore[assignment]
    youtube_entry.named_approvals = list(
        dict.fromkeys([*youtube_entry.named_approvals, "related_video_assignment"])
    )
    youtube_entry.review_approved = True
    if state == "assignment_pending":
        youtube_entry.status = "published"
    elif state == "assigned_verified" or (
        state == "waived_by_neil" and short_remote_id and short_public_url
    ):
        youtube_entry.status = "verified_public"
    elif state == "blocked_capability" and short_remote_id and short_public_url:
        youtube_entry.status = "published"
    youtube_entry.verification_notes = list(
        dict.fromkeys([*youtube_entry.verification_notes, reason])
    )
    distribution.generated_at = now
    distribution.cleanup_dependencies = _cleanup_dependencies(campaign, distribution.entries)
    distribution = GrowthDistributionManifest.model_validate(distribution.model_dump(mode="json"))
    atomic_write_json(campaign_path, campaign.model_dump(mode="json"))
    atomic_write_json(distribution_path, distribution.model_dump(mode="json"))
    append_growth_event(
        campaign_path.parent / "growth-ledger.jsonl",
        GrowthLedgerEvent(
            event_id=(
                f"related-target:{campaign.campaign_id}:{content_id}:{state}:{now:%Y%m%dT%H%M%S%f}"
            ),
            recorded_at=now,
            campaign_id=campaign.campaign_id,
            event_type="related_target_observed",
            subject_id=content_id,
            actor="Neil Mitchell",
            reason=reason,
            evidence_ids=[item for item in [short_remote_id, assignment_receipt] if item],
            payload={
                "state": state,
                "target_content_id": current.target_content_id,
                "target_url": target_url,
                "short_public_url": short_public_url,
                "remote_mutations": 0,
            },
        ),
    )
    return campaign


def set_recap_decision(
    campaign_path: Path,
    *,
    decision: str,
    reason: str,
    approved: bool,
    candidate_beats: list[str] | None = None,
    source_ranges: list[SourceRange] | None = None,
    audio_plan: str = "not_applicable",
    estimated_active_minutes: float | None = None,
) -> CampaignManifest:
    """Record whether this raid earns a recap lane, without rendering it."""

    if not approved:
        raise GrowthPackageError("Recap decisions require --approved")
    aliases = {"create": "candidate", "skip": "skipped"}
    normalized = aliases.get(decision, decision)
    if normalized not in {"candidate", "hold", "skipped"}:
        raise GrowthPackageError("Recap decision must be candidate, hold, or skipped")
    campaign = CampaignManifest.model_validate_json(campaign_path.read_text(encoding="utf-8"))
    now = datetime.now(UTC)
    campaign.recap = RecapDecision(
        decision=normalized,  # type: ignore[arg-type]
        coherent_story=normalized == "candidate",
        reason=reason,
        reviewed_at=now,
        candidate_beats=list(candidate_beats or []),
        source_ranges=list(source_ranges or []),
        audio_plan=audio_plan,  # type: ignore[arg-type]
        estimated_active_minutes=estimated_active_minutes,
    )
    campaign.updated_at = now
    atomic_write_json(campaign_path, campaign.model_dump(mode="json"))
    append_growth_event(
        campaign_path.parent / "growth-ledger.jsonl",
        GrowthLedgerEvent(
            event_id=f"recap-decided:{campaign.campaign_id}:{now:%Y%m%dT%H%M%S%f}",
            recorded_at=now,
            campaign_id=campaign.campaign_id,
            event_type="recap_decided",
            subject_id="recap",
            actor="Neil Mitchell",
            reason=reason,
            payload={
                "decision": normalized,
                "candidate_beats": list(candidate_beats or []),
                "source_ranges": [item.model_dump(mode="json") for item in source_ranges or []],
                "audio_plan": audio_plan,
                "estimated_active_minutes": estimated_active_minutes,
                "remote_mutations": 0,
            },
        ),
    )
    return campaign


def record_packaging_experiment_state(
    campaign_path: Path,
    *,
    series_id: str,
    status: str,
    decision: str | None,
    reason: str,
    approved: bool,
) -> PackagingExperiment:
    """Append an observed experiment state without starting or changing Studio."""

    if not approved:
        raise GrowthPackageError("Experiment state receipts require --approved")
    if status not in {"running", "completed", "cancelled"}:
        raise GrowthPackageError("Experiment status must be running, completed, or cancelled")
    if status == "completed" and decision not in {
        "adopt",
        "revise",
        "retire",
        "inconclusive",
    }:
        raise GrowthPackageError(
            "Completed experiments require adopt, revise, retire, or inconclusive"
        )
    if status != "completed" and decision is not None:
        raise GrowthPackageError("Only completed experiments may include a decision")
    campaign = CampaignManifest.model_validate_json(campaign_path.read_text(encoding="utf-8"))
    experiment_path = campaign_path.parent / "packaging-experiments.jsonl"
    candidates = [
        experiment
        for experiment in load_packaging_experiments(experiment_path)
        if (experiment.series_id or experiment.experiment_id) == series_id
    ]
    if not candidates:
        raise GrowthPackageError(f"Unknown experiment series: {series_id}")
    latest = candidates[-1]
    now = datetime.now(UTC)
    version = latest.model_copy(
        update={
            "experiment_id": f"{series_id}:{status}:{now:%Y%m%dT%H%M%S%f}",
            "series_id": series_id,
            "supersedes_experiment_id": latest.experiment_id,
            "created_at": now,
            "status": status,
            "decision": decision,
            "notes": [*latest.notes, reason],
        }
    )
    version = PackagingExperiment.model_validate(version.model_dump(mode="json"))
    append_packaging_experiment(experiment_path, version)
    append_growth_event(
        campaign_path.parent / "growth-ledger.jsonl",
        GrowthLedgerEvent(
            event_id=f"experiment:{campaign.campaign_id}:{version.experiment_id}",
            recorded_at=now,
            campaign_id=campaign.campaign_id,
            event_type="experiment_recorded",
            subject_id=series_id,
            actor="Neil Mitchell",
            reason=reason,
            evidence_ids=[latest.experiment_id],
            payload={
                "status": status,
                "decision": decision,
                "factor": latest.factor,
                "remote_mutations": 0,
            },
        ),
    )
    return version


def write_growth_status(campaign_path: Path) -> tuple[dict[str, Any], Path]:
    """Write a compact readiness, scheduling, and cleanup report."""

    campaign = CampaignManifest.model_validate_json(campaign_path.read_text(encoding="utf-8"))
    distribution_path = campaign_path.parent / "distribution-manifest.json"
    distribution = GrowthDistributionManifest.model_validate_json(
        distribution_path.read_text(encoding="utf-8")
    )
    asset_counts: dict[str, int] = {}
    for asset in campaign.assets:
        asset_counts[asset.state] = asset_counts.get(asset.state, 0) + 1
    distribution_counts: dict[str, int] = {}
    for entry in distribution.entries:
        distribution_counts[entry.status] = distribution_counts.get(entry.status, 0) + 1
    cleanup_ready = [
        row.dependency_id for row in distribution.cleanup_dependencies if row.cleanup_eligible
    ]
    cleanup_held = [
        row.dependency_id for row in distribution.cleanup_dependencies if not row.cleanup_eligible
    ]
    events = load_growth_events(campaign_path.parent / "growth-ledger.jsonl")
    active_operator_minutes = sum(event.active_operator_minutes or 0 for event in events)
    unattended_runtime_seconds = sum(event.unattended_runtime_seconds or 0 for event in events)
    time_by_lane: dict[str, dict[str, float]] = {}
    for event in events:
        if event.event_type != "production_time_recorded":
            continue
        lane = str(event.payload.get("content_lane") or "campaign")
        totals = time_by_lane.setdefault(lane, {"active_minutes": 0, "unattended_seconds": 0})
        totals["active_minutes"] += event.active_operator_minutes or 0
        totals["unattended_seconds"] += event.unattended_runtime_seconds or 0
    report: dict[str, Any] = {
        "campaign_id": campaign.campaign_id,
        "asset_state_counts": asset_counts,
        "distribution_state_counts": distribution_counts,
        "short_count": sum(asset.lane == "short" for asset in campaign.assets),
        "locked_schedule_count": sum(row.lock == "locked_remote" for row in distribution.schedule),
        "proposed_schedule_count": sum(row.lock == "proposed" for row in distribution.schedule),
        "cleanup_ready": cleanup_ready,
        "cleanup_held": cleanup_held,
        "active_operator_minutes": active_operator_minutes,
        "unattended_runtime_seconds": unattended_runtime_seconds,
        "time_by_lane": time_by_lane,
        "remote_mutations_performed": False,
    }
    calendar_path = campaign_path.parent / "global-content-calendar.json"
    collision_warnings: list[str] = []
    if calendar_path.is_file():
        calendar_payload = json.loads(calendar_path.read_text(encoding="utf-8"))
        raw_collisions = calendar_payload.get("collision_warnings", [])
        if isinstance(raw_collisions, list):
            collision_warnings = [str(item) for item in raw_collisions]
    report["collision_warnings"] = collision_warnings
    destination = campaign_path.parent / "growth-status.md"
    lines = [
        "# Growth workflow status",
        "",
        f"- Campaign: {campaign.campaign_id}",
        f"- Shorts selected: {report['short_count']}/2",
        f"- Locked remote schedule entries preserved: {report['locked_schedule_count']}",
        f"- New local schedule proposals: {report['proposed_schedule_count']}",
        "- Remote mutations performed: no",
        f"- Active operator time recorded: {active_operator_minutes:.2f} minutes",
        f"- Unattended machine time recorded: {unattended_runtime_seconds / 60:.2f} minutes",
        f"- Schedule collision warnings: {len(collision_warnings)}",
        "",
        "## Production time by lane",
        "",
        *[
            f"- {lane}: {totals['active_minutes']:.2f} active minutes; "
            f"{totals['unattended_seconds'] / 60:.2f} unattended minutes"
            for lane, totals in sorted(time_by_lane.items())
        ],
        "",
        "## Asset states",
        "",
        *[f"- {key}: {value}" for key, value in sorted(asset_counts.items())],
        "",
        "## Distribution states",
        "",
        *[f"- {key}: {value}" for key, value in sorted(distribution_counts.items())],
        "",
        "## Cleanup",
        "",
        f"- Eligible: {', '.join(cleanup_ready) or 'none'}",
        f"- Held: {', '.join(cleanup_held) or 'none'}",
    ]
    atomic_write_text(destination, "\n".join(lines) + "\n")
    return report, destination
