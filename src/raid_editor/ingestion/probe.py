"""FFprobe-backed media inspection with bounded source fingerprint caching."""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from raid_editor.util.paths import atomic_write_json, ensure_directory, quick_file_fingerprint


class ProbeError(RuntimeError):
    """Expected FFprobe or media-inspection failure."""


class VideoStream(BaseModel):
    index: int
    codec: str
    profile: str | None = None
    width: int
    height: int
    frame_rate: float | None
    pixel_format: str | None = None
    bitrate: int | None = None
    duration_seconds: float | None = None
    title: str | None = None
    language: str | None = None
    hardware_decoding_compatibility: list[str] = Field(default_factory=list)


class AudioStream(BaseModel):
    index: int
    audio_ordinal: int
    codec: str
    channels: int | None = None
    channel_layout: str | None = None
    sample_rate: int | None = None
    bitrate: int | None = None
    duration_seconds: float | None = None
    title: str | None = None
    language: str | None = None


class MediaProbe(BaseModel):
    schema_version: int = 1
    source: dict[str, Any]
    format_name: str
    format_long_name: str | None = None
    duration_seconds: float
    size_bytes: int
    bitrate: int | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    video_streams: list[VideoStream]
    audio_streams: list[AudioStream]


def _float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    converted = _float(value)
    return int(converted) if converted is not None else None


def _frame_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None


def _tags(stream: dict[str, Any]) -> dict[str, str]:
    return {str(key).casefold(): str(value) for key, value in stream.get("tags", {}).items()}


def _hardware_paths(codec: str) -> list[str]:
    known = {
        "h264": ["d3d11va", "dxva2", "cuda", "qsv"],
        "hevc": ["d3d11va", "dxva2", "cuda", "qsv"],
        "av1": ["d3d11va", "cuda", "qsv"],
        "vp9": ["d3d11va", "cuda", "qsv"],
    }
    return known.get(codec.casefold(), [])


def probe_media(
    recording: Path, output_path: Path | None = None, force: bool = False
) -> MediaProbe:
    source = recording.expanduser().resolve()
    if not source.is_file():
        raise ProbeError(f"Recording does not exist: {source}")
    fingerprint = quick_file_fingerprint(source)
    if output_path and output_path.is_file() and not force:
        try:
            cached = MediaProbe.model_validate_json(output_path.read_text(encoding="utf-8"))
            if cached.source == fingerprint:
                return cached
        except (OSError, ValueError):
            pass

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-print_format",
        "json",
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe is not installed or not available on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown FFprobe error").strip()
        raise ProbeError(f"FFprobe failed: {detail}") from exc
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError("FFprobe returned invalid JSON") from exc

    raw_format = raw.get("format", {})
    audio_ordinal = 0
    video_streams: list[VideoStream] = []
    audio_streams: list[AudioStream] = []
    for stream in raw.get("streams", []):
        tags = _tags(stream)
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            codec = str(stream.get("codec_name", "unknown"))
            video_streams.append(
                VideoStream(
                    index=int(stream["index"]),
                    codec=codec,
                    profile=stream.get("profile"),
                    width=int(stream.get("width", 0)),
                    height=int(stream.get("height", 0)),
                    frame_rate=_frame_rate(stream.get("avg_frame_rate")),
                    pixel_format=stream.get("pix_fmt"),
                    bitrate=_int(stream.get("bit_rate")),
                    duration_seconds=_float(stream.get("duration")),
                    title=tags.get("title") or tags.get("name"),
                    language=tags.get("language"),
                    hardware_decoding_compatibility=_hardware_paths(codec),
                )
            )
        elif codec_type == "audio":
            audio_streams.append(
                AudioStream(
                    index=int(stream["index"]),
                    audio_ordinal=audio_ordinal,
                    codec=str(stream.get("codec_name", "unknown")),
                    channels=_int(stream.get("channels")),
                    channel_layout=stream.get("channel_layout"),
                    sample_rate=_int(stream.get("sample_rate")),
                    bitrate=_int(stream.get("bit_rate")),
                    duration_seconds=_float(stream.get("duration")),
                    title=tags.get("title") or tags.get("name"),
                    language=tags.get("language"),
                )
            )
            audio_ordinal += 1

    duration = _float(raw_format.get("duration"))
    if duration is None:
        stream_durations = [
            value
            for value in [
                *(stream.duration_seconds for stream in video_streams),
                *(stream.duration_seconds for stream in audio_streams),
            ]
            if value is not None
        ]
        duration = max(stream_durations, default=0.0)
    result = MediaProbe(
        source=fingerprint,
        format_name=str(raw_format.get("format_name", "unknown")),
        format_long_name=raw_format.get("format_long_name"),
        duration_seconds=duration,
        size_bytes=int(raw_format.get("size", fingerprint["size_bytes"])),
        bitrate=_int(raw_format.get("bit_rate")),
        tags={str(key): str(value) for key, value in raw_format.get("tags", {}).items()},
        video_streams=video_streams,
        audio_streams=audio_streams,
    )
    if output_path:
        ensure_directory(output_path.parent)
        atomic_write_json(output_path, result.model_dump(mode="json"))
    return result
