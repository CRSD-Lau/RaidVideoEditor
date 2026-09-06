"""Read-only Friday OBS, audio-routing, disk, log, and smoke-recording checks."""

from __future__ import annotations

import configparser
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from raid_editor.config.models import ProjectConfig
from raid_editor.ingestion.probe import probe_media
from raid_editor.util.paths import atomic_write_json, atomic_write_text

CheckStatus = Literal["passed", "warning", "failed"]


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """Describe one named read-only readiness check and its evidence."""

    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Collect the timestamped Friday readiness verdict and all checks."""

    status: Literal["passed", "failed"]
    checked_at: str
    checks: tuple[PreflightCheck, ...]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serializable report without changing local state."""

        return {
            "status": self.status,
            "checked_at": self.checked_at,
            "checks": [
                {"name": item.name, "status": item.status, "detail": item.detail}
                for item in self.checks
            ],
        }


def _check(name: str, passed: bool, detail: str, *, warning: bool = False) -> PreflightCheck:
    status: CheckStatus = "passed" if passed else ("warning" if warning else "failed")
    return PreflightCheck(name=name, status=status, detail=detail)


def _load_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(path, encoding="utf-8-sig")
    return parser


def _active_obs_names(obs_root: Path) -> tuple[str | None, str | None]:
    user_ini = obs_root / "user.ini"
    if not user_ini.is_file():
        return None, None
    parser = _load_ini(user_ini)
    return (
        parser.get("Basic", "ProfileDir", fallback=None),
        parser.get("Basic", "SceneCollectionFile", fallback=None),
    )


def _track_enabled(mask: int, track: int) -> bool:
    return bool(mask & (1 << (track - 1)))


def _source_track_set(mask: int) -> set[int]:
    return {track for track in range(1, 7) if _track_enabled(mask, track)}


def _scene_sources(collection: dict[str, object], scene_name: str) -> tuple[set[str], bool]:
    sources = collection.get("sources", [])
    if not isinstance(sources, list):
        return set(), False
    for source in sources:
        if not isinstance(source, dict) or source.get("name") != scene_name:
            continue
        settings = source.get("settings", {})
        if not isinstance(settings, dict):
            return set(), True
        items = settings.get("items", [])
        if not isinstance(items, list):
            return set(), True
        names = {
            str(item["name"])
            for item in items
            if isinstance(item, dict) and item.get("name") and item.get("visible", True)
        }
        return names, True
    return set(), False


def _smoke_checks(
    recording: Path,
    config: ProjectConfig,
) -> list[PreflightCheck]:
    try:
        probe = probe_media(recording, force=True)
    except (OSError, ValueError, RuntimeError) as exc:
        return [_check("smoke_recording_readable", False, str(exc))]
    video = probe.video_streams[0] if probe.video_streams else None
    expected_width, expected_height = map(int, config.preflight.expected_resolution.split("x"))
    age_minutes = (
        datetime.now(UTC) - datetime.fromtimestamp(recording.stat().st_mtime, tz=UTC)
    ).total_seconds() / 60
    checks = [
        _check(
            "smoke_recording_fresh",
            age_minutes <= config.preflight.smoke_recording_max_age_minutes,
            f"last write was {age_minutes:.1f} minutes ago",
        ),
        _check(
            "smoke_recording_duration",
            config.preflight.smoke_recording_min_seconds
            <= probe.duration_seconds
            <= config.preflight.smoke_recording_max_seconds,
            f"duration is {probe.duration_seconds:.1f} seconds; expected "
            f"{config.preflight.smoke_recording_min_seconds:.0f} to "
            f"{config.preflight.smoke_recording_max_seconds:.0f} seconds",
        ),
        _check(
            "smoke_recording_geometry",
            video is not None
            and video.width == expected_width
            and video.height == expected_height
            and video.frame_rate is not None
            and abs(video.frame_rate - config.preflight.expected_fps) < 0.01,
            (
                f"expected {config.preflight.expected_resolution} at "
                f"{config.preflight.expected_fps} fps; found "
                f"{video.width}x{video.height} at {video.frame_rate:.3f} fps"
                if video is not None and video.frame_rate is not None
                else "smoke recording has no usable video stream"
            ),
        ),
    ]
    titles = {stream.title or "" for stream in probe.audio_streams}
    required_titles = set(config.preflight.required_recording_tracks.values())
    checks.append(
        _check(
            "smoke_recording_audio_tracks",
            required_titles <= titles,
            f"required labels {sorted(required_titles)}; found {sorted(titles)}",
        )
    )
    return checks


def _vertical_smoke_checks(
    recording: Path,
    config: ProjectConfig,
    *,
    landscape_duration: float | None = None,
) -> list[PreflightCheck]:
    try:
        probe = probe_media(recording, force=True)
    except (OSError, ValueError, RuntimeError) as exc:
        return [_check("vertical_smoke_recording_readable", False, str(exc))]
    video = probe.video_streams[0] if probe.video_streams else None
    expected_width, expected_height = map(
        int, config.preflight.vertical_expected_resolution.split("x")
    )
    age_minutes = (
        datetime.now(UTC) - datetime.fromtimestamp(recording.stat().st_mtime, tz=UTC)
    ).total_seconds() / 60
    required_audio_count = len(config.preflight.vertical_required_recording_tracks)
    checks = [
        _check(
            "vertical_smoke_recording_fresh",
            age_minutes <= config.preflight.smoke_recording_max_age_minutes,
            f"last write was {age_minutes:.1f} minutes ago",
        ),
        _check(
            "vertical_smoke_recording_duration",
            config.preflight.smoke_recording_min_seconds
            <= probe.duration_seconds
            <= config.preflight.smoke_recording_max_seconds,
            f"duration is {probe.duration_seconds:.1f} seconds; expected "
            f"{config.preflight.smoke_recording_min_seconds:.0f} to "
            f"{config.preflight.smoke_recording_max_seconds:.0f} seconds",
        ),
        _check(
            "vertical_smoke_recording_geometry",
            video is not None
            and video.width == expected_width
            and video.height == expected_height
            and video.frame_rate is not None
            and abs(video.frame_rate - config.preflight.expected_fps) < 0.01,
            (
                f"expected {config.preflight.vertical_expected_resolution} at "
                f"{config.preflight.expected_fps} fps; found "
                f"{video.width}x{video.height} at {video.frame_rate:.3f} fps"
                if video is not None and video.frame_rate is not None
                else "vertical smoke recording has no usable video stream"
            ),
        ),
        _check(
            "vertical_smoke_recording_audio_tracks",
            len(probe.audio_streams) >= required_audio_count,
            f"expected at least {required_audio_count} audio streams; "
            f"found {len(probe.audio_streams)}",
        ),
    ]
    if landscape_duration is not None:
        difference = abs(probe.duration_seconds - landscape_duration)
        checks.append(
            _check(
                "dual_smoke_recording_alignment",
                difference <= 2.0,
                f"landscape/portrait duration difference is {difference:.3f} seconds; "
                "expected no more than 2.000 seconds",
            )
        )
    return checks


def _aitum_vertical_checks(
    profile_directory: Path,
    record_path: Path | None,
    config: ProjectConfig,
) -> list[PreflightCheck]:
    aitum_path = profile_directory / config.preflight.aitum_config_file
    try:
        payload = json.loads(aitum_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [_check("aitum_vertical_configuration", False, str(exc))]

    canvases = payload.get("canvas", [])
    canvas = next(
        (
            value
            for value in canvases
            if isinstance(value, dict)
            and value.get("name") == config.preflight.vertical_canvas_name
        ),
        None,
    )
    expected_width, expected_height = map(
        int, config.preflight.vertical_expected_resolution.split("x")
    )
    outputs = payload.get("outputs", [])
    output = next(
        (
            value
            for value in outputs
            if isinstance(value, dict)
            and value.get("name") == config.preflight.vertical_recording_output_name
        ),
        None,
    )
    canvas_ok = bool(
        canvas and canvas.get("width") == expected_width and canvas.get("height") == expected_height
    )
    canvas_detail = (
        f"expected {config.preflight.vertical_canvas_name} at "
        f"{config.preflight.vertical_expected_resolution}; found "
        f"{canvas.get('width')}x{canvas.get('height')}"
        if canvas
        else "canvas not found"
    )
    checks = [_check("aitum_vertical_canvas", canvas_ok, canvas_detail)]
    if output is None:
        checks.append(
            _check(
                "aitum_vertical_recording_output",
                False,
                f"output not found: {config.preflight.vertical_recording_output_name}",
            )
        )
        return checks

    expected_path = (
        record_path / config.preflight.vertical_recording_subdirectory
        if record_path is not None
        else None
    )
    output_path = Path(str(output.get("path", "")))
    path_ok = expected_path is not None and output_path.resolve() == expected_path.resolve()
    tracks_mask = int(output.get("audio_tracks", 0))
    missing_tracks = [
        track
        for track in config.preflight.vertical_required_recording_tracks
        if not _track_enabled(tracks_mask, track)
    ]
    video_settings = output.get("video_encoder_settings", {})
    if not isinstance(video_settings, dict):
        video_settings = {}
    checks.extend(
        [
            _check(
                "aitum_vertical_recording_output",
                output.get("enabled") is True
                and output.get("type") == "record"
                and output.get("canvas") == config.preflight.vertical_canvas_name
                and output.get("format") in {"hybrid_mp4", "mkv"},
                f"enabled={output.get('enabled')}; type={output.get('type')}; "
                f"canvas={output.get('canvas')}; format={output.get('format')}",
            ),
            _check(
                "aitum_vertical_recording_path",
                path_ok and output_path.is_dir(),
                f"expected {expected_path}; configured {output_path}",
            ),
            _check(
                "aitum_vertical_video_encoder",
                output.get("advanced") is True
                and output.get("video_encoder") == config.preflight.vertical_video_encoder
                and video_settings.get("rate_control") == config.preflight.vertical_rate_control
                and video_settings.get("cqp") == config.preflight.vertical_quality,
                f"encoder={output.get('video_encoder')}; "
                f"rate_control={video_settings.get('rate_control')}; "
                f"quality={video_settings.get('cqp')}",
            ),
            _check(
                "aitum_vertical_audio_tracks",
                not missing_tracks,
                f"recording mask {tracks_mask}; missing tracks {missing_tracks or 'none'}",
            ),
            _check(
                "aitum_start_all_recordings_hotkey",
                bool(payload.get("start_all_recordings_hotkey")),
                "a dedicated Start All Recordings hotkey is configured"
                if payload.get("start_all_recordings_hotkey")
                else "Start All Recordings has no hotkey",
            ),
            _check(
                "aitum_vertical_stop_hotkey",
                bool(output.get("stop_hotkey")),
                "a dedicated portrait Stop Recording hotkey is configured"
                if output.get("stop_hotkey")
                else "portrait Stop Recording has no hotkey",
            ),
        ]
    )
    return checks


def run_preflight(
    config: ProjectConfig,
    *,
    destination_json: Path,
    destination_markdown: Path,
    obs_root: Path | None = None,
    smoke_recording: Path | None = None,
    vertical_smoke_recording: Path | None = None,
) -> PreflightReport:
    """Inspect local configuration without changing OBS, WoW, or recordings.

    Args:
        config: Validated project and expected OBS policy.
        destination_json: Machine-readable report destination.
        destination_markdown: Human-readable report destination.
        obs_root: Optional OBS root used by tests or alternate installs.
        smoke_recording: Optional fresh short recording to probe end to end.
        vertical_smoke_recording: Optional matching Aitum portrait smoke file.

    Returns:
        A passed/failed report; warnings do not fail the overall status.

    Raises:
        OSError: If local configuration or reports cannot be read or written.
    """

    root = obs_root or Path(os.environ.get("APPDATA", "")) / "obs-studio"
    active_profile, active_collection = _active_obs_names(root)
    expected_profile = config.preflight.obs_profile_dir or active_profile
    expected_collection = config.preflight.obs_scene_collection_file or active_collection
    checks: list[PreflightCheck] = []
    checks.append(
        _check(
            "obs_configuration_found",
            root.is_dir(),
            f"OBS configuration root: {root}",
        )
    )
    if expected_profile is None:
        checks.append(_check("obs_profile_selected", False, "No OBS profile is selected"))
    else:
        checks.append(
            _check(
                "obs_profile_selected",
                active_profile == expected_profile,
                "expected profile directory "
                f"{expected_profile}; active {active_profile or 'unknown'}",
            )
        )
    if expected_collection is None:
        checks.append(
            _check("obs_scene_collection_selected", False, "No OBS scene collection is selected")
        )
    else:
        checks.append(
            _check(
                "obs_scene_collection_selected",
                active_collection == expected_collection,
                f"expected {expected_collection}; active {active_collection or 'unknown'}",
            )
        )

    profile_directory = root / "basic" / "profiles" / str(expected_profile)
    profile_path = profile_directory / "basic.ini"
    record_path: Path | None = None
    if profile_path.is_file():
        profile = _load_ini(profile_path)
        output_width = profile.getint("Video", "OutputCX", fallback=0)
        output_height = profile.getint("Video", "OutputCY", fallback=0)
        fps = profile.getint("Video", "FPSCommon", fallback=0)
        expected_width, expected_height = map(int, config.preflight.expected_resolution.split("x"))
        checks.append(
            _check(
                "obs_recording_geometry",
                output_width == expected_width
                and output_height == expected_height
                and fps == config.preflight.expected_fps,
                f"expected {expected_width}x{expected_height} at "
                f"{config.preflight.expected_fps} fps; "
                f"profile has {output_width}x{output_height} at {fps} fps",
            )
        )
        record_path = Path(profile.get("AdvOut", "RecFilePath", fallback=""))
        checks.append(
            _check(
                "obs_recording_path",
                record_path.is_dir(),
                f"recording path: {record_path}",
            )
        )
        if record_path.is_dir():
            free_gib = shutil.disk_usage(record_path).free / (1024**3)
            checks.append(
                _check(
                    "recording_disk_space",
                    free_gib >= config.preflight.minimum_free_space_gib,
                    f"{free_gib:.1f} GiB free; minimum "
                    f"{config.preflight.minimum_free_space_gib:.1f} GiB",
                )
            )
        format_name = profile.get("AdvOut", "RecFormat2", fallback="")
        checks.append(
            _check(
                "safe_recording_format",
                format_name in {"hybrid_mp4", "mkv"},
                f"recording format is {format_name or 'unset'}; expected Hybrid MP4 or MKV",
            )
        )
        recording_mask = profile.getint("AdvOut", "RecTracks", fallback=0)
        missing_tracks = [
            track
            for track in config.preflight.required_recording_tracks
            if not _track_enabled(recording_mask, track)
        ]
        checks.append(
            _check(
                "recording_tracks_enabled",
                not missing_tracks,
                f"recording mask {recording_mask}; missing tracks {missing_tracks or 'none'}",
            )
        )
        names_ok = True
        name_details: list[str] = []
        for track, expected_name in config.preflight.required_recording_tracks.items():
            actual = profile.get("AdvOut", f"Track{track}Name", fallback="")
            names_ok = names_ok and actual == expected_name
            name_details.append(f"{track}:{actual or 'unnamed'}")
        checks.append(
            _check(
                "recording_track_labels",
                names_ok,
                "tracks " + ", ".join(name_details),
            )
        )
        if config.preflight.require_vertical_recording:
            stop_recording_hotkey = profile.get("Hotkeys", "OBSBasic.StopRecording", fallback="")
            checks.append(
                _check(
                    "obs_stop_recording_hotkey",
                    bool(stop_recording_hotkey),
                    "a dedicated Stop Recording hotkey is configured"
                    if stop_recording_hotkey
                    else "Stop Recording has no hotkey",
                )
            )
    else:
        checks.append(_check("obs_profile_readable", False, f"missing profile: {profile_path}"))

    if config.preflight.require_vertical_recording:
        checks.extend(_aitum_vertical_checks(profile_directory, record_path, config))

    collection_path = root / "basic" / "scenes" / str(expected_collection)
    if collection_path.is_file():
        try:
            collection = json.loads(collection_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            checks.append(_check("scene_collection_readable", False, str(exc)))
            collection = {}
        current_scene = collection.get("current_program_scene") or collection.get("current_scene")
        checks.append(
            _check(
                "raid_scene_active",
                config.preflight.expected_scene is None
                or current_scene == config.preflight.expected_scene,
                f"expected {config.preflight.expected_scene or 'any scene'}; "
                f"active {current_scene}",
            )
        )
        sources = collection.get("sources", [])
        source_map = (
            {
                str(source.get("name")): source
                for source in sources
                if isinstance(source, dict) and source.get("name")
            }
            if isinstance(sources, list)
            else {}
        )
        source_map.update(
            {
                str(value.get("name")): value
                for value in collection.values()
                if isinstance(value, dict) and value.get("name") and "mixers" in value
            }
        )
        for source_name, expected_tracks in config.preflight.required_source_tracks.items():
            source = source_map.get(source_name)
            actual_tracks = (
                _source_track_set(int(source.get("mixers", 0))) if source is not None else set()
            )
            checks.append(
                _check(
                    f"audio_route_{source_name}",
                    actual_tracks == set(expected_tracks),
                    f"expected tracks {expected_tracks}; found {sorted(actual_tracks)}",
                )
            )
        if config.preflight.expected_scene is not None:
            scene_sources, found = _scene_sources(collection, config.preflight.expected_scene)
            required = set(config.preflight.required_scene_sources)
            checks.append(
                _check(
                    "raid_scene_sources",
                    found and required <= scene_sources,
                    f"required {sorted(required)}; visible {sorted(scene_sources)}",
                )
            )
    else:
        checks.append(
            _check("scene_collection_readable", False, f"missing collection: {collection_path}")
        )

    combat_log = config.input.combat_log
    if combat_log is None or not combat_log.is_file():
        checks.append(
            _check("combat_log_present", False, "Combat log is not configured or missing")
        )
    else:
        age_minutes = (
            datetime.now(UTC) - datetime.fromtimestamp(combat_log.stat().st_mtime, tz=UTC)
        ).total_seconds() / 60
        fresh = age_minutes <= config.preflight.combat_log_max_age_minutes
        checks.append(
            _check(
                "combat_log_fresh",
                fresh,
                f"last write was {age_minutes:.1f} minutes ago; "
                "run /combatlog and create a fresh event",
                warning=not config.preflight.require_fresh_combat_log,
            )
        )

    landscape_duration: float | None = None
    if smoke_recording is None:
        checks.append(
            _check(
                "smoke_recording",
                False,
                "No smoke recording supplied; make a 10-second test and pass --smoke-recording",
                warning=True,
            )
        )
    else:
        resolved_smoke = smoke_recording.expanduser().resolve()
        checks.extend(_smoke_checks(resolved_smoke, config))
        try:
            landscape_duration = probe_media(resolved_smoke, force=True).duration_seconds
        except (OSError, ValueError, RuntimeError):
            landscape_duration = None

    if config.preflight.require_vertical_recording:
        if vertical_smoke_recording is None:
            checks.append(
                _check(
                    "vertical_smoke_recording",
                    False,
                    "No portrait smoke recording supplied; pass "
                    "--vertical-smoke-recording with the matching Aitum file",
                    warning=smoke_recording is None,
                )
            )
        else:
            checks.extend(
                _vertical_smoke_checks(
                    vertical_smoke_recording.expanduser().resolve(),
                    config,
                    landscape_duration=landscape_duration,
                )
            )

    report = PreflightReport(
        status="failed" if any(item.status == "failed" for item in checks) else "passed",
        checked_at=datetime.now(UTC).isoformat(),
        checks=tuple(checks),
    )
    atomic_write_json(destination_json, report.as_dict())
    lines = [
        "# Friday Raid Preflight",
        "",
        f"Overall: **{report.status.upper()}**",
        "",
        "| Check | Status | Detail |",
        "|---|---:|---|",
        *[
            f"| {item.name} | {item.status} | {item.detail.replace('|', '/')} |"
            for item in report.checks
        ],
        "",
        "This report is read-only. It never reads OBS service credentials or stream keys.",
    ]
    atomic_write_text(destination_markdown, "\n".join(lines) + "\n")
    return report
