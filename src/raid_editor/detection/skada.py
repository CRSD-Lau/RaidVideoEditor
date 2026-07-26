"""Safe, non-executing parser for legacy SkadaStorage boss segments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TOP_SET = re.compile(r"^\s*(?:\{|(?P<index>\[\d+\])\s*=\s*\{)\s*$")
_SCALAR = re.compile(r'^\s*\["(?P<key>[A-Za-z0-9_]+)"\]\s*=\s*(?P<value>.+?),\s*$')


@dataclass(frozen=True, slots=True)
class SkadaSegment:
    start_epoch: int
    end_epoch: int
    mob_name: str
    success: bool | None
    raid_type: str | None

    @property
    def duration_seconds(self) -> int:
        return self.end_epoch - self.start_epoch


def _brace_delta(line: str) -> int:
    """Count structural braces while ignoring braces inside quoted strings."""

    in_string = False
    escaped = False
    delta = 0
    for character in line:
        if escaped:
            escaped = False
            continue
        if character == "\\" and in_string:
            escaped = True
            continue
        if character == '"':
            in_string = not in_string
        elif not in_string and character == "{":
            delta += 1
        elif not in_string and character == "}":
            delta -= 1
    return delta


def _parse_scalar(value: str) -> str | int | bool | None:
    stripped = value.strip()
    if stripped == "true":
        return True
    if stripped == "false":
        return False
    if stripped == "nil":
        return None
    if stripped.startswith('"') and stripped.endswith('"'):
        return stripped[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    try:
        return int(stripped)
    except ValueError:
        return stripped


def parse_skada_storage(path: Path) -> list[SkadaSegment]:
    """Read only top-level scalar metadata; nested actor data is never interpreted."""

    source = path.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Skada export does not exist: {source}")
    depth = 0
    in_set = False
    aggregate = False
    fields: dict[str, str | int | bool | None] = {}
    segments: list[SkadaSegment] = []

    def finish() -> None:
        if aggregate:
            return
        start = fields.get("starttime")
        end = fields.get("endtime")
        mob = fields.get("mobname")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and end > start
            and isinstance(mob, str)
            and mob.strip()
        ):
            success = fields.get("success")
            raid_type = fields.get("type")
            segments.append(
                SkadaSegment(
                    start_epoch=start,
                    end_epoch=end,
                    mob_name=mob.strip(),
                    success=success if isinstance(success, bool) else None,
                    raid_type=raid_type if isinstance(raid_type, str) else None,
                )
            )

    with source.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if depth == 1 and not in_set:
                match = _TOP_SET.match(line)
                if match:
                    in_set = True
                    aggregate = match.group("index") == "[0]"
                    fields = {}
            if in_set and depth == 2:
                scalar = _SCALAR.match(line)
                if scalar:
                    fields[scalar.group("key")] = _parse_scalar(scalar.group("value"))
            depth += _brace_delta(line)
            if in_set and depth == 1:
                finish()
                in_set = False
                aggregate = False
                fields = {}
    return sorted(segments, key=lambda item: (item.start_epoch, item.end_epoch))
