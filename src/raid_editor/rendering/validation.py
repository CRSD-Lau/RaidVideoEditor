"""Inspect and explicitly adopt an existing final without rendering its media."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter

from raid_editor.config.models import PresentationConfig, ProjectConfig
from raid_editor.ingestion.probe import MediaProbe, probe_media
from raid_editor.models import PullCandidate, TimelineDocument
from raid_editor.util.paths import atomic_write_json, atomic_write_text, full_file_sha256

_SHA = re.compile(r"^[0-9a-f]{64}$")
_PULLS = TypeAdapter(list[PullCandidate])
ORIGIN = "existing_final_review"


class FinalValidationError(RuntimeError):
    """Existing media or its evidence cannot safely be accepted."""


@dataclass(frozen=True)
class FinalContext:
    video: Path
    timeline: TimelineDocument
    pulls: list[PullCandidate]
    presentation: PresentationConfig | None


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (OSError, ValueError) as exc:
        raise FinalValidationError(f"Missing or invalid saved evidence: {path}") from exc


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _binding(value: object, path: Path, sha256: str) -> None:
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("path"), str)
        or not Path(value["path"]).is_absolute()
        or Path(value["path"]).resolve() != path.resolve()
        or value.get("sha256") != sha256
    ):
        raise FinalValidationError("An existing artifact binding conflicts with this file")


def _managed_video(root: Path, video: Path) -> Path:
    path = video.expanduser().resolve()
    final_root = (root / "final").resolve()
    if (
        not final_root.is_relative_to(root.resolve())
        or not path.is_relative_to(final_root)
        or path.suffix.lower() != ".mp4"
    ):
        raise FinalValidationError("Select an MP4 inside this project's final directory")
    if not path.is_file():
        raise FinalValidationError(f"The selected final is missing: {path}")
    return path


def _document(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": full_file_sha256(path)}


def _evidence(root: Path) -> dict[str, dict[str, str]]:
    paths = {
        "timeline": root / "timeline" / "timeline.json",
        "source_probe": root / "analysis" / "media-probe.json",
    }
    pulls = root / "analysis" / "pull-candidates.json"
    if pulls.is_file():
        paths["pulls"] = pulls
    try:
        return {name: _document(path) for name, path in paths.items()}
    except OSError as exc:
        raise FinalValidationError(
            "Restore the saved timeline and source probe before validation"
        ) from exc


def _load_context(root: Path, video: Path, render: dict[str, Any]) -> FinalContext:
    try:
        timeline = TimelineDocument.model_validate(_json(root / "timeline" / "timeline.json"))
        source = MediaProbe.model_validate(_json(root / "analysis" / "media-probe.json"))
        pulls_path = root / "analysis" / "pull-candidates.json"
        pulls = _PULLS.validate_json(pulls_path.read_bytes()) if pulls_path.is_file() else []
        presentation = (
            PresentationConfig.model_validate(render["presentation"])
            if render.get("presentation") is not None
            else None
        )
        if (
            render.get("approved") is not True
            or not isinstance(render.get("signature"), str)
            or not _SHA.fullmatch(render["signature"])
            or not isinstance(render.get("resolution"), str)
            or not re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", render["resolution"])
            or type(render.get("fps")) is not int
            or not 1 <= render["fps"] <= 120
            or render.get("codec") not in {"h264", "hevc"}
        ):
            raise ValueError("render sidecar is not an approved supported render")
        source_path = source.source.get("path")
        if not isinstance(source_path, str) or not Path(source_path).is_absolute():
            raise ValueError("saved source path must be absolute")
        if (
            not timeline.source.is_absolute()
            or timeline.source.resolve() != Path(source_path).resolve()
        ):
            raise ValueError("timeline and probe identify different sources")
        if (
            timeline.schema_version != 1
            or source.schema_version != 1
            or not math.isfinite(source.duration_seconds)
            or source.duration_seconds <= 0
            or not math.isfinite(timeline.source_duration_seconds)
            or abs(timeline.source_duration_seconds - source.duration_seconds) > 0.1
            or len(source.video_streams) != 1
            or not math.isfinite(timeline.source_fps)
            or source.video_streams[0].frame_rate is None
            or not math.isfinite(source.video_streams[0].frame_rate)
            or abs(timeline.source_fps - source.video_streams[0].frame_rate) > 0.01
            or not timeline.clips
        ):
            raise ValueError("inconsistent source timing or missing edit")
        audio_indexes = {stream.index for stream in source.audio_streams}
        retained = timeline.retained_audio_stream_indexes
        if (
            not retained
            or len(retained) != len(set(retained))
            or not set(retained).issubset(audio_indexes)
            or timeline.excluded_microphone_stream_index in retained
            or (
                timeline.excluded_microphone_stream_index is not None
                and timeline.excluded_microphone_stream_index not in audio_indexes
            )
        ):
            raise ValueError("invalid saved audio routing")
        elapsed = 0.0
        pull_ids = {pull.id for pull in pulls}
        pull_by_id = {pull.id: pull for pull in pulls}
        if len(pull_ids) != len(pulls) or any(
            not all(math.isfinite(v) for v in (pull.start_seconds, pull.end_seconds))
            or pull.end_seconds > source.duration_seconds + 0.1
            for pull in pulls
        ):
            raise ValueError("invalid saved pull identities or timing")
        for clip in timeline.clips:
            if (
                not all(
                    math.isfinite(v) for v in (clip.source_in, clip.source_out, clip.timeline_in)
                )
                or clip.source_out <= clip.source_in
                or clip.source_out > source.duration_seconds + 0.1
                or abs(clip.timeline_in - elapsed) > 0.01
                or (pulls and not set(clip.pull_ids).issubset(pull_ids))
            ):
                raise ValueError("invalid saved edit boundaries or pull references")
            for pull_id in clip.pull_ids:
                if pull_id not in pull_by_id:
                    continue
                pull = pull_by_id[pull_id]
                if (
                    pull.start_seconds < clip.source_in - 0.1
                    or pull.end_seconds > clip.source_out + 0.1
                    or (clip.encounter is not None and pull.encounter != clip.encounter)
                    or (clip.result == "kill" and pull.result != "kill")
                    or (
                        clip.difficulty != "UNKNOWN"
                        and pull.difficulty != "UNKNOWN"
                        and clip.difficulty != pull.difficulty
                    )
                ):
                    raise ValueError("saved pull evidence contradicts the approved timeline")
            elapsed += clip.source_out - clip.source_in
        expected_duration = elapsed + (
            presentation.intro_seconds + presentation.outro_seconds if presentation else 0
        )
        for key, expected in (
            ("timeline_duration_seconds", elapsed),
            ("output_duration_seconds", expected_duration),
        ):
            actual = render.get(key)
            if (
                not isinstance(actual, (int, float))
                or isinstance(actual, bool)
                or not math.isfinite(actual)
                or actual <= 0
                or abs(actual - expected) > 0.1
            ):
                raise ValueError(f"render sidecar disagrees with saved edit: {key}")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise FinalValidationError(f"Saved final evidence is inconsistent: {exc}") from exc
    return FinalContext(video, timeline, pulls, presentation)


def _decode(video: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FinalValidationError("ffmpeg is required for complete existing-final validation")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-err_detect",
                "explode",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FinalValidationError(
            "Full video/audio decode failed; no approval was recorded"
        ) from exc


def accepted_final_context(root: Path, validation: dict[str, Any]) -> FinalContext:
    """Verify the accepted media and unchanged saved context without reading raw footage."""
    try:
        if validation.get("status") != "passed" or validation.get("validation_origin") != ORIGIN:
            raise ValueError("not an accepted existing-final report")
        artifact = validation["artifact"]
        video = _managed_video(root, Path(artifact["path"]))
        before = _identity(video)
        _binding(artifact, video, full_file_sha256(video))
        acceptance = validation["existing_final_validation"]
        if acceptance.get("approved") is not True or acceptance.get("schema_version") != 1:
            raise ValueError("missing explicit existing-final approval")
        if acceptance["evidence"] != _evidence(root):
            raise ValueError("saved edit evidence changed after inspection")
        sidecar = video.with_suffix(".manifest.json")
        if acceptance["render_manifest"] != _document(sidecar):
            raise ValueError("render sidecar changed after approval")
        render = _json(sidecar)
        _binding(render.get("artifact"), video, artifact["sha256"])
        if render.get("existing_final_validation") != acceptance["review"]:
            raise ValueError("approval records disagree")
        context = _load_context(root, video, render)
        if (
            _identity(video) != before
            or acceptance["evidence"] != _evidence(root)
            or acceptance["render_manifest"] != _document(sidecar)
        ):
            raise ValueError("final or saved evidence changed during verification")
        return context
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FinalValidationError(f"Existing final validation is no longer usable: {exc}") from exc


def validate_existing_final(
    config: ProjectConfig,
    root: Path,
    video: Path,
    *,
    approved: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Inspect without media writes; acceptance additionally requires the inspected SHA."""
    if approved and (expected_sha256 is None or not _SHA.fullmatch(expected_sha256)):
        raise FinalValidationError("--approved requires --expected-sha256 from a prior inspection")
    video = _managed_video(root, video)
    before = _identity(video)
    sidecar = video.with_suffix(".manifest.json")
    report = root / "reports" / "final-validation.json"
    markdown = report.with_suffix(".md")
    inspection_path = root / "reports" / "final-inspection.json"
    originals = {
        path: path.read_bytes() if path.is_file() else None for path in (sidecar, report, markdown)
    }
    render = _json(sidecar)
    existing = _json(report) if report.is_file() else None
    if existing is not None and existing.get("status") != "passed":
        raise FinalValidationError(
            "Existing final validation failed or is invalid; preserve its evidence"
        )
    if existing is not None and "checks" in existing:
        checks = existing["checks"]
        if not isinstance(checks, list) or any(
            not isinstance(check, dict) or check.get("passed") is not True for check in checks
        ):
            raise FinalValidationError("Existing final evidence contains a failed or invalid check")
    evidence = _evidence(root)
    context = _load_context(root, video, render)
    if approved and not context.pulls:
        raise FinalValidationError(
            "Restore saved analysis/pull-candidates.json before approving this final "
            "for downstream chapters and raid claims"
        )
    if context.timeline.source.resolve() != config.input.recording.resolve():
        raise FinalValidationError("Saved edit belongs to a different project recording")
    sha256 = full_file_sha256(video)
    if expected_sha256 is not None and expected_sha256 != sha256:
        raise FinalValidationError("The final no longer matches the inspected --expected-sha256")
    for record in (render, existing):
        if record is not None and "artifact" in record:
            _binding(record["artifact"], video, sha256)
    if existing is not None and existing.get("validation_origin") == ORIGIN:
        accepted_final_context(root, existing)
        if approved:
            return existing
    if approved:
        inspected = _json(inspection_path)
        if (
            inspected.get("artifact") != {"path": str(video), "sha256": sha256}
            or inspected.get("evidence") != evidence
            or inspected.get("render_manifest") != _document(sidecar)
            or inspected.get("previous_validation")
            != (_document(report) if report.is_file() else None)
        ):
            raise FinalValidationError(
                "Media or saved evidence changed; run a new inspection before approval"
            )
    final_probe = probe_media(video, force=True)
    width, height = (int(part) for part in render["resolution"].split("x"))
    final_video = final_probe.video_streams[0] if len(final_probe.video_streams) == 1 else None
    if (
        final_video is None
        or (final_video.width, final_video.height) != (width, height)
        or final_video.codec != render["codec"]
        or final_video.frame_rate is None
        or not math.isfinite(final_video.frame_rate)
        or abs(final_video.frame_rate - render["fps"]) > 0.01
        or not math.isfinite(final_probe.duration_seconds)
        or abs(final_probe.duration_seconds - render["output_duration_seconds"]) > 0.1
        or len(final_probe.audio_streams) != 1
        or final_probe.audio_streams[0].codec != "aac"
        or final_probe.audio_streams[0].channels != 2
        or final_probe.audio_streams[0].sample_rate != 48000
    ):
        raise FinalValidationError(
            "Final geometry, duration, codec, or audio shape differs from its saved render"
        )
    _decode(video)
    if (
        _identity(video) != before
        or full_file_sha256(video) != sha256
        or _identity(video) != before
        or _evidence(root) != evidence
        or any((p.read_bytes() if p.is_file() else None) != b for p, b in originals.items())
    ):
        raise FinalValidationError(
            "Media or saved evidence changed during inspection; nothing was approved"
        )
    inspected = {
        "author": "Neil Mitchell",
        "last_modified_by": "Neil Mitchell",
        "status": "passed",
        "artifact": {"path": str(video), "sha256": sha256},
        "evidence": evidence,
        "render_manifest": _document(sidecar),
        "previous_validation": _document(report) if report.is_file() else None,
        "inspected_at": datetime.now(UTC).isoformat(),
        "source_available": config.input.recording.is_file(),
        "source_preservation_reverified": False,
        "audio_routing_evidence": (
            "Saved stream mapping only; audible content requires operator review."
        ),
        "checks": [
            {"name": name, "passed": True, "detail": detail}
            for name, detail in (
                (
                    "saved_edit_consistent",
                    "Saved timeline, source probe, and render settings agree",
                ),
                (
                    "final_media_shape",
                    "Observed geometry, duration, video codec and audio shape agree",
                ),
                ("full_decode", "The complete video and audio decoded without errors"),
                (
                    "stable_media_and_evidence",
                    "Full media hash and saved evidence stayed unchanged",
                ),
            )
        ],
    }
    if not approved:
        atomic_write_json(inspection_path, inspected)
        return {**inspected, "approved": False}
    migration_id = uuid4().hex
    backup = root / "reports" / "final-validation-history" / migration_id
    backup.mkdir(parents=True, exist_ok=False)
    for path, content in originals.items():
        if content is not None:
            (backup / path.name).write_bytes(content)
    review = {
        "id": migration_id,
        "approved": True,
        "approved_at": datetime.now(UTC).isoformat(),
        "expected_sha256": sha256,
        "backup": str(backup.resolve()),
        "meaning": "Operator reviewed this existing final's picture, audio and edit; no new render",
    }
    upgraded = {
        **render,
        "author": "Neil Mitchell",
        "last_modified_by": "Neil Mitchell",
        "artifact": inspected["artifact"],
        "existing_final_validation": review,
    }
    sidecar_bytes = (json.dumps(upgraded, indent=2, ensure_ascii=False) + "\n").encode()
    accepted = {
        **inspected,
        "validation_origin": ORIGIN,
        "existing_final_validation": {
            "schema_version": 1,
            "approved": True,
            "review": review,
            "evidence": evidence,
            "render_manifest": {
                "path": str(sidecar.resolve()),
                "sha256": hashlib.sha256(sidecar_bytes).hexdigest(),
            },
        },
    }
    summary = (
        "---\nauthor: Neil Mitchell\nlast_modified_by: Neil Mitchell\n---\n\n"
        "# Existing Final Validation\n\nStatus: **PASSED**\n\n"
        f"File: `{video}`\n\nSHA-256: `{sha256}`\n\n"
        "The existing file was fully decoded, checked against saved edit records, and explicitly "
        "reviewed and approved. No video was rendered or changed. Source preservation was not "
        "reverified. Audio routing is historical evidence; audible content was reviewed "
        "by the operator.\n"
    )
    accepted["render_manifest"] = accepted["existing_final_validation"]["render_manifest"]
    intended = {
        sidecar: sidecar_bytes,
        markdown: summary.encode(),
        report: (json.dumps(accepted, indent=2, ensure_ascii=False) + "\n").encode(),
    }
    try:
        if (
            _identity(video) != before
            or _evidence(root) != evidence
            or any((p.read_bytes() if p.is_file() else None) != b for p, b in originals.items())
        ):
            raise FinalValidationError("Media or evidence changed before approval could be saved")
        atomic_write_text(sidecar, sidecar_bytes.decode())
        atomic_write_text(markdown, summary)
        if (
            _identity(video) != before
            or _evidence(root) != evidence
            or sidecar.read_bytes() != sidecar_bytes
            or (report.read_bytes() if report.is_file() else None) != originals[report]
        ):
            raise FinalValidationError(
                "Media or saved evidence changed while approval was being saved"
            )
        atomic_write_json(report, accepted)  # Commit the accepted pair last.
    except (OSError, FinalValidationError):
        for path, content in originals.items():
            current = path.read_bytes() if path.is_file() else None
            if current == content or current != intended[path]:
                continue  # Never overwrite another writer's unrelated changes.
            if content is not None:
                atomic_write_text(path, content.decode("utf-8"))
            else:
                path.unlink(missing_ok=True)
        raise
    return accepted
