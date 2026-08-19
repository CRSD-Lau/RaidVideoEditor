"""One-command setup for a completed Pizza Warriors Friday recording."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

from raid_editor.audio.tracks import infer_track_roles
from raid_editor.config.loader import PROJECT_ROOT, load_project_config
from raid_editor.ingestion.probe import MediaProbe, probe_media
from raid_editor.util.paths import atomic_write_text

_RECORDING_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".flv", ".ts"})
_RECORDING_TIMESTAMP = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})[ _](?P<time>\d{2}-\d{2}-\d{2})")
_DATED_CONFIG = re.compile(r"^pizza-warriors-(?P<date>\d{4}-\d{2}-\d{2})\.local\.yaml$")
_SKIP_RECORDING_MARKERS = ("smoke", "test", "replay")


@dataclass(frozen=True, slots=True)
class WeeklyProjectSetup:
    """Result of creating or safely reusing one dated local configuration."""

    config_path: Path
    recording: Path
    raid_date: date
    recording_started_at: datetime | None
    audio_roles: dict[str, int]
    created: bool


def verify_completed_recording(
    recording: Path,
    *,
    minimum_age_minutes: float = 2.0,
    stability_seconds: float = 2.0,
) -> Path:
    """Reject an explicit or discovered recording that may still be changing."""

    source = recording.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Recording does not exist: {source}")
    before = source.stat()
    age_seconds = time.time() - before.st_mtime
    if age_seconds < minimum_age_minutes * 60:
        raise ValueError(
            f"Recording is only {age_seconds / 60:.1f} minutes old and may still be active: "
            f"{source}"
        )
    if stability_seconds > 0:
        time.sleep(stability_seconds)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"The recording is still changing: {source}")
    return source


def find_latest_recording(
    recording_directory: Path,
    *,
    minimum_age_minutes: float = 2.0,
    stability_seconds: float = 2.0,
) -> Path:
    """Return the newest completed non-smoke recording without modifying it."""

    directory = recording_directory.expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Recording directory does not exist: {directory}")
    cutoff = time.time() - minimum_age_minutes * 60
    candidates = [
        path
        for path in directory.iterdir()
        if path.is_file()
        and path.suffix.casefold() in _RECORDING_EXTENSIONS
        and path.stat().st_mtime <= cutoff
        and not any(marker in path.stem.casefold() for marker in _SKIP_RECORDING_MARKERS)
    ]
    if not candidates:
        raise ValueError(f"No completed raid recording was found in {directory}")
    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return verify_completed_recording(
        latest,
        minimum_age_minutes=minimum_age_minutes,
        stability_seconds=stability_seconds,
    )


def recording_date_and_start(recording: Path) -> tuple[date, datetime | None]:
    """Read an OBS timestamp from the filename, with an honest date-only fallback."""

    match = _RECORDING_TIMESTAMP.match(recording.stem)
    if match is not None:
        local_start = datetime.strptime(
            f"{match.group('date')} {match.group('time')}",
            "%Y-%m-%d %H-%M-%S",
        ).astimezone()
        return local_start.date(), local_start
    modified = datetime.fromtimestamp(recording.stat().st_mtime).astimezone()
    return modified.date(), None


def _latest_template(config_directory: Path, *, before: date) -> Path:
    candidates: list[tuple[date, Path]] = []
    for path in config_directory.glob("pizza-warriors-*.local.yaml"):
        match = _DATED_CONFIG.match(path.name)
        if match is None:
            continue
        candidate_date = date.fromisoformat(match.group("date"))
        if candidate_date < before:
            candidates.append((candidate_date, path))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    fallback = config_directory / "project.example.yaml"
    if fallback.is_file():
        return fallback
    raise ValueError("No earlier weekly config or project.example.yaml is available")


def _verified_audio_roles(probe: MediaProbe) -> dict[str, int]:
    video = probe.video_streams[0] if probe.video_streams else None
    if video is None:
        raise ValueError("The recording has no video stream")
    if (video.width, video.height) != (2560, 1440):
        raise ValueError(
            f"Friday recordings must be 2560x1440 landscape; found {video.width}x{video.height}"
        )
    if video.frame_rate is None or abs(video.frame_rate - 60) > 0.01:
        raise ValueError(f"Friday recordings must be 60 fps; found {video.frame_rate or 'unknown'}")
    inferred = infer_track_roles(probe.audio_streams)
    required = ("mixed", "game", "discord", "microphone")
    missing = [role for role in required if inferred.get(role) is None]
    if missing:
        raise ValueError(
            "The recording is missing labelled independent audio stems: " + ", ".join(missing)
        )
    roles: dict[str, int] = {}
    for role in required:
        stream_index = inferred[role]
        if stream_index is None:
            raise AssertionError("missing roles were handled above")
        roles[role] = stream_index
    if len(set(roles.values())) != len(roles):
        raise ValueError("Full Mix, WoW Game, Discord, and Microphone must be distinct streams")
    return roles


def _date_label(value: date) -> str:
    return f"{value.strftime('%B')} {value.day}, {value.year}".upper()


def create_weekly_project_config(
    recording: Path,
    *,
    template_path: Path | None = None,
    config_directory: Path | None = None,
    project_root: Path = PROJECT_ROOT,
    probe: MediaProbe | None = None,
) -> WeeklyProjectSetup:
    """Create one ignored dated config and preserve every previous weekly project."""

    source = recording.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Recording does not exist: {source}")
    raid_date, recording_started_at = recording_date_and_start(source)
    config_dir = (config_directory or project_root / "config").expanduser().resolve()
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / f"pizza-warriors-{raid_date.isoformat()}.local.yaml"
    media_probe = probe or probe_media(source)
    roles = _verified_audio_roles(media_probe)
    if target.is_file():
        existing = load_project_config(target)
        if existing.input.recording.resolve() != source:
            raise ValueError(
                f"A different recording already owns the dated config {target}; refusing overwrite"
            )
        return WeeklyProjectSetup(
            config_path=target,
            recording=source,
            raid_date=raid_date,
            recording_started_at=recording_started_at,
            audio_roles=roles,
            created=False,
        )

    template = (
        template_path.expanduser().resolve()
        if template_path is not None
        else _latest_template(config_dir, before=raid_date)
    )
    if not template.is_file():
        raise ValueError(f"Weekly template does not exist: {template}")
    payload = load_project_config(template).model_dump(mode="json")
    label = _date_label(raid_date)
    payload["project"].update(
        {
            "name": f"Pizza Warriors ICC - {raid_date.isoformat()}",
            "game": "World of Warcraft",
            "expansion": "Wrath of the Lich King",
            "raid": "Icecrown Citadel",
            "raid_date": raid_date.isoformat(),
        }
    )
    payload["input"].update(
        {
            "recording": str(source),
            "manual_pulls": None,
        }
    )
    payload["audio"].update(
        {
            "microphone_track": roles["microphone"],
            "game_track": roles["game"],
            "discord_track": roles["discord"],
            "mixed_track": roles["mixed"],
            "keep_game_audio": True,
            "keep_discord_audio": False,
            "remove_microphone": True,
        }
    )
    payload["detection"].update(
        {
            "minimum_pull_seconds": 15,
            "merge_gap_seconds": 8,
            "pre_roll_seconds": 5,
            "post_roll_seconds": 8,
            "confidence_threshold": 0.70,
            "combat_log_offset_seconds": 0,
            "recording_started_at": (
                recording_started_at.isoformat() if recording_started_at is not None else None
            ),
        }
    )
    payload["difficulty"].update(
        {
            "enabled": True,
            "raid_size": 25,
            "expected_bosses": 12,
            "title_raid_abbreviation": "ICC",
            "require_confirmed_for_auto_title": True,
        }
    )
    payload["highlights"].update(
        {
            "enabled": True,
            "manual_selection": None,
            "keep_game_audio": True,
            "keep_discord_audio": True,
            "keep_microphone_audio": True,
            "vertical_resolution": "1080x1920",
            "hardware_encoding": True,
        }
    )
    payload["editing"].update(
        {
            "include_trash_pulls": False,
            "include_boss_wipes": False,
            "include_boss_kills": True,
            "include_run_backs": False,
            "include_loot": True,
            "transition_duration_seconds": 0.4,
        }
    )
    watermark = project_root / "assets" / "pizza-warriors-lausudo-camera-cover-v1.png"
    presentation = (
        project_root / "assets" / "pizza-warriors-raid-presentation-v2-clean-1920x1080.png"
    )
    for asset in (watermark, presentation):
        if not asset.is_file():
            raise ValueError(f"Required weekly presentation asset is missing: {asset}")
    payload["preview"].update(
        {
            "resolution": "1280x720",
            "fps": 30,
            "bitrate": "4M",
            "hardware_encoding": True,
            "review_clip_mode": "full",
            "watermark": {
                "image": str(watermark),
                "x_fraction": 0.0,
                "y_fraction": 0.742,
                "width_fraction": 0.258,
                "height_fraction": 0.258,
            },
            "presentation": {
                "theme": "icecrown_v2",
                "background_image": str(presentation),
                "intro_seconds": 5,
                "outro_seconds": 5,
                "intro_kicker": "WEEKLY RAID COVERAGE",
                "intro_title": "ICECROWN CITADEL",
                "intro_subtitle": f"{label} / 25 PLAYER RAID",
                "outro_kicker": "FULL CLEAR",
                "outro_title": "RAID COMPLETE",
                "outro_subtitle": None,
                "boss_kicker": "PIZZA WARRIORS / ICECROWN CITADEL",
            },
        }
    )
    payload["final"].update(
        {
            "resolution": "source",
            "fps": "source",
            "codec": "h264",
            "hardware_encoding": True,
            "constant_qp": 18,
            "preset": "p6",
            "audio_bitrate": "320k",
        }
    )
    payload["youtube"].update(
        {
            "privacy_status": "public",
            "category_id": "20",
            "category_name": "Gaming",
            "game_title": "World of Warcraft",
            "game_rating": "Unrated",
            "title": None,
            "description": None,
            "tags": [
                "World of Warcraft",
                "Wrath of the Lich King",
                "Icecrown Citadel",
                "ICC raid",
                "WotLK",
                "Pizza Warriors",
                "Lich King",
                "ICC full clear",
            ],
            "hashtags": ["#WorldOfWarcraft", "#WotLK", "#IcecrownCitadel"],
            "default_language": "en",
            "made_for_kids": False,
            "age_restricted": False,
            "contains_synthetic_media": False,
            "forbid_em_dash": True,
            "thumbnail_variants": 3,
            "selected_thumbnail_variant": 1,
            "playlist_auto_add": True,
            "playlist_title": "Pizza Warriors Weekly ICC Clears",
            "playlist_privacy_status": "public",
            "analytics_enabled": True,
        }
    )
    atomic_write_text(
        target,
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
    )
    load_project_config(target)
    return WeeklyProjectSetup(
        config_path=target,
        recording=source,
        raid_date=raid_date,
        recording_started_at=recording_started_at,
        audio_roles=roles,
        created=True,
    )
