"""Local highlight review media and downloadable approval overrides."""

from __future__ import annotations

import hashlib
import html
import json
import os
import subprocess
from pathlib import Path

from raid_editor.highlights.portrait import PortraitSource
from raid_editor.highlights.render import _filter_graph
from raid_editor.ingestion.probe import probe_media
from raid_editor.models import HighlightCandidate
from raid_editor.review.media import ReviewMediaFormat, review_encoding_args, review_media_type
from raid_editor.util.paths import (
    atomic_write_json,
    atomic_write_text,
    ensure_directory,
    quick_file_fingerprint,
)

_REVIEW_RENDER_SCHEMA_VERSION = 3


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
    media_format: ReviewMediaFormat = "webm",
    portrait_source: PortraitSource | None = None,
) -> dict[str, Path]:
    """Render full-context review clips with the configured highlight mix.

    Args:
        recording: Source media file.
        candidates: Candidate windows to render.
        destination: Highlight review root.
        audio_stream_indexes: Absolute game, Discord, and optionally microphone
            streams to retain and mix.
        media_format: Review-only container and codec policy. Does not affect exports.

    Returns:
        Candidate IDs mapped to reusable local review files.

    Raises:
        HighlightReviewError: If FFmpeg is missing or a review render fails.
    """

    if portrait_source is not None and recording.resolve() != (
        portrait_source.landscape_recording.resolve()
    ):
        raise HighlightReviewError("Portrait binding belongs to a different landscape audio source")
    root = ensure_directory(destination)
    assets = ensure_directory(root / "assets")
    encoding_args = [
        *review_encoding_args(media_format),
        "-metadata",
        "author=Neil Mitchell",
        "-metadata",
        "last_modified_by=Neil Mitchell",
        *(["-movflags", "+faststart+use_metadata_tags"] if media_format == "mp4" else []),
    ]
    result = {candidate.id: assets / f"{candidate.id}.{media_format}" for candidate in candidates}
    signature = {
        "schema_version": _REVIEW_RENDER_SCHEMA_VERSION,
        "media_format": media_format,
        "encoding_args": encoding_args,
        "recording": quick_file_fingerprint(recording),
        "audio_stream_indexes": audio_stream_indexes,
        "portrait_source": portrait_source.signature if portrait_source else None,
        "portrait_offset_seconds": portrait_source.offset_seconds if portrait_source else None,
        "composition": "preserve-native-no-overlays-v1" if portrait_source else "landscape",
        "candidates": [
            {
                "id": candidate.id,
                "start_seconds": candidate.start_seconds,
                "end_seconds": candidate.end_seconds,
            }
            for candidate in candidates
        ],
    }
    if portrait_source is not None:
        for candidate in candidates:
            portrait_source.validate_window(candidate.start_seconds, candidate.end_seconds)
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
            temporary = clip.with_name(f".{clip.stem}.rendering{clip.suffix}")
            temporary.unlink(missing_ok=True)
            temporary_outputs[clip] = temporary
            duration = candidate.end_seconds - candidate.start_seconds
            input_args = (
                portrait_source.input_args(candidate.start_seconds, candidate.end_seconds)
                if portrait_source
                else ["-ss", f"{candidate.start_seconds:.3f}", "-i", str(recording)]
            )
            if portrait_source is not None:
                # Same picture/audio composition as export, at a smaller review size.
                graph = _filter_graph(
                    candidate,
                    width=540,
                    height=960,
                    audio_stream_indexes=audio_stream_indexes,
                    native_portrait=True,
                ).replace("format=yuv420p[vout]", "fps=30,format=yuv420p[vout]")
                media_args = ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]"]
            else:
                media_args = [
                    *filter_args,
                    "-map",
                    "0:v:0",
                    *audio_map,
                    "-vf",
                    "scale=960:-2,fps=30",
                ]
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                *input_args,
                "-t",
                f"{duration:.3f}",
                *media_args,
                *encoding_args,
                "-y",
                str(temporary),
            ]
            _run(command)
            if not temporary.is_file():
                raise HighlightReviewError(
                    f"FFmpeg reported success but did not create review media: {temporary}"
                )
            if portrait_source is not None:
                probe = probe_media(temporary)
                if (
                    len(probe.video_streams) != 1
                    or len(probe.audio_streams) != 1
                    or probe.video_streams[0].width != 540
                    or probe.video_streams[0].height != 960
                    or abs(probe.duration_seconds - duration) > 0.20
                ):
                    raise HighlightReviewError("Native portrait review failed geometry/duration QA")
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
    speech_status: dict[str, object] | None = None,
    intelligence_status: dict[str, object] | None = None,
    source_reference: str | None = None,
    presentation_reference: str | None = None,
    native_portrait: bool = False,
) -> None:
    """Write the local interactive highlight approval page.

    Args:
        candidates: Candidate metadata shown to the reviewer.
        assets: Candidate IDs mapped to local review clips.
        destination: HTML file destination.
        includes_game: Whether the retained review mix contains game audio.
        includes_discord: Whether the retained review mix contains Discord.
        includes_microphone: Whether the retained review mix contains microphone audio.
        speech_status: Optional transcript-free spoken-command coverage summary.
        intelligence_status: Optional semantic coverage and degraded-mode summary.
        source_reference: Stable recording/candidate reference for feedback deduplication.

    Raises:
        KeyError: If a candidate has no matching review asset.
        OSError: If the page cannot be written.
    """

    rows: list[str] = []
    links: list[str] = []
    for candidate in candidates:
        source = html.escape(assets[candidate.id].relative_to(destination.parent).as_posix())
        media_type = review_media_type(assets[candidate.id].suffix.lower().lstrip("."))
        links.append(f'<a href="#{html.escape(candidate.id)}">{html.escape(candidate.title)}</a>')
        rating = "keep" if candidate.include else candidate.review_rating
        factual_label = {
            "heuristic": "Gameplay signal",
            "speech": "Spoken clip request",
            "semantic": "Context-based suggestion",
            "manual": "Manual selection",
        }[candidate.origin]
        rationale = candidate.rationale or (
            "Activity or a marked event was detected here. Watch the clip to decide whether "
            "it contains a distinctive moment and a complete payoff."
        )
        timing = " · ".join(
            f"{label}: {seconds:.3f}s" if seconds is not None else f"{label}: not identified"
            for label, seconds in (
                ("Setup", candidate.setup_seconds),
                ("Payoff", candidate.payoff_seconds),
            )
        )
        confidence = (
            f"{candidate.confidence:.2f} (model self-assessment; uncalibrated)"
            if candidate.confidence is not None
            else "not assessed"
        )
        rows.append(
            f"""
            <article class="candidate" id="{html.escape(candidate.id)}" data-id="{html.escape(candidate.id)}">
              <h2>{html.escape(candidate.title)} <span>{factual_label}</span></h2>
              <video controls playsinline preload="metadata">
                <source src="{source}" type="{media_type}">
                Your browser cannot play this review clip. <a href="{source}">Open the full clip</a>.
              </video>
              <p><a class="open-clip" href="{source}" target="_blank" rel="noopener">Open full review clip</a></p>
              <p class="playback-error privacy" role="alert" hidden></p>
              <p><strong>Advisory ranking:</strong> {candidate.score:.2f} (not a probability) · <strong>Peak:</strong> {candidate.peak_seconds:.1f}s ·
                 <strong>Encounter:</strong> {html.escape(candidate.encounter or "Between bosses")}</p>
              <p><strong>Why review this:</strong> {html.escape(rationale)}</p>
              <p><strong>Source timing:</strong> {html.escape(timing)} · <strong>Evidence confidence:</strong> {confidence}</p>
              <p><strong>Signals:</strong> {html.escape(", ".join(candidate.signals))}</p>
              <label>Quick review rating
                <select class="review-rating">
                  {"".join(f'<option value="{value}" {"selected" if value == rating else ""}>{label}</option>' for value, label in (("unreviewed", "Not reviewed"), ("keep", "Worth keeping"), ("maybe", "Maybe"), ("reject", "Reject")))}
                </select>
              </label>
              <label>Rejection reason
                <select class="rejection-reason">
                  {"".join(f'<option value="{value}" {"selected" if value == candidate.rejection_reason else ""}>{label}</option>' for value, label in (("unset", "No reason recorded"), ("ordinary_kill", "Ordinary boss finish"), ("nothing_happens", "Nothing worth sharing"), ("missing_context", "Missing setup or context"), ("wrong_timing", "Wrong start or end"), ("duplicate", "Duplicate moment"), ("not_my_style", "Not my style")))}
                </select>
              </label>
              <label class="approve"><input class="include" type="checkbox" {"checked" if candidate.include else ""}> Approve for vertical export</label>
              <label>Proposed category (editable)
                <select class="category">
                  {"".join(f'<option value="{kind}" {"selected" if kind == candidate.category else ""}>{kind}</option>' for kind in ("funny", "reaction", "intense", "movement", "clutch"))}
                </select>
              </label>
              <label>Title <input class="title" value="{html.escape(candidate.title)}"></label>
              <label>Start seconds <input class="start" type="number" min="0" step="0.001" value="{candidate.start_seconds:.3f}"></label>
              <label>End seconds <input class="end" type="number" min="0" step="0.001" value="{candidate.end_seconds:.3f}"></label>
              <label>Peak seconds <input class="peak" type="number" min="0" step="0.001" value="{candidate.peak_seconds:.3f}"></label>
              <p class="window-error privacy" role="alert" hidden></p>
              <p>Keep the peak inside your edited window. Machine setup/payoff markers outside that window will be cleared in the download.</p>
              <label>Notes <textarea class="notes">{html.escape(candidate.notes)}</textarea></label>
            </article>
            """
        )
    serialized = json.dumps(
        [candidate.model_dump(mode="json") for candidate in candidates]
    ).replace("</", "<\\/")
    serialized_source_reference = json.dumps(
        source_reference or str(destination.resolve())
    ).replace("</", "<\\/")
    serialized_presentation_reference = json.dumps(presentation_reference)
    picture_description = (
        "Native portrait recording with aligned landscape audio. Times below use the landscape recording clock."
        if native_portrait
        else "Landscape recording. Times below use its recording clock."
    )
    audio_mix = ", ".join(
        f"{label} {'included' if included else 'excluded'}"
        for label, included in (
            ("game", includes_game),
            ("Discord", includes_discord),
            ("microphone", includes_microphone),
        )
    )
    spoken_coverage = "disabled"
    spoken_detail = ""
    if speech_status is not None:
        spoken_coverage = str(speech_status.get("status", "unknown"))
        raw_events = speech_status.get("events", [])
        match_count = len(raw_events) if isinstance(raw_events, list) else 0
        spoken_detail = f"; exact 'clip it' matches: {match_count}"
        diagnostics = speech_status.get("diagnostics", [])
        if isinstance(diagnostics, list) and diagnostics:
            spoken_detail += f"; note: {str(diagnostics[0])}"
    intelligence_detail = "not run; these suggestions have no semantic assessment"
    if intelligence_status is not None:
        intelligence_detail = str(intelligence_status.get("status", "unknown"))
        for key in ("detail", "reason", "coverage_summary"):
            value = intelligence_status.get(key)
            if isinstance(value, str) and value:
                intelligence_detail += f"; {value}"
        diagnostics = intelligence_status.get("diagnostics", [])
        if isinstance(diagnostics, list) and diagnostics:
            intelligence_detail += "; " + "; ".join(str(value) for value in diagnostics[:3])
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="author" content="Neil Mitchell">
  <meta name="last-modified-by" content="Neil Mitchell">
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
    .open-clip {{ color: #a9d9ff; }}
    @media (max-width: 760px) {{ body {{ padding: .5rem; }} header {{ position: static; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Highlight candidates</h1>
    <p>These are ranked suggestions, not approvals. Watch each full clip and only select moments worth sharing.</p>
    <p>Zero strong highlights is a valid result. Ratings record feedback; only the separate approval checkbox selects an export. Unreviewed clips are not recorded as rejections.</p>
    <p class="privacy">Review audio mix: {audio_mix}.</p>
    <p class="privacy">{picture_description}</p>
    <p class="privacy">Spoken command coverage: {html.escape(spoken_coverage + spoken_detail)}. No full transcript is retained.</p>
    <p class="privacy" id="intelligence-status">Editorial intelligence: {html.escape(intelligence_detail)}.</p>
    <nav>{"".join(links)}</nav>
    <button id="download">Download highlight-overrides.json</button>
  </header>
  {"".join(rows) if rows else "<p>No candidates met the configured threshold.</p>"}
  <script>
    const original = {serialized};
    const sourceReference = {serialized_source_reference};
    const presentationReference = {serialized_presentation_reference};
    document.querySelectorAll(".candidate").forEach(card => {{
      const include = card.querySelector(".include");
      const rating = card.querySelector(".review-rating");
      const reason = card.querySelector(".rejection-reason");
      include.addEventListener("change", () => {{
        if (include.checked) {{ rating.value = "keep"; reason.value = "unset"; }}
        else if (rating.value === "keep") rating.value = "unreviewed";
      }});
      rating.addEventListener("change", () => {{
        if (rating.value !== "keep") include.checked = false;
        if (rating.value !== "reject") reason.value = "unset";
      }});
      reason.addEventListener("change", () => {{
        if (reason.value !== "unset") {{ rating.value = "reject"; include.checked = false; }}
      }});
    }});
    document.querySelectorAll(".candidate video").forEach(video => {{
      const reportError = () => {{
        const message = video.closest(".candidate").querySelector(".playback-error");
        const code = video.error ? ` (media error ${{video.error.code}})` : "";
        message.textContent = `The review player could not load or decode this clip${{code}}. Use Open full review clip to try it separately. Your selections have not changed.`;
        message.hidden = false;
      }};
      video.addEventListener("error", reportError);
      video.querySelector("source").addEventListener("error", reportError);
      video.addEventListener("loadeddata", () => {{
        video.closest(".candidate").querySelector(".playback-error").hidden = true;
      }});
      if (video.error) reportError();
    }});
    document.querySelector("#download").addEventListener("click", () => {{
      const byId = Object.fromEntries(original.map(item => [item.id, item]));
      const cards = [...document.querySelectorAll(".candidate")];
      let invalidWindow = false;
      cards.forEach(card => {{
        const fields = [".start", ".end", ".peak"].map(name => card.querySelector(name).value);
        const [start, end, peak] = fields.map(Number);
        const valid = fields.every(value => value.trim() !== "") &&
          [start, end, peak].every(Number.isFinite) && start >= 0 && end > start && peak >= start && peak <= end;
        const error = card.querySelector(".window-error");
        error.hidden = valid;
        error.textContent = valid ? "" : "Download paused: use valid start/end times and place Peak seconds inside the edited window. Your ratings and approval checkbox have not changed.";
        if (!valid) invalidWindow = true;
      }});
      if (invalidWindow) return;
      const highlights = cards.map(card => {{
        const item = {{...byId[card.dataset.id]}};
        item.review_rating = card.querySelector(".review-rating").value;
        item.rejection_reason = card.querySelector(".rejection-reason").value;
        item.include = card.querySelector(".include").checked && item.review_rating === "keep" && item.rejection_reason === "unset";
        item.category = card.querySelector(".category").value;
        item.title = card.querySelector(".title").value;
        item.start_seconds = Number(card.querySelector(".start").value);
        item.end_seconds = Number(card.querySelector(".end").value);
        item.peak_seconds = Number(card.querySelector(".peak").value);
        ["setup_seconds", "payoff_seconds"].forEach(anchor => {{
          if (item[anchor] != null && (item[anchor] < item.start_seconds || item[anchor] > item.end_seconds)) item[anchor] = null;
        }});
        item.notes = card.querySelector(".notes").value;
        return item;
      }});
      const blob = new Blob([JSON.stringify({{schema_version: 1, author: "Neil Mitchell", last_modified_by: "Neil Mitchell", source_reference: sourceReference, presentation_reference: presentationReference, highlights}}, null, 2) + "\\n"], {{type: "application/json"}});
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


def generate_highlight_comparison_page(
    variants: dict[str, tuple[list[HighlightCandidate], dict[str, Path]]],
    destination: Path,
    *,
    source_reference: str,
    seed: int = 0,
    includes_game: bool,
    includes_discord: bool,
    includes_microphone: bool,
    presentation_reference: str | None = None,
) -> dict[str, object]:
    """Write a blinded, stable-order comparison; ratings never select exports.

    The caller must verify that every variant belongs to the same source media.
    Equal source windows (to 1 ms) appear once with all variant memberships in
    the returned/exported manifest. Names, scores and rationales are hidden from
    the visible review so the reviewer judges the shared clip. The manifest is
    available in page source; this is editorial blinding, not access control.
    """
    if not source_reference.strip():
        raise ValueError("A stable source reference is required for comparison")
    grouped: dict[str, dict[str, object]] = {}
    candidates_by_id: dict[str, HighlightCandidate] = {}
    assets_by_id: dict[str, Path] = {}
    for variant, (candidates, assets) in variants.items():
        if len({candidate.id for candidate in candidates}) != len(candidates):
            raise ValueError(f"Duplicate candidate IDs in comparison variant: {variant}")
        for candidate in candidates:
            identity = hashlib.sha256(
                json.dumps(
                    [
                        source_reference,
                        round(candidate.start_seconds, 3),
                        round(candidate.end_seconds, 3),
                    ]
                ).encode("utf-8")
            ).hexdigest()[:20]
            identifier = f"comparison-{identity}"
            member = {"variant": variant, "candidate_id": candidate.id}
            if identifier in grouped:
                members = grouped[identifier]["members"]
                assert isinstance(members, list)
                members.append(member)
                continue
            grouped[identifier] = {
                "id": identifier,
                "start_seconds": candidate.start_seconds,
                "end_seconds": candidate.end_seconds,
                "members": [member],
            }
            assets_by_id[identifier] = assets[candidate.id]
            candidates_by_id[identifier] = candidate.model_copy(
                update={
                    "id": identifier,
                    "include": False,
                    "review_rating": "unreviewed",
                    "rejection_reason": "unset",
                    "title": "Raid Reaction Moment",
                    "notes": "",
                    "rationale": "",
                }
            )
    identifiers = sorted(
        grouped, key=lambda item: hashlib.sha256(f"{seed}:{item}".encode()).hexdigest()
    )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "author": "Neil Mitchell",
        "last_modified_by": "Neil Mitchell",
        "kind": "blind_highlight_comparison",
        "source_reference": source_reference,
        "presentation_reference": presentation_reference,
        "seed": seed,
        "unique_clip_count": len(identifiers),
        "variant_candidate_counts": {
            name: len(candidates) for name, (candidates, _) in variants.items()
        },
        "clips": [grouped[identifier] for identifier in identifiers],
        "limitation": (
            "Candidate-only comparison cannot measure missed moments; review independently "
            "identified positives and a separate random source sample for recall."
        ),
        "timing_definition": "Elapsed page-open time, including idle time and interruptions.",
    }
    rows = []
    for number, identifier in enumerate(identifiers, 1):
        asset = assets_by_id[identifier]
        source = html.escape(Path(os.path.relpath(asset, destination.parent)).as_posix())
        media_type = review_media_type(asset.suffix.lower().lstrip("."))
        rows.append(f"""
<article class="comparison-clip" data-id="{identifier}">
  <h2>Review clip {number:02d}</h2>
  <video controls playsinline preload="metadata"><source src="{source}" type="{media_type}"></video>
  <p><a href="{source}" target="_blank" rel="noopener">Open full review clip</a></p>
  <p class="playback-error" hidden role="alert">Could not play this clip. Try Open full review clip; ratings have not changed.</p>
  <label>Rating <select class="review-rating"><option value="unreviewed">Not reviewed</option><option value="keep">Worth keeping</option><option value="maybe">Maybe</option><option value="reject">Reject</option></select></label>
  <label>Rejection reason <select class="rejection-reason"><option value="unset">No reason recorded</option><option value="ordinary_kill">Ordinary boss finish</option><option value="nothing_happens">Nothing worth sharing</option><option value="missing_context">Missing setup or context</option><option value="wrong_timing">Wrong start or end</option><option value="duplicate">Duplicate moment</option><option value="not_my_style">Not my style</option></select></label>
</article>""")
    payload = json.dumps(
        {
            "manifest": manifest,
            "highlights": [
                candidates_by_id[identifier].model_dump(mode="json") for identifier in identifiers
            ],
        }
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
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="author" content="Neil Mitchell"><meta name="last-modified-by" content="Neil Mitchell">
<title>Blind Highlight Comparison</title><style>
:root {{color-scheme:dark;font-family:system-ui,sans-serif}} body {{max-width:1100px;margin:auto;padding:1rem;background:#10141a;color:#f7f2e8}}
article {{margin:1.5rem 0;padding:1rem;background:#1c232d;border:1px solid #596579;border-radius:.7rem}}
video {{width:100%;max-height:75vh;background:#080a0d}} label {{display:block;margin:.7rem 0}}
select,button {{min-height:44px;padding:.6rem}} a {{color:#a9d9ff}}
</style></head><body>
<h1>Blind highlight comparison</h1><p>Judge each complete moment before seeing which approach found it. Equal source windows appear once.</p>
<p>Ratings record feedback only. This page cannot approve or select an export. Zero strong moments is a valid result.</p>
<p>Review audio mix: {audio_mix}.</p><p>{html.escape(str(manifest["limitation"]))}</p>
<button id="download">Download comparison ratings</button>
{"".join(rows) if rows else "<p>No candidates are available to compare.</p>"}
<script>
const comparison = {payload};
const startedAt = Date.now();
document.querySelectorAll('.comparison-clip').forEach(card => {{
  const rating = card.querySelector('.review-rating');
  const reason = card.querySelector('.rejection-reason');
  rating.addEventListener('change', () => {{if (rating.value !== 'reject') reason.value = 'unset';}});
  reason.addEventListener('change', () => {{if (reason.value !== 'unset') rating.value = 'reject';}});
  const video = card.querySelector('video');
  const reportError = () => {{card.querySelector('.playback-error').hidden = false;}};
  video.addEventListener('error', reportError);
  video.querySelector('source').addEventListener('error', reportError);
}});
document.querySelector('#download').addEventListener('click', () => {{
  const byId = Object.fromEntries(comparison.highlights.map(item => [item.id, item]));
  const highlights = [...document.querySelectorAll('.comparison-clip')].map(card => ({{
    ...byId[card.dataset.id], include: false,
    review_rating: card.querySelector('.review-rating').value,
    rejection_reason: card.querySelector('.rejection-reason').value
  }}));
  const ratings = {{...comparison.manifest, elapsed_review_seconds: Math.round((Date.now()-startedAt)/1000), highlights}};
  const blob = new Blob([JSON.stringify(ratings, null, 2) + '\\n'], {{type:'application/json'}});
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob);
  link.download = 'highlight-comparison-ratings.json'; link.click(); URL.revokeObjectURL(link.href);
}});
</script></body></html>"""
    atomic_write_text(destination, document)
    return manifest
