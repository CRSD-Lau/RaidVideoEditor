"""Reusable project workflows behind the CLI."""

from __future__ import annotations

import json
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
from raid_editor.config.loader import project_output_dir
from raid_editor.config.models import ProjectConfig
from raid_editor.detection.pipeline import analyse_pulls
from raid_editor.ingestion.probe import MediaProbe, probe_media
from raid_editor.models import PullCandidate, TimelineDocument
from raid_editor.music.library import (
    MusicTrack,
    approved_tracks,
    load_music_library,
    write_music_plan,
    write_music_reports,
)
from raid_editor.rendering.preview import render_preview
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
    quick_file_fingerprint,
)

_PULL_LIST = TypeAdapter(list[PullCandidate])
_ANALYSIS_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    analysis: Path
    review: Path
    timeline: Path
    generated_assets: Path
    preview: Path
    reports: Path
    resolve: Path

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
            reports=root / "reports",
            resolve=root / "resolve",
        )

    def create(self) -> ProjectPaths:
        for path in (
            self.analysis,
            self.review,
            self.timeline,
            self.generated_assets,
            self.preview,
            self.reports,
            self.resolve,
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
        write_pull_candidates(
            pulls,
            candidates_path,
            paths.analysis / "pull-candidates.csv",
        )
        atomic_write_json(manifest_path, signature)
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
        assets = generate_pull_media(
            config.input.recording,
            pulls,
            paths.review,
            config.audio.retained_stream_indexes(),
        )
        generate_pull_review_page(
            pulls,
            assets,
            paths.review / "pull-review.html",
        )
    return probe, pulls, paths


def _load_saved_pulls(config: ProjectConfig, paths: ProjectPaths) -> list[PullCandidate]:
    if config.input.manual_pulls is not None:
        return analyse_pulls(
            recording=config.input.recording,
            recording_duration_seconds=probe_media(config.input.recording).duration_seconds,
            settings=config.detection,
            combat_log=config.input.combat_log,
            skada_export=config.input.skada_export,
            manual_pulls=config.input.manual_pulls,
        )
    source = paths.analysis / "pull-candidates.json"
    if not source.is_file():
        return analyse_project(config, create_review_media=False)[1]
    return _PULL_LIST.validate_json(source.read_text(encoding="utf-8"))


def build_timeline_project(
    config: ProjectConfig,
) -> tuple[MediaProbe, list[PullCandidate], TimelineDocument, Path, ProjectPaths]:
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


def render_preview_project(
    config: ProjectConfig,
    *,
    dry_run: bool = False,
) -> tuple[Path, ProjectPaths]:
    probe, pulls, timeline, _, paths = build_timeline_project(config)
    music = selected_music(config)
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
        presentation=config.preview.presentation,
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


def validate_project_artifacts(config: ProjectConfig) -> tuple[dict[str, object], ProjectPaths]:
    probe, pulls, timeline, sidecar, paths = build_timeline_project(config)
    preview = paths.preview / f"{paths.root.name}-review-720p.mp4"
    microphone_free_probe = probe_media(sidecar) if sidecar.is_file() else None
    preview_probe = probe_media(preview) if preview.is_file() else None
    result = validate_artifacts(
        probe=probe,
        pulls=pulls,
        timeline=timeline,
        microphone_free_probe=microphone_free_probe,
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
