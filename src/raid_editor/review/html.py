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
    max_preview_seconds: float | None = 10.0,
    lead_in_seconds: float = 0.0,
    lead_out_seconds: float = 0.0,
    recording_duration_seconds: float | None = None,
) -> dict[str, dict[str, Path]]:
    assets_dir = ensure_directory(destination_dir / "assets")
    result: dict[str, dict[str, Path]] = {}
    for pull in pulls:
        thumbnail = assets_dir / f"{pull.id}.jpg"
        preview_suffix = "-full" if max_preview_seconds is None else ""
        preview = assets_dir / f"{pull.id}{preview_suffix}.mp4"
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
            preview_start = max(0.0, pull.start_seconds - lead_in_seconds)
            preview_end = pull.end_seconds + lead_out_seconds
            if recording_duration_seconds is not None:
                preview_end = min(recording_duration_seconds, preview_end)
            full_duration = preview_end - preview_start
            preview_duration = (
                full_duration
                if max_preview_seconds is None
                else min(max_preview_seconds, full_duration)
            )
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{preview_start:.3f}",
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
                    "scale=960:-2,fps=30",
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
    jump_links: list[str] = []
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
        title = pull.title or pull.encounter or pull.id
        preview_is_full = preview is not None and preview.stem.endswith("-full")
        preview_label = "Full winning take" if preview_is_full else "Review sample"
        difficulty_class = (
            "unknown"
            if pull.difficulty == "UNKNOWN"
            else ("heroic" if pull.difficulty.endswith("H") else "normal")
        )
        jump_links.append(f'<a href="#{html.escape(pull.id)}">{html.escape(title)}</a>')
        rows.append(
            f"""
            <article class="pull" id="{html.escape(pull.id)}" data-id="{html.escape(pull.id)}">
              <h2>{html.escape(title)} <span class="difficulty {difficulty_class}">{html.escape(pull.difficulty)}</span></h2>
              <div class="media">
                <video controls playsinline preload="metadata" poster="{thumbnail_src}" src="{preview_src}"></video>
                <p class="clip-meta">{preview_label} · {pull.duration_seconds:.1f} second core cut</p>
              </div>
              <details class="fields">
                <summary>Adjust cut or notes</summary>
                <p><strong>ID:</strong> {html.escape(pull.id)} · <strong>Type:</strong> {html.escape(pull.type)}</p>
                <label><input class="include" type="checkbox" {"checked" if pull.include else ""}> Include</label>
                <label>Title <input class="title" value="{html.escape(title)}"></label>
                <label>Start seconds <input class="start" type="number" min="0" step="0.001" value="{pull.start_seconds:.3f}"></label>
                <label>End seconds <input class="end" type="number" min="0" step="0.001" value="{pull.end_seconds:.3f}"></label>
                <label>Notes <textarea class="notes">{html.escape(pull.notes)}</textarea></label>
                <p><strong>Encounter:</strong> {html.escape(pull.encounter or "Unknown")} ·
                   <strong>Result:</strong> {html.escape(pull.result)} ·
                   <strong>Confidence:</strong> {pull.confidence:.2f}</p>
                <p><strong>Evidence:</strong> {html.escape(", ".join(pull.evidence) or "None")}</p>
                <p><strong>Difficulty:</strong> {html.escape(pull.difficulty)} ·
                   <strong>Difficulty confidence:</strong> {html.escape(pull.difficulty_confidence)}<br>
                   <strong>Reason:</strong> {html.escape(pull.difficulty_reason or "No supported evidence yet")}<br>
                   <strong>Difficulty evidence:</strong> {html.escape(", ".join(pull.difficulty_evidence) or "None")}</p>
              </details>
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
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0 auto; max-width: 1500px; padding: 1.5rem; background: #11151b; color: #f6f3ea; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 1rem; background: #171d26ee; border-bottom: 1px solid #495466; }}
    button {{ padding: .7rem 1rem; font-weight: 700; }}
    nav {{ display: flex; gap: .45rem; overflow-x: auto; padding: .5rem 0; }}
    nav a {{ flex: 0 0 auto; padding: .45rem .65rem; color: #f6f3ea; background: #273244; border-radius: .35rem; text-decoration: none; }}
    .pull {{ margin: 1.2rem 0; padding: 1rem; scroll-margin-top: 12rem; background: #1c232d; border: 1px solid #3c4656; border-radius: .7rem; }}
    .pull h2 {{ margin-top: 0; }}
    .difficulty {{ display: inline-block; margin-left: .45rem; padding: .22rem .5rem; border-radius: 999px; font-size: .72em; vertical-align: .15em; background: #4b5565; }}
    .difficulty.heroic {{ background: #8a2f24; color: #fff1df; }}
    .difficulty.normal {{ background: #244d75; color: #eaf5ff; }}
    .difficulty.unknown {{ background: #715a1f; color: #fff4c7; }}
    .media video {{ display: block; width: 100%; max-height: 78vh; margin-bottom: .4rem; background: #080a0d; }}
    .clip-meta {{ color: #b9c8dc; }}
    details summary {{ cursor: pointer; padding: .6rem 0; font-weight: 700; }}
    .fields label {{ display: grid; gap: .25rem; margin: .55rem 0; }}
    input, textarea {{ padding: .5rem; background: #0e1218; color: inherit; border: 1px solid #596579; }}
    textarea {{ min-height: 4rem; }}
    @media (max-width: 760px) {{ body {{ padding: .6rem; }} header {{ position: static; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Winning takes review</h1>
    <p>Play each complete encounter below. Adjust complete pull windows only. Download the corrections and set
       <code>input.manual_pulls</code> to that file before rebuilding.</p>
    <nav>{"".join(jump_links)}</nav>
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
