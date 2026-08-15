"""Copy-only, hash-verified raid archives with no deletion capability."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from raid_editor.config.models import ProjectConfig
from raid_editor.util.paths import atomic_write_json, atomic_write_text, ensure_directory


class ArchiveError(RuntimeError):
    """Expected archive planning, approval, or verification failure."""


@dataclass(frozen=True, slots=True)
class ArchiveItem:
    """Describe one immutable source file and its relative archive destination."""

    source: Path
    relative_destination: Path
    size_bytes: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _public_1440p_verified(project_root: Path) -> bool:
    manifest = project_root / "youtube" / "upload-manifest.json"
    if not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    quality = str(payload.get("maximum_quality_confirmed", "")).casefold()
    return (
        payload.get("privacy_status") == "public"
        and payload.get("public_playback_confirmed") is True
        and quality.startswith("1440p")
    )


def build_archive_items(
    config: ProjectConfig,
    *,
    config_path: Path,
    project_root: Path,
) -> list[ArchiveItem]:
    """Build the deterministic copy list for one project archive.

    Args:
        config: Validated project and archive settings.
        config_path: Local YAML file to preserve with project artifacts.
        project_root: Generated project output directory.

    Returns:
        Unique archive items in deterministic source order; an empty list when
        no configured source exists.
    """

    items: list[ArchiveItem] = []

    def add(source: Path, relative: Path) -> None:
        if source.is_file():
            items.append(ArchiveItem(source.resolve(), relative, source.stat().st_size))

    if config.archive.include_raw_recording:
        add(config.input.recording, Path("raw") / config.input.recording.name)
    if config.archive.include_final_master:
        for final in sorted((project_root / "final").glob("*.mp4")):
            add(final, Path("final") / final.name)
        for manifest in sorted((project_root / "final").glob("*.manifest.json")):
            add(manifest, Path("final") / manifest.name)
    if config.archive.include_project_artifacts:
        add(config_path, Path("project") / "config" / config_path.name)
        if config.input.manual_pulls is not None:
            add(
                config.input.manual_pulls,
                Path("project") / "config" / config.input.manual_pulls.name,
            )
        if config.highlights.manual_selection is not None:
            add(
                config.highlights.manual_selection,
                Path("project") / "config" / config.highlights.manual_selection.name,
            )
        for folder_name in ("analysis", "reports", "timeline", "youtube"):
            folder = project_root / folder_name
            for source in sorted(path for path in folder.rglob("*") if path.is_file()):
                add(source, Path("project") / source.relative_to(project_root))
        vertical = project_root / "highlights" / "vertical"
        for source in sorted(path for path in vertical.rglob("*") if path.is_file()):
            add(source, Path("project") / source.relative_to(project_root))
    unique: dict[Path, ArchiveItem] = {}
    for item in items:
        unique.setdefault(item.relative_destination, item)
    return list(unique.values())


def write_archive_plan(
    config: ProjectConfig,
    *,
    config_path: Path,
    project_root: Path,
    json_destination: Path,
    markdown_destination: Path,
) -> list[ArchiveItem]:
    """Write reviewable JSON and Markdown plans without copying any files.

    Args:
        config: Validated project and archive settings.
        config_path: Local YAML file considered by the plan.
        project_root: Generated project output directory.
        json_destination: Machine-readable plan destination.
        markdown_destination: Human-readable plan destination.

    Returns:
        The same archive items serialized into both reports.

    Raises:
        OSError: If a source cannot be inspected or a report cannot be written.
    """

    items = build_archive_items(config, config_path=config_path, project_root=project_root)
    total = sum(item.size_bytes for item in items)
    payload = {
        "copy_only": True,
        "source_deletion_supported": False,
        "destination": str(config.archive.destination) if config.archive.destination else None,
        "public_1440p_verified": _public_1440p_verified(project_root),
        "total_bytes": total,
        "items": [
            {
                "source": str(item.source),
                "relative_destination": item.relative_destination.as_posix(),
                "size_bytes": item.size_bytes,
            }
            for item in items
        ],
    }
    atomic_write_json(json_destination, payload)
    lines = [
        "# Archive Plan",
        "",
        "This is a copy-only plan. The editor has no source deletion command.",
        "",
        f"- Destination: {config.archive.destination or 'not configured'}",
        f"- Public 1440p verified: {'yes' if payload['public_1440p_verified'] else 'no'}",
        f"- Files: {len(items)}",
        f"- Total: {total / (1024**3):.2f} GiB",
        "",
        "| Source | Archive path | Size |",
        "|---|---|---:|",
        *[
            f"| {item.source} | {item.relative_destination.as_posix()} | "
            f"{item.size_bytes / (1024**2):.1f} MiB |"
            for item in items
        ],
    ]
    atomic_write_text(markdown_destination, "\n".join(lines) + "\n")
    return items


def create_verified_archive(
    config: ProjectConfig,
    *,
    config_path: Path,
    project_root: Path,
    approved: bool,
) -> Path:
    """Copy an approved archive and verify every destination with SHA-256.

    Args:
        config: Validated project and archive settings.
        config_path: Local YAML file to include with project artifacts.
        project_root: Generated project output directory.
        approved: Explicit operator approval for the copy operation.

    Returns:
        Final verified archive directory.

    Raises:
        ArchiveError: If approval, configuration, space, destination safety, or
            a source/destination hash check fails.
        OSError: If an underlying filesystem copy or rename fails.
    """

    if not approved:
        raise ArchiveError("Archive copying requires explicit approval")
    if not config.archive.enabled:
        raise ArchiveError("Archiving is disabled in the project configuration")
    if config.archive.destination is None:
        raise ArchiveError("archive.destination is not configured")
    project_resolved = project_root.resolve()
    archive_resolved = config.archive.destination.resolve()
    if archive_resolved == project_resolved or archive_resolved.is_relative_to(project_resolved):
        raise ArchiveError("Archive destination must be outside the live project output")
    if config.archive.require_public_1440p_verified and not _public_1440p_verified(project_root):
        raise ArchiveError("Public playback and 1440p verification are required before archiving")
    destination_root = config.archive.destination / project_root.name
    if destination_root.exists():
        raise ArchiveError(
            f"Archive destination already exists; refusing to overwrite: {destination_root}"
        )
    items = build_archive_items(config, config_path=config_path, project_root=project_root)
    if not items:
        raise ArchiveError("Archive plan contains no files")
    total = sum(item.size_bytes for item in items)
    ensure_directory(config.archive.destination)
    free = shutil.disk_usage(config.archive.destination).free
    if free < total * 1.1:
        raise ArchiveError(f"Insufficient archive space: need {total * 1.1 / (1024**3):.1f} GiB")
    staging_root = config.archive.destination / (
        f".{project_root.name}.partial-{uuid.uuid4().hex[:8]}"
    )
    ensure_directory(staging_root)
    rows: list[dict[str, object]] = []
    for item in items:
        target = staging_root / item.relative_destination
        ensure_directory(target.parent)
        if target.exists():
            raise ArchiveError(f"Refusing to overwrite archive file: {target}")
        shutil.copy2(item.source, target)
        source_sha256 = _sha256(item.source)
        destination_sha256 = _sha256(target)
        if source_sha256 != destination_sha256:
            raise ArchiveError(f"Archive hash verification failed: {target}")
        rows.append(
            {
                "source": str(item.source),
                "destination": str((destination_root / item.relative_destination).resolve()),
                "size_bytes": item.size_bytes,
                "sha256": source_sha256,
            }
        )
    atomic_write_json(
        staging_root / "archive-manifest.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "verified": True,
            "copy_only": True,
            "source_deleted": False,
            "items": rows,
        },
    )
    staging_root.rename(destination_root)
    return destination_root
