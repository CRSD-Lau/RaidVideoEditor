"""Verify the rendered fixture contains retained tones and excludes mic-only tones."""

from __future__ import annotations

import argparse
import math
import struct
import subprocess
from pathlib import Path


def goertzel_db(samples: tuple[float, ...], sample_rate: int, frequency: float) -> float:
    coefficient = 2 * math.cos(2 * math.pi * frequency / sample_rate)
    previous = 0.0
    previous_two = 0.0
    for sample in samples:
        current = sample + coefficient * previous - previous_two
        previous_two = previous
        previous = current
    power = previous_two**2 + previous**2 - coefficient * previous * previous_two
    normalized = max(power / max(1, len(samples) ** 2), 1e-30)
    return 10 * math.log10(normalized)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("preview", type=Path)
    args = parser.parse_args()
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(args.preview.resolve()),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "f32le",
        "-",
    ]
    completed = subprocess.run(command, check=True, capture_output=True)
    sample_count = len(completed.stdout) // 4
    samples = struct.unpack(f"<{sample_count}f", completed.stdout)
    levels = {
        "microphone_fundamental_115_hz": goertzel_db(samples, 8000, 115),
        "retained_game_220_hz": goertzel_db(samples, 8000, 220),
        "microphone_harmonic_345_hz": goertzel_db(samples, 8000, 345),
        "retained_discord_660_hz": goertzel_db(samples, 8000, 660),
    }
    for name, level in levels.items():
        print(f"{name}={level:.2f} dB")
    retained_floor = min(levels["retained_game_220_hz"], levels["retained_discord_660_hz"])
    mic_ceiling = max(
        levels["microphone_fundamental_115_hz"],
        levels["microphone_harmonic_345_hz"],
    )
    separation = retained_floor - mic_ceiling
    print(f"minimum_retained_to_mic_separation={separation:.2f} dB")
    if separation < 20:
        print("FAIL: microphone-only tones are not sufficiently suppressed")
        return 1
    print("PASS: retained tones are present and microphone-only tones are suppressed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
