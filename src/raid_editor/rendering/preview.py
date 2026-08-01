"""FFmpeg preview rendering; no final-render or upload path exists here."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from raid_editor.models import TimelineDocument
from raid_editor.music.library import MusicTrack
from raid_editor.timeline.export import timeline_digest
from raid_editor.util.paths import atomic_write_json, atomic_write_text, ensure_directory


class PreviewRenderError(RuntimeError):
    """Expected, user-actionable review-render failure."""


def _escape_drawtext(value: str) -> str:
    safe = re.sub(r"[^\w .()\-–—]", " ", value, flags=re.UNICODE)
    return safe.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_filter_graph(
    timeline: TimelineDocument,
    *,
    width: int,
    height: int,
    fps: int,
    transition_seconds: float,
    music: MusicTrack | None,
) -> str:
    if not timeline.clips:
        raise PreviewRenderError("Timeline has no included clips")
    filters: list[str] = []
    video_labels: list[str] = []
    transition = max(0.0, transition_seconds)
    for clip_index, clip in enumerate(timeline.clips):
        duration = clip.source_out - clip.source_in
        fade = min(transition, duration / 4)
        label = f"v{clip_index}"
        video_filter = (
            f"[0:v:0]trim=start={clip.source_in:.6f}:end={clip.source_out:.6f},"
            "setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={fps},"
            "drawbox=x=0:y=0:w=iw:h=72:color=black@0.55:t=fill:enable='lt(t,4)',"
            "drawtext=fontfile='C\\:/Windows/Fonts/segoeui.ttf':"
            f"text='{_escape_drawtext(clip.label)}':"
            "fontcolor=white:fontsize=30:x=36:y=22:enable='lt(t,4)'"
        )
        if fade > 0:
            video_filter += (
                f",fade=t=in:st=0:d={fade:.3f},"
                f"fade=t=out:st={max(0.0, duration - fade):.6f}:d={fade:.3f}"
            )
        filters.append(f"{video_filter}[{label}]")
        video_labels.append(f"[{label}]")
    filters.append(
        "".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0,format=yuv420p[vout]"
    )

    track_outputs: list[str] = []
    for track_number, stream_index in enumerate(timeline.retained_audio_stream_indexes):
        segment_labels: list[str] = []
        for clip_index, clip in enumerate(timeline.clips):
            duration = clip.source_out - clip.source_in
            fade = min(transition, duration / 4)
            label = f"a{track_number}_{clip_index}"
            audio_filter = (
                f"[0:{stream_index}]atrim=start={clip.source_in:.6f}:end={clip.source_out:.6f},"
                "asetpts=PTS-STARTPTS,aresample=48000"
            )
            if fade > 0:
                audio_filter += (
                    f",afade=t=in:st=0:d={fade:.3f},"
                    f"afade=t=out:st={max(0.0, duration - fade):.6f}:d={fade:.3f}"
                )
            filters.append(f"{audio_filter}[{label}]")
            segment_labels.append(f"[{label}]")
        output = f"atrack{track_number}"
        filters.append(
            "".join(segment_labels) + f"concat=n={len(segment_labels)}:v=0:a=1[{output}]"
        )
        track_outputs.append(f"[{output}]")

    mix_inputs = list(track_outputs)
    if music is not None:
        duration = timeline.duration_seconds
        fade_out_start = max(0.0, duration - 2.0)
        filters.append(
            f"[1:a:0]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
            "aresample=48000,volume=0.16,"
            f"afade=t=in:st=0:d=2,afade=t=out:st={fade_out_start:.6f}:d=2[music]"
        )
        mix_inputs.append("[music]")
    if not mix_inputs:
        raise PreviewRenderError("Preview has no retained audio; configure audio roles first")
    if len(mix_inputs) == 1:
        filters.append(f"{mix_inputs[0]}alimiter=limit=0.95[aout]")
    else:
        filters.append(
            "".join(mix_inputs)
            + f"amix=inputs={len(mix_inputs)}:duration=longest:dropout_transition=2:"
            "normalize=0,alimiter=limit=0.95[aout]"
        )
    return ";\n".join(filters) + "\n"


def build_preview_command(
    timeline: TimelineDocument,
    filter_script: Path,
    destination: Path,
    *,
    bitrate: str,
    music: MusicTrack | None,
    hardware_encoding: bool = False,
) -> list[str]:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", str(timeline.source)]
    if music is not None:
        command.extend(["-stream_loop", "-1", "-i", str(music.local_file)])
    command.extend(
        [
            "-/filter_complex",
            str(filter_script),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
        ]
    )
    if hardware_encoding:
        command.extend(
            [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p5",
                "-tune",
                "hq",
                "-rc",
                "vbr",
            ]
        )
    else:
        command.extend(["-c:v", "libx264", "-preset", "medium"])
    command.extend(
        [
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            bitrate,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-metadata",
            "comment=Review render only; generated by WoW Raid Video Editor",
            "-y",
            str(destination),
        ]
    )
    return command


def _bitrate_bits_per_second(value: str) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", value.strip())
    if not match:
        return 4_000_000
    amount = float(match.group(1))
    suffix = match.group(2).casefold()
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[suffix]
    return round(amount * multiplier)


def render_preview(
    timeline: TimelineDocument,
    destination: Path,
    *,
    resolution: str,
    fps: int,
    bitrate: str,
    transition_seconds: float,
    music: MusicTrack | None = None,
    hardware_encoding: bool = False,
    dry_run: bool = False,
) -> list[str]:
    width_text, height_text = resolution.split("x", maxsplit=1)
    width, height = int(width_text), int(height_text)
    ensure_directory(destination.parent)
    filter_script = destination.with_suffix(".filters.txt")
    graph = build_filter_graph(
        timeline,
        width=width,
        height=height,
        fps=fps,
        transition_seconds=transition_seconds,
        music=music,
    )
    atomic_write_text(filter_script, graph)
    command = build_preview_command(
        timeline,
        filter_script,
        destination,
        bitrate=bitrate,
        music=music,
        hardware_encoding=hardware_encoding,
    )
    signature = hashlib.sha256(
        (
            timeline_digest(timeline)
            + graph
            + json.dumps(command, ensure_ascii=False)
            + (music.sha256 if music else "")
        ).encode()
    ).hexdigest()
    manifest = destination.with_suffix(".manifest.json")
    if destination.is_file() and manifest.is_file():
        try:
            if json.loads(manifest.read_text(encoding="utf-8")).get("signature") == signature:
                return command
        except (OSError, json.JSONDecodeError):
            pass
    estimated_bytes = (_bitrate_bits_per_second(bitrate) + 192_000) * timeline.duration_seconds / 8
    free_bytes = shutil.disk_usage(destination.parent).free
    if free_bytes < estimated_bytes * 1.5:
        raise PreviewRenderError(
            f"Insufficient free space: need about {estimated_bytes * 1.5 / 1e9:.2f} GB"
        )
    if dry_run:
        return command
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise PreviewRenderError("ffmpeg is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise PreviewRenderError(f"Preview render failed with exit code {exc.returncode}") from exc
    atomic_write_json(
        manifest,
        {
            "signature": signature,
            "review_only": True,
            "timeline_duration_seconds": timeline.duration_seconds,
            "music_track_id": music.id if music else None,
        },
    )
    return command
