"""Load user-corrected pull windows from JSON or CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from raid_editor.models import PullCandidate

_PULL_LIST = TypeAdapter(list[PullCandidate])


class ManualPullError(ValueError):
    """Expected correction-file validation failure."""


def load_manual_pulls(path: Path) -> list[PullCandidate]:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise ManualPullError(f"Manual pull file does not exist: {source}")
    try:
        if source.suffix.casefold() == ".csv":
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                rows: Any = list(csv.DictReader(handle))
        else:
            raw: Any = json.loads(source.read_text(encoding="utf-8"))
            rows = raw.get("pulls", raw) if isinstance(raw, dict) else raw
        return _PULL_LIST.validate_python(rows)
    except (OSError, json.JSONDecodeError, ValidationError, csv.Error) as exc:
        raise ManualPullError(f"Invalid manual pull file {source}: {exc}") from exc
