"""Command-line and guided entry points."""

from __future__ import annotations

import json
import logging
import webbrowser
from datetime import date
from pathlib import Path
from typing import NoReturn

import typer
import yaml

from raid_editor.archive import create_verified_archive, write_archive_plan
from raid_editor.audio.tracks import (
    create_audio_samples,
    generate_audio_review_page,
    infer_track_roles,
)
from raid_editor.config.loader import PROJECT_ROOT, load_project_config
from raid_editor.ingestion.probe import probe_media
from raid_editor.preflight import run_preflight
from raid_editor.resolve.bridge import run_resolve_bridge
from raid_editor.util.logging import configure_logging
from raid_editor.util.paths import atomic_write_text, ensure_directory, slugify
from raid_editor.workflow import (
    ProjectPaths,
    analyse_highlights_project,
    analyse_project,
    build_timeline_project,
    inspect_project,
    render_final_project,
    render_highlights_project,
    render_preview_project,
    upload_youtube_project,
    validate_project_artifacts,
)
from raid_editor.youtube.growth import add_video_to_weekly_playlist, fetch_video_analytics
from raid_editor.youtube.upload import record_publication_confirmation

app = typer.Typer(
    name="raid-editor",
    no_args_is_help=True,
    help="Build review-first edits of long WoW raid recordings without modifying source media.",
)
LOGGER = logging.getLogger(__name__)


def _error(exc: Exception) -> NoReturn:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(2)


def _open(path: Path) -> None:
    webbrowser.open(path.resolve().as_uri())


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Configure structured logging for every command."""

    configure_logging(verbose)


@app.command()
def inspect(
    target: Path = typer.Argument(..., help="Project YAML or a recording path."),
    force: bool = typer.Option(False, help="Ignore a matching cached probe."),
    audio_samples: bool = typer.Option(True, "--audio-samples/--no-audio-samples"),
    open_review: bool = typer.Option(False, "--open-review"),
) -> None:
    """Inspect streams and create a local audio-identification review."""

    try:
        if target.suffix.casefold() in {".yaml", ".yml"}:
            config = load_project_config(target)
            probe, paths = inspect_project(
                config,
                create_samples=audio_samples,
                force=force,
            )
            review = paths.review / "audio-track-review.html"
            output = paths.analysis / "media-probe.json"
        else:
            recording = target.expanduser().resolve()
            root = PROJECT_ROOT / "output" / f"adhoc-{slugify(recording.stem)}"
            analysis = ensure_directory(root / "analysis")
            review_dir = ensure_directory(root / "review")
            output = analysis / "media-probe.json"
            probe = probe_media(recording, output, force=force)
            review = review_dir / "audio-track-review.html"
            if audio_samples:
                samples = create_audio_samples(recording, probe, review_dir / "audio-samples")
                generate_audio_review_page(probe, samples, review)
        typer.echo(f"Media probe: {output}")
        typer.echo(f"Video streams: {len(probe.video_streams)}")
        typer.echo(f"Audio streams: {len(probe.audio_streams)}")
        for stream in probe.audio_streams:
            typer.echo(
                f"  stream {stream.index}: {stream.title or 'unlabelled'} "
                f"({stream.codec}, {stream.channel_layout or 'unknown layout'})"
            )
        if audio_samples:
            typer.echo(f"Audio review: {review}")
            if open_review:
                _open(review)
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command()
def analyse(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    review_media: bool = typer.Option(True, "--review-media/--no-review-media"),
) -> None:
    """Detect pulls, write JSON/CSV, and generate the editable review package."""

    try:
        config = load_project_config(config_path)
        _, pulls, paths = analyse_project(config, create_review_media=review_media)
        typer.echo(f"Detected pulls: {len(pulls)}")
        typer.echo(f"Candidates: {paths.analysis / 'pull-candidates.json'}")
        if review_media:
            typer.echo(f"Pull review: {paths.review / 'pull-review.html'}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command()
def review(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Generate and optionally open the local pull review."""

    try:
        config = load_project_config(config_path)
        _, _, paths = analyse_project(config, create_review_media=True)
        page = paths.review / "pull-review.html"
        typer.echo(f"Pull review: {page}")
        if open_browser:
            _open(page)
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("analyse-highlights")
def analyse_highlights_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    review_media: bool = typer.Option(True, "--review-media/--no-review-media"),
    open_browser: bool = typer.Option(False, "--open/--no-open"),
) -> None:
    """Rank funny, reaction, movement, clutch, and intense moments for review."""

    try:
        config = load_project_config(config_path)
        candidates, paths = analyse_highlights_project(
            config,
            create_review_media=review_media,
        )
        page = paths.highlights / "review" / "index.html"
        typer.echo(f"Highlight candidates: {len(candidates)}")
        typer.echo(f"Candidate data: {paths.highlights / 'candidates.json'}")
        if review_media:
            typer.echo(f"Highlight review: {page}")
            if open_browser:
                _open(page)
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("render-highlights")
def render_highlights_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Confirm the selected highlight clips and configured reaction audio were reviewed.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Render only include=true highlight selections as portrait social clips."""

    try:
        config = load_project_config(config_path)
        outputs, paths = render_highlights_project(
            config,
            approved=approved,
            dry_run=dry_run,
        )
        typer.echo(f"Vertical clips: {len(outputs)}")
        typer.echo(f"Package: {paths.highlights / 'vertical' / 'posting-package.md'}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("prepare-weekly")
def prepare_weekly_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Prepare boss and highlight review gates without rendering a final or uploading."""

    try:
        config = load_project_config(config_path)
        _, pulls, paths = analyse_project(config, create_review_media=True)
        highlights, _ = analyse_highlights_project(config, create_review_media=True)
        pull_page = paths.review / "pull-review.html"
        highlight_page = paths.highlights / "review" / "index.html"
        typer.echo(f"Winning-pull candidates: {len(pulls)}")
        typer.echo(f"Highlight candidates: {len(highlights)}")
        typer.echo(f"Pull review: {pull_page}")
        typer.echo(f"Highlight review: {highlight_page}")
        if open_browser:
            _open(pull_page)
            _open(highlight_page)
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("build-timeline")
def build_timeline_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
) -> None:
    """Build neutral JSON, labels, FCPXML, and a microphone-free Resolve source."""

    try:
        config = load_project_config(config_path)
        _, _, timeline, sidecar, paths = build_timeline_project(config)
        typer.echo(f"Timeline clips: {len(timeline.clips)}")
        typer.echo(f"Timeline JSON: {paths.timeline / 'timeline.json'}")
        typer.echo(f"FCPXML: {paths.timeline / 'timeline.fcpxml'}")
        typer.echo(f"Microphone-free source: {sidecar}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("create-resolve-project")
def create_resolve_project(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Create a uniquely named Resolve project through the isolated 3.13 bridge."""

    try:
        config = load_project_config(config_path)
        _, _, _, _, paths = build_timeline_project(config)
        payload = paths.resolve / "create-project.json"
        command = run_resolve_bridge(payload, dry_run=dry_run)
        if dry_run:
            typer.echo("Bridge command: " + json.dumps(command))
        else:
            typer.echo("Resolve project created and saved.")
        typer.echo(f"Fallback FCPXML: {paths.timeline / 'timeline.fcpxml'}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("render-preview")
def render_preview_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Render only the configured low-resolution review; never a final."""

    try:
        config = load_project_config(config_path)
        preview, paths = render_preview_project(config, dry_run=dry_run)
        typer.echo(
            ("Preview command prepared for: " if dry_run else "Review render: ") + str(preview)
        )
        typer.echo(f"Edit summary: {paths.reports / 'edit-summary.md'}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("render-final")
def render_final_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Confirm the complete preview was reviewed and accepted.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Render an approved local master; never upload or publish it."""

    try:
        config = load_project_config(config_path)
        final, paths, validation = render_final_project(
            config,
            approved=approved,
            dry_run=dry_run,
        )
        typer.echo(("Final command prepared for: " if dry_run else "Final master: ") + str(final))
        if validation is not None:
            typer.echo(f"Final validation: {str(validation['status']).upper()}")
            typer.echo(f"Report: {paths.reports / 'final-validation.md'}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("upload-youtube")
def upload_youtube_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Confirm the validated final and generated YouTube metadata are approved.",
    ),
    public_approved: bool = typer.Option(
        False,
        "--public-approved",
        help="Separately confirm immediate public publishing when configured as public.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Package and resumably upload the approved final to YouTube."""

    try:
        config = load_project_config(config_path)

        def show_progress(percent: int) -> None:
            typer.echo(f"YouTube upload: {percent}%")

        package, result, paths = upload_youtube_project(
            config,
            approved=approved,
            public_approved=public_approved,
            dry_run=dry_run,
            progress=show_progress,
        )
        typer.echo(f"YouTube package: {package.root}")
        typer.echo(f"Metadata: {package.metadata}")
        if result is None:
            typer.echo("Dry run only; no file was transmitted.")
        else:
            typer.echo(f"YouTube video: {result.url}")
            typer.echo(f"Privacy: {result.privacy_status}")
            typer.echo(
                "Custom thumbnail: " + ("applied" if result.thumbnail_applied else "not applied")
            )
            if result.thumbnail_error is not None:
                typer.echo(f"Thumbnail note: {result.thumbnail_error}")
            typer.echo(f"Report: {paths.reports / 'youtube-upload.md'}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command()
def validate(
    config_path: Path = typer.Argument(..., help="Project YAML."),
) -> None:
    """Validate source safety, audio exclusion, pull boundaries, and review output."""

    try:
        config = load_project_config(config_path)
        result, paths = validate_project_artifacts(config)
        typer.echo(f"Validation: {str(result['status']).upper()}")
        typer.echo(f"Report: {paths.reports / 'validation.md'}")
        if result["status"] != "passed":
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command()
def preflight(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    smoke_recording: Path | None = typer.Option(
        None,
        "--smoke-recording",
        help="A fresh 10-second OBS test recording to probe.",
    ),
    obs_root: Path | None = typer.Option(
        None,
        "--obs-root",
        help="Override the OBS configuration root for testing.",
        hidden=True,
    ),
) -> None:
    """Run the read-only Friday OBS, disk, logging, scene, and track check."""

    report = None
    try:
        config = load_project_config(config_path)
        if not config.preflight.enabled:
            raise ValueError("Preflight is disabled in the project configuration")
        paths = ProjectPaths.for_config(config).create()
        report = run_preflight(
            config,
            destination_json=paths.reports / "preflight.json",
            destination_markdown=paths.reports / "preflight.md",
            obs_root=obs_root,
            smoke_recording=smoke_recording,
        )
        typer.echo(f"Preflight: {report.status.upper()}")
        for item in report.checks:
            typer.echo(f"  {item.status.upper():7} {item.name}: {item.detail}")
        typer.echo(f"Report: {paths.reports / 'preflight.md'}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)
    if report is not None and report.status != "passed":
        raise typer.Exit(1)


@app.command("sync-playlist")
def sync_playlist_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    video_id: str = typer.Option(..., "--video-id", help="Published YouTube video ID."),
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Confirm creation or modification of the configured YouTube playlist.",
    ),
) -> None:
    """Idempotently add an approved upload to the weekly raid playlist."""

    try:
        config = load_project_config(config_path)
        paths = ProjectPaths.for_config(config).create()
        result = add_video_to_weekly_playlist(
            config,
            video_id=video_id,
            approved=approved,
            report_destination=paths.reports / "youtube-playlist.md",
        )
        typer.echo(f"Playlist: {result.playlist_title} ({result.playlist_id})")
        typer.echo("Video already present." if result.already_present else "Video added.")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("confirm-youtube-publication")
def confirm_youtube_publication_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    video_id: str = typer.Option(..., "--video-id", help="Public YouTube video ID."),
    maximum_quality: str = typer.Option(
        "1440p60",
        "--maximum-quality",
        help="Operator-observed public playback quality: 1440p or 1440p60.",
    ),
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Confirm the public watch page and maximum quality were personally checked.",
    ),
) -> None:
    """Record local evidence of an operator-verified public 1440p watch page."""

    try:
        config = load_project_config(config_path)
        package, _result, paths = upload_youtube_project(
            config,
            approved=False,
            public_approved=False,
            dry_run=True,
        )
        payload = record_publication_confirmation(
            config,
            package,
            video_id=video_id,
            maximum_quality=maximum_quality,
            approved=approved,
            report_destination=paths.reports / "youtube-publication.md",
        )
        typer.echo(f"Recorded public verification: {payload['url']}")
        typer.echo(f"Maximum quality: {payload['maximum_quality_confirmed']}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("youtube-analytics")
def youtube_analytics_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    video_id: str = typer.Option(..., "--video-id", help="Published YouTube video ID."),
    label: str = typer.Option("48h", "--label", help="Report label, normally 48h or 7d."),
    start_date: str | None = typer.Option(None, "--start-date", help="YYYY-MM-DD."),
    end_date: str | None = typer.Option(None, "--end-date", help="YYYY-MM-DD."),
    studio_impressions: int | None = typer.Option(None, "--studio-impressions", min=0),
    studio_ctr_percent: float | None = typer.Option(
        None,
        "--studio-ctr-percent",
        min=0,
        max=100,
    ),
) -> None:
    """Write a read-only 48-hour or 7-day YouTube performance report."""

    try:
        config = load_project_config(config_path)
        _, _, timeline, _, paths = build_timeline_project(config)
        final = next(paths.final_master.glob("*final*.mp4"), None)
        duration = (
            probe_media(final).duration_seconds
            if final is not None and final.is_file()
            else timeline.duration_seconds
        )
        report_start = (
            date.fromisoformat(start_date)
            if start_date is not None
            else config.project.raid_date or date.today()
        )
        report_end = date.fromisoformat(end_date) if end_date is not None else date.today()
        if report_end < report_start:
            raise ValueError("end_date must not precede start_date")
        payload = fetch_video_analytics(
            config,
            video_id=video_id,
            start_date=report_start,
            end_date=report_end,
            video_duration_seconds=duration,
            label=label,
            json_destination=paths.analytics / f"{label}.json",
            markdown_destination=paths.analytics / f"{label}.md",
            studio_impressions=studio_impressions,
            studio_ctr_percent=studio_ctr_percent,
        )
        typer.echo(f"Analytics report: {paths.analytics / f'{label}.md'}")
        summary = payload.get("summary")
        views = summary.get("views", "unavailable") if isinstance(summary, dict) else "unavailable"
        typer.echo(f"Views: {views}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("archive-plan")
def archive_plan_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
) -> None:
    """Write a copy-only archive plan without hashing, copying, moving, or deleting."""

    try:
        config = load_project_config(config_path)
        paths = ProjectPaths.for_config(config).create()
        items = write_archive_plan(
            config,
            config_path=config_path.expanduser().resolve(),
            project_root=paths.root,
            json_destination=paths.archive / "plan.json",
            markdown_destination=paths.archive / "plan.md",
        )
        typer.echo(f"Archive files: {len(items)}")
        typer.echo(f"Plan: {paths.archive / 'plan.md'}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("archive")
def archive_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Confirm copy-only archival to the configured destination.",
    ),
) -> None:
    """Copy and hash-verify approved raid files; never delete source files."""

    try:
        config = load_project_config(config_path)
        paths = ProjectPaths.for_config(config).create()
        destination = create_verified_archive(
            config,
            config_path=config_path.expanduser().resolve(),
            project_root=paths.root,
            approved=approved,
        )
        typer.echo(f"Verified archive: {destination}")
        typer.echo("Source files were not deleted.")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


def _choose_recording() -> Path:
    try:
        from tkinter import Tk, filedialog

        root = Tk()
        root.withdraw()
        selected = filedialog.askopenfilename(
            title="Select an OBS raid recording",
            filetypes=[
                ("Video recordings", "*.mkv *.mov *.mp4"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        if selected:
            return Path(selected)
    except Exception as exc:
        LOGGER.debug("Native file picker unavailable: %s", exc)
    return Path(typer.prompt("Recording path")).expanduser()


@app.command()
def wizard(
    config_path: Path | None = typer.Argument(None, help="Existing project YAML, if available."),
) -> None:
    """Walk a non-developer through inspection, role mapping, analysis, and review."""

    try:
        if config_path is None:
            recording = _choose_recording().resolve()
            probe = probe_media(recording)
            roles = infer_track_roles(probe.audio_streams)
            typer.echo("Audio streams use absolute FFprobe indexes:")
            for stream in probe.audio_streams:
                typer.echo(f"  {stream.index}: {stream.title or 'unlabelled'}")

            def role_prompt(role: str) -> int | None:
                inferred = roles.get(role)
                default = "" if inferred is None else str(inferred)
                value = typer.prompt(f"{role.title()} stream (blank if none)", default=default)
                return int(value) if str(value).strip() else None

            microphone = role_prompt("microphone")
            game = role_prompt("game")
            discord = role_prompt("discord")
            mixed = role_prompt("mixed")
            common_log = Path(r"D:\world of warcraft 3.3.5a hd\Logs\WoWCombatLog.txt")
            log_default = str(common_log) if common_log.is_file() else ""
            combat_log = typer.prompt(
                "Combat log path (blank for manual pulls)", default=log_default
            )
            name = typer.prompt("Project name", default=recording.stem)
            generated_config = PROJECT_ROOT / "config" / f"{slugify(name)}.local.yaml"
            payload = {
                "project": {
                    "name": name,
                    "game": "World of Warcraft",
                    "expansion": None,
                    "raid": None,
                    "raid_date": None,
                },
                "input": {
                    "recording": str(recording),
                    "combat_log": combat_log or None,
                    "details_export": None,
                    "skada_export": None,
                    "manual_pulls": None,
                },
                "audio": {
                    "microphone_track": microphone,
                    "game_track": game,
                    "discord_track": discord,
                    "mixed_track": mixed,
                    "keep_game_audio": game is not None,
                    "keep_discord_audio": discord is not None,
                    "remove_microphone": True,
                },
                "detection": {
                    "minimum_pull_seconds": 15,
                    "merge_gap_seconds": 8,
                    "pre_roll_seconds": 5,
                    "post_roll_seconds": 8,
                    "confidence_threshold": 0.70,
                    "combat_log_offset_seconds": 0,
                    "recording_started_at": None,
                },
                "editing": {
                    "include_trash_pulls": True,
                    "include_boss_wipes": True,
                    "include_boss_kills": True,
                    "include_run_backs": False,
                    "include_loot": True,
                    "transition_duration_seconds": 0.4,
                },
                "music": {
                    "library": str(PROJECT_ROOT / "music" / "music-library.json"),
                    "approved_track_ids": [],
                },
                "preview": {
                    "resolution": "1280x720",
                    "fps": 30,
                    "bitrate": "4M",
                    "hardware_encoding": False,
                },
                "final": {
                    "resolution": "source",
                    "fps": "source",
                    "codec": "h264",
                    "hardware_encoding": True,
                },
            }
            atomic_write_text(
                generated_config,
                yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            )
            config_path = generated_config
            typer.echo(f"Saved local configuration: {config_path}")
        config = load_project_config(config_path)
        _, pulls, paths = analyse_project(config, create_review_media=True)
        typer.echo(f"Detected {len(pulls)} pulls.")
        typer.echo(f"Review audio roles: {paths.review / 'audio-track-review.html'}")
        typer.echo(f"Review pulls: {paths.review / 'pull-review.html'}")
        _open(paths.review / "pull-review.html")
        if typer.confirm("Build the current timeline and render a low-resolution review now?"):
            preview, report_paths = render_preview_project(config)
            typer.echo(f"Review render: {preview}")
            typer.echo(f"Edit summary: {report_paths.reports / 'edit-summary.md'}")
        else:
            typer.echo("Stopped at the review boundary; no final render or upload was performed.")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


if __name__ == "__main__":
    app()
