"""Benchmark deterministic stages against the generated 30-second fixture."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

from raid_editor.config.loader import load_project_config
from raid_editor.detection.pipeline import analyse_pulls
from raid_editor.ingestion.probe import probe_media
from raid_editor.rendering.preview import render_preview
from raid_editor.timeline.builder import build_timeline
from raid_editor.util.paths import full_file_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def timed[**P, R](
    callable_object: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> tuple[R, float]:
    started = perf_counter()
    result = callable_object(*args, **kwargs)
    return result, perf_counter() - started


def main() -> int:
    config = load_project_config(PROJECT_ROOT / "samples" / "synthetic-project.yaml")
    source_hash_before = full_file_sha256(config.input.recording)
    with tempfile.TemporaryDirectory(prefix="raid-editor-benchmark-") as temporary:
        root = Path(temporary)
        probe, probe_seconds = timed(
            probe_media,
            config.input.recording,
            root / "media-probe.json",
            True,
        )
        pulls, detection_seconds = timed(
            analyse_pulls,
            recording=config.input.recording,
            recording_duration_seconds=probe.duration_seconds,
            settings=config.detection,
            combat_log=config.input.combat_log,
            skada_export=config.input.skada_export,
            manual_pulls=config.input.manual_pulls,
        )
        video = probe.video_streams[0]
        if video.frame_rate is None:
            raise RuntimeError("Synthetic fixture is missing a usable frame rate")
        timeline, timeline_seconds = timed(
            build_timeline,
            name="Synthetic Benchmark",
            source=str(config.input.recording),
            source_duration_seconds=probe.duration_seconds,
            source_fps=video.frame_rate,
            retained_audio_stream_indexes=config.audio.retained_stream_indexes(),
            excluded_microphone_stream_index=config.audio.microphone_track,
            pulls=pulls,
            detection=config.detection,
            editing=config.editing,
        )
        destination = root / "benchmark-review.mp4"
        _, render_seconds = timed(
            render_preview,
            timeline,
            destination,
            resolution="640x360",
            fps=30,
            bitrate="1M",
            transition_seconds=config.editing.transition_duration_seconds,
        )
        rendered_probe = probe_media(destination)
        result = {
            "fixture_duration_seconds": probe.duration_seconds,
            "fixture_size_bytes": probe.size_bytes,
            "pull_count": len(pulls),
            "timeline_duration_seconds": timeline.duration_seconds,
            "probe_seconds": probe_seconds,
            "detection_seconds": detection_seconds,
            "timeline_build_seconds": timeline_seconds,
            "preview_render_seconds": render_seconds,
            "preview_render_realtime_factor": (
                timeline.duration_seconds / render_seconds if render_seconds else None
            ),
            "preview_size_bytes": rendered_probe.size_bytes,
            "source_sha256_unchanged": (
                source_hash_before == full_file_sha256(config.input.recording)
            ),
        }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
