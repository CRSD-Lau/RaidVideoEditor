"""Append-only local decision and experiment ledger."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from raid_editor.growth.models import (
    GrowthLedgerEvent,
    PackagingExperiment,
    ProductionTimeEntry,
)
from raid_editor.util.paths import atomic_write_text, ensure_directory

_EVENT = TypeAdapter(GrowthLedgerEvent)
_EXPERIMENT = TypeAdapter(PackagingExperiment)


class GrowthLedgerError(RuntimeError):
    """Expected immutable-ledger identity or correction error."""


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GrowthLedgerError(f"Invalid JSONL at {path}:{number}") from exc
        if not isinstance(value, dict):
            raise GrowthLedgerError(f"Expected an object at {path}:{number}")
        rows.append(value)
    return rows


def append_growth_event(path: Path, event: GrowthLedgerEvent) -> None:
    """Append one unique event using an atomic whole-file replacement."""

    rows = _load_jsonl(path)
    existing_ids = {str(row.get("event_id")) for row in rows}
    if event.event_id in existing_ids:
        existing = next(row for row in rows if row.get("event_id") == event.event_id)
        if _EVENT.validate_python(existing) == event:
            return
        raise GrowthLedgerError(f"Event ID already exists with different content: {event.event_id}")
    if event.supersedes_event_id and event.supersedes_event_id not in existing_ids:
        raise GrowthLedgerError("A correction can only supersede an existing event")
    ensure_directory(path.parent)
    serialized = [*rows, event.model_dump(mode="json")]
    atomic_write_text(
        path,
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in serialized),
    )


def append_packaging_experiment(path: Path, experiment: PackagingExperiment) -> None:
    """Append one immutable experiment version; corrections use growth events."""

    rows = _load_jsonl(path)
    experiments = [_EXPERIMENT.validate_python(row) for row in rows]
    for existing in experiments:
        if existing.experiment_id != experiment.experiment_id:
            continue
        if existing == experiment:
            return
        raise GrowthLedgerError(
            f"Experiment ID already exists; record a new version ID: {experiment.experiment_id}"
        )
    latest_by_series: dict[str, PackagingExperiment] = {}
    for existing in experiments:
        latest_by_series[existing.series_id or existing.experiment_id] = existing
    if experiment.supersedes_experiment_id is not None:
        superseded = next(
            (
                existing
                for existing in experiments
                if existing.experiment_id == experiment.supersedes_experiment_id
            ),
            None,
        )
        if superseded is None:
            raise GrowthLedgerError("Experiment versions may supersede only an existing ID")
        series_id = experiment.series_id or experiment.experiment_id
        if latest_by_series.get(series_id) != superseded:
            raise GrowthLedgerError("Experiment versions must supersede the latest series state")
        if (
            superseded.campaign_id != experiment.campaign_id
            or superseded.content_id != experiment.content_id
            or superseded.factor != experiment.factor
            or superseded.control != experiment.control
            or superseded.treatment != experiment.treatment
        ):
            raise GrowthLedgerError("Experiment versions cannot change scope or treatment")
        allowed_transitions = {
            "planned": {"running", "completed", "cancelled"},
            "running": {"completed", "cancelled"},
        }
        if experiment.status not in allowed_transitions.get(superseded.status, set()):
            raise GrowthLedgerError(
                f"Invalid experiment transition: {superseded.status} -> {experiment.status}"
            )
    elif experiment.status in {"planned", "running"}:
        active = [
            existing
            for existing in latest_by_series.values()
            if existing.campaign_id == experiment.campaign_id
            and existing.content_id == experiment.content_id
            and existing.status in {"planned", "running"}
        ]
        if active:
            raise GrowthLedgerError(
                "Only one packaging variable may be active for an asset; "
                f"finish or cancel {active[0].experiment_id} first"
            )
    serialized = [*rows, experiment.model_dump(mode="json")]
    ensure_directory(path.parent)
    atomic_write_text(
        path,
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in serialized),
    )


def load_growth_events(path: Path) -> list[GrowthLedgerEvent]:
    return [_EVENT.validate_python(row) for row in _load_jsonl(path)]


def load_packaging_experiments(path: Path) -> list[PackagingExperiment]:
    return [_EXPERIMENT.validate_python(row) for row in _load_jsonl(path)]


def append_production_time(path: Path, entry: ProductionTimeEntry) -> None:
    """Record comparable production effort in the same immutable decision stream."""

    append_growth_event(
        path,
        GrowthLedgerEvent(
            event_id=entry.entry_id,
            recorded_at=entry.recorded_at,
            campaign_id=entry.campaign_id,
            event_type="production_time_recorded",
            subject_id=entry.stage,
            actor="Neil Mitchell",
            active_operator_minutes=entry.minutes,
            unattended_runtime_seconds=entry.unattended_runtime_seconds,
            payload=entry.model_dump(mode="json"),
        ),
    )
