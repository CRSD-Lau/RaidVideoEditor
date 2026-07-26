"""Audio role inference, sampling, and non-destructive microphone exclusion."""

from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path
from typing import Any

from raid_editor.config.models import AudioConfig
from raid_editor.ingestion.probe import AudioStream, MediaProbe, probe_media
from raid_editor.util.paths import atomic_write_json, atomic_write_text, ensure_directory


class AudioMappingError(ValueError):
    """Expected audio-role or FFmpeg mapping failure."""


ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "microphone": ("microphone", "mic", "voice input"),
    "game": ("game", "desktop", "world of warcraft", "wow"),
    "discord": ("discord", "chat", "voice chat"),
    "mixed": ("stream mix", "full mix", "mixed", "track 1"),
}


def infer_track_roles(streams: list[AudioStream]) -> dict[str, int | None]:
    roles: dict[str, int | None] = {role: None for role in ROLE_KEYWORDS}
    for stream in streams:
        title = (stream.title or "").casefold()
        for role, keywords in ROLE_KEYWORDS.items():
            if roles[role] is None and any(keyword in title for keyword in keywords):
                roles[role] = stream.index
    return roles


def validate_audio_mapping(audio: AudioConfig, probe: MediaProbe) -> list[str]:
    known = {stream.index for stream in probe.audio_streams}
    issues: list[str] = []
    assigned = {
        "microphone_track": audio.microphone_track,
        "game_track": audio.game_track,
        "discord_track": audio.discord_track,
        "mixed_track": audio.mixed_track,
    }
    for role, index in assigned.items():
        if index is not None and index not in known:
            issues.append(
                f"{role} references stream {index}, but available audio streams are {sorted(known)}"
            )
    if audio.remove_microphone and audio.microphone_track is None and len(known) > 1:
        issues.append(
            "Microphone removal is requested but microphone_track is not identified; "
            "review the generated audio samples before rendering"
        )
    if audio.remove_microphone and audio.microphone_track is None and audio.mixed_track is not None:
        issues.append(
            "The configured mixed track may contain the microphone; selective removal of only "
            "the local speaker is experimental and requires an explicit mixed-audio policy"
        )
    if not audio.retained_stream_indexes():
        issues.append("No retained audio streams are configured")
    return issues


def _run_ffmpeg(command: list[str]) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise AudioMappingError("ffmpeg is not installed or not available on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown FFmpeg error").strip()
        raise AudioMappingError(f"FFmpeg failed: {detail[-2000:]}") from exc


def create_audio_samples(
    recording: Path,
    probe: MediaProbe,
    output_dir: Path,
    *,
    sample_duration_seconds: float = 6.0,
) -> dict[int, list[Path]]:
    """Create bounded PCM samples from three positions in each audio stream."""

    ensure_directory(output_dir)
    duration = probe.duration_seconds
    sample_duration = min(sample_duration_seconds, max(1.0, duration))
    centers = (0.2, 0.5, 0.8)
    samples: dict[int, list[Path]] = {}
    for stream in probe.audio_streams:
        stream_samples: list[Path] = []
        for number, fraction in enumerate(centers, start=1):
            start = max(
                0.0, min(duration - sample_duration, duration * fraction - sample_duration / 2)
            )
            destination = output_dir / f"stream-{stream.index}-sample-{number}.wav"
            if not destination.is_file():
                _run_ffmpeg(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{start:.3f}",
                        "-i",
                        str(recording),
                        "-t",
                        f"{sample_duration:.3f}",
                        "-map",
                        f"0:{stream.index}",
                        "-vn",
                        "-c:a",
                        "pcm_s16le",
                        "-y",
                        str(destination),
                    ]
                )
            stream_samples.append(destination)
        samples[stream.index] = stream_samples
    return samples


def generate_audio_review_page(
    probe: MediaProbe,
    samples: dict[int, list[Path]],
    destination: Path,
) -> None:
    """Generate a self-contained role-selection page that downloads JSON."""

    stream_cards: list[str] = []
    for stream in probe.audio_streams:
        audio_controls = "\n".join(
            f'<audio controls preload="none" src="{html.escape(path.relative_to(destination.parent).as_posix())}"></audio>'
            for path in samples.get(stream.index, [])
        )
        stream_cards.append(
            f"""
            <section class="stream">
              <h2>Stream {stream.index}: {html.escape(stream.title or "Unlabelled")}</h2>
              <p>Audio ordinal {stream.audio_ordinal}; {html.escape(stream.codec)};
                 {stream.channels or "?"} channels; {html.escape(stream.channel_layout or "unknown layout")}</p>
              <div class="samples">{audio_controls}</div>
              <label>Role
                <select data-stream="{stream.index}">
                  <option value="unassigned">Unassigned</option>
                  <option value="microphone">My microphone</option>
                  <option value="game">Game</option>
                  <option value="discord">Discord</option>
                  <option value="mixed">Full/mixed track</option>
                  <option value="empty">Empty or duplicate</option>
                </select>
              </label>
            </section>
            """
        )
    initial = infer_track_roles(probe.audio_streams)
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Raid Editor Audio Track Review</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
    body {{ max-width: 960px; margin: 2rem auto; padding: 0 1rem; background: #12151a; color: #f2f4f7; }}
    .stream {{ background: #1c222b; border: 1px solid #394353; border-radius: .6rem; padding: 1rem; margin: 1rem 0; }}
    .samples {{ display: grid; gap: .6rem; margin: .8rem 0; }}
    audio {{ width: 100%; }}
    button {{ padding: .7rem 1rem; font-weight: 700; }}
    code {{ color: #ffcf70; }}
  </style>
</head>
<body>
  <h1>Audio track review</h1>
  <p>Listen at several points, assign each role once, then download
     <code>audio-map.json</code>. Stream numbers are absolute FFprobe indexes.</p>
  {"".join(stream_cards)}
  <button id="download">Download audio-map.json</button>
  <script>
    const inferred = {json.dumps(initial)};
    for (const select of document.querySelectorAll("select[data-stream]")) {{
      const stream = Number(select.dataset.stream);
      for (const [role, index] of Object.entries(inferred)) {{
        if (index === stream) select.value = role;
      }}
    }}
    document.querySelector("#download").addEventListener("click", () => {{
      const result = {{microphone_track: null, game_track: null, discord_track: null, mixed_track: null}};
      for (const select of document.querySelectorAll("select[data-stream]")) {{
        if (Object.hasOwn(result, `${{select.value}}_track`)) {{
          result[`${{select.value}}_track`] = Number(select.dataset.stream);
        }}
      }}
      const blob = new Blob([JSON.stringify(result, null, 2) + "\\n"], {{type: "application/json"}});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "audio-map.json";
      link.click();
      URL.revokeObjectURL(link.href);
    }});
  </script>
</body>
</html>
"""
    atomic_write_text(destination, document)


def create_mic_free_remux(
    recording: Path,
    retained_stream_indexes: list[int],
    microphone_stream_index: int | None,
    destination: Path,
) -> Path:
    """Stream-copy a Resolve input sidecar containing only approved audio streams."""

    if not retained_stream_indexes:
        raise AudioMappingError(
            "Cannot create microphone-free sidecar without retained audio tracks"
        )
    if microphone_stream_index is not None and microphone_stream_index in retained_stream_indexes:
        raise AudioMappingError("Refusing to retain the configured microphone stream")
    ensure_directory(destination.parent)
    manifest = destination.with_suffix(destination.suffix + ".json")
    desired: dict[str, Any] = {
        "source": str(recording.resolve()),
        "source_size_bytes": recording.stat().st_size,
        "source_modified_ns": recording.stat().st_mtime_ns,
        "retained_stream_indexes": retained_stream_indexes,
        "excluded_microphone_stream_index": microphone_stream_index,
    }
    if destination.is_file() and manifest.is_file():
        try:
            if json.loads(manifest.read_text(encoding="utf-8")) == desired:
                return destination
        except (OSError, json.JSONDecodeError):
            pass
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(recording),
        "-map",
        "0:v:0",
    ]
    for stream_index in retained_stream_indexes:
        command.extend(["-map", f"0:{stream_index}"])
    command.extend(["-map_metadata", "0", "-c", "copy", "-y", str(destination)])
    _run_ffmpeg(command)
    output_probe = probe_media(destination)
    if len(output_probe.audio_streams) != len(retained_stream_indexes):
        destination.unlink(missing_ok=True)
        raise AudioMappingError(
            "Microphone-free sidecar verification found an unexpected audio count"
        )
    atomic_write_json(manifest, desired)
    return destination
