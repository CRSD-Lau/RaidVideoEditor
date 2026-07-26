"""Safe path, hashing, and atomic serialization helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _SLUG_PATTERN.sub("-", value.casefold()).strip("-")
    return slug or "raid-project"


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def assert_within(path: Path, root: Path) -> Path:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Refusing path outside managed root {resolved_root}: {resolved_path}")
    return resolved_path


def atomic_write_text(path: Path, text: str) -> None:
    ensure_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def quick_file_fingerprint(path: Path, chunk_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
    """Hash bounded head/tail chunks plus stable file metadata.

    This avoids reading a multi-hour recording in full during every command while
    still detecting ordinary source replacement or modification.
    """

    resolved = path.resolve()
    stat = resolved.stat()
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        digest.update(handle.read(chunk_bytes))
        if stat.st_size > chunk_bytes:
            handle.seek(max(0, stat.st_size - chunk_bytes))
            digest.update(handle.read(chunk_bytes))
    return {
        "path": str(resolved),
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "head_tail_sha256": digest.hexdigest(),
        "sample_bytes_per_end": chunk_bytes,
    }


def full_file_sha256(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()
