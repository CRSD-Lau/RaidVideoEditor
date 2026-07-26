"""Static, local pull-review package with downloadable corrections."""

from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path

from raid_editor.models import PullCandidate
from raid_editor.util.paths import atomic_write_text, ensure_directory


class ReviewGenerationError(RuntimeError):
    """Expected FFmpeg review-asset failure."""


def _run(command: list[str]) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise ReviewGenerationError(f"Could not generate review media: {detail[-1500:]}") from exc


def generate_pull_media(
    recording: Path,
    pulls: list[PullCandidate],
    destination_dir: Path,
    retained_audio_stream_indexes: list[int],
    *,
    max_preview_seconds: float = 10.0,
) -> dict[str, dict[str, Path]]:
    assets_dir = ensure_directory(destination_dir / "assets")
    result: dict[str, dict[str, Path]] = {}
    for pull in pulls:
        thumbnail = assets_dir / f"{pull.id}.jpg"
        preview = assets_dir / f"{pull.id}.mp4"
        if not thumbnail.is_file():
            _run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{pull.start_seconds:.3f}",
                    "-i",
                    str(recording),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=480:-2",
                    "-q:v",
                    "3",
                    "-y",
                    str(thumbnail),
                ]
            )
        if not preview.is_file():
            preview_duration = min(max_preview_seconds, pull.end_seconds - pull.start_seconds)
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{pull.start_seconds:.3f}",
                "-i",
                str(recording),
                "-t",
                f"{preview_duration:.3f}",
                "-map",
                "0:v:0",
            ]
            if retained_audio_stream_indexes:
                command.extend(["-map", f"0:{retained_audio_stream_indexes[0]}"])
            else:
                command.append("-an")
            command.extend(
                [
                    "-vf",
                    "scale=480:-2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "28",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(preview),
                ]
            )
            _run(command)
        result[pull.id] = {"thumbnail": thumbnail, "preview": preview}
    return result


def generate_pull_review_page(
    pulls: list[PullCandidate],
    assets: dict[str, dict[str, Path]],
    destination: Path,
) -> None:
    rows: list[str] = []
    for pull in pulls:
        item = assets.get(pull.id, {})
        thumbnail = item.get("thumbnail")
        preview = item.get("preview")
        thumbnail_src = (
            html.escape(thumbnail.relative_to(destination.parent).as_posix()) if thumbnail else ""
        )
        preview_src = (
            html.escape(preview.relative_to(destination.parent).as_posix()) if preview else ""
        )
        rows.append(
            f"""
            <article class="pull" data-id="{html.escape(pull.id)}">
              <div class="media">
                <img src="{thumbnail_src}" alt="Thumbnail for {html.escape(pull.id)}">
                <video controls preload="none" src="{preview_src}"></video>
              </div>
              <div class="fields">
                <h2>{html.escape(pull.id)} · {html.escape(pull.type)}</h2>
                <label><input class="include" type="checkbox" {"checked" if pull.include else ""}> Include</label>
                <label>Title <input class="title" value="{html.escape(pull.title or pull.encounter or pull.id)}"></label>
                <label>Start seconds <input class="start" type="number" min="0" step="0.001" value="{pull.start_seconds:.3f}"></label>
                <label>End seconds <input class="end" type="number" min="0" step="0.001" value="{pull.end_seconds:.3f}"></label>
                <label>Notes <textarea class="notes">{html.escape(pull.notes)}</textarea></label>
                <p><strong>Encounter:</strong> {html.escape(pull.encounter or "Unknown")} ·
                   <strong>Result:</strong> {html.escape(pull.result)} ·
                   <strong>Confidence:</strong> {pull.confidence:.2f}</p>
                <p><strong>Evidence:</strong> {html.escape(", ".join(pull.evidence) or "None")}</p>
              </div>
            </article>
            """
        )
    serialized = json.dumps([pull.model_dump(mode="json") for pull in pulls]).replace("</", "<\\/")
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WoW Raid Pull Review</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; }}
    body {{ margin: 0 auto; max-width: 1180px; padding: 1.5rem; background: #11151b; color: #f6f3ea; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 1rem; background: #171d26ee; border-bottom: 1px solid #495466; }}
    button {{ padding: .7rem 1rem; font-weight: 700; }}
    .pull {{ display: grid; grid-template-columns: minmax(260px, 42%) 1fr; gap: 1.2rem; margin: 1.2rem 0; padding: 1rem; background: #1c232d; border: 1px solid #3c4656; border-radius: .7rem; }}
    .media img, .media video {{ display: block; width: 100%; margin-bottom: .7rem; background: #080a0d; }}
    .fields label {{ display: grid; gap: .25rem; margin: .55rem 0; }}
    input, textarea {{ padding: .5rem; background: #0e1218; color: inherit; border: 1px solid #596579; }}
    textarea {{ min-height: 4rem; }}
    @media (max-width: 760px) {{ .pull {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Pull review</h1>
    <p>Adjust complete pull windows only. Download the corrections and set
       <code>input.manual_pulls</code> to that file before rebuilding.</p>
    <button id="download">Download pull-overrides.json</button>
  </header>
  {"".join(rows)}
  <script>
    const original = {serialized};
    document.querySelector("#download").addEventListener("click", () => {{
      const byId = Object.fromEntries(original.map(pull => [pull.id, pull]));
      const pulls = [...document.querySelectorAll(".pull")].map(card => {{
        const pull = {{...byId[card.dataset.id]}};
        pull.include = card.querySelector(".include").checked;
        pull.title = card.querySelector(".title").value;
        pull.start_seconds = Number(card.querySelector(".start").value);
        pull.end_seconds = Number(card.querySelector(".end").value);
        pull.duration_seconds = pull.end_seconds - pull.start_seconds;
        pull.notes = card.querySelector(".notes").value;
        return pull;
      }});
      const blob = new Blob([JSON.stringify({{pulls}}, null, 2) + "\\n"], {{type: "application/json"}});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "pull-overrides.json";
      link.click();
      URL.revokeObjectURL(link.href);
    }});
  </script>
</body>
</html>
"""
    atomic_write_text(destination, document)
