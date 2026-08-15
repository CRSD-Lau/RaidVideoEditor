"""FFmpeg rendering for review media and explicitly approved local masters."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from raid_editor.config.models import PresentationConfig, WatermarkConfig
from raid_editor.models import TimelineDocument
from raid_editor.music.library import MusicTrack
from raid_editor.timeline.export import timeline_digest
from raid_editor.util.paths import atomic_write_json, atomic_write_text, ensure_directory


class PreviewRenderError(RuntimeError):
    """Expected, user-actionable review-render failure."""


class FinalRenderError(RuntimeError):
    """Expected, user-actionable approved-master render failure."""


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
    watermark: WatermarkConfig | None = None,
    presentation: PresentationConfig | None = None,
) -> str:
    if not timeline.clips:
        raise PreviewRenderError("Timeline has no included clips")
    filters: list[str] = []
    video_labels: list[str] = []
    transition = max(0.0, transition_seconds)
    ui_scale = height / 720

    def ui(value: int) -> int:
        return max(1, round(value * ui_scale))

    intro_seconds = presentation.intro_seconds if presentation is not None else 0.0
    outro_seconds = presentation.outro_seconds if presentation is not None else 0.0
    has_cards = intro_seconds > 0 or outro_seconds > 0
    boss_kicker = presentation.boss_kicker if presentation is not None else "PIZZA WARRIORS"
    for clip_index, clip in enumerate(timeline.clips):
        duration = clip.source_out - clip.source_in
        fade = min(transition, duration / 4)
        label = f"v{clip_index}"
        show_difficulty = clip.type.startswith("boss")
        if clip.difficulty.endswith("H"):
            difficulty_badge = "HEROIC"
            difficulty_color = "0xA83A2F"
        elif clip.difficulty.endswith("N"):
            difficulty_badge = "NORMAL"
            difficulty_color = "0x2F6595"
        else:
            difficulty_badge = "UNCONFIRMED"
            difficulty_color = "0x596579"
        raid_size = clip.difficulty[:2] if clip.difficulty != "UNKNOWN" else None
        clip_kicker = (
            f"{boss_kicker} - {raid_size} PLAYER" if raid_size is not None else boss_kicker
        )
        difficulty_overlay = (
            f",drawbox=x={ui(592)}:y={ui(34)}:w={ui(116)}:h={ui(30)}:"
            f"color={difficulty_color}@0.94:t=fill:enable='lt(t,4)',"
            "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
            f"text='{difficulty_badge}':fontcolor=white:fontsize={ui(12)}:"
            f"x={ui(592)}+({ui(116)}-text_w)/2:y={ui(41)}:"
            "enable='lt(t,4)'"
            if show_difficulty
            else ""
        )
        video_filter = (
            f"[0:v:0]trim=start={clip.source_in:.6f}:end={clip.source_out:.6f},"
            "setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={fps},"
            f"drawbox=x={ui(24)}:y={ui(18)}:w={ui(704)}:h={ui(94)}:"
            "color=0x05070D@0.82:t=fill:"
            "enable='lt(t,4)',"
            f"drawbox=x={ui(24)}:y={ui(18)}:w={ui(5)}:h={ui(94)}:"
            "color=0xD79A2B@0.96:t=fill:"
            "enable='lt(t,4)',"
            f"drawbox=x={ui(29)}:y={ui(108)}:w={ui(699)}:h={ui(2)}:"
            "color=0xD79A2B@0.82:t=fill:"
            "enable='lt(t,4)',"
            "drawtext=fontfile='C\\:/Windows/Fonts/georgiab.ttf':"
            f"text='{_escape_drawtext(clip.label)}':"
            f"fontcolor=0xF2D28B:fontsize={ui(32)}:x={ui(48)}:y={ui(28)}:"
            "enable='lt(t,4)',"
            "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
            f"text='{_escape_drawtext(clip_kicker)}':"
            f"fontcolor=0x8ECDF2:fontsize={ui(14)}:x={ui(49)}:y={ui(72)}:"
            "enable='lt(t,4)'"
            f"{difficulty_overlay}"
        )
        if fade > 0:
            video_filter += (
                f",fade=t=in:st=0:d={fade:.3f},"
                f"fade=t=out:st={max(0.0, duration - fade):.6f}:d={fade:.3f}"
            )
        filters.append(f"{video_filter}[{label}]")
        video_labels.append(f"[{label}]")
    filters.append(
        "".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0,format=yuv420p[vbase]"
    )

    logo_labels: dict[str, str] = {}
    if watermark is not None:
        watermark_input = 1 + int(music is not None)
        logo_branches = ["watermark_raw"]
        if intro_seconds > 0:
            logo_branches.append("intro_logo_raw")
        if outro_seconds > 0:
            logo_branches.append("outro_logo_raw")
        if len(logo_branches) > 1:
            outputs = "".join(f"[{label}]" for label in logo_branches)
            filters.append(f"[{watermark_input}:v:0]split={len(logo_branches)}{outputs}")
            watermark_source = "[watermark_raw]"
        else:
            watermark_source = f"[{watermark_input}:v:0]"
        if intro_seconds > 0:
            logo_labels["intro"] = "intro_logo_raw"
        if outro_seconds > 0:
            logo_labels["outro"] = "outro_logo_raw"
        watermark_width = max(1, round(width * watermark.width_fraction))
        watermark_height = max(1, round(height * watermark.height_fraction))
        watermark_x = round(width * watermark.x_fraction)
        watermark_y = round(height * watermark.y_fraction)
        filters.append(
            f"{watermark_source}fps={fps},scale={watermark_width}:{watermark_height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={watermark_width}:{watermark_height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1[watermark]"
        )
        filters.append(
            f"[vbase][watermark]overlay=x={watermark_x}:y={watermark_y}:"
            "shortest=1:eof_action=endall[vprogram]"
        )
        program_video = "vprogram"
    else:
        program_video = "vbase"

    def add_card(
        card_name: str,
        duration: float,
        title: str,
        subtitle: str,
    ) -> str:
        background = f"{card_name}_background"
        canvas = f"{card_name}_canvas"
        output = f"v{card_name}"
        filters.append(
            f"color=c=0x04070D:s={width}x{height}:r={fps}:d={duration:.6f},"
            f"format=yuv420p[{background}]"
        )
        logo_source = logo_labels.get(card_name)
        if logo_source is not None:
            logo_size = max(1, round(height * 0.42))
            logo = f"{card_name}_logo"
            filters.append(
                f"[{logo_source}]fps={fps},trim=duration={duration:.6f},"
                "setpts=PTS-STARTPTS,"
                f"scale={logo_size}:{logo_size}:force_original_aspect_ratio=decrease,"
                f"pad={logo_size}:{logo_size}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
                f"setsar=1,format=rgba[{logo}]"
            )
            filters.append(
                f"[{background}][{logo}]overlay=x=(W-w)/2:y={round(height * 0.075)}:"
                f"shortest=1[{canvas}]"
            )
        else:
            filters.append(f"[{background}]null[{canvas}]")
        fade = min(0.5, duration / 4)
        filters.append(
            f"[{canvas}]"
            f"drawbox=x=(iw-{ui(620)})/2:y={ui(400)}:w={ui(620)}:h={ui(2)}:"
            "color=0xD79A2B@0.88:t=fill,"
            "drawtext=fontfile='C\\:/Windows/Fonts/georgiab.ttf':"
            f"text='{_escape_drawtext(title)}':fontcolor=0xF2D28B:fontsize={ui(44)}:"
            f"x=(w-text_w)/2:y={ui(416)},"
            "drawtext=fontfile='C\\:/Windows/Fonts/seguisb.ttf':"
            f"text='{_escape_drawtext(subtitle)}':fontcolor=0x8ECDF2:fontsize={ui(20)}:"
            f"x=(w-text_w)/2:y={ui(478)},"
            f"drawbox=x=(iw-{ui(420)})/2:y={ui(520)}:w={ui(420)}:h={ui(2)}:"
            "color=0xD79A2B@0.62:t=fill,"
            f"fade=t=in:st=0:d={fade:.3f},"
            f"fade=t=out:st={max(0.0, duration - fade):.6f}:d={fade:.3f},"
            f"format=yuv420p[{output}]"
        )
        return output

    video_sequence: list[str] = []
    if intro_seconds > 0 and presentation is not None:
        video_sequence.append(
            add_card(
                "intro",
                intro_seconds,
                presentation.intro_title,
                presentation.intro_subtitle,
            )
        )
    video_sequence.append(program_video)
    if outro_seconds > 0 and presentation is not None:
        video_sequence.append(
            add_card(
                "outro",
                outro_seconds,
                presentation.outro_title,
                presentation.outro_subtitle,
            )
        )
    if has_cards:
        filters.append(
            "".join(f"[{label}]" for label in video_sequence)
            + f"concat=n={len(video_sequence)}:v=1:a=0,format=yuv420p[vout]"
        )
    else:
        filters.append(f"[{program_video}]format=yuv420p[vout]")

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
    audio_output = "aprogram" if has_cards else "aout"
    if len(mix_inputs) == 1:
        filters.append(
            f"{mix_inputs[0]}alimiter=limit=0.95,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
            f"[{audio_output}]"
        )
    else:
        filters.append(
            "".join(mix_inputs)
            + f"amix=inputs={len(mix_inputs)}:duration=longest:dropout_transition=2:"
            "normalize=0,alimiter=limit=0.95,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
            f"[{audio_output}]"
        )
    if has_cards:
        audio_sequence: list[str] = []
        if intro_seconds > 0:
            filters.append(
                "anullsrc=r=48000:cl=stereo,"
                f"atrim=duration={intro_seconds:.6f},asetpts=PTS-STARTPTS,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
                "[aintro]"
            )
            audio_sequence.append("aintro")
        audio_sequence.append("aprogram")
        if outro_seconds > 0:
            filters.append(
                "anullsrc=r=48000:cl=stereo,"
                f"atrim=duration={outro_seconds:.6f},asetpts=PTS-STARTPTS,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
                "[aoutro]"
            )
            audio_sequence.append("aoutro")
        filters.append(
            "".join(f"[{label}]" for label in audio_sequence)
            + f"concat=n={len(audio_sequence)}:v=0:a=1[aout]"
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
    watermark: Path | None = None,
) -> list[str]:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", str(timeline.source)]
    if music is not None:
        command.extend(["-stream_loop", "-1", "-i", str(music.local_file)])
    if watermark is not None:
        if watermark.suffix.casefold() == ".gif":
            command.extend(["-stream_loop", "-1", "-ignore_loop", "1", "-i", str(watermark)])
        else:
            command.extend(["-loop", "1", "-i", str(watermark)])
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


def build_final_command(
    timeline: TimelineDocument,
    filter_script: Path,
    destination: Path,
    *,
    codec: str,
    constant_qp: int,
    preset: str,
    audio_bitrate: str,
    music: MusicTrack | None,
    hardware_encoding: bool,
    watermark: Path | None = None,
) -> list[str]:
    if codec.casefold() != "h264":
        raise FinalRenderError("The approved-master renderer currently supports only H.264")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", str(timeline.source)]
    if music is not None:
        command.extend(["-stream_loop", "-1", "-i", str(music.local_file)])
    if watermark is not None:
        if watermark.suffix.casefold() == ".gif":
            command.extend(["-stream_loop", "-1", "-ignore_loop", "1", "-i", str(watermark)])
        else:
            command.extend(["-loop", "1", "-i", str(watermark)])
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
                preset,
                "-tune",
                "hq",
                "-rc",
                "constqp",
                "-qp",
                str(constant_qp),
                "-profile:v",
                "high",
            ]
        )
    else:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "slow",
                "-crf",
                str(constant_qp),
                "-profile:v",
                "high",
            ]
        )
    command.extend(
        [
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-metadata",
            "title=Pizza Warriors - Icecrown Citadel",
            "-metadata",
            "artist=Pizza Warriors",
            "-metadata",
            "comment=Approved local master; generated by WoW Raid Video Editor",
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
    watermark: WatermarkConfig | None = None,
    presentation: PresentationConfig | None = None,
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
        watermark=watermark,
        presentation=presentation,
    )
    atomic_write_text(filter_script, graph)
    command = build_preview_command(
        timeline,
        filter_script,
        destination,
        bitrate=bitrate,
        music=music,
        hardware_encoding=hardware_encoding,
        watermark=watermark.image if watermark is not None else None,
    )
    signature = hashlib.sha256(
        (
            timeline_digest(timeline)
            + graph
            + json.dumps(command, ensure_ascii=False)
            + (music.sha256 if music else "")
            + (
                hashlib.sha256(watermark.image.read_bytes()).hexdigest()
                if watermark is not None
                else ""
            )
        ).encode()
    ).hexdigest()
    manifest = destination.with_suffix(".manifest.json")
    if destination.is_file() and manifest.is_file():
        try:
            if json.loads(manifest.read_text(encoding="utf-8")).get("signature") == signature:
                return command
        except (OSError, json.JSONDecodeError):
            pass
    intro_seconds = presentation.intro_seconds if presentation is not None else 0.0
    outro_seconds = presentation.outro_seconds if presentation is not None else 0.0
    output_duration = timeline.duration_seconds + intro_seconds + outro_seconds
    estimated_bytes = (_bitrate_bits_per_second(bitrate) + 192_000) * output_duration / 8
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
            "output_duration_seconds": output_duration,
            "presentation": presentation.model_dump(mode="json") if presentation else None,
            "music_track_id": music.id if music else None,
        },
    )
    return command


def render_final(
    timeline: TimelineDocument,
    destination: Path,
    *,
    resolution: str,
    fps: int,
    codec: str,
    constant_qp: int,
    preset: str,
    audio_bitrate: str,
    transition_seconds: float,
    music: MusicTrack | None = None,
    hardware_encoding: bool = True,
    watermark: WatermarkConfig | None = None,
    presentation: PresentationConfig | None = None,
    approved: bool = False,
    dry_run: bool = False,
) -> list[str]:
    if not approved and not dry_run:
        raise FinalRenderError(
            "Final rendering requires explicit approval; rerun with --approved after reviewing "
            "the complete preview"
        )
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
        watermark=watermark,
        presentation=presentation,
    )
    atomic_write_text(filter_script, graph)
    temporary = destination.with_name(f"{destination.stem}.rendering{destination.suffix}")
    command = build_final_command(
        timeline,
        filter_script,
        temporary,
        codec=codec,
        constant_qp=constant_qp,
        preset=preset,
        audio_bitrate=audio_bitrate,
        music=music,
        hardware_encoding=hardware_encoding,
        watermark=watermark.image if watermark is not None else None,
    )
    signature = hashlib.sha256(
        (
            timeline_digest(timeline)
            + graph
            + json.dumps(command, ensure_ascii=False)
            + (music.sha256 if music else "")
            + (
                hashlib.sha256(watermark.image.read_bytes()).hexdigest()
                if watermark is not None
                else ""
            )
        ).encode()
    ).hexdigest()
    manifest = destination.with_suffix(".manifest.json")
    if destination.is_file() and manifest.is_file():
        try:
            if json.loads(manifest.read_text(encoding="utf-8")).get("signature") == signature:
                return command
        except (OSError, json.JSONDecodeError):
            pass
        raise FinalRenderError(
            f"A different final master already exists at {destination}; move it before rerendering"
        )
    intro_seconds = presentation.intro_seconds if presentation is not None else 0.0
    outro_seconds = presentation.outro_seconds if presentation is not None else 0.0
    output_duration = timeline.duration_seconds + intro_seconds + outro_seconds
    estimated_bytes = (60_000_000 + _bitrate_bits_per_second(audio_bitrate)) * output_duration / 8
    free_bytes = shutil.disk_usage(destination.parent).free
    if free_bytes < estimated_bytes * 1.5:
        raise FinalRenderError(
            f"Insufficient free space: need about {estimated_bytes * 1.5 / 1e9:.2f} GB"
        )
    if dry_run:
        return command
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise FinalRenderError("ffmpeg is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise FinalRenderError(f"Final render failed with exit code {exc.returncode}") from exc
    temporary.replace(destination)
    atomic_write_json(
        manifest,
        {
            "signature": signature,
            "approved": True,
            "resolution": resolution,
            "fps": fps,
            "codec": codec,
            "constant_qp": constant_qp,
            "audio_bitrate": audio_bitrate,
            "timeline_duration_seconds": timeline.duration_seconds,
            "output_duration_seconds": output_duration,
            "presentation": presentation.model_dump(mode="json") if presentation else None,
            "music_track_id": music.id if music else None,
        },
    )
    return command
