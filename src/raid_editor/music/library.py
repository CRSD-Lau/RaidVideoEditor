"""Strict local music licence registry and deterministic MVP planning."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from raid_editor.util.paths import atomic_write_text, full_file_sha256


class MusicLibraryError(ValueError):
    """Expected missing or incomplete music licence metadata."""


class MusicTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    artist: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_page: HttpUrl
    licence: str = Field(min_length=1)
    licence_version: str | None = None
    attribution_required: bool
    date_obtained: date
    local_file: Path
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    youtube_use_permitted: bool
    monetized_youtube_permitted: bool
    derivative_synchronization_permitted: bool
    required_description_text: str = ""
    tags: list[str] = Field(default_factory=list)
    tempo_bpm: float | None = Field(default=None, gt=0)
    energy: float | None = Field(default=None, ge=0, le=1)
    integrated_loudness_lufs: float | None = None
    instrumental: bool | None = None
    duration_seconds: float | None = Field(default=None, gt=0)


class MusicLibrary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    tracks: list[MusicTrack] = Field(default_factory=list)


def load_music_library(path: Path) -> MusicLibrary:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise MusicLibraryError(f"Music library does not exist: {source}")
    try:
        library = MusicLibrary.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        raise MusicLibraryError(f"Invalid music library {source}: {exc}") from exc
    base = source.parent
    normalized: list[MusicTrack] = []
    for track in library.tracks:
        local_file = (
            track.local_file if track.local_file.is_absolute() else (base / track.local_file)
        )
        normalized.append(track.model_copy(update={"local_file": local_file.resolve()}))
    return library.model_copy(update={"tracks": normalized})


def approved_tracks(library: MusicLibrary, approved_ids: list[str]) -> list[MusicTrack]:
    by_id = {track.id: track for track in library.tracks}
    missing = [track_id for track_id in approved_ids if track_id not in by_id]
    if missing:
        raise MusicLibraryError(f"Approved music IDs are absent from the library: {missing}")
    selected = [by_id[track_id] for track_id in approved_ids]
    for track in selected:
        if not (
            track.youtube_use_permitted
            and track.monetized_youtube_permitted
            and track.derivative_synchronization_permitted
        ):
            raise MusicLibraryError(
                f"Track {track.id!r} lacks explicit YouTube/monetization/synchronization permission"
            )
        if not track.local_file.is_file():
            raise MusicLibraryError(f"Track {track.id!r} file is missing: {track.local_file}")
        actual_hash = full_file_sha256(track.local_file)
        if actual_hash.casefold() != track.sha256.casefold():
            raise MusicLibraryError(f"Track {track.id!r} hash does not match its licence record")
        if track.attribution_required and not track.required_description_text.strip():
            raise MusicLibraryError(f"Track {track.id!r} requires attribution text")
    return selected


def write_music_reports(
    selected: list[MusicTrack],
    licence_report: Path,
    attribution_file: Path,
) -> None:
    report = ["# Music Licence Report", ""]
    if not selected:
        report.append("No music is approved or used in this review.")
    for track in selected:
        report.extend(
            [
                f"## {track.title} — {track.artist}",
                "",
                f"- ID: `{track.id}`",
                f"- Source: {track.source}",
                f"- Source page: {track.source_page}",
                f"- Licence: {track.licence} {track.licence_version or ''}".rstrip(),
                f"- Date obtained: {track.date_obtained.isoformat()}",
                f"- Local SHA-256: `{track.sha256}`",
                f"- Monetized YouTube use: {'yes' if track.monetized_youtube_permitted else 'no'}",
                "- Derivative synchronization: "
                + ("permitted" if track.derivative_synchronization_permitted else "not permitted"),
                "",
            ]
        )
    atomic_write_text(licence_report, "\n".join(report).rstrip() + "\n")
    attribution = "\n\n".join(
        track.required_description_text.strip()
        for track in selected
        if track.attribution_required and track.required_description_text.strip()
    )
    atomic_write_text(attribution_file, attribution + ("\n" if attribution else ""))


def write_music_plan(selected: list[MusicTrack], destination: Path) -> None:
    lines = ["# Music Plan", ""]
    if not selected:
        lines.append(
            "No music will be applied. Add only locally stored tracks with complete licence "
            "metadata, then explicitly list their IDs in `music.approved_track_ids`."
        )
    else:
        lines.append(
            "MVP policy: use the first approved track as a low-level continuous bed, looped only "
            "for the review render, with fades and game/Discord mixed above it."
        )
        for number, track in enumerate(selected, start=1):
            lines.append(f"{number}. {track.title} — {track.artist} ({', '.join(track.tags)})")
    atomic_write_text(destination, "\n".join(lines) + "\n")
