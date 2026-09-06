"""Command-line and guided entry points."""

from __future__ import annotations

import json
import logging
import time
import webbrowser
from datetime import date, datetime
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
from raid_editor.growth.analytics import record_growth_analytics
from raid_editor.growth.ledger import append_production_time
from raid_editor.growth.models import ProductionTimeEntry, SourceRange
from raid_editor.growth.package import (
    approve_growth_package,
    prepare_growth_package,
    record_manual_claim_approval,
    record_packaging_experiment_state,
    record_related_target_state,
    set_recap_decision,
    write_growth_status,
)
from raid_editor.highlights.feedback import record_editorial_feedback
from raid_editor.ingestion.probe import probe_media
from raid_editor.preflight import run_preflight
from raid_editor.rendering.validation import validate_existing_final
from raid_editor.resolve.bridge import run_resolve_bridge
from raid_editor.social.analytics import record_analytics_snapshot
from raid_editor.social.ledger import (
    approve_social_review,
    record_publication_status,
    write_social_status,
)
from raid_editor.social.package import prepare_social_source
from raid_editor.util.logging import configure_logging
from raid_editor.util.paths import atomic_write_text, ensure_directory, slugify
from raid_editor.weekly import (
    create_weekly_project_config,
    find_latest_recording,
    verify_completed_recording,
)
from raid_editor.workflow import (
    ProjectPaths,
    analyse_highlights_project,
    analyse_project,
    build_timeline_project,
    inspect_project,
    prepare_highlight_comparison,
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
    """Discover supported raid moments with explicit local intelligence coverage."""

    try:
        config = load_project_config(config_path)
        candidates, paths = analyse_highlights_project(
            config,
            create_review_media=review_media,
        )
        page = paths.highlights / "review" / "index.html"
        typer.echo(f"Highlight candidates: {len(candidates)}")
        typer.echo(f"Candidate data: {paths.highlights / 'candidates.json'}")
        status_path = paths.highlights / "intelligence-status.json"
        if status_path.is_file():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            typer.echo(f"Local intelligence coverage: {status.get('status', 'unknown')}")
        if review_media:
            typer.echo(f"Highlight review: {page}")
            if open_browser:
                _open(page)
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("import-highlight-feedback")
def import_highlight_feedback_command(
    selection: Path = typer.Argument(..., help="Reviewed highlight-overrides JSON."),
    destination: Path = typer.Option(
        PROJECT_ROOT / "config" / "highlight-feedback.local.json", "--destination"
    ),
) -> None:
    """Record explicit editorial decisions without approving exports or publishing."""
    try:
        result = record_editorial_feedback(selection, destination)
        records = result.get("records", [])
        typer.echo(f"Editorial feedback saved: {destination}")
        typer.echo(f"Recorded decisions: {len(records) if isinstance(records, list) else 0}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("compare-highlights")
def compare_highlights_command(
    config_path: Path = typer.Argument(..., help="Project YAML with completed highlight analysis."),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Build a blinded same-recording comparison of heuristics and current recommendations."""
    try:
        page = prepare_highlight_comparison(load_project_config(config_path))
        typer.echo(f"Blind highlight comparison: {page}")
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
        campaign, _, growth_review = prepare_growth_package(config, selected_short_ids=[])
        pull_page = paths.review / "pull-review.html"
        highlight_page = paths.highlights / "review" / "index.html"
        typer.echo(f"Winning-pull candidates: {len(pulls)}")
        typer.echo(f"Highlight candidates: {len(highlights)}")
        typer.echo(f"Pull review: {pull_page}")
        typer.echo(f"Highlight review: {highlight_page}")
        typer.echo(f"Growth campaign: {campaign.campaign_id}")
        typer.echo(f"Growth review: {growth_review}")
        if open_browser:
            _open(pull_page)
            _open(highlight_page)
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("friday")
def friday_command(
    recording: Path | None = typer.Option(
        None,
        "--recording",
        help="Completed OBS recording. Defaults to the newest stable raid recording.",
    ),
    recording_directory: Path = typer.Option(
        Path(r"D:\RaidRecordings"),
        "--recording-directory",
        help="Folder searched when --recording is omitted.",
    ),
    template: Path | None = typer.Option(
        None,
        "--template",
        help="Earlier approved local config to clone. Defaults to the newest dated config.",
    ),
    minimum_age_minutes: float = typer.Option(
        2.0,
        "--minimum-age-minutes",
        min=0,
        help="Ignore a newest file that may still be recording.",
    ),
    stability_seconds: float = typer.Option(
        2.0,
        "--stability-seconds",
        min=0,
        help="Seconds used to confirm the selected recording stopped changing.",
    ),
    prepare_reviews: bool = typer.Option(
        True,
        "--prepare/--config-only",
        help="Prepare both review lanes, or stop after safely creating the dated config.",
    ),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Start the post-raid workflow from the newest verified Friday recording."""

    command_started = time.perf_counter()
    try:
        selected = (
            verify_completed_recording(
                recording,
                minimum_age_minutes=minimum_age_minutes,
                stability_seconds=stability_seconds,
            )
            if recording is not None
            else find_latest_recording(
                recording_directory,
                minimum_age_minutes=minimum_age_minutes,
                stability_seconds=stability_seconds,
            )
        )
        setup = create_weekly_project_config(selected, template_path=template)
        typer.echo(f"Recording: {setup.recording}")
        typer.echo(f"Project config: {setup.config_path}")
        typer.echo(f"Config: {'created' if setup.created else 'reused without overwrite'}")
        typer.echo(
            "Verified tracks: "
            f"Full Mix={setup.audio_roles['mixed']}, WoW Game={setup.audio_roles['game']}, "
            f"Discord={setup.audio_roles['discord']}, "
            f"Microphone={setup.audio_roles['microphone']}"
        )
        if not prepare_reviews:
            typer.echo("Stopped after config creation; no review, render, or upload was performed.")
            return
        config = load_project_config(setup.config_path)
        _, pulls, paths = analyse_project(config, create_review_media=True)
        highlights, _ = analyse_highlights_project(config, create_review_media=True)
        campaign, _, growth_review = prepare_growth_package(config, selected_short_ids=[])
        measured_at = datetime.now().astimezone()
        append_production_time(
            growth_review.parent.parent / "growth-ledger.jsonl",
            ProductionTimeEntry(
                entry_id=(
                    f"production-time:{campaign.campaign_id}:analysis:{measured_at:%Y%m%dT%H%M%S%f}"
                ),
                campaign_id=campaign.campaign_id,
                recorded_at=measured_at,
                stage="analysis",
                unattended_runtime_seconds=time.perf_counter() - command_started,
                source="measured_command",
                note="Friday source verification, analysis, and review preparation runtime.",
            ),
        )
        pull_page = paths.review / "pull-review.html"
        highlight_page = paths.highlights / "review" / "index.html"
        typer.echo(f"Winning-pull candidates: {len(pulls)}")
        typer.echo(f"Highlight candidates: {len(highlights)}")
        typer.echo(f"Pull review: {pull_page}")
        typer.echo(f"Highlight review: {highlight_page}")
        typer.echo(f"Growth campaign: {campaign.campaign_id}")
        typer.echo(f"Growth review: {growth_review}")
        typer.echo("Stopped at the review gates; no final render or upload was performed.")
        if open_browser:
            _open(pull_page)
            _open(highlight_page)
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("prepare-growth")
def prepare_growth_command(
    config_path: Path = typer.Argument(..., help="Project YAML."),
    short_ids: list[str] | None = typer.Option(
        None,
        "--short-id",
        help="Approved vertical highlight ID. Repeat zero to two times.",
    ),
    no_shorts: bool = typer.Option(
        False,
        "--no-shorts",
        help="Create the accurate archive lane with zero Shorts for this campaign.",
    ),
    portrait_source: Path | None = typer.Option(
        None,
        "--portrait-source",
        help="Optional paired native 1080x1920 OBS recording for future portrait edits.",
    ),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Build the local campaign, calendar, claims, experiments, and cleanup holds."""

    try:
        if no_shorts and short_ids:
            raise ValueError("Use --no-shorts or --short-id, not both")
        config = load_project_config(config_path)
        campaign, distribution, review = prepare_growth_package(
            config,
            selected_short_ids=[] if no_shorts else short_ids,
            portrait_source=portrait_source,
        )
        typer.echo(f"Campaign: {campaign.campaign_id}")
        typer.echo(f"Content assets: {len(campaign.assets)}")
        typer.echo(f"Shorts selected: {sum(asset.lane == 'short' for asset in campaign.assets)}/2")
        typer.echo(f"Distribution entries: {len(distribution.entries)}")
        typer.echo(f"Calendar entries: {len(distribution.schedule)}")
        typer.echo(f"Growth review: {review}")
        typer.echo("Nothing was uploaded, published, scheduled, or changed remotely.")
        if open_browser:
            _open(review)
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("approve-growth")
def approve_growth_command(
    campaign_path: Path = typer.Argument(..., help="growth/campaign-manifest.json"),
    approved: bool = typer.Option(False, "--approved"),
) -> None:
    """Approve the local package and each Short's related-video target."""

    try:
        campaign = approve_growth_package(campaign_path, approved=approved)
        typer.echo(f"Locally approved assets: {len(campaign.assets)}")
        typer.echo("Nothing was uploaded, published, scheduled, or changed remotely.")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("growth-recap")
def growth_recap_command(
    campaign_path: Path = typer.Argument(..., help="growth/campaign-manifest.json"),
    decision: str = typer.Option(
        ...,
        "--decision",
        help="candidate, hold, or skipped (create and skip remain accepted aliases)",
    ),
    reason: str = typer.Option(..., "--reason"),
    beats: list[str] | None = typer.Option(
        None,
        "--beat",
        help="Candidate recap beat. Repeat for each reviewed beat.",
    ),
    source_ranges_json: Path | None = typer.Option(
        None,
        "--source-ranges-json",
        help="JSON array of reviewed recap SourceRange records.",
    ),
    audio_plan: str = typer.Option(
        "not_applicable",
        "--audio-plan",
        help="not_applicable, game_only, voice_candidates_require_review, or reviewed_voice",
    ),
    estimated_minutes: float | None = typer.Option(
        None,
        "--estimated-minutes",
        min=0.01,
        max=1440,
    ),
    approved: bool = typer.Option(False, "--approved"),
) -> None:
    """Record the reviewed recap decision without rendering or publishing it."""

    try:
        source_ranges: list[SourceRange] = []
        if source_ranges_json is not None:
            raw_ranges = json.loads(source_ranges_json.read_text(encoding="utf-8"))
            if not isinstance(raw_ranges, list):
                raise ValueError("source-ranges-json must contain one JSON array")
            source_ranges = [SourceRange.model_validate(item) for item in raw_ranges]
        campaign = set_recap_decision(
            campaign_path,
            decision=decision,
            reason=reason,
            approved=approved,
            candidate_beats=beats,
            source_ranges=source_ranges,
            audio_plan=audio_plan,
            estimated_active_minutes=estimated_minutes,
        )
        typer.echo(f"Recap decision: {campaign.recap.decision}")
        typer.echo("Nothing was rendered, uploaded, or changed remotely.")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("growth-claim")
def growth_claim_command(
    campaign_path: Path = typer.Argument(..., help="growth/campaign-manifest.json"),
    content_id: str = typer.Option(..., "--content-id"),
    claim_id: str = typer.Option(..., "--claim-id"),
    kind: str = typer.Option(..., "--kind"),
    text: str = typer.Option(..., "--text"),
    reason: str = typer.Option(..., "--reason"),
    evidence_ids: list[str] | None = typer.Option(
        None,
        "--evidence-id",
        help="Reviewed evidence identity. Repeat at least once.",
    ),
    approved: bool = typer.Option(False, "--approved"),
) -> None:
    """Record an explicit evidence-backed custom claim approval locally."""

    try:
        campaign = record_manual_claim_approval(
            campaign_path,
            content_id=content_id,
            claim_id=claim_id,
            kind=kind,  # type: ignore[arg-type]
            text=text,
            reason=reason,
            evidence_ids=evidence_ids or [],
            approved=approved,
        )
        typer.echo(f"Approved claim {claim_id} in {campaign.campaign_id}")
        typer.echo("No title, description, upload, or remote state was changed.")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("growth-related-target")
def growth_related_target_command(
    campaign_path: Path = typer.Argument(..., help="growth/campaign-manifest.json"),
    content_id: str = typer.Option(..., "--content-id"),
    state: str = typer.Option(..., "--state"),
    reason: str = typer.Option(..., "--reason"),
    short_remote_id: str | None = typer.Option(None, "--short-remote-id"),
    short_public_url: str | None = typer.Option(None, "--short-public-url"),
    target_url: str | None = typer.Option(None, "--target-url"),
    assignment_receipt: str | None = typer.Option(None, "--assignment-receipt"),
    approved: bool = typer.Option(False, "--approved"),
) -> None:
    """Record a separately reviewed native related-video assignment receipt."""

    try:
        campaign = record_related_target_state(
            campaign_path,
            content_id=content_id,
            state=state,  # type: ignore[arg-type]
            reason=reason,
            approved=approved,
            short_remote_id=short_remote_id,
            short_public_url=short_public_url,
            target_url=target_url,
            assignment_receipt=assignment_receipt,
        )
        short = next(asset for asset in campaign.assets if asset.content_id == content_id)
        if short.related_target is None:
            raise ValueError("Short related target unexpectedly disappeared")
        typer.echo(f"Related target state: {short.related_target.state}")
        typer.echo("This recorded local evidence only; it did not contact YouTube Studio.")
    except (OSError, ValueError, RuntimeError, StopIteration) as exc:
        _error(exc)


@app.command("growth-time")
def growth_time_command(
    campaign_path: Path = typer.Argument(..., help="growth/campaign-manifest.json"),
    stage: str = typer.Option(..., "--stage"),
    content_id: str | None = typer.Option(None, "--content-id"),
    minutes: float = typer.Option(0, "--minutes", min=0, max=1440),
    unattended_seconds: float = typer.Option(
        0,
        "--unattended-seconds",
        min=0,
        max=604800,
    ),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """Append one production-effort measurement for pilot economics."""

    try:
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        campaign_id = str(campaign["campaign_id"])
        content_lane: str | None = None
        if content_id is not None:
            matches = [
                asset
                for asset in campaign.get("assets", [])
                if isinstance(asset, dict) and asset.get("content_id") == content_id
            ]
            if len(matches) != 1:
                raise ValueError("content-id must match exactly one campaign asset")
            content_lane = str(matches[0]["lane"])
        now = datetime.now().astimezone()
        entry = ProductionTimeEntry(
            entry_id=f"production-time:{campaign_id}:{stage}:{now:%Y%m%dT%H%M%S%f}",
            campaign_id=campaign_id,
            recorded_at=now,
            stage=stage,  # type: ignore[arg-type]
            content_id=content_id,
            content_lane=content_lane,  # type: ignore[arg-type]
            minutes=minutes,
            unattended_runtime_seconds=unattended_seconds,
            note=note,
        )
        append_production_time(campaign_path.parent / "growth-ledger.jsonl", entry)
        typer.echo(
            f"Recorded {entry.minutes:.2f} active minutes and "
            f"{entry.unattended_runtime_seconds / 60:.2f} unattended minutes "
            f"for {entry.stage}"
        )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        _error(exc)


@app.command("growth-experiment")
def growth_experiment_command(
    campaign_path: Path = typer.Argument(..., help="growth/campaign-manifest.json"),
    series_id: str = typer.Option(..., "--series-id"),
    status: str = typer.Option(..., "--status"),
    decision: str | None = typer.Option(None, "--decision"),
    reason: str = typer.Option(..., "--reason"),
    approved: bool = typer.Option(False, "--approved"),
) -> None:
    """Record an observed packaging-test state without controlling YouTube Studio."""

    try:
        version = record_packaging_experiment_state(
            campaign_path,
            series_id=series_id,
            status=status,
            decision=decision,
            reason=reason,
            approved=approved,
        )
        typer.echo(f"Experiment state: {version.status}")
        typer.echo(f"Experiment version: {version.experiment_id}")
        typer.echo("This recorded local evidence only; it did not contact YouTube Studio.")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("growth-status")
def growth_status_command(
    campaign_path: Path = typer.Argument(..., help="growth/campaign-manifest.json"),
) -> None:
    """Report campaign readiness, schedule locks, distribution, and cleanup holds."""

    try:
        report, destination = write_growth_status(campaign_path)
        typer.echo(f"Shorts: {report['short_count']}/2")
        typer.echo(f"Locked schedules: {report['locked_schedule_count']}")
        typer.echo(f"Cleanup holds: {len(report['cleanup_held'])}")
        typer.echo(f"Status report: {destination}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("growth-analytics")
def growth_analytics_command(
    campaign_path: Path = typer.Argument(..., help="growth/campaign-manifest.json"),
    content_id: str = typer.Option(..., "--content-id"),
    destination: str = typer.Option(..., "--destination"),
    published_at: str = typer.Option(
        ...,
        "--published-at",
        help="Observed publication timestamp with timezone, in ISO-8601 form.",
    ),
    checkpoint_hours: int = typer.Option(..., "--checkpoint-hours", min=1),
    metrics_json: Path = typer.Option(..., "--metrics-json"),
    source: str = typer.Option("manual_studio_entry", "--source"),
    surface: str | None = typer.Option(None, "--surface"),
    metric_definitions_json: Path | None = typer.Option(
        None,
        "--metric-definitions-json",
    ),
    event_alignment_json: Path | None = typer.Option(None, "--event-alignment-json"),
) -> None:
    """Record actual content age, platform surface, metrics, and event alignment."""

    try:
        published = datetime.fromisoformat(published_at)
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        campaign_id = str(campaign["campaign_id"])
        assets = campaign.get("assets", [])
        matching_assets = [
            asset
            for asset in assets
            if isinstance(asset, dict) and asset.get("content_id") == content_id
        ]
        if len(matching_assets) != 1:
            raise ValueError("content-id must match exactly one campaign asset")
        snapshot, path = record_growth_analytics(
            campaign_path.parent / "analytics",
            campaign_id=campaign_id,
            content_id=content_id,
            destination=destination,
            published_at=published,
            requested_checkpoint_hours=checkpoint_hours,
            metrics_path=metrics_json,
            source=source,
            content_lane=str(matching_assets[0]["lane"]),
            cohort=campaign_id,
            surface=surface,
            metric_definitions_path=metric_definitions_json,
            event_alignment_path=event_alignment_json,
        )
        typer.echo(f"Actual age: {snapshot.actual_age_seconds / 3600:.2f} hours")
        typer.echo(f"Outside checkpoint tolerance: {snapshot.outside_tolerance}")
        typer.echo(f"Analytics snapshot: {path}")
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
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


@app.command("validate-final")
def validate_final_command(
    config_path: Path = typer.Argument(..., help="Project YAML with saved edit records."),
    video: Path = typer.Option(
        ..., "--video", help="Exact existing MP4 in the project's final directory."
    ),
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Confirm you reviewed this existing final's picture, audio, and edit.",
    ),
    expected_sha256: str | None = typer.Option(
        None, "--expected-sha256", help="Exact SHA-256 printed by the previous inspection."
    ),
) -> None:
    """Inspect and approve an existing final without rerendering or uploading it."""
    try:
        config = load_project_config(config_path)
        paths = ProjectPaths.for_config(config)
        typer.echo(
            "Checking the complete existing video and saved edit records; media stays unchanged."
        )
        result = validate_existing_final(
            config, paths.root, video, approved=approved, expected_sha256=expected_sha256
        )
        typer.echo(f"Final: {result['artifact']['path']}")
        typer.echo(f"SHA-256: {result['artifact']['sha256']}")
        if approved:
            typer.echo(f"Final validation: PASSED. Report: {paths.reports / 'final-validation.md'}")
            typer.echo("Upload and public-publishing approval remain separate.")
        else:
            typer.echo(f"Inspection passed. Report: {paths.reports / 'final-inspection.json'}")
            typer.echo(
                "Watch the complete final and accept its picture, audio, and edit before approving:"
            )
            quoted_config = str(config_path.resolve()).replace("'", "''")
            quoted_video = str(video.resolve()).replace("'", "''")
            typer.echo(
                f"uv run --no-sync raid-editor validate-final '{quoted_config}' --video "
                f"'{quoted_video}' --approved --expected-sha256 {result['artifact']['sha256']}"
            )
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
    vertical_smoke_recording: Path | None = typer.Option(
        None,
        "--vertical-smoke-recording",
        help="The matching Aitum 1080x1920 smoke recording.",
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
            vertical_smoke_recording=vertical_smoke_recording,
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
        _, _, timeline, _, paths = build_timeline_project(config, resolve_exports=False)
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


@app.command("prepare-social")
def prepare_social_command(
    source: Path = typer.Argument(
        ...,
        help="Project YAML or an explicit owned-assets JSON manifest.",
    ),
    facebook_handle: str = typer.Option("Pizza Warriors", "--facebook-handle"),
    instagram_handle: str = typer.Option("pizzawarriorswow", "--instagram-handle"),
    tiktok_handle: str = typer.Option("lausudo", "--tiktok-handle"),
    twitch_handle: str = typer.Option("lausudo", "--twitch-handle"),
    start_at: str | None = typer.Option(
        None,
        "--start-at",
        help="First release as an ISO-8601 local or timezone-aware timestamp.",
    ),
    cadence_days: int = typer.Option(2, "--cadence-days", min=1, max=30),
    open_review: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Build four reviewed social packages per approved portrait highlight."""

    try:
        start = datetime.fromisoformat(start_at) if start_at else None
        handles = {
            "facebook": facebook_handle,
            "instagram": instagram_handle,
            "tiktok": tiktok_handle,
            "twitch": twitch_handle,
        }
        manifest, manifest_path = prepare_social_source(
            source,
            handles=handles,
            start_at=start,
            cadence_days=cadence_days,
        )
        review = manifest_path.parent / "review" / "index.html"
        typer.echo(f"Social clips: {len(manifest.assets)}")
        typer.echo(f"Destination packages: {len(manifest.posts)}")
        typer.echo(f"Distribution manifest: {manifest_path}")
        typer.echo(f"Review: {review}")
        typer.echo("Nothing was uploaded or published.")
        if open_review:
            _open(review)
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("approve-social")
def approve_social_command(
    manifest_path: Path = typer.Argument(..., help="Social distribution-manifest.json."),
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Confirm all clips, copy, accounts, and release times were reviewed.",
    ),
) -> None:
    """Record the social package review gate without contacting a platform."""

    try:
        manifest = approve_social_review(manifest_path, approved=approved)
        typer.echo(f"Approved destination packages: {len(manifest.posts)}")
        typer.echo("Nothing was uploaded or published.")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("confirm-social-publication")
def confirm_social_publication_command(
    manifest_path: Path = typer.Argument(..., help="Social distribution-manifest.json."),
    platform: str = typer.Option(..., "--platform"),
    content_id: str = typer.Option(..., "--content-id"),
    status: str = typer.Option(..., "--status"),
    remote_content_id: str | None = typer.Option(None, "--remote-id"),
    public_url: str | None = typer.Option(None, "--url"),
    target_account_id: str | None = typer.Option(None, "--account-id"),
    checks_json: Path | None = typer.Option(
        None,
        "--checks-json",
        help="Optional JSON object with playback, crop, audio, audience, and notice checks.",
    ),
    error: str | None = typer.Option(None, "--error"),
    approved: bool = typer.Option(False, "--approved"),
) -> None:
    """Record one observed platform state; never perform the remote post itself."""

    allowed_statuses = {
        "prepared",
        "reviewed",
        "awaiting_confirmation",
        "uploaded",
        "processing",
        "scheduled",
        "published",
        "verified_public",
        "failed",
        "blocked_account",
        "blocked_capability",
        "remote_state_uncertain",
        "waived_by_neil",
    }
    try:
        if status not in allowed_statuses:
            raise ValueError(f"Unknown social status: {status}")
        checks = None
        if checks_json is not None:
            loaded = json.loads(checks_json.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("checks-json must contain a JSON object")
            checks = loaded
        manifest = record_publication_status(
            manifest_path,
            platform=platform,
            content_id=content_id,
            status=status,  # type: ignore[arg-type]
            approved=approved,
            remote_content_id=remote_content_id,
            public_url=public_url,
            target_account_id=target_account_id,
            verification_checks=checks,
            error=error,
        )
        post = next(
            item
            for item in manifest.posts
            if item.platform == platform and item.content_id == content_id
        )
        typer.echo(f"Recorded: {post.platform}/{post.content_id} -> {post.status}")
        typer.echo(f"Idempotency key: {post.idempotency_key}")
    except (OSError, ValueError, RuntimeError, StopIteration) as exc:
        _error(exc)


@app.command("social-status")
def social_status_command(
    manifest_path: Path = typer.Argument(..., help="Social distribution-manifest.json."),
) -> None:
    """Write publication counts and social-master cleanup holds."""

    try:
        report, destination = write_social_status(manifest_path)
        typer.echo(f"Review approved: {report['review_approved']}")
        typer.echo(f"Social cleanup eligible: {report['cleanup']['eligible']}")
        typer.echo(f"Report: {destination}")
    except (OSError, ValueError, RuntimeError) as exc:
        _error(exc)


@app.command("social-analytics")
def social_analytics_command(
    manifest_path: Path = typer.Argument(..., help="Social distribution-manifest.json."),
    platform: str = typer.Option(..., "--platform"),
    content_id: str = typer.Option(..., "--content-id"),
    label: str = typer.Option(..., "--label", help="For example 24h, 7d, or 28d."),
    metrics_json: Path = typer.Option(..., "--metrics-json"),
    source: str = typer.Option("manual_studio_entry", "--source"),
) -> None:
    """Store one fixed-age platform snapshot and defensible per-view rates."""

    try:
        snapshot, destination = record_analytics_snapshot(
            manifest_path,
            platform=platform,
            content_id=content_id,
            label=label,
            metrics_path=metrics_json,
            source=source,
        )
        typer.echo(f"Analytics snapshot: {destination}")
        typer.echo(f"Captured: {snapshot.captured_at.isoformat()}")
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
