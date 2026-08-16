"""Local highlight review media and downloadable approval overrides."""

from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path

from raid_editor.models import HighlightCandidate
from raid_editor.util.paths import (
    atomic_write_json,
    atomic_write_text,
    ensure_directory,
    quick_file_fingerprint,
)

_REVIEW_RENDER_SCHEMA_VERSION = 1


class HighlightReviewError(RuntimeError):
    """Expected highlight review media failure."""


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
    except FileNotFoundError as exc:
        raise HighlightReviewError("ffmpeg is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown FFmpeg error").strip()
        raise HighlightReviewError(f"Could not render highlight review: {detail[-2000:]}") from exc


def _audio_mix_filter(stream_indexes: list[int]) -> tuple[list[str], list[str]]:
    if not stream_indexes:
        return [], ["-an"]
    if len(stream_indexes) == 1:
        return [], ["-map", f"0:{stream_indexes[0]}"]
    inputs = []
    filters = []
    for number, stream_index in enumerate(stream_indexes):
        filters.append(
            f"[0:{stream_index}]aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{number}]"
        )
        inputs.append(f"[a{number}]")
    filters.append(
        "".join(inputs) + f"amix=inputs={len(inputs)}:duration=longest:normalize=0,"
        "alimiter=limit=0.95[aout]"
    )
    return ["-filter_complex", ";".join(filters)], ["-map", "[aout]"]


def generate_highlight_review_media(
    recording: Path,
    candidates: list[HighlightCandidate],
    destination: Path,
    *,
    audio_stream_indexes: list[int],
) -> dict[str, Path]:
    """Render full-context review clips with the configured highlight mix.

    Args:
        recording: Source media file.
        candidates: Candidate windows to render.
        destination: Highlight review root.
        audio_stream_indexes: Absolute game, Discord, and optionally microphone
            streams to retain and mix.

    Returns:
        Candidate IDs mapped to reusable local MP4 review files.

    Raises:
        HighlightReviewError: If FFmpeg is missing or a review render fails.
    """

    root = ensure_directory(destination)
    assets = ensure_directory(root / "assets")
    result = {candidate.id: assets / f"{candidate.id}.mp4" for candidate in candidates}
    signature = {
        "schema_version": _REVIEW_RENDER_SCHEMA_VERSION,
        "recording": quick_file_fingerprint(recording),
        "audio_stream_indexes": audio_stream_indexes,
        "candidates": [
            {
                "id": candidate.id,
                "start_seconds": candidate.start_seconds,
                "end_seconds": candidate.end_seconds,
            }
            for candidate in candidates
        ],
    }
    manifest_path = root / "render-manifest.json"
    try:
        current_signature = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current_signature = None
    if current_signature == signature and all(clip.is_file() for clip in result.values()):
        return result

    filter_args, audio_map = _audio_mix_filter(audio_stream_indexes)
    temporary_outputs: dict[Path, Path] = {}
    try:
        for candidate in candidates:
            clip = result[candidate.id]
            temporary = clip.with_name(f".{clip.stem}.rendering.mp4")
            temporary.unlink(missing_ok=True)
            temporary_outputs[clip] = temporary
            duration = candidate.end_seconds - candidate.start_seconds
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
                *filter_args,
                "-map",
                "0:v:0",
                *audio_map,
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
                "160k",
                "-movflags",
                "+faststart",
                "-y",
                str(temporary),
            ]
            _run(command)
            if not temporary.is_file():
                raise HighlightReviewError(
                    f"FFmpeg reported success but did not create review media: {temporary}"
                )
        for clip, temporary in temporary_outputs.items():
            temporary.replace(clip)
        atomic_write_json(manifest_path, signature)
    finally:
        for temporary in temporary_outputs.values():
            temporary.unlink(missing_ok=True)
    return result


def generate_highlight_review_page(
    candidates: list[HighlightCandidate],
    assets: dict[str, Path],
    destination: Path,
    *,
    includes_game: bool,
    includes_discord: bool,
    includes_microphone: bool,
) -> None:
    """Write the local interactive highlight approval page.

    Args:
        candidates: Candidate metadata shown to the reviewer.
        assets: Candidate IDs mapped to local review clips.
        destination: HTML file destination.
        includes_game: Whether the retained review mix contains game audio.
        includes_discord: Whether the retained review mix contains Discord.
        includes_microphone: Whether the retained review mix contains microphone audio.

    Raises:
        KeyError: If a candidate has no matching review asset.
        OSError: If the page cannot be written.
    """

    rows: list[str] = []
    links: list[str] = []
    for candidate in candidates:
        source = html.escape(assets[candidate.id].relative_to(destination.parent).as_posix())
        links.append(f'<a href="#{html.escape(candidate.id)}">{html.escape(candidate.title)}</a>')
        rows.append(
            f"""
            <article class="candidate" id="{html.escape(candidate.id)}" data-id="{html.escape(candidate.id)}">
              <h2>{html.escape(candidate.title)} <span>{html.escape(candidate.category)}</span></h2>
              <video controls playsinline preload="metadata" src="{source}"></video>
              <p><strong>Score:</strong> {candidate.score:.2f} · <strong>Peak:</strong> {candidate.peak_seconds:.1f}s ·
                 <strong>Encounter:</strong> {html.escape(candidate.encounter or "Between bosses")}</p>
              <p><strong>Signals:</strong> {html.escape(", ".join(candidate.signals))}</p>
              <label class="approve"><input class="include" type="checkbox" {"checked" if candidate.include else ""}> Approve for vertical export</label>
              <label>Category
                <select class="category">
                  {"".join(f'<option value="{kind}" {"selected" if kind == candidate.category else ""}>{kind}</option>' for kind in ("funny", "reaction", "intense", "movement", "clutch"))}
                </select>
              </label>
              <label>Title <input class="title" value="{html.escape(candidate.title)}"></label>
              <label>Start seconds <input class="start" type="number" min="0" step="0.001" value="{candidate.start_seconds:.3f}"></label>
              <label>End seconds <input class="end" type="number" min="0" step="0.001" value="{candidate.end_seconds:.3f}"></label>
              <label>Notes <textarea class="notes">{html.escape(candidate.notes)}</textarea></label>
            </article>
            """
        )
    serialized = json.dumps(
        [candidate.model_dump(mode="json") for candidate in candidates]
    ).replace("</", "<\\/")
    audio_mix = ", ".join(
        f"{label} {'included' if included else 'excluded'}"
        for label, included in (
            ("game", includes_game),
            ("Discord", includes_discord),
            ("microphone", includes_microphone),
        )
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pizza Warriors Highlight Review</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0 auto; max-width: 1500px; padding: 1.25rem; background: #10141a; color: #f7f2e8; }}
    header {{ position: sticky; top: 0; z-index: 2; padding: 1rem; background: #171d26f2; border-bottom: 1px solid #455166; }}
    nav {{ display: flex; gap: .45rem; overflow-x: auto; padding: .5rem 0; }}
    nav a {{ flex: 0 0 auto; padding: .45rem .65rem; color: inherit; background: #273244; border-radius: .35rem; text-decoration: none; }}
    button {{ min-height: 44px; padding: .7rem 1rem; font-weight: 700; }}
    .candidate {{ margin: 1.2rem 0; padding: 1rem; scroll-margin-top: 13rem; background: #1c232d; border: 1px solid #3c4656; border-radius: .7rem; }}
    .candidate h2 span {{ margin-left: .45rem; padding: .2rem .5rem; border-radius: 999px; background: #7a2f25; font-size: .7em; }}
    video {{ display: block; width: 100%; max-height: 75vh; background: #080a0d; }}
    label {{ display: grid; gap: .25rem; margin: .65rem 0; }}
    .approve {{ display: block; padding: .75rem; background: #26374c; font-weight: 700; }}
    input, select, textarea {{ min-height: 44px; padding: .45rem; background: #0e1218; color: inherit; border: 1px solid #596579; }}
    textarea {{ min-height: 5rem; }}
    .privacy {{ color: #ffd68a; }}
    @media (max-width: 760px) {{ body {{ padding: .5rem; }} header {{ position: static; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Highlight candidates</h1>
    <p>These are ranked suggestions, not approvals. Watch each full clip and only select moments worth sharing.</p>
    <p class="privacy">Review audio mix: {audio_mix}.</p>
    <nav>{"".join(links)}</nav>
    <button id="download">Download highlight-overrides.json</button>
  </header>
  {"".join(rows) if rows else "<p>No candidates met the configured threshold.</p>"}
  <script>
    const original = {serialized};
    document.querySelector("#download").addEventListener("click", () => {{
      const byId = Object.fromEntries(original.map(item => [item.id, item]));
      const highlights = [...document.querySelectorAll(".candidate")].map(card => {{
        const item = {{...byId[card.dataset.id]}};
        item.include = card.querySelector(".include").checked;
        item.category = card.querySelector(".category").value;
        item.title = card.querySelector(".title").value;
        item.start_seconds = Number(card.querySelector(".start").value);
        item.end_seconds = Number(card.querySelector(".end").value);
        item.notes = card.querySelector(".notes").value;
        return item;
      }});
      const blob = new Blob([JSON.stringify({{highlights}}, null, 2) + "\\n"], {{type: "application/json"}});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "highlight-overrides.json";
      link.click();
      URL.revokeObjectURL(link.href);
    }});
  </script>
</body>
</html>
"""
    atomic_write_text(destination, document)
