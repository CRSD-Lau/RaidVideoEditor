"""Bind native portrait footage to the landscape clock using verified audio."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from raid_editor.audio.tracks import ROLE_KEYWORDS
from raid_editor.config.models import ProjectConfig
from raid_editor.highlights._portrait_sync import (
    MAX_OFFSET_DEVIATION_SECONDS,
    MIN_CORRELATION,
    MIN_PEAK_MARGIN,
    measure_audio_offset,
)
from raid_editor.ingestion.probe import MediaProbe, probe_media
from raid_editor.util.paths import atomic_write_json, quick_file_fingerprint

_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[ _](\d{2}-\d{2}-\d{2})")
_EXTENSIONS = {".mkv", ".mp4", ".mov", ".flv", ".ts"}
_SCHEMA_VERSION = 1


def _verify_completed_recording(path: Path) -> None:
    # Kept independent of weekly.py: weekly source discovery imports this module.
    if not path.is_file():
        raise ValueError(f"Paired recording does not exist: {path}")
    before = path.stat()
    if before.st_size == 0 or time.time() - before.st_mtime < 120:
        raise ValueError(f"Paired recording is too recent or empty and may still be active: {path}")
    time.sleep(0.25)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Paired recording is still changing: {path}")


@dataclass(frozen=True, slots=True)
class PortraitSource:
    recording: Path
    offset_seconds: float
    landscape_duration_seconds: float
    duration_seconds: float
    signature: dict[str, Any]
    landscape_recording: Path

    def __post_init__(self) -> None:
        if (
            not all(
                math.isfinite(value)
                for value in (
                    self.offset_seconds,
                    self.landscape_duration_seconds,
                    self.duration_seconds,
                )
            )
            or min(self.landscape_duration_seconds, self.duration_seconds) <= 0
        ):
            raise ValueError("Portrait source requires finite offset and positive video durations")

    def validate_window(self, start: float, end: float) -> None:
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError(
                "Native portrait highlight requires a finite, increasing source window"
            )
        coverage_start = max(0.0, self.offset_seconds)
        coverage_end = min(
            self.landscape_duration_seconds, self.offset_seconds + self.duration_seconds
        )
        if start < coverage_start - 1e-6 or end > coverage_end + 1e-6:
            raise ValueError(
                f"Highlight {start:.3f}-{end:.3f}s lies outside verified native portrait coverage "
                f"{coverage_start:.3f}-{coverage_end:.3f}s on the landscape clock"
            )

    def input_args(self, start: float, end: float) -> list[str]:
        """Portrait video is input 0; authoritative landscape audio is input 1."""
        self.validate_window(start, end)
        return [
            "-ss",
            f"{max(0.0, start - self.offset_seconds):.6f}",
            "-i",
            str(self.recording),
            "-ss",
            f"{start:.6f}",
            "-i",
            str(self.landscape_recording),
        ]


def _filename_start(path: Path) -> datetime | None:
    matched = _TIMESTAMP.match(path.stem)
    if matched is None:
        return None
    try:
        return datetime.strptime(" ".join(matched.groups()), "%Y-%m-%d %H-%M-%S")
    except ValueError:
        return None


def discover_portrait_recording(recording: Path, subdirectory: str = "Vertical") -> Path | None:
    """Find one close filename timestamp, never select a newest-file fallback."""
    relative = Path(subdirectory)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Portrait discovery subdirectory must stay within the recording directory")
    start = _filename_start(recording)
    if start is None:
        return None
    directory = recording.expanduser().resolve().parent / relative
    if not directory.is_dir():
        return None
    candidates = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.casefold() not in _EXTENSIONS:
            continue
        candidate_start = _filename_start(path)
        if candidate_start is not None and abs((candidate_start - start).total_seconds()) <= 10:
            candidates.append(path.resolve())
    if len(candidates) > 1:
        raise ValueError(
            "Multiple native portrait recordings match this start time; "
            "set input.vertical_recording to the exact paired file"
        )
    return candidates[0] if candidates else None


def _role_stream(probe: MediaProbe, role: str) -> int:
    matches = []
    for stream in probe.audio_streams:
        title = (stream.title or "").casefold()
        labels = {
            name
            for name, keywords in ROLE_KEYWORDS.items()
            if any(word in title for word in keywords)
        }
        if role in labels:
            if labels != {role}:
                raise ValueError(f"Portrait synchronization audio label is ambiguous for {role}")
            matches.append(stream.index)
    if len(matches) != 1:
        raise ValueError(
            f"Portrait synchronization needs exactly one labelled {role} audio stream; "
            f"found {len(matches)}"
        )
    return matches[0]


def _ffprobe_packets(recording: Path, start: float) -> list[dict[str, Any]]:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise ValueError("Portrait video coverage requires FFprobe on PATH")
    command = [
        executable,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-read_intervals",
        f"{start:.6f}%",
        "-show_entries",
        "packet=pts_time,duration_time",
        "-of",
        "json",
        str(recording),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 or len(result.stdout) > 8_000_000:
            raise ValueError("Portrait video tail could not be verified")
        payload = json.loads(result.stdout)
        packets = payload.get("packets")
        if not isinstance(packets, list) or any(not isinstance(item, dict) for item in packets):
            raise ValueError("Portrait video tail packet metadata is invalid")
        return packets
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise ValueError("Portrait video coverage could not be determined") from None


def _video_duration(recording: Path, probe: MediaProbe) -> float:
    """Use the video stream or actual video tail, never audio/container duration."""
    if len(probe.video_streams) != 1:
        raise ValueError("Native portrait pairing requires exactly one video stream per recording")
    video = probe.video_streams[0]
    duration = video.duration_seconds
    if duration is not None and math.isfinite(duration) and duration > 0:
        return duration
    fps = video.frame_rate
    if fps is None or not math.isfinite(fps) or fps <= 0:
        raise ValueError("Portrait coverage requires a known video frame rate")
    if not math.isfinite(probe.duration_seconds) or probe.duration_seconds <= 0:
        raise ValueError("Portrait coverage requires a valid container timeline")
    for span in (30.0, 120.0, 600.0):
        start = max(0.0, probe.duration_seconds - span)
        ends = []
        for packet in _ffprobe_packets(recording, start):
            try:
                pts = float(packet.get("pts_time", "nan"))
                length = float(packet.get("duration_time", 1 / fps))
            except (TypeError, ValueError):
                continue
            if math.isfinite(pts) and math.isfinite(length) and pts >= 0:
                ends.append(pts + (length if length > 0 else 1 / fps))
        if ends and max(ends) > 0:
            return max(ends)
        if start == 0:
            break
    raise ValueError(
        "Could not verify actual video coverage; container audio duration is insufficient"
    )


def _geometry(landscape: MediaProbe, portrait: MediaProbe, expected: str) -> None:
    if len(landscape.video_streams) != 1 or len(portrait.video_streams) != 1:
        raise ValueError("Native portrait pairing requires one video stream in each file")
    original, native = landscape.video_streams[0], portrait.video_streams[0]
    width, height = map(int, expected.split("x"))
    if native.width >= native.height or (native.width, native.height) != (width, height):
        raise ValueError(
            f"Native portrait recording must be {expected}; found {native.width}x{native.height}"
        )
    if original.width <= original.height:
        raise ValueError("The authoritative landscape recording is not landscape")
    if (
        original.frame_rate is None
        or native.frame_rate is None
        or not math.isfinite(original.frame_rate)
        or not math.isfinite(native.frame_rate)
        or original.frame_rate <= 0
        or native.frame_rate <= 0
        or abs(original.frame_rate - native.frame_rate) > 0.01
    ):
        raise ValueError(
            "Native portrait and landscape recordings must have matching known frame rates"
        )


def _cached_signature(path: Path, request: dict[str, Any]) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("request") != request:
            return None
        signature = payload.get("signature")
        if not isinstance(signature, dict) or signature.get("status") != "verified":
            return None
        if any(signature.get(key) != value for key, value in request.items()):
            return None
        sync = signature["sync"]
        observations = sync["observations"]
        if not isinstance(observations, list) or len(observations) != 3:
            return None
        offsets = []
        for row in observations:
            offset, score, second = (
                float(row[key])
                for key in (
                    "offset_seconds",
                    "correlation",
                    "runner_up_correlation",
                )
            )
            if not all(math.isfinite(value) for value in (offset, score, second)):
                return None
            if score < MIN_CORRELATION or score - second < MIN_PEAK_MARGIN:
                return None
            offsets.append(offset)
        measured = float(signature["offset_seconds"])
        if (
            not math.isfinite(measured)
            or max(offsets) - min(offsets) > MAX_OFFSET_DEVIATION_SECONDS
            or abs(statistics.median(offsets) - measured) > 0.00001
        ):
            return None
        return signature
    except (OSError, ValueError, KeyError, TypeError):
        return None


def resolve_portrait_source(config: ProjectConfig, destination: Path) -> PortraitSource | None:
    if config.highlights.video_source == "landscape":
        return None
    landscape_path = config.input.recording.expanduser().resolve()
    portrait_path = config.input.vertical_recording or discover_portrait_recording(
        landscape_path,
        subdirectory=config.preflight.vertical_recording_subdirectory,
    )
    if portrait_path is None:
        raise ValueError(
            "Native portrait highlights need the matching Vertical recording; "
            "set input.vertical_recording when automatic pairing is unavailable"
        )
    portrait_path = portrait_path.expanduser().resolve()
    if portrait_path == landscape_path:
        raise ValueError("Native portrait video must be a separate recording from landscape audio")
    # Existing completed-recording policy: two minutes old and unchanged across
    # a stability interval. New synthetic fixtures can set an old mtime after
    # FFmpeg has exited; real in-progress recordings must not bypass this check.
    _verify_completed_recording(landscape_path)
    _verify_completed_recording(portrait_path)
    landscape_before = quick_file_fingerprint(landscape_path)
    portrait_before = quick_file_fingerprint(portrait_path)
    landscape_probe, portrait_probe = probe_media(landscape_path), probe_media(portrait_path)
    _geometry(landscape_probe, portrait_probe, config.highlights.vertical_resolution)
    landscape_duration = _video_duration(landscape_path, landscape_probe)
    portrait_duration = _video_duration(portrait_path, portrait_probe)
    role = config.highlights.vertical_sync_audio_role
    configured_index = getattr(config.audio, f"{role}_track")
    landscape_index = _role_stream(landscape_probe, role)
    portrait_index = _role_stream(portrait_probe, role)
    if configured_index != landscape_index:
        raise ValueError(
            f"Configured landscape {role} stream does not match the unique audio label"
        )
    hint = config.highlights.vertical_offset_hint_seconds
    if hint is None:
        first, second = _filename_start(landscape_path), _filename_start(portrait_path)
        hint = (second - first).total_seconds() if first is not None and second is not None else 0.0
    if not math.isfinite(hint):
        raise ValueError("Portrait offset hint must be finite")
    engine = hashlib.sha256(
        Path(__file__).read_bytes() + Path(__file__).with_name("_portrait_sync.py").read_bytes()
    ).hexdigest()
    request = {
        "schema_version": _SCHEMA_VERSION,
        "engine_fingerprint": engine,
        "landscape": landscape_before,
        "portrait": portrait_before,
        "landscape_duration_seconds": landscape_duration,
        "portrait_duration_seconds": portrait_duration,
        "settings": {
            "video_source": "native_vertical",
            "expected_resolution": config.highlights.vertical_resolution,
            "offset_hint_seconds": config.highlights.vertical_offset_hint_seconds,
            "audio_role": role,
        },
    }
    manifest = destination / "portrait-source.json"
    signature = _cached_signature(manifest, request)
    if signature is None:
        offset, observations = measure_audio_offset(
            landscape_path,
            portrait_path,
            landscape_stream_index=landscape_index,
            portrait_stream_index=portrait_index,
            landscape_duration=landscape_duration,
            portrait_duration=portrait_duration,
            hint_seconds=hint,
        )
        signature = {
            **request,
            "status": "verified",
            "offset_seconds": offset,
            "sync": {
                "method": "pcm_envelope_correlation",
                "audio_role": role,
                "landscape_stream_index": landscape_index,
                "portrait_stream_index": portrait_index,
                "maximum_deviation_seconds": MAX_OFFSET_DEVIATION_SECONDS,
                "observations": [row.to_dict() for row in observations],
            },
        }
    if (
        quick_file_fingerprint(landscape_path) != landscape_before
        or quick_file_fingerprint(portrait_path) != portrait_before
    ):
        raise ValueError(
            "A paired recording changed during portrait validation; no binding was saved"
        )
    source = PortraitSource(
        recording=portrait_path,
        landscape_recording=landscape_path,
        offset_seconds=float(signature["offset_seconds"]),
        landscape_duration_seconds=landscape_duration,
        duration_seconds=portrait_duration,
        signature=signature,
    )
    atomic_write_json(
        manifest,
        {
            "author": "Neil Mitchell",
            "last_modified_by": "Neil Mitchell",
            "status": "verified",
            "request": request,
            "signature": signature,
        },
    )
    return source
