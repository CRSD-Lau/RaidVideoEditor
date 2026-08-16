"""Explicitly approved portrait highlight export with policy-checked audio."""

from __future__ import annotations

import subprocess
from pathlib import Path

from raid_editor.config.models import HighlightConfig
from raid_editor.ingestion.probe import probe_media
from raid_editor.models import HighlightCandidate
from raid_editor.util.paths import atomic_write_json, atomic_write_text, ensure_directory, slugify


class HighlightRenderError(RuntimeError):
    """Expected approved highlight rendering failure."""


def _escape_drawtext(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def _filter_graph(
    candidate: HighlightCandidate,
    *,
    width: int,
    height: int,
    audio_stream_indexes: list[int],
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
    audio_labels: list[str] = []
    for number, stream_index in enumerate(audio_stream_indexes):
        filters.append(
            f"[0:{stream_index}]aresample=48000,"
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
        )
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{candidate.start_seconds:.3f}",
            "-i",
            str(recording),
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
                "+faststart",
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
                "audio_stream_indexes": audio_stream_indexes,
                "microphone_stream_index": microphone_stream_index,
                "microphone_included": microphone_included,
                "excluded_microphone_stream_index": (
                    None if microphone_included else microphone_stream_index
                ),
                "output": str(output.resolve()),
                "command": command,
                "rendered": not dry_run,
            }
        )
    atomic_write_json(root / "manifest.json", {"approved": approved, "clips": manifest_rows})
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
