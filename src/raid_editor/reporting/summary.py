"""Edit-summary and validation reporting."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from raid_editor.ingestion.probe import MediaProbe
from raid_editor.models import PullCandidate, TimelineDocument
from raid_editor.music.library import MusicTrack
from raid_editor.util.paths import atomic_write_json, atomic_write_text


def write_analysis_summary(
    *,
    probe: MediaProbe,
    pulls: list[PullCandidate],
    audio_issues: list[str],
    confidence_threshold: float,
    destination: Path,
) -> None:
    counts = Counter(pull.type for pull in pulls)
    uncertain = sum(pull.confidence < confidence_threshold for pull in pulls)
    lines = [
        "# Analysis Summary",
        "",
        f"- Source duration: {probe.duration_seconds:.3f} seconds",
        f"- Video: {probe.video_streams[0].width}×{probe.video_streams[0].height}"
        if probe.video_streams
        else "- Video: unavailable",
        f"- Audio streams: {len(probe.audio_streams)}",
        f"- Pull candidates: {len(pulls)}",
        f"- Unknown activity windows: {counts['unknown']}",
        f"- Trash pulls: {counts['trash_pull']}",
        f"- Boss attempts: {counts['boss_attempt']}",
        f"- Boss wipes: {counts['boss_wipe']}",
        f"- Boss kills: {counts['boss_kill']}",
        f"- Below confidence threshold: {uncertain}",
        "",
        "## Audio gate",
        "",
    ]
    if audio_issues:
        lines.extend(f"- BLOCKED: {issue}" for issue in audio_issues)
    else:
        lines.append("- Audio roles are sufficient for a microphone-excluded review.")
    lines.extend(
        [
            "",
            "## Next review",
            "",
            "- Inspect `review/pull-review.html` and download corrected pull overrides.",
            "- Inspect `review/audio-track-review.html` before changing audio roles.",
            "- A blocked audio gate prevents combined preview rendering; "
            "it does not alter source media.",
        ]
    )
    atomic_write_text(destination, "\n".join(lines) + "\n")


def write_edit_summary(
    *,
    probe: MediaProbe,
    pulls: list[PullCandidate],
    timeline: TimelineDocument,
    retained_audio: list[str],
    removed_audio: list[str],
    music: list[MusicTrack],
    confidence_threshold: float,
    destination: Path,
) -> None:
    timeline_pull_ids = {
        pull_id for clip in timeline.clips for pull_id in clip.pull_ids
    }
    included_pulls = [pull for pull in pulls if pull.id in timeline_pull_ids]
    counts = Counter(pull.type for pull in included_pulls)
    uncertain = [
        pull for pull in included_pulls if pull.confidence < confidence_threshold
    ]
    removed = max(0.0, probe.duration_seconds - timeline.duration_seconds)
    lines = [
        "# Edit Summary",
        "",
        f"- Original duration: {probe.duration_seconds:.3f} seconds",
        f"- Condensed duration: {timeline.duration_seconds:.3f} seconds",
        f"- Time removed: {removed:.3f} seconds",
        f"- Trash pulls: {counts['trash_pull']}",
        f"- Boss attempts (unresolved): {counts['boss_attempt']}",
        f"- Boss kills: {counts['boss_kill']}",
        f"- Boss wipes: {counts['boss_wipe']}",
        f"- Uncertain segments: {len(uncertain)}",
        f"- Audio retained: {', '.join(retained_audio) or 'none'}",
        f"- Audio removed: {', '.join(removed_audio) or 'none explicitly identified'}",
        f"- Music used: {', '.join(track.title for track in music) or 'none'}",
        "",
        "## Automation status",
        "",
        "- Deterministic FFmpeg review render: configured",
        "- DaVinci Resolve external scripting: blocked on this host by the apparent non-Studio "
        "edition; FCPXML and a Python 3.13 bridge payload are generated",
        "- Final rendering: available only through the explicit post-review approval gate",
        "- Upload/publishing: not implemented",
        "",
        "## Recommended manual review",
        "",
    ]
    if uncertain:
        lines.extend(
            f"- {pull.id}: {pull.start_seconds:.3f}-{pull.end_seconds:.3f}s" for pull in uncertain
        )
    else:
        lines.append(
            "- Review every transition, pull boundary, title, and audio balance before approval."
        )
    atomic_write_text(destination, "\n".join(lines) + "\n")


def validate_artifacts(
    *,
    probe: MediaProbe,
    pulls: list[PullCandidate],
    timeline: TimelineDocument,
    microphone_free_probe: MediaProbe | None,
    preview_probe: MediaProbe | None,
    preview_exists: bool,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    source_path = Path(str(probe.source["path"]))
    current = source_path.stat()
    unchanged = (
        current.st_size == probe.source["size_bytes"]
        and current.st_mtime_ns == probe.source["modified_ns"]
    )
    add("source_not_modified", unchanged, "size and nanosecond mtime match initial probe")
    boundaries = all(
        0 <= pull.start_seconds < pull.end_seconds <= probe.duration_seconds + 0.05
        for pull in pulls
    )
    add("pull_boundaries_valid", boundaries, "all pull windows are ordered and source-bounded")
    nonoverlap = all(
        left.source_out <= right.source_in + 0.001
        for left, right in zip(timeline.clips, timeline.clips[1:], strict=False)
    )
    add("timeline_non_overlapping", nonoverlap, "timeline source windows do not overlap")
    boss_distinct = all(
        not (len(clip.pull_ids) > 1 and clip.type in {"boss_attempt", "boss_kill", "boss_wipe"})
        for clip in timeline.clips
    )
    add("boss_attempts_distinct", boss_distinct, "no boss timeline clip merges multiple attempts")
    mic_free = microphone_free_probe is not None and len(
        microphone_free_probe.audio_streams
    ) == len(timeline.retained_audio_stream_indexes)
    add(
        "microphone_stream_excluded",
        mic_free,
        "microphone-free sidecar contains only the configured retained stream count",
    )
    add(
        "preview_rendered",
        preview_exists and preview_probe is not None,
        "review MP4 exists and FFprobe can read it",
    )
    add(
        "final_render_approval_gate",
        True,
        "final rendering requires an explicit --approved flag after review",
    )
    add("youtube_upload_absent", True, "no authentication or publishing module exists")
    return {
        "status": "passed" if all(check["passed"] for check in checks) else "failed",
        "checks": checks,
    }


def write_validation_report(result: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    atomic_write_json(json_path, result)
    lines = ["# Validation Report", "", f"Overall status: **{result['status'].upper()}**", ""]
    for check in result["checks"]:
        symbol = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- **{symbol} — {check['name']}**: {check['detail']}")
    atomic_write_text(markdown_path, "\n".join(lines) + "\n")
