"""Run explicit fictional editorial cases against the installed local models.

Not part of the default unit tests. This checks ordinary-kill abstention through
real ASR and extraction of an embedded exchange from supplied fictional speech.
No real raid transcript or raw model response is printed or saved.
Author: Neil Mitchell
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from raid_editor.config.models import HighlightIntelligenceConfig
from raid_editor.highlights import intelligence
from raid_editor.highlights._intelligence_runtime import LocalOllama, Utterance, Word
from raid_editor.highlights.local_runtime import managed_local_intelligence
from raid_editor.util.paths import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]

# Authored fictional test input, not a retained recording transcript.
FICTIONAL_EXCHANGE = [
    (2, 7, "Everyone check your flasks and food buffs. We pull when the timer reaches zero."),
    (15, 20, "The tank moves the boss to the wall. Ranged players spread on the left."),
    (28, 33, "That was another normal kill. Please pass the tokens to the usual people."),
    (42, 47, "Mana is back. We can move to the next room when everyone is ready."),
    (59, 64, "I have a foolproof new shortcut. Follow me and I will save us a minute."),
    (68, 71, "Are you certain that platform is safe?"),
    (74, 78, "Of course, I have used this shortcut a thousand times."),
    (82, 86, "You just ran straight off the edge into the pit."),
    (90, 95, "The shortcut works perfectly. It takes you to the graveyard twice as fast."),
    (108, 113, "All right, back to the raid. Rebuff the group and check the ready status."),
    (120, 126, "Healers stand near the entrance for this next pull. Use the normal positioning."),
    (137, 142, "Our next break is after the next boss. Please keep the passage clear."),
]


def _supplied_utterances() -> list[Utterance]:
    rows = []
    for index, (start, end, text) in enumerate(FICTIONAL_EXCHANGE):
        tokens = text.split()
        stride = (end - start) / len(tokens)
        rows.append(
            Utterance(
                id=f"fictional_{index:03d}",
                role="discord",
                start=start,
                end=end,
                text=text,
                words=tuple(
                    Word(token, start + position * stride, start + (position + 1) * stride, 0.95)
                    for position, token in enumerate(tokens)
                ),
            )
        )
    return rows


class SuppliedFictionalSpeech:
    """Replace only ASR for the explicit text-model selection regression."""

    actual_device = "supplied_test_utterances"
    actual_compute_type = "not_applicable"
    diagnostics: list[str] = []

    def __init__(self, _settings: HighlightIntelligenceConfig) -> None:
        self.rows = _supplied_utterances()

    def transcribe(self, _recording: Path, **kwargs: Any) -> list[Utterance]:
        return [
            row for row in self.rows if kwargs["start"] <= row.start and row.end <= kwargs["end"]
        ]

    def close(self) -> None:
        self.rows.clear()


def _ordinary_fixture(positive: Path, destination: Path) -> Path:
    negative = destination / "synthetic-routine-kill.mkv"
    executable = shutil.which("ffmpeg")
    if executable is None or not positive.is_file():
        raise RuntimeError("Generate the fictional speech fixture and install FFmpeg first")
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-t",
            "8",
            "-i",
            str(positive),
            "-map",
            "0",
            "-vf",
            "tpad=stop_mode=clone:stop_duration=22",
            "-af",
            "apad=pad_dur=22",
            "-t",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-c:a",
            "pcm_s16le",
            "-metadata",
            "author=Neil Mitchell",
            "-metadata",
            "last_modified_by=Neil Mitchell",
            str(negative),
        ],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return negative


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Already-installed local Ollama model to evaluate")
    parser.add_argument(
        "--vision-frame", type=Path, help="Optional local raid image for vision context"
    )
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=ROOT / "samples" / "generated" / "highlight-intelligence",
        help="Fictional media and fixture-timing.json from generate-highlight-speech-smoke.ps1",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ROOT,
        help="Directory containing explicitly installed .tools and .models (no downloads here)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "highlight-editorial-smoke",
        help="Directory for the generated ordinary-kill fixture and JSON validation report",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = argument_parser()
    args = parser.parse_args(argv)
    positive = args.fixture_dir.expanduser().resolve() / "synthetic-dialogue.mkv"
    try:
        timing = json.loads(
            positive.with_name("fixture-timing.json").read_text(encoding="utf-8-sig")
        )
        with positive.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if not isinstance(timing, dict) or timing.get("source_sha256") != digest:
            raise ValueError("Fictional fixture fingerprint does not match")
    except (OSError, ValueError):
        parser.error(
            "Generate the fictional fixture first with scripts/generate-highlight-speech-smoke.ps1."
        )
    runtime_root = args.runtime_root.expanduser().resolve()
    for asset in (
        runtime_root / ".models" / "faster-whisper-medium.en" / "model.bin",
        runtime_root / ".tools" / "ollama-0.33.3" / "ollama.exe",
    ):
        if not asset.is_file():
            parser.error(
                "Install optional local assets with scripts/setup-highlight-intelligence.py first, "
                "or pass --runtime-root for an existing installation."
            )
    if args.vision_frame is not None and not args.vision_frame.is_file():
        parser.error("--vision-frame must name an existing local raid image.")
    output_dir = args.output_dir.expanduser().resolve()
    settings = HighlightIntelligenceConfig(
        enabled=True,
        visual_verification=False,
        whisper_model_path=runtime_root / ".models/faster-whisper-medium.en",
        ollama_executable=runtime_root / ".tools/ollama-0.33.3/ollama.exe",
        ollama_models_path=runtime_root / ".models/ollama",
        model=args.model or HighlightIntelligenceConfig().model,
    )
    started = time.monotonic()
    with managed_local_intelligence(settings):
        ordinary = intelligence.analyse_intelligent_highlights(
            _ordinary_fixture(positive, output_dir),
            source_streams={"discord": 2, "microphone": 3},
            recording_duration_seconds=30,
            pulls=[],
            heuristic_candidates=[],
            settings=settings,
        )
        original = intelligence.LocalWhisper
        try:
            intelligence.LocalWhisper = SuppliedFictionalSpeech  # type: ignore[assignment]
            diluted = intelligence.analyse_intelligent_highlights(
                Path("supplied-fictional-utterances-no-media-read"),
                source_streams={"discord": 2},
                recording_duration_seconds=150,
                pulls=[],
                heuristic_candidates=[],
                settings=settings,
            )
        finally:
            intelligence.LocalWhisper = original
        selected = [
            candidate
            for candidate in diluted.candidates
            if 47 <= candidate.start_seconds <= 59
            and 95 <= candidate.end_seconds <= 108
            and candidate.end_seconds - candidate.start_seconds <= 90
        ]
        vision: dict[str, object] = {"status": "not_requested"}
        if args.vision_frame is not None:
            image = base64.b64encode(args.vision_frame.read_bytes()).decode("ascii")
            verdict = intelligence.VisionResponse.model_validate(
                LocalOllama(settings).chat(
                    instruction=intelligence._VISION_INSTRUCTION,
                    payload={
                        "candidate_category": "clutch",
                        "speech_backed": False,
                        "sample_times": [0],
                    },
                    schema=intelligence.VisionResponse.model_json_schema(),
                    images=[image],
                )
            )
            vision = {"status": "complete", **verdict.model_dump()}
        passed = (
            ordinary.status == "complete"
            and not ordinary.candidates
            and ordinary.rejected_proposals == 0
            and diluted.status == "complete"
            and bool(selected)
            and all(not candidate.include for candidate in diluted.candidates)
            and (args.vision_frame is None or vision.get("visible_context") == "raid_gameplay")
        )
        report = {
            "author": "Neil Mitchell",
            "last_modified_by": "Neil Mitchell",
            "status": "passed" if passed else "failed",
            "fixture": "Authored fictional dialogue; supplied speech for diluted selection case",
            "ordinary_real_asr": ordinary.to_dict(),
            "diluted_supplied_speech": diluted.to_dict(),
            "embedded_arc_selected": bool(selected),
            "expected_arc_seconds": [59, 95],
            "unrelated_speech_exclusion_bounds_seconds": [47, 108],
            "vision_context_only": vision,
            "runtime_signature": intelligence.intelligence_runtime_signature(settings),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "transcript_persisted": False,
        }
        destination = output_dir / "editorial-regression-smoke.json"
        atomic_write_json(destination, report)
    print(
        f"Editorial local-model regression: {report['status']}; report: {destination}", flush=True
    )
    if not passed:
        raise RuntimeError("Local editorial regression failed; inspect the transcript-free report")


if __name__ == "__main__":
    main()
