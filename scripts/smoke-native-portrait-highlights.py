"""Real FFmpeg paired-source regression; all footage/audio here is synthetic.

Author: Neil Mitchell. Run with the project's installed intelligence environment
for NumPy (fixture generation and numeric QA only; no models are started).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "native-portrait-smoke",
        help="Directory for generated synthetic fixtures, review/export media, and validation.json",
    )
    return parser


def run(args: list[str]) -> bytes:
    executable = shutil.which(args[0])
    if executable is None:
        raise RuntimeError(f"Install {args[0]} and make it available on PATH before this smoke.")
    return subprocess.run(
        [executable, *args[1:]],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout


def fixture(directory: Path, *, offset: float, negative: bool = False) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    vertical = directory / "Vertical"
    vertical.mkdir(exist_ok=True)
    sample_rate, duration = 16000, 100
    t = np.arange(sample_rate * duration) / sample_rate
    rng = np.random.default_rng(716)
    amplitudes = np.repeat(rng.uniform(0.02, 0.28, duration * 100), sample_rate // 100)
    pcm = amplitudes * np.sin(2 * np.pi * 440 * t)
    audio = directory / "synthetic-shared-game.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        handle.writeframes((pcm * 32767).astype("<i2").tobytes())
    landscape = directory / "2026-09-11 22-00-03.mp4"
    portrait = vertical / ("2026-09-11 22-00-01.mp4" if negative else "2026-09-11 22-00-05.mp4")
    for path, geometry, color, seek in (
        (landscape, "640x360", "blue", abs(offset) if negative else 0),
        (portrait, "1080x1920", "green", 0 if negative else offset),
    ):
        clock_shift = seek - (abs(offset) if negative else 0)
        vf = (
            f"drawbox=x=0:y=ih/2:w=iw:h=ih/3:color=yellow:t=fill:"
            f"enable='between(t+{clock_shift},46,47)'"
        )
        # Portrait's first audio stream deliberately contains the wrong tone.
        # Its labeled game reference is index 2, landscape game is index 1.
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={geometry}:r=15:d=90",
            "-ss",
            str(seek),
            "-i",
            str(audio),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=16000:duration=90",
            "-map",
            "0:v:0",
        ]
        if path == portrait:
            cmd += [
                "-map",
                "2:a:0",
                "-map",
                "1:a:0",
                "-metadata:s:a:0",
                "title=Unwanted portrait mix",
                "-metadata:s:a:1",
                "title=WoW Game",
            ]
        else:
            cmd += ["-map", "1:a:0", "-metadata:s:a:0", "title=WoW Game"]
        cmd += [
            "-t",
            "90",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-metadata",
            "artist=Neil Mitchell",
            "-metadata",
            "author=Neil Mitchell",
            "-metadata",
            "last_modified_by=Neil Mitchell",
            "-y",
            str(path),
        ]
        run(cmd)
        os.utime(path, (time.time() - 300, time.time() - 300))
    return landscape, portrait


def main(argv: list[str] | None = None) -> None:
    global np
    parser = argument_parser()
    arguments = parser.parse_args(argv)
    try:
        np = importlib.import_module("numpy")
    except ImportError:
        parser.error(
            "NumPy is needed only for this synthetic fixture and numeric QA. "
            "Install it with: uv sync --extra dev --extra intelligence --frozen"
        )
    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            parser.error(f"Install {executable} and make it available on PATH.")
    destination = arguments.output_dir.expanduser().resolve()
    from raid_editor import workflow
    from raid_editor.config.models import ProjectConfig
    from raid_editor.highlights.portrait import resolve_portrait_source
    from raid_editor.highlights.render import (
        portrait_presentation_reference,
        render_vertical_highlights,
    )
    from raid_editor.highlights.review import (
        generate_highlight_review_media,
        generate_highlight_review_page,
    )
    from raid_editor.ingestion.probe import probe_media
    from raid_editor.models import HighlightCandidate
    from raid_editor.util.paths import atomic_write_json, quick_file_fingerprint

    results = []
    for name, offset in (("portrait-starts-later", 2.35), ("portrait-starts-earlier", -2.35)):
        directory = destination / name
        landscape, portrait = fixture(directory, offset=abs(offset), negative=offset < 0)
        before = [quick_file_fingerprint(p) for p in (landscape, portrait)]
        config = ProjectConfig.model_validate(
            {
                "project": {"name": "Synthetic native portrait regression"},
                "input": {"recording": landscape, "vertical_recording": portrait},
                "audio": {"game_track": 1, "keep_discord_audio": False},
                "music": {"library": directory / "unused.json"},
                "highlights": {
                    "video_source": "native_vertical",
                    "hardware_encoding": False,
                    "keep_discord_audio": False,
                },
            }
        )
        source = resolve_portrait_source(config, directory / "binding")
        assert source is not None
        assert abs(source.offset_seconds - offset) <= 0.08, (source.offset_seconds, offset)
        cached = resolve_portrait_source(config, directory / "binding")
        assert cached is not None and cached.signature == source.signature
        candidate = HighlightCandidate(
            id="synthetic-001",
            title="Synthetic sync fixture",
            start_seconds=43,
            end_seconds=49,
            peak_seconds=46,
            category="reaction",
            score=0.8,
            include=False,
            signals=["synthetic_validation_only"],
        )
        assets = generate_highlight_review_media(
            landscape,
            [candidate],
            directory / "review",
            audio_stream_indexes=[1],
            portrait_source=source,
        )
        ref = portrait_presentation_reference(
            source, audio_stream_indexes=[1], resolution=config.highlights.vertical_resolution
        )
        generate_highlight_review_page(
            [candidate],
            assets,
            directory / "review" / "index.html",
            includes_game=True,
            includes_discord=False,
            includes_microphone=False,
            native_portrait=True,
            presentation_reference=ref,
        )
        outputs = render_vertical_highlights(
            landscape,
            [candidate.model_copy(update={"include": True})],
            directory / "exports",
            audio_stream_indexes=[1],
            microphone_stream_index=None,
            settings=config.highlights,
            approved=True,
            portrait_source=source,
        )
        # Exercise the actual workflow gate and path plumbing, including the
        # native review, without starting speech models for this fixture.
        pulls_path = directory / "empty-pulls.json"
        selection_path = directory / "synthetic-selection.json"
        atomic_write_json(pulls_path, {"pulls": []})
        atomic_write_json(
            selection_path,
            {
                "author": "Neil Mitchell",
                "last_modified_by": "Neil Mitchell",
                "presentation_reference": ref,
                "highlights": [candidate.model_copy(update={"include": True}).model_dump()],
            },
        )
        config.input.manual_pulls = pulls_path
        config.highlights.manual_selection = selection_path
        original_output_dir = workflow.project_output_dir
        try:
            workflow.project_output_dir = lambda _, target=directory / "workflow": target
            reviewed, paths = workflow.analyse_highlights_project(config, create_review_media=True)
            assert len(reviewed) == 1 and reviewed[0].include
            workflow_outputs, _ = workflow.render_highlights_project(config, approved=True)
            workflow_manifest = json.loads(
                (paths.highlights / "vertical" / "manifest.json").read_text()
            )
            assert workflow_manifest["clips"][0]["presentation_reference"] == ref
            assert workflow_manifest["clips"][0]["video_source"] == "native_vertical"
            assert workflow_manifest["clips"][0]["audio_recording"] == str(landscape.resolve())
        finally:
            workflow.project_output_dir = original_output_dir
        media_checks = []
        for path in [assets[candidate.id], *outputs, *workflow_outputs]:
            probe = probe_media(path)
            decoded = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
            assert decoded == b""
            # Yellow marker is at landscape 46.0 seconds = output 3.0 seconds.
            frame_results = []
            for position in (0.5, 3.4, 5.5):
                frame = run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-ss",
                        str(position),
                        "-i",
                        str(path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=9:16",
                        "-pix_fmt",
                        "rgb24",
                        "-f",
                        "rawvideo",
                        "-",
                    ]
                )
                pixels = np.frombuffer(frame, dtype=np.uint8).reshape((16, 9, 3))
                red, green, blue = pixels[10, 4].tolist()
                assert green > 60 and blue < 60, (position, red, green, blue)
                assert (red > 100) == (position == 3.4), (position, red, green, blue)
                frame_results.append({"seconds": position, "sample_rgb": [red, green, blue]})
            pcm = run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-map",
                    "0:a:0",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-f",
                    "f32le",
                    "-",
                ]
            )
            samples = np.frombuffer(pcm, dtype="<f4")
            spectrum = np.abs(np.fft.rfft(samples))
            frequencies = np.fft.rfftfreq(len(samples), 1 / 16000)
            game_energy = float(spectrum[np.abs(frequencies - 440) < 3].sum())
            unwanted_energy = float(spectrum[np.abs(frequencies - 880) < 3].sum())
            assert game_energy > unwanted_energy * 20
            assert abs(probe.duration_seconds - 6) <= 0.20
            tags = {key.casefold(): value for key, value in probe.tags.items()}
            assert tags.get("artist") == "Neil Mitchell", probe.tags
            assert tags.get("author") == "Neil Mitchell", probe.tags
            assert tags.get("last_modified_by") == "Neil Mitchell", probe.tags
            media_checks.append(
                {
                    "path": str(path),
                    "duration": probe.duration_seconds,
                    "video": probe.video_streams[0].model_dump(),
                    "audio": probe.audio_streams[0].model_dump(),
                    "frames": frame_results,
                    "decode_errors": 0,
                    "game_to_unwanted_tone_ratio": game_energy / unwanted_energy,
                }
            )
        assert before == [quick_file_fingerprint(p) for p in (landscape, portrait)]
        results.append(
            {
                "case": name,
                "expected_offset": offset,
                "measured_offset": source.offset_seconds,
                "cache_verified": True,
                "source_files_unchanged": True,
                "media_checks": media_checks,
                "actual_review_and_export_workflow": "passed",
                "presentation_reference": ref,
            }
        )
    atomic_write_json(
        destination / "validation.json",
        {
            "author": "Neil Mitchell",
            "last_modified_by": "Neil Mitchell",
            "passed": True,
            "synthetic_only": True,
            "cases": results,
        },
    )
    print(json.dumps({"passed": True, "report": str(destination / "validation.json")}))


if __name__ == "__main__":
    main()
