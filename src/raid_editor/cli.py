"""Command-line and guided entry points."""

from __future__ import annotations

import json
import logging
import webbrowser
from pathlib import Path
from typing import NoReturn

import typer
import yaml

from raid_editor.audio.tracks import (
    create_audio_samples,
    generate_audio_review_page,
    infer_track_roles,
)
from raid_editor.config.loader import PROJECT_ROOT, load_project_config
from raid_editor.ingestion.probe import probe_media
from raid_editor.resolve.bridge import run_resolve_bridge
from raid_editor.util.logging import configure_logging
from raid_editor.util.paths import atomic_write_text, ensure_directory, slugify
from raid_editor.workflow import (
    analyse_project,
    build_timeline_project,
    inspect_project,
    render_final_project,
    render_preview_project,
    validate_project_artifacts,
)

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
