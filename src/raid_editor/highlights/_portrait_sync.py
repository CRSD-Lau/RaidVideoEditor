"""Bounded, transcript-free synchronization of two local audio streams."""

from __future__ import annotations

import array
import math
import shutil
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PCM_RATE = 8000
ENVELOPE_RATE = 100
SEARCH_RADIUS_SECONDS = 12.0
MIN_CORRELATION = 0.80
MIN_PEAK_MARGIN = 0.12
MAX_OFFSET_DEVIATION_SECONDS = 0.08


@dataclass(frozen=True, slots=True)
class SyncObservation:
    landscape_start_seconds: float
    sample_seconds: float
    offset_seconds: float
    correlation: float
    runner_up_correlation: float

    def to_dict(self) -> dict[str, float]:
        return {key: round(value, 6) for key, value in asdict(self).items()}


def decode_envelope(
    recording: Path, stream_index: int, start: float, seconds: float
) -> list[float]:
    """Decode one absolute audio stream to an in-memory 100-Hz RMS envelope."""
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise ValueError("Portrait synchronization requires FFmpeg on PATH")
    command = [
        executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(recording),
        "-t",
        f"{seconds:.6f}",
        "-map",
        f"0:{stream_index}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(PCM_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(
            "Portrait synchronization could not decode a bounded audio sample"
        ) from None
    if result.returncode != 0 or len(result.stdout) % 2:
        raise ValueError("Portrait synchronization could not decode the selected audio stream")
    samples = array.array("h")
    samples.frombytes(result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    if len(samples) < (seconds - 0.10) * PCM_RATE:
        raise ValueError("Portrait synchronization audio does not cover the sampled window")
    stride = PCM_RATE // ENVELOPE_RATE
    width = stride * 3
    if len(samples) < width:
        raise ValueError("Portrait synchronization sample is too short")
    energies = [0]
    for sample in samples:
        energies.append(energies[-1] + sample * sample)
    # Overlapping 30-ms windows soften codec phase differences; timestamps still
    # advance in 10-ms increments. Identical processing preserves offset sign.
    return [
        math.log1p(30 * math.sqrt((energies[index + width] - energies[index]) / width) / 32768)
        for index in range(0, len(samples) - width + 1, stride)
    ]


def correlate_envelopes(reference: list[float], search: list[float]) -> tuple[int, float, float]:
    """Return the unique matching shift or reject weak/repetitive evidence."""
    width = len(reference)
    if width < ENVELOPE_RATE * 3 or len(search) < width:
        raise ValueError("Portrait synchronization has insufficient overlapping audio")
    average = statistics.fmean(reference)
    centered = [value - average for value in reference]
    reference_energy = sum(value * value for value in centered)
    if average < 0.02 or reference_energy / width < 0.000004:
        raise ValueError("Portrait synchronization audio is silent or lacks distinctive variation")
    totals = [0.0]
    squares = [0.0]
    for value in search:
        totals.append(totals[-1] + value)
        squares.append(squares[-1] + value * value)
    scores = []
    for shift in range(len(search) - width + 1):
        total = totals[shift + width] - totals[shift]
        energy = squares[shift + width] - squares[shift] - total * total / width
        if energy <= 1e-12:
            scores.append(-1.0)
            continue
        covariance = sum(value * search[shift + index] for index, value in enumerate(centered))
        scores.append(max(-1.0, min(1.0, covariance / math.sqrt(reference_energy * energy))))
    best_shift = max(range(len(scores)), key=scores.__getitem__)
    best = scores[best_shift]
    exclusion = round(0.25 * ENVELOPE_RATE)
    alternative = max(
        (score for index, score in enumerate(scores) if abs(index - best_shift) > exclusion),
        default=-1.0,
    )
    if best < MIN_CORRELATION:
        raise ValueError("Portrait audio does not clearly match the landscape recording")
    if best - alternative < MIN_PEAK_MARGIN:
        raise ValueError(
            "Portrait synchronization is ambiguous; audio has competing matching offsets"
        )
    return best_shift, best, alternative


def measure_audio_offset(
    landscape: Path,
    portrait: Path,
    *,
    landscape_stream_index: int,
    portrait_stream_index: int,
    landscape_duration: float,
    portrait_duration: float,
    hint_seconds: float,
) -> tuple[float, list[SyncObservation]]:
    """Require three separated, distinctive samples to agree on one offset."""
    if not all(
        math.isfinite(value)
        for value in (
            landscape_duration,
            portrait_duration,
            hint_seconds,
        )
    ):
        raise ValueError("Portrait synchronization requires finite durations and offset hint")
    overlap_start = max(0.0, hint_seconds)
    overlap_end = min(landscape_duration, hint_seconds + portrait_duration)
    overlap = overlap_end - overlap_start
    if overlap < 18:
        raise ValueError("Portrait synchronization requires at least 18 seconds of common footage")
    sample_seconds = min(12.0, overlap / 4)
    starts = [
        overlap_start + (overlap - sample_seconds) * fraction for fraction in (0.10, 0.50, 0.90)
    ]
    observations = []
    for start in starts:
        expected_portrait_start = start - hint_seconds
        search_start = max(0.0, expected_portrait_start - SEARCH_RADIUS_SECONDS)
        last_start = min(
            portrait_duration - sample_seconds, expected_portrait_start + SEARCH_RADIUS_SECONDS
        )
        if last_start <= search_start:
            raise ValueError("Portrait synchronization has no bounded search interval")
        reference = decode_envelope(landscape, landscape_stream_index, start, sample_seconds)
        search = decode_envelope(
            portrait,
            portrait_stream_index,
            search_start,
            last_start - search_start + sample_seconds,
        )
        shift, score, alternative = correlate_envelopes(reference, search)
        offset = start - search_start - shift / ENVELOPE_RATE
        observations.append(SyncObservation(start, sample_seconds, offset, score, alternative))
    offsets = [row.offset_seconds for row in observations]
    if max(offsets) - min(offsets) > MAX_OFFSET_DEVIATION_SECONDS:
        raise ValueError(
            "Portrait and landscape synchronization drifts or disagrees between separated samples"
        )
    return statistics.median(offsets), observations
