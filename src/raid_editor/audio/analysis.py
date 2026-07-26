"""Bounded before/after volume sampling for review reports."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from statistics import mean
from typing import Any

from raid_editor.util.paths import atomic_write_text

_MEAN = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB")
_MAX = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB")


def measure_volume_samples(
    media: Path,
    *,
    stream_index: int,
    duration_seconds: float,
    sample_seconds: float = 20.0,
) -> dict[str, Any]:
    """Measure three bounded regions; this is explicitly not full-program loudness."""

    sample = min(sample_seconds, max(1.0, duration_seconds))
    starts = [
        max(0.0, min(duration_seconds - sample, fraction * duration_seconds - sample / 2))
        for fraction in (0.2, 0.5, 0.8)
    ]
    measurements: list[dict[str, float]] = []
    for start in starts:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(media),
            "-t",
            f"{sample:.3f}",
            "-map",
            f"0:{stream_index}",
            "-vn",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = completed.stderr
        mean_match = _MEAN.search(output)
        max_match = _MAX.search(output)
        measurements.append(
            {
                "start_seconds": start,
                "sample_seconds": sample,
                "mean_db": float(mean_match.group(1)) if mean_match else float("-inf"),
                "max_db": float(max_match.group(1)) if max_match else float("-inf"),
            }
        )
    finite_means = [item["mean_db"] for item in measurements if item["mean_db"] != float("-inf")]
    finite_maxes = [item["max_db"] for item in measurements if item["max_db"] != float("-inf")]
    return {
        "method": "three bounded FFmpeg volumedetect samples; not integrated LUFS",
        "samples": measurements,
        "average_mean_db": mean(finite_means) if finite_means else None,
        "highest_peak_db": max(finite_maxes) if finite_maxes else None,
    }


def write_audio_report(
    before: dict[int, dict[str, Any]],
    after: dict[str, Any] | None,
    destination: Path,
) -> None:
    lines = [
        "# Audio Analysis",
        "",
        "Measurements use three bounded FFmpeg `volumedetect` samples. They are useful for "
        "clipping and balance review but are not a broadcast loudness-compliance claim.",
        "",
        "## Before",
        "",
        "| Source stream | Average mean | Highest sampled peak |",
        "|---:|---:|---:|",
    ]
    for stream, result in before.items():
        average = result["average_mean_db"]
        peak = result["highest_peak_db"]
        lines.append(
            f"| {stream} | {average:.1f} dB | {peak:.1f} dB |"
            if average is not None and peak is not None
            else f"| {stream} | unavailable | unavailable |"
        )
    lines.extend(["", "## Review mix", ""])
    if after and after["average_mean_db"] is not None:
        lines.append(
            f"- Average sampled mean: {after['average_mean_db']:.1f} dB\n"
            f"- Highest sampled peak: {after['highest_peak_db']:.1f} dB"
        )
    else:
        lines.append("Review mix has not been measured.")
    atomic_write_text(destination, "\n".join(lines) + "\n")
