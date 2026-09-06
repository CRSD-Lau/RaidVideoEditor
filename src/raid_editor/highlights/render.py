"""Explicitly approved portrait highlight export with policy-checked audio."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from raid_editor.config.models import HighlightConfig
from raid_editor.highlights.portrait import PortraitSource
from raid_editor.ingestion.probe import probe_media
from raid_editor.models import HighlightCandidate
from raid_editor.util.paths import (
    atomic_write_json,
    atomic_write_text,
    ensure_directory,
    quick_file_fingerprint,
    slugify,
)


class HighlightRenderError(RuntimeError):
    """Expected approved highlight rendering failure."""


def _escape_drawtext(value: str) -> str:
    # FFmpeg's filtergraph parser terminates a single-quoted drawtext value at
    # a straight apostrophe even when it is written as \'.  Normalize it to the
    # visually equivalent typographic apostrophe, then discard characters that
    # are unsafe or unsupported in the title-card font.  The original title is
    # retained unchanged in manifests and posting metadata.
    normalized = value.replace("'", "’")
    safe = re.sub(r"[^\w .(),/’\-–—:!?]", " ", normalized, flags=re.UNICODE)
    return safe.replace("\\", "\\\\").replace(":", "\\:")


def _filter_graph(
    candidate: HighlightCandidate,
    *,
    width: int,
    height: int,
    audio_stream_indexes: list[int],
    native_portrait: bool = False,
) -> str:
    foreground_height = round(width * 9 / 16)
    filters = [
        "[0:v:0]split=2[bgraw][fgraw]",
        f"[bgraw]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=32[bg]",
        f"[fgraw]scale={width}:{foreground_height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{foreground_height}:(ow-iw)/2:(oh-ih)/2:color=black[fg]",
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"drawbox=x=0:y=80:w={width}:h=190:color=black@0.68:t=fill,"
        "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
        f"text='{_escape_drawtext(candidate.title)}':fontcolor=white:fontsize=54:"
        "x=(w-text_w)/2:y=125,"
        "drawtext=fontfile='C\\:/Windows/Fonts/segoeui.ttf':"
        "text='PIZZA WARRIORS':fontcolor=0xF2C45A:fontsize=30:"
        "x=(w-text_w)/2:y=205,format=yuv420p[vout]",
    ]
    if native_portrait:
        # Aitum already composed this picture. Preserve the complete capture;
        # the landscape title panel would cover its camera/HUD.
        filters = [
            f"[0:v:0]setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1,format=yuv420p[vout]"
        ]
    audio_labels: list[str] = []
    for number, stream_index in enumerate(audio_stream_indexes):
        filters.append(
            f"[{1 if native_portrait else 0}:{stream_index}]"
            "asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{number}]"
        )
        audio_labels.append(f"[a{number}]")
    if len(audio_labels) == 1:
        filters.append(f"{audio_labels[0]}alimiter=limit=0.95[aout]")
    else:
        filters.append(
            "".join(audio_labels) + f"amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
            "alimiter=limit=0.95[aout]"
        )
    return ";".join(filters)


def portrait_presentation_reference(
    source: PortraitSource,
    *,
    audio_stream_indexes: list[int],
    resolution: str,
) -> str:
    """Bind a native review to both sources, timing, audio and composition."""
    payload = {
        "schema_version": 1,
        "source_binding": source.signature,
        "offset_seconds": source.offset_seconds,
        "video_source": "native_vertical",
        "audio_stream_indexes": audio_stream_indexes,
        "resolution": resolution,
        "composition": "preserve-native-no-overlays-v1",
        "audio_mix": "landscape-stems-normalize0-limiter095-v1",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def render_vertical_highlights(
    recording: Path,
    candidates: list[HighlightCandidate],
    destination: Path,
    *,
    audio_stream_indexes: list[int],
    microphone_stream_index: int | None,
    settings: HighlightConfig,
    approved: bool,
    dry_run: bool = False,
    portrait_source: PortraitSource | None = None,
) -> list[Path]:
    """Render explicitly selected portrait clips and a posting package.

    Args:
        recording: Source media file.
        candidates: Reviewed highlight candidates.
        destination: Managed portrait-export directory.
        audio_stream_indexes: Absolute game, Discord, and optionally microphone
            streams to retain.
        microphone_stream_index: Absolute configured microphone stream.
        settings: Portrait geometry and encoder policy.
        approved: Explicit operator approval for real rendering.
        dry_run: Write commands and manifests without invoking FFmpeg.

    Returns:
        Expected or rendered portrait MP4 paths.

    Raises:
        HighlightRenderError: If approval or safe audio is missing, no candidate
            is selected, FFmpeg fails, or a rendered clip fails validation.
    """

    if not approved and not dry_run:
        raise HighlightRenderError(
            "Highlight export requires explicit approval after reviewing the candidate page"
        )
    if not audio_stream_indexes:
        raise HighlightRenderError("Highlight export requires at least one approved audio track")
    if portrait_source is not None and recording.resolve() != (
        portrait_source.landscape_recording.resolve()
    ):
        raise HighlightRenderError("Portrait binding belongs to a different landscape audio source")
    microphone_included = (
        microphone_stream_index is not None and microphone_stream_index in audio_stream_indexes
    )
    if settings.keep_microphone_audio:
        if microphone_stream_index is None:
            raise HighlightRenderError(
                "Microphone audio is enabled but no microphone stream is configured"
            )
        if not microphone_included:
            raise HighlightRenderError(
                "Microphone audio is enabled but the microphone stream is absent from the mix"
            )
    elif microphone_included:
        raise HighlightRenderError("Refusing microphone audio without explicit configuration")
    approved_candidates = [candidate for candidate in candidates if candidate.include]
    if not approved_candidates:
        raise HighlightRenderError("No highlight candidates are marked include=true")
    if settings.video_source == "native_vertical" and portrait_source is None:
        raise HighlightRenderError("Native portrait export requires a verified portrait source")
    if portrait_source is not None:
        # Validate the complete batch before creating any output.
        for candidate in approved_candidates:
            portrait_source.validate_window(candidate.start_seconds, candidate.end_seconds)
    presentation_reference = (
        portrait_presentation_reference(
            portrait_source,
            audio_stream_indexes=audio_stream_indexes,
            resolution=settings.vertical_resolution,
        )
        if portrait_source is not None
        else None
    )
    width, height = map(int, settings.vertical_resolution.split("x"))
    root = ensure_directory(destination)
    outputs: list[Path] = []
    manifest_rows: list[dict[str, object]] = []
    for number, candidate in enumerate(approved_candidates, start=1):
        output = root / f"{number:02d}-{slugify(candidate.title)}-vertical.mp4"
        duration = candidate.end_seconds - candidate.start_seconds
        graph = _filter_graph(
            candidate,
            width=width,
            height=height,
            audio_stream_indexes=audio_stream_indexes,
            native_portrait=portrait_source is not None,
        )
        input_args = (
            portrait_source.input_args(candidate.start_seconds, candidate.end_seconds)
            if portrait_source is not None
            else ["-ss", f"{candidate.start_seconds:.3f}", "-i", str(recording)]
        )
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            *input_args,
            "-t",
            f"{duration:.3f}",
            "-filter_complex",
            graph,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
        ]
        if settings.hardware_encoding:
            command.extend(["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr"])
        else:
            command.extend(["-c:v", "libx264", "-preset", "slow", "-crf", "18"])
        command.extend(
            [
                "-b:v",
                "14M",
                "-maxrate",
                "18M",
                "-bufsize",
                "28M",
                "-c:a",
                "aac",
                "-b:a",
                "256k",
                "-movflags",
                "+faststart+use_metadata_tags",
                "-metadata",
                "artist=Neil Mitchell",
                "-metadata",
                "author=Neil Mitchell",
                "-metadata",
                "last_modified_by=Neil Mitchell",
                "-y",
                str(output),
            ]
        )
        if not dry_run:
            try:
                subprocess.run(command, check=True)
            except FileNotFoundError as exc:
                raise HighlightRenderError("ffmpeg is not installed or not on PATH") from exc
            except subprocess.CalledProcessError as exc:
                raise HighlightRenderError(
                    f"Vertical highlight render failed with exit code {exc.returncode}"
                ) from exc
            probe = probe_media(output)
            valid = (
                len(probe.video_streams) == 1
                and probe.video_streams[0].width == width
                and probe.video_streams[0].height == height
                and len(probe.audio_streams) == 1
                and abs(probe.duration_seconds - duration) <= 0.20
            )
            if not valid:
                raise HighlightRenderError(f"Vertical highlight failed validation: {output}")
        outputs.append(output)
        manifest_rows.append(
            {
                "id": candidate.id,
                "title": candidate.title,
                "category": candidate.category,
                "source_start_seconds": candidate.start_seconds,
                "source_end_seconds": candidate.end_seconds,
                "video_source": "native_vertical" if portrait_source else "landscape",
                "video_recording": str(
                    (portrait_source.recording if portrait_source else recording).resolve()
                ),
                "audio_recording": str(recording.resolve()),
                "portrait_start_seconds": (
                    candidate.start_seconds - portrait_source.offset_seconds
                    if portrait_source
                    else None
                ),
                "portrait_end_seconds": (
                    candidate.end_seconds - portrait_source.offset_seconds
                    if portrait_source
                    else None
                ),
                "presentation_reference": presentation_reference,
                "source_binding": portrait_source.signature if portrait_source else None,
                "audio_stream_indexes": audio_stream_indexes,
                "microphone_stream_index": microphone_stream_index,
                "microphone_included": microphone_included,
                "excluded_microphone_stream_index": (
                    None if microphone_included else microphone_stream_index
                ),
                "output": str(output.resolve()),
                "output_fingerprint": quick_file_fingerprint(output) if not dry_run else None,
                "command": command,
                "rendered": not dry_run,
            }
        )
    atomic_write_json(
        root / "manifest.json",
        {
            "approved": approved,
            "clips": manifest_rows,
            "author": "Neil Mitchell",
            "last_modified_by": "Neil Mitchell",
        },
    )
    captions = [
        "# Shorts and TikTok Package",
        "",
        "Nothing in this folder has been uploaded. Review each portrait export before posting.",
        "",
    ]
    for number, candidate in enumerate(approved_candidates, start=1):
        captions.extend(
            [
                f"## {number}. {candidate.title}",
                "",
                f"- Suggested caption: {candidate.title} with Pizza Warriors in Icecrown Citadel.",
                "- Hashtags: #WorldOfWarcraft #WotLK #IcecrownCitadel",
                f"- Source signals: {', '.join(candidate.signals)}",
                "",
            ]
        )
    atomic_write_text(root / "posting-package.md", "\n".join(captions))
    return outputs
