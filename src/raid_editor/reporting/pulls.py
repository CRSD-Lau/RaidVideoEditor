"""Pull-candidate JSON/CSV and human-readable report outputs."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from raid_editor.models import PullCandidate
from raid_editor.util.paths import atomic_write_json, atomic_write_text, ensure_directory


def write_pull_candidates(
    pulls: list[PullCandidate],
    json_path: Path,
    csv_path: Path,
) -> None:
    atomic_write_json(json_path, [pull.model_dump(mode="json") for pull in pulls])
    ensure_directory(csv_path.parent)
    stream = io.StringIO(newline="")
    fieldnames = [
        "id",
        "start_seconds",
        "end_seconds",
        "duration_seconds",
        "type",
        "encounter",
        "result",
        "confidence",
        "evidence",
        "include",
        "title",
        "notes",
        "difficulty",
        "difficulty_confidence",
        "difficulty_evidence",
        "difficulty_reason",
    ]
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    for pull in pulls:
        row = pull.model_dump(mode="json")
        row["evidence"] = "; ".join(pull.evidence)
        row["difficulty_evidence"] = "; ".join(pull.difficulty_evidence)
        writer.writerow(row)
    atomic_write_text(csv_path, stream.getvalue())


def write_uncertain_segments(
    pulls: list[PullCandidate],
    destination: Path,
    confidence_threshold: float,
) -> None:
    uncertain = [pull for pull in pulls if pull.confidence < confidence_threshold]
    lines = ["# Uncertain Segments", ""]
    if not uncertain:
        lines.append("No pull candidates fall below the configured confidence threshold.")
    else:
        lines.extend(
            f"- `{pull.id}` {pull.start_seconds:.3f}-{pull.end_seconds:.3f}s "
            f"({pull.type}, confidence {pull.confidence:.2f}): "
            f"{', '.join(pull.evidence) or 'no supporting evidence'}"
            for pull in uncertain
        )
    atomic_write_text(destination, "\n".join(lines) + "\n")
