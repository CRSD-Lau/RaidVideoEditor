"""Reusable project workflows behind the CLI."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import TypeAdapter

from raid_editor.audio.analysis import (
    measure_volume_samples,
    write_audio_report,
)
from raid_editor.audio.tracks import (
    create_audio_samples,
    create_mic_free_remux,
    generate_audio_review_page,
    validate_audio_mapping,
)
from raid_editor.classification.difficulty import (
    DETECTOR_VERSION,
    classify_pull_difficulties,
    scoreline_title_prefix,
    summarize_raid_progress,
    write_difficulty_report,
)
from raid_editor.config.loader import project_output_dir
from raid_editor.config.models import PresentationConfig, ProjectConfig
from raid_editor.detection.pipeline import analyse_pulls
from raid_editor.highlights.detection import (
    Signal,
    analyse_highlights,
    load_highlight_selection,
    select_highlight_candidates,
    write_highlight_candidates,
)
from raid_editor.highlights.feedback import load_editorial_feedback, record_editorial_feedback
from raid_editor.highlights.intelligence import (
    analyse_intelligent_highlights,
    intelligence_runtime_signature,
)
from raid_editor.highlights.local_runtime import managed_local_intelligence
from raid_editor.highlights.portrait import PortraitSource, resolve_portrait_source
from raid_editor.highlights.render import (
    portrait_presentation_reference,
    render_vertical_highlights,
)
from raid_editor.highlights.review import (
    generate_highlight_comparison_page,
    generate_highlight_review_media,
    generate_highlight_review_page,
)
from raid_editor.highlights.speech import (
    SpeechTriggerResult,
    detect_spoken_commands,
    speech_runtime_signature,
    speech_trigger_result_from_dict,
)
from raid_editor.ingestion.probe import MediaProbe, probe_media
from raid_editor.models import HighlightCandidate, PullCandidate, TimelineDocument
from raid_editor.music.library import (
    MusicTrack,
    approved_tracks,
    load_music_library,
    write_music_plan,
    write_music_reports,
)
from raid_editor.rendering.preview import FinalRenderError, render_final, render_preview
from raid_editor.reporting.pulls import write_pull_candidates, write_uncertain_segments
from raid_editor.reporting.summary import (
    validate_artifacts,
    write_analysis_summary,
    write_edit_summary,
    write_validation_report,
)
from raid_editor.resolve.bridge import write_bridge_payload
from raid_editor.review.html import generate_pull_media, generate_pull_review_page
from raid_editor.timeline.builder import build_timeline
from raid_editor.timeline.export import (
    write_chapters,
    write_fcpxml,
    write_labels_srt,
    write_timeline_json,
)
from raid_editor.util.paths import (
    atomic_write_json,
    atomic_write_text,
    ensure_directory,
    full_file_sha256,
    quick_file_fingerprint,
)
from raid_editor.youtube.upload import (
    YouTubePackage,
    YouTubeUploadError,
    YouTubeUploadResult,
    upload_youtube_video,
    write_youtube_package,
)

_PULL_LIST = TypeAdapter(list[PullCandidate])
_HIGHLIGHT_LIST = TypeAdapter(list[HighlightCandidate])
_ANALYSIS_SCHEMA_VERSION = 5
_HIGHLIGHT_SCHEMA_VERSION = 4
_SPEECH_TRIGGER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    analysis: Path
    review: Path
    timeline: Path
    generated_assets: Path
    preview: Path
    final_master: Path
    reports: Path
    resolve: Path
    highlights: Path
    analytics: Path
    archive: Path

    @classmethod
    def for_config(cls, config: ProjectConfig) -> ProjectPaths:
        root = project_output_dir(config)
        return cls(
            root=root,
            analysis=root / "analysis",
            review=root / "review",
            timeline=root / "timeline",
            generated_assets=root / "generated-assets",
            preview=root / "preview",
            final_master=root / "final",
            reports=root / "reports",
            resolve=root / "resolve",
            highlights=root / "highlights",
            analytics=root / "analytics",
            archive=root / "archive",
        )

    def create(self) -> ProjectPaths:
        for path in (
            self.analysis,
            self.review,
            self.timeline,
            self.generated_assets,
            self.preview,
            self.final_master,
            self.reports,
            self.resolve,
            self.highlights,
            self.analytics,
            self.archive,
        ):
            ensure_directory(path)
        return self


def inspect_project(
    config: ProjectConfig,
    *,
    create_samples: bool = True,
    force: bool = False,
) -> tuple[MediaProbe, ProjectPaths]:
    paths = ProjectPaths.for_config(config).create()
    probe = probe_media(
        config.input.recording,
        paths.analysis / "media-probe.json",
        force=force,
    )
    if create_samples:
        samples = create_audio_samples(
            config.input.recording,
            probe,
            paths.review / "audio-samples",
        )
        generate_audio_review_page(
            probe,
            samples,
            paths.review / "audio-track-review.html",
        )
    return probe, paths


def analyse_project(
    config: ProjectConfig,
    *,
    create_review_media: bool = True,
) -> tuple[MediaProbe, list[PullCandidate], ProjectPaths]:
    probe, paths = inspect_project(config, create_samples=True)
    signature = {
        "analysis_schema_version": _ANALYSIS_SCHEMA_VERSION,
        "recording": probe.source,
        "combat_log": (
            quick_file_fingerprint(config.input.combat_log)
            if config.input.combat_log is not None and config.input.combat_log.is_file()
            else None
        ),
        "skada_export": (
            quick_file_fingerprint(config.input.skada_export)
            if config.input.skada_export is not None and config.input.skada_export.is_file()
            else None
        ),
        "manual_pulls": (
            quick_file_fingerprint(config.input.manual_pulls)
            if config.input.manual_pulls is not None and config.input.manual_pulls.is_file()
            else None
        ),
        "detection": config.detection.model_dump(mode="json"),
        "difficulty": config.difficulty.model_dump(mode="json"),
        "difficulty_detector_version": DETECTOR_VERSION,
    }
    manifest_path = paths.analysis / "analysis-manifest.json"
    candidates_path = paths.analysis / "pull-candidates.json"
    cached = False
    if manifest_path.is_file() and candidates_path.is_file():
        try:
            cached_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if cached_manifest == signature:
                pulls = _PULL_LIST.validate_json(candidates_path.read_text(encoding="utf-8"))
                cached = True
        except (OSError, ValueError, json.JSONDecodeError):
            cached = False
    if not cached:
        pulls = analyse_pulls(
            recording=config.input.recording,
            recording_duration_seconds=probe.duration_seconds,
            settings=config.detection,
            combat_log=config.input.combat_log,
            skada_export=config.input.skada_export,
            manual_pulls=config.input.manual_pulls,
            issues_destination=paths.analysis / "combat-log-issues.json",
        )
        pulls = classify_pull_difficulties(
            pulls,
            combat_log=config.input.combat_log,
            recording_started_at=config.detection.recording_started_at,
            recording_duration_seconds=probe.duration_seconds,
            recording_offset_seconds=config.detection.combat_log_offset_seconds,
            settings=config.difficulty,
        )
        write_pull_candidates(
            pulls,
            candidates_path,
            paths.analysis / "pull-candidates.csv",
        )
        atomic_write_json(manifest_path, signature)
    progress = summarize_raid_progress(
        pulls,
        raid_name=config.project.raid,
        settings=config.difficulty,
    )
    write_difficulty_report(
        pulls,
        progress,
        json_destination=paths.analysis / "difficulty.json",
        markdown_destination=paths.reports / "difficulty.md",
    )
    write_uncertain_segments(
        pulls,
        paths.reports / "uncertain-segments.md",
        config.detection.confidence_threshold,
    )
    write_analysis_summary(
        probe=probe,
        pulls=pulls,
        audio_issues=validate_audio_mapping(config.audio, probe),
        confidence_threshold=config.detection.confidence_threshold,
        destination=paths.reports / "analysis-summary.md",
    )
    if create_review_media:
        full_pull_review = config.preview.review_clip_mode == "full"
        assets = generate_pull_media(
            config.input.recording,
            pulls,
            paths.review,
            config.audio.retained_stream_indexes(),
            media_format=config.preview.review_media_format,
            max_preview_seconds=(None if full_pull_review else config.preview.review_clip_seconds),
            lead_in_seconds=(config.detection.pre_roll_seconds if full_pull_review else 0.0),
            lead_out_seconds=(config.detection.post_roll_seconds if full_pull_review else 0.0),
            recording_duration_seconds=probe.duration_seconds,
        )
        generate_pull_review_page(
            pulls,
            assets,
            paths.review / "pull-review.html",
        )
    return probe, pulls, paths


def _highlight_audio_streams(config: ProjectConfig) -> list[int]:
    indexes: list[int] = []
    if config.highlights.keep_game_audio and config.audio.game_track is not None:
        indexes.append(config.audio.game_track)
    if config.highlights.keep_discord_audio and config.audio.discord_track is not None:
        indexes.append(config.audio.discord_track)
    if config.audio.microphone_track is not None and config.audio.microphone_track in indexes:
        raise ValueError(
            "The configured microphone stream cannot also be a highlight game/Discord stream"
        )
    if config.highlights.keep_microphone_audio:
        if config.audio.microphone_track is None:
            raise ValueError(
                "Highlight microphone audio is enabled but audio.microphone_track is not configured"
            )
        indexes.append(config.audio.microphone_track)
    indexes = list(dict.fromkeys(indexes))
    if not indexes:
        raise ValueError("Highlight review requires at least one enabled audio stream")
    return indexes


def _highlight_source_reference(candidates_path: Path, source: dict[str, object]) -> str:
    """Keep review identities scoped to one exact recording, including rescans."""
    fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{candidates_path.resolve()}#recording={fingerprint}"


def _portrait_source(config: ProjectConfig, paths: ProjectPaths) -> PortraitSource | None:
    """Keep unavailable native footage local to the highlight media lane."""
    try:
        source = resolve_portrait_source(config, paths.highlights)
    except (ValueError, OSError, RuntimeError) as exc:
        atomic_write_json(
            paths.highlights / "portrait-source-status.json",
            {
                "status": "blocked",
                "detail": str(exc),
                "author": "Neil Mitchell",
                "last_modified_by": "Neil Mitchell",
            },
        )
        raise
    atomic_write_json(
        paths.highlights / "portrait-source-status.json",
        {
            "status": "verified" if source else "landscape_mode",
            "author": "Neil Mitchell",
            "last_modified_by": "Neil Mitchell",
        },
    )
    return source


def _selection_presentation_reference(path: Path | None) -> str | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    value = payload.get("presentation_reference") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


def _presentation_reference(config: ProjectConfig, source: PortraitSource | None) -> str | None:
    return (
        portrait_presentation_reference(
            source,
            audio_stream_indexes=_highlight_audio_streams(config),
            resolution=config.highlights.vertical_resolution,
        )
        if source
        else None
    )


def _review_source_reference(
    path: Path,
    source: dict[str, object],
    presentation: str | None,
) -> str:
    reference = _highlight_source_reference(path, source)
    return f"{reference}&presentation={presentation}" if presentation else reference


def analyse_highlights_project(
    config: ProjectConfig,
    *,
    create_review_media: bool = True,
) -> tuple[list[HighlightCandidate], ProjectPaths]:
    if not config.highlights.enabled or config.highlights.manual_selection is not None:
        return _analyse_highlights_project(config, create_review_media=create_review_media)
    with managed_local_intelligence(config.highlights.intelligence):
        return _analyse_highlights_project(config, create_review_media=create_review_media)


def _analyse_highlights_project(
    config: ProjectConfig,
    *,
    create_review_media: bool = True,
) -> tuple[list[HighlightCandidate], ProjectPaths]:
    probe, pulls, paths = analyse_project(config, create_review_media=False)
    if not config.highlights.enabled:
        disabled_status: dict[str, object] = {
            "schema_version": 1,
            "author": "Neil Mitchell",
            "last_modified_by": "Neil Mitchell",
            "status": "disabled",
            "transcript_persisted": False,
            "cacheable": False,
        }
        atomic_write_json(paths.highlights / "intelligence-status.json", disabled_status)
        atomic_write_json(
            paths.highlights / "analysis-manifest.json",
            {
                "signature": {"recording": probe.source, "highlights_enabled": False},
                "cacheable": False,
            },
        )
        write_highlight_candidates(
            [],
            json_destination=paths.highlights / "candidates.json",
            markdown_destination=paths.reports / "highlight-candidates.md",
        )
        if create_review_media:
            generate_highlight_review_page(
                [],
                {},
                paths.highlights / "review" / "index.html",
                includes_game=False,
                includes_discord=False,
                includes_microphone=False,
                intelligence_status=disabled_status,
                source_reference=_highlight_source_reference(
                    paths.highlights / "candidates.json", probe.source
                ),
            )
        return [], paths
    audio_streams = _highlight_audio_streams(config)
    known_streams = {stream.index for stream in probe.audio_streams}
    unknown = [stream for stream in audio_streams if stream not in known_streams]
    if unknown:
        raise ValueError(f"Highlight audio references missing streams: {unknown}")
    candidates_path = paths.highlights / "candidates.json"
    manifest_path = paths.highlights / "analysis-manifest.json"
    speech_status_path = paths.highlights / "speech-trigger-status.json"
    speech_manifest_path = paths.highlights / "speech-trigger-manifest.json"
    speech_report_path = paths.reports / "speech-trigger-status.md"
    speech_result: SpeechTriggerResult | None = None
    intelligence_status_path = paths.highlights / "intelligence-status.json"
    intelligence_status: dict[str, object] | None = None
    intelligence_settings = config.highlights.intelligence
    if intelligence_status_path.is_file():
        try:
            payload = json.loads(intelligence_status_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                intelligence_status = payload
        except (OSError, ValueError):
            pass
    if config.highlights.manual_selection is not None:
        candidates = load_highlight_selection(config.highlights.manual_selection)
        # A reviewed override must never be reused as an automatic, unapproved
        # proposal batch if the operator later clears manual_selection.
        atomic_write_json(
            manifest_path,
            {
                "signature": {
                    "recording": probe.source,
                    "manual_selection": quick_file_fingerprint(config.highlights.manual_selection),
                },
                "cacheable": False,
                "author": "Neil Mitchell",
                "last_modified_by": "Neil Mitchell",
            },
        )
        if intelligence_settings.feedback_path is not None:
            record_editorial_feedback(
                config.highlights.manual_selection, intelligence_settings.feedback_path
            )
        if speech_status_path.is_file():
            try:
                cached_status = json.loads(speech_status_path.read_text(encoding="utf-8"))
                if isinstance(cached_status, dict):
                    speech_result = speech_trigger_result_from_dict(cached_status)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                speech_result = None
    else:
        speech_settings = config.highlights.speech_triggers
        source_streams = {
            "discord": config.audio.discord_track,
            "microphone": config.audio.microphone_track,
        }
        speech_signature = {
            "schema_version": _SPEECH_TRIGGER_SCHEMA_VERSION,
            "recording": probe.source,
            "source_streams": source_streams,
            "settings": speech_settings.model_dump(mode="json"),
            "runtime": speech_runtime_signature(speech_settings),
        }
        if speech_status_path.is_file() and speech_manifest_path.is_file():
            try:
                cached_manifest = json.loads(speech_manifest_path.read_text(encoding="utf-8"))
                cached_payload = json.loads(speech_status_path.read_text(encoding="utf-8"))
                if (
                    isinstance(cached_manifest, dict)
                    and cached_manifest.get("signature") == speech_signature
                    and cached_manifest.get("cacheable") is True
                    and isinstance(cached_payload, dict)
                ):
                    cached_result = speech_trigger_result_from_dict(cached_payload)
                    if cached_result.cacheable:
                        speech_result = cached_result
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                speech_result = None
        if speech_result is None:
            speech_result = detect_spoken_commands(
                config.input.recording,
                source_streams=source_streams,  # type: ignore[arg-type]
                settings=speech_settings,
            )
            atomic_write_json(speech_status_path, speech_result.to_dict())
            atomic_write_json(
                speech_manifest_path,
                {
                    "signature": speech_signature,
                    "cacheable": speech_result.cacheable,
                },
            )
        speech_report = [
            "# Spoken highlight command status",
            "",
            f"- Status: `{speech_result.status}`",
            f"- Backend: `{speech_result.backend}`",
            f"- Backend version: `{speech_result.backend_version or 'unavailable'}`",
            f"- Model: `{speech_result.model_identity or 'unavailable'}`",
            f"- Completed sources: `{', '.join(speech_result.completed_sources) or 'none'}`",
            f"- Exact command matches: `{len(speech_result.events)}`",
            "- Full transcript persisted: `no`",
        ]
        if speech_result.diagnostics:
            speech_report.extend(
                ["", "## Diagnostics", "", *(f"- {item}" for item in speech_result.diagnostics)]
            )
        atomic_write_text(speech_report_path, "\n".join(speech_report) + "\n")
        spoken_command_signals = [
            Signal(
                seconds=event.end_seconds,
                kind="speech_clip_command",
                strength=event.confidence,
                detail=(
                    f"speech_clip_command:{event.source_role}:{event.phrase}:{event.confidence:.2f}"
                ),
            )
            for event in speech_result.events
        ]
        signature = {
            "schema_version": _HIGHLIGHT_SCHEMA_VERSION,
            "recording": probe.source,
            "combat_log": (
                quick_file_fingerprint(config.input.combat_log)
                if config.input.combat_log is not None and config.input.combat_log.is_file()
                else None
            ),
            "pulls": [pull.model_dump(mode="json") for pull in pulls],
            "highlights": config.highlights.model_dump(mode="json"),
            "audio_streams": audio_streams,
            "source_streams": source_streams,
            "recording_started_at": (
                config.detection.recording_started_at.isoformat()
                if config.detection.recording_started_at is not None
                else None
            ),
            "recording_offset_seconds": config.detection.combat_log_offset_seconds,
            "speech_triggers": speech_result.to_dict(),
            "intelligence_runtime": intelligence_runtime_signature(intelligence_settings),
            "editorial_feedback": load_editorial_feedback(intelligence_settings.feedback_path),
        }
        cached = False
        if candidates_path.is_file() and manifest_path.is_file():
            try:
                cached_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    isinstance(cached_manifest, dict)
                    and cached_manifest.get("signature") == signature
                    and cached_manifest.get("cacheable") is True
                    and (
                        not intelligence_settings.enabled
                        or (
                            intelligence_status is not None
                            and intelligence_status.get("status") == "complete"
                        )
                    )
                ):
                    candidates = _HIGHLIGHT_LIST.validate_json(
                        candidates_path.read_text(encoding="utf-8")
                    )
                    cached = True
            except (OSError, ValueError, json.JSONDecodeError):
                cached = False
        if not cached:
            candidates = analyse_highlights(
                config.input.recording,
                pulls,
                game_stream_index=config.audio.game_track,
                discord_stream_index=config.audio.discord_track,
                microphone_stream_index=config.audio.microphone_track,
                combat_log=config.input.combat_log,
                recording_started_at=config.detection.recording_started_at,
                recording_duration_seconds=probe.duration_seconds,
                recording_offset_seconds=config.detection.combat_log_offset_seconds,
                settings=config.highlights,
                spoken_command_signals=spoken_command_signals,
                limit_candidates=not intelligence_settings.enabled,
            )
            intelligence_cacheable = True
            if intelligence_settings.enabled:
                pool = candidates
                baseline = select_highlight_candidates(pool, settings=config.highlights)
                for name, rows in (
                    ("discovery-pool.json", pool),
                    ("repaired-baseline.json", baseline),
                ):
                    atomic_write_json(
                        paths.highlights / name,
                        {
                            "schema_version": 1,
                            "author": "Neil Mitchell",
                            "last_modified_by": "Neil Mitchell",
                            "source": probe.source,
                            "highlights": [row.model_dump(mode="json") for row in rows],
                        },
                    )
                result = analyse_intelligent_highlights(
                    config.input.recording,
                    source_streams=source_streams,
                    recording_duration_seconds=probe.duration_seconds,
                    pulls=pulls,
                    heuristic_candidates=pool,
                    settings=intelligence_settings,
                    feedback=load_editorial_feedback(intelligence_settings.feedback_path),
                )
                intelligence_status = result.to_dict()
                intelligence_cacheable = result.cacheable
                atomic_write_json(intelligence_status_path, intelligence_status)
                if intelligence_settings.required and result.status != "complete":
                    atomic_write_json(
                        manifest_path,
                        {
                            "signature": signature,
                            "cacheable": False,
                            "author": "Neil Mitchell",
                            "last_modified_by": "Neil Mitchell",
                        },
                    )
                    raise RuntimeError(
                        f"Required highlight intelligence did not complete ({result.status}); "
                        f"see {intelligence_status_path} for coverage and diagnostics"
                    )
                command_candidates = [
                    row
                    for row in pool
                    if row.origin == "speech"
                    or any(signal.startswith("speech_clip_command:") for signal in row.signals)
                ]
                if intelligence_status.get("status") == "complete":
                    proposals = [*result.candidates, *command_candidates]
                else:
                    proposals = [*result.candidates, *baseline]
                candidates = select_highlight_candidates(
                    proposals,
                    settings=config.highlights.model_copy(
                        update={"maximum_candidates": intelligence_settings.maximum_candidates}
                    ),
                )
            else:
                intelligence_status = {
                    "schema_version": 1,
                    "author": "Neil Mitchell",
                    "last_modified_by": "Neil Mitchell",
                    "status": "disabled",
                    "transcript_persisted": False,
                    "cacheable": True,
                }
                atomic_write_json(intelligence_status_path, intelligence_status)
            atomic_write_json(
                candidates_path,
                [candidate.model_dump(mode="json") for candidate in candidates],
            )
            atomic_write_json(
                manifest_path,
                {
                    "signature": signature,
                    "cacheable": speech_result.cacheable and intelligence_cacheable,
                },
            )
    write_highlight_candidates(
        candidates,
        json_destination=candidates_path,
        markdown_destination=paths.reports / "highlight-candidates.md",
    )
    if intelligence_status is not None:
        status = str(intelligence_status.get("status", "unknown"))
        report = [
            "# Highlight intelligence status",
            "",
            "Author: Neil Mitchell",
            "",
            f"- Coverage: `{status}`",
            f"- Local model: `{intelligence_settings.model}`",
            f"- Review candidates: {len(candidates)}",
            "- Transcript and decoded voice audio persisted: no",
            "- Recommendations are unapproved; scores do not establish editorial quality.",
            "- A degraded pass uses corrected heuristic suggestions and is not a complete AI scan.",
            "",
            "See highlights/intelligence-status.json for source coverage and model provenance.",
        ]
        atomic_write_text(paths.reports / "highlight-intelligence.md", "\n".join(report) + "\n")
    if create_review_media:
        portrait = _portrait_source(config, paths)
        presentation_reference = _presentation_reference(config, portrait)
        # Preserve editorial ratings, but do not carry a selected export across
        # a change of picture, timing or audio. The source files remain untouched.
        if (
            config.highlights.manual_selection is not None
            and _selection_presentation_reference(config.highlights.manual_selection)
            != presentation_reference
        ):
            candidates = [row.model_copy(update={"include": False}) for row in candidates]
            write_highlight_candidates(
                candidates,
                json_destination=candidates_path,
                markdown_destination=paths.reports / "highlight-candidates.md",
            )
        assets = generate_highlight_review_media(
            config.input.recording,
            candidates,
            paths.highlights / "review",
            audio_stream_indexes=audio_streams,
            media_format=config.preview.review_media_format,
            **({"portrait_source": portrait} if portrait is not None else {}),
        )
        generate_highlight_review_page(
            candidates,
            assets,
            paths.highlights / "review" / "index.html",
            includes_game=(
                config.highlights.keep_game_audio and config.audio.game_track is not None
            ),
            includes_discord=(
                config.highlights.keep_discord_audio and config.audio.discord_track is not None
            ),
            includes_microphone=(
                config.highlights.keep_microphone_audio
                and config.audio.microphone_track is not None
            ),
            speech_status=(speech_result.to_dict() if speech_result is not None else None),
            intelligence_status=intelligence_status,
            source_reference=_review_source_reference(
                candidates_path,
                probe.source,
                presentation_reference,
            ),
            presentation_reference=presentation_reference,
            native_portrait=portrait is not None,
        )
    return candidates, paths


def prepare_highlight_comparison(config: ProjectConfig) -> Path:
    """Blindly compare saved proposals from the same source without approving exports."""
    probe, _, paths = analyse_project(config, create_review_media=False)
    baseline_path = paths.highlights / "repaired-baseline.json"
    candidate_path = paths.highlights / "candidates.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    manifest = json.loads((paths.highlights / "analysis-manifest.json").read_text(encoding="utf-8"))
    signature = manifest.get("signature") if isinstance(manifest, dict) else None
    if (
        not isinstance(baseline, dict)
        or baseline.get("source") != probe.source
        or not isinstance(signature, dict)
        or signature.get("recording") != probe.source
    ):
        raise ValueError("Comparison proposals must match the current recording fingerprint")
    variants = {
        "repaired_heuristics": _HIGHLIGHT_LIST.validate_python(baseline.get("highlights")),
        "current_recommendations": load_highlight_selection(candidate_path),
    }
    root = paths.highlights / "comparison"
    portrait = _portrait_source(config, paths)
    presentation_reference = _presentation_reference(config, portrait)
    prepared = {}
    for name, rows in variants.items():
        unapproved = [row.model_copy(update={"include": False}) for row in rows]
        asset_directory = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        assets = generate_highlight_review_media(
            config.input.recording,
            unapproved,
            root / "variants" / asset_directory,
            audio_stream_indexes=_highlight_audio_streams(config),
            media_format=config.preview.review_media_format,
            **({"portrait_source": portrait} if portrait is not None else {}),
        )
        prepared[name] = (unapproved, assets)
    comparison = generate_highlight_comparison_page(
        prepared,
        root / "index.html",
        source_reference=_review_source_reference(
            candidate_path,
            probe.source,
            presentation_reference,
        ),
        includes_game=config.highlights.keep_game_audio,
        includes_discord=config.highlights.keep_discord_audio,
        includes_microphone=config.highlights.keep_microphone_audio,
        presentation_reference=presentation_reference,
    )
    atomic_write_json(root / "comparison-manifest.json", comparison)
    return root / "index.html"


def render_highlights_project(
    config: ProjectConfig,
    *,
    approved: bool,
    dry_run: bool = False,
) -> tuple[list[Path], ProjectPaths]:
    source_paths = ProjectPaths.for_config(config).create()
    portrait = _portrait_source(config, source_paths)
    current_reference = _presentation_reference(config, portrait)
    selected_reference = _selection_presentation_reference(config.highlights.manual_selection)
    if (portrait is not None and selected_reference != current_reference) or (
        portrait is None and selected_reference is not None
    ):
        raise ValueError(
            "Highlight selection does not match the current picture, timing and audio. "
            "Regenerate the highlight review and download a new selection before export."
        )
    candidates, paths = analyse_highlights_project(config, create_review_media=False)
    outputs = render_vertical_highlights(
        config.input.recording,
        candidates,
        paths.highlights / "vertical",
        audio_stream_indexes=_highlight_audio_streams(config),
        microphone_stream_index=config.audio.microphone_track,
        settings=config.highlights,
        approved=approved,
        dry_run=dry_run,
        **({"portrait_source": portrait} if portrait is not None else {}),
    )
    return outputs, paths


def _load_saved_pulls(config: ProjectConfig, paths: ProjectPaths) -> list[PullCandidate]:
    del paths
    return analyse_project(config, create_review_media=False)[1]


def build_timeline_project(
    config: ProjectConfig,
    *,
    resolve_exports: bool = True,
) -> tuple[MediaProbe, list[PullCandidate], TimelineDocument, Path | None, ProjectPaths]:
    """Build a timeline, optionally materializing the full-size Resolve-safe source.

    FFmpeg reads the original recording with explicit audio-stream selection; it
    does not need the separate remux used by Resolve/FCPXML imports.
    """
    paths = ProjectPaths.for_config(config).create()
    probe = probe_media(config.input.recording, paths.analysis / "media-probe.json")
    issues = validate_audio_mapping(config.audio, probe)
    if issues:
        raise ValueError("; ".join(issues))
    pulls = _load_saved_pulls(config, paths)
    if not pulls:
        raise ValueError("No pull candidates are available for the timeline")
    video = probe.video_streams[0] if probe.video_streams else None
    if video is None or video.frame_rate is None:
        raise ValueError("Source video stream or frame rate is unavailable")
    timeline = build_timeline(
        name=f"{config.project.raid or config.project.name} Condensed Review",
        source=str(config.input.recording),
        source_duration_seconds=probe.duration_seconds,
        source_fps=video.frame_rate,
        retained_audio_stream_indexes=config.audio.retained_stream_indexes(),
        excluded_microphone_stream_index=config.audio.microphone_track,
        pulls=pulls,
        detection=config.detection,
        editing=config.editing,
    )
    if not timeline.clips:
        raise ValueError("All detected pulls were excluded by the current edit policy")
    write_timeline_json(timeline, paths.timeline / "timeline.json")
    write_labels_srt(timeline, paths.timeline / "pull-labels.srt")
    write_chapters(timeline, paths.reports / "chapters.txt")
    if not resolve_exports:
        return probe, pulls, timeline, None, paths
    sidecar = create_mic_free_remux(
        config.input.recording,
        config.audio.retained_stream_indexes(),
        config.audio.microphone_track,
        paths.generated_assets / "source-microphone-free.mov",
    )
    width = video.width
    height = video.height
    write_fcpxml(
        timeline,
        paths.timeline / "timeline.fcpxml",
        media_path=sidecar,
        width=width,
        height=height,
    )
    raid_date = config.project.raid_date or date.today()
    resolve_name = f"WoW Raid Editor - {config.project.raid or config.project.name} - {raid_date}"
    write_bridge_payload(
        timeline,
        paths.resolve / "create-project.json",
        project_name=resolve_name,
        media_path=sidecar,
    )
    return probe, pulls, timeline, sidecar, paths


def selected_music(config: ProjectConfig) -> list[MusicTrack]:
    library = load_music_library(config.music.library)
    return approved_tracks(library, config.music.approved_track_ids)


def _resolved_presentation(
    config: ProjectConfig,
    pulls: list[PullCandidate],
) -> PresentationConfig | None:
    presentation = config.preview.presentation
    if presentation is None or presentation.outro_subtitle is not None:
        return presentation
    progress = summarize_raid_progress(
        pulls,
        raid_name=config.project.raid,
        settings=config.difficulty,
    )
    scoreline = scoreline_title_prefix(
        progress,
        raid_name=config.project.raid,
        settings=config.difficulty,
    ).removesuffix(" Full Clear")
    raid_date = config.project.raid_date or date.today()
    date_label = f"{raid_date.strftime('%B')} {raid_date.day}, {raid_date.year}".upper()
    return presentation.model_copy(update={"outro_subtitle": f"{scoreline} / {date_label}"})


def render_preview_project(
    config: ProjectConfig,
    *,
    dry_run: bool = False,
) -> tuple[Path, ProjectPaths]:
    probe, pulls, timeline, _, paths = build_timeline_project(config, resolve_exports=False)
    music = selected_music(config)
    presentation = _resolved_presentation(config, pulls)
    pull_csv = paths.analysis / "pull-candidates.csv"
    if pull_csv.is_file():
        atomic_write_text(
            paths.reports / "pull-list.csv",
            pull_csv.read_text(encoding="utf-8"),
        )
    write_music_reports(
        music,
        paths.reports / "music-licence-report.md",
        paths.reports / "youtube-attribution.txt",
    )
    write_music_plan(music, paths.reports / "music-plan.md")
    preview = paths.preview / f"{paths.root.name}-review-720p.mp4"
    render_preview(
        timeline,
        preview,
        resolution=config.preview.resolution,
        fps=config.preview.fps,
        bitrate=config.preview.bitrate,
        transition_seconds=config.editing.transition_duration_seconds,
        music=music[0] if music else None,
        hardware_encoding=config.preview.hardware_encoding,
        watermark=config.preview.watermark,
        presentation=presentation,
        dry_run=dry_run,
    )
    retained_names = [
        stream.title or f"stream {stream.index}"
        for stream in probe.audio_streams
        if stream.index in timeline.retained_audio_stream_indexes
    ]
    removed_names = [
        stream.title or f"stream {stream.index}"
        for stream in probe.audio_streams
        if stream.index == timeline.excluded_microphone_stream_index
    ]
    write_edit_summary(
        probe=probe,
        pulls=pulls,
        timeline=timeline,
        retained_audio=retained_names,
        removed_audio=removed_names,
        music=music,
        confidence_threshold=config.detection.confidence_threshold,
        destination=paths.reports / "edit-summary.md",
    )
    if not dry_run and preview.is_file():
        before = {
            stream_index: measure_volume_samples(
                config.input.recording,
                stream_index=stream_index,
                duration_seconds=probe.duration_seconds,
            )
            for stream_index in timeline.retained_audio_stream_indexes
        }
        preview_probe = probe_media(preview)
        after = measure_volume_samples(
            preview,
            stream_index=preview_probe.audio_streams[0].index,
            duration_seconds=preview_probe.duration_seconds,
        )
        write_audio_report(before, after, paths.reports / "audio-analysis.md")
    return preview, paths


def _final_output_settings(
    config: ProjectConfig,
    probe: MediaProbe,
    paths: ProjectPaths,
) -> tuple[str, int, int, int, Path]:
    video = probe.video_streams[0] if probe.video_streams else None
    if video is None:
        raise FinalRenderError("The source has no usable video stream")
    if config.final.resolution == "source":
        resolution = f"{video.width}x{video.height}"
    else:
        resolution = config.final.resolution
    if config.final.fps == "source":
        if video.frame_rate is None:
            raise FinalRenderError("The source frame rate is unavailable")
        fps = round(video.frame_rate)
        if abs(video.frame_rate - fps) > 0.01:
            raise FinalRenderError(
                "The source uses a fractional frame rate; configure final.fps explicitly"
            )
    else:
        fps = int(config.final.fps)
    width_text, height_text = resolution.split("x", maxsplit=1)
    width, height = int(width_text), int(height_text)
    destination = paths.final_master / f"{paths.root.name}-final-{height}p{fps}.mp4"
    return resolution, fps, width, height, destination


def render_final_project(
    config: ProjectConfig,
    *,
    approved: bool = False,
    dry_run: bool = False,
) -> tuple[Path, ProjectPaths, dict[str, object] | None]:
    if not approved and not dry_run:
        raise FinalRenderError(
            "Final rendering requires explicit approval; rerun with --approved after reviewing "
            "the complete preview"
        )
    review_validation, _ = validate_project_artifacts(config)
    if review_validation["status"] != "passed":
        raise FinalRenderError("The review validation must pass before final rendering")
    probe, pulls, timeline, _, paths = build_timeline_project(config, resolve_exports=False)
    resolution, fps, width, height, destination = _final_output_settings(config, probe, paths)
    music = selected_music(config)
    presentation = _resolved_presentation(config, pulls)
    render_final(
        timeline,
        destination,
        resolution=resolution,
        fps=fps,
        codec=config.final.codec,
        constant_qp=config.final.constant_qp,
        preset=config.final.preset,
        audio_bitrate=config.final.audio_bitrate,
        transition_seconds=config.editing.transition_duration_seconds,
        music=music[0] if music else None,
        hardware_encoding=config.final.hardware_encoding,
        watermark=config.preview.watermark,
        presentation=presentation,
        approved=approved,
        dry_run=dry_run,
    )
    if dry_run:
        return destination, paths, None

    final_probe = probe_media(destination)
    expected_duration = timeline.duration_seconds
    if presentation is not None:
        expected_duration += presentation.intro_seconds + presentation.outro_seconds
    source_path = Path(str(probe.source["path"]))
    source_stat = source_path.stat()
    checks = [
        {
            "name": "approval_recorded",
            "passed": destination.with_suffix(".manifest.json").is_file(),
            "detail": "the final manifest records the explicit approval gate",
        },
        {
            "name": "source_not_modified",
            "passed": (
                source_stat.st_size == probe.source["size_bytes"]
                and source_stat.st_mtime_ns == probe.source["modified_ns"]
            ),
            "detail": "size and nanosecond mtime still match the initial source probe",
        },
        {
            "name": "final_video_geometry",
            "passed": (
                len(final_probe.video_streams) == 1
                and final_probe.video_streams[0].width == width
                and final_probe.video_streams[0].height == height
                and final_probe.video_streams[0].frame_rate is not None
                and abs(final_probe.video_streams[0].frame_rate - fps) < 0.01
            ),
            "detail": f"final video is {resolution} at {fps} fps",
        },
        {
            "name": "final_duration",
            "passed": abs(final_probe.duration_seconds - expected_duration) < 0.1,
            "detail": f"final duration matches the approved {expected_duration:.3f}-second edit",
        },
        {
            "name": "final_audio_mapping",
            "passed": (
                len(final_probe.audio_streams) == 1
                and timeline.excluded_microphone_stream_index
                not in timeline.retained_audio_stream_indexes
            ),
            "detail": "one mixed output track is present and the microphone stream stays excluded",
        },
        {
            "name": "upload_absent",
            "passed": True,
            "detail": "the final-render workflow performs no upload or publishing action",
        },
    ]
    final_validation: dict[str, object] = {
        "status": "passed" if all(bool(check["passed"]) for check in checks) else "failed",
        "checks": checks,
        "artifact": {
            "path": str(destination.resolve()),
            "sha256": full_file_sha256(destination),
        },
    }
    write_validation_report(
        final_validation,
        paths.reports / "final-validation.json",
        paths.reports / "final-validation.md",
    )
    if final_validation["status"] != "passed":
        raise FinalRenderError("The final master rendered but failed post-render validation")
    return destination, paths, final_validation


def upload_youtube_project(
    config: ProjectConfig,
    *,
    approved: bool = False,
    public_approved: bool = False,
    dry_run: bool = False,
    progress: Callable[[int], None] | None = None,
) -> tuple[YouTubePackage, YouTubeUploadResult | None, ProjectPaths]:
    if not approved and not dry_run:
        raise YouTubeUploadError(
            "YouTube upload requires explicit approval; review the generated package and rerun "
            "with --approved"
        )
    if not dry_run and config.youtube.privacy_status == "public" and not public_approved:
        raise YouTubeUploadError("Public publishing requires the additional --public-approved flag")
    probe, pulls, timeline, _, paths = build_timeline_project(config, resolve_exports=False)
    _, _, _, _, final = _final_output_settings(config, probe, paths)
    validation_path = paths.reports / "final-validation.json"
    if not validation_path.is_file():
        raise YouTubeUploadError("The final validation report is missing")
    try:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise YouTubeUploadError("The final validation report is unreadable") from exc
    if not isinstance(validation, dict) or validation.get("status") != "passed":
        raise YouTubeUploadError("The final master must pass validation before upload")
    recovery = (
        "Re-render the reviewed final with render-final --approved to generate fresh, "
        "file-bound validation. If an old master blocks rendering, preserve it at a separate "
        "path first; preview validation alone cannot approve that file."
    )
    artifact = validation.get("artifact")
    if not isinstance(artifact, dict):
        raise YouTubeUploadError("Final validation has no artifact binding. " + recovery)
    validated_path = artifact.get("path")
    validated_sha256 = artifact.get("sha256")
    if (
        not isinstance(validated_path, str)
        or not validated_path
        or not isinstance(validated_sha256, str)
        or len(validated_sha256) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in validated_sha256)
    ):
        raise YouTubeUploadError("Final validation has an invalid artifact binding. " + recovery)
    try:
        bound_path = Path(validated_path)
        matches_path = bound_path.is_absolute() and bound_path.resolve() == final.resolve()
        if not matches_path:
            raise YouTubeUploadError(
                "Final validation belongs to a different final path. " + recovery
            )
        actual_sha256 = full_file_sha256(final)
    except (OSError, ValueError) as exc:
        raise YouTubeUploadError(
            "The selected final master could not be verified. " + recovery
        ) from exc
    if actual_sha256 != validated_sha256.lower():
        raise YouTubeUploadError("The final master changed after validation. " + recovery)
    package = write_youtube_package(
        config,
        timeline,
        final,
        paths.root / "youtube",
        pulls=pulls,
    )
    if dry_run:
        return package, None, paths
    result = upload_youtube_video(
        config,
        package,
        approved=approved,
        progress=progress,
    )
    atomic_write_text(
        paths.reports / "youtube-upload.md",
        "# YouTube Upload\n\n"
        f"- Video ID: `{result.video_id}`\n"
        f"- URL: {result.url}\n"
        f"- Privacy: {result.privacy_status}\n"
        f"- Existing upload reused: {'yes' if result.skipped_existing else 'no'}\n"
        f"- Custom thumbnail applied: {'yes' if result.thumbnail_applied else 'no'}\n"
        + (
            f"- Thumbnail note: {result.thumbnail_error}\n"
            if result.thumbnail_error is not None
            else ""
        ),
    )
    return package, result, paths


def validate_project_artifacts(config: ProjectConfig) -> tuple[dict[str, object], ProjectPaths]:
    probe, pulls, timeline, _, paths = build_timeline_project(config, resolve_exports=False)
    preview = paths.preview / f"{paths.root.name}-review-720p.mp4"
    preview_probe = probe_media(preview) if preview.is_file() else None
    result = validate_artifacts(
        probe=probe,
        pulls=pulls,
        timeline=timeline,
        microphone_free_probe=None,
        expected_audio_stream_indexes=config.audio.retained_stream_indexes(),
        preview_probe=preview_probe,
        preview_exists=preview.is_file(),
    )
    write_validation_report(
        result,
        paths.reports / "validation.json",
        paths.reports / "validation.md",
    )
    return result, paths


def load_timeline(path: Path) -> TimelineDocument:
    return TimelineDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
