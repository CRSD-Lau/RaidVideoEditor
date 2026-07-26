"""Pydantic models for project configuration."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects misspelled configuration keys."""

    model_config = ConfigDict(extra="forbid")


class ProjectMetadata(StrictModel):
    name: str = Field(min_length=1)
    game: str = "World of Warcraft"
    expansion: str | None = None
    raid: str | None = None
    raid_date: date | None = None


class InputConfig(StrictModel):
    recording: Path
    combat_log: Path | None = None
    details_export: Path | None = None
    skada_export: Path | None = None
    manual_pulls: Path | None = None


class AudioConfig(StrictModel):
    """Audio references use absolute FFprobe stream indexes, not audio ordinals."""

    microphone_track: int | None = Field(default=None, ge=0)
    game_track: int | None = Field(default=None, ge=0)
    discord_track: int | None = Field(default=None, ge=0)
    mixed_track: int | None = Field(default=None, ge=0)
    keep_game_audio: bool = True
    keep_discord_audio: bool = True
    remove_microphone: bool = True

    @model_validator(mode="after")
    def microphone_must_not_be_retained_role(self) -> AudioConfig:
        if not self.remove_microphone or self.microphone_track is None:
            return self
        retained = [
            track
            for track, enabled in (
                (self.game_track, self.keep_game_audio),
                (self.discord_track, self.keep_discord_audio),
            )
            if enabled and track is not None
        ]
        if self.microphone_track in retained:
            raise ValueError("microphone_track cannot also be a retained game/Discord track")
        return self

    def retained_stream_indexes(self) -> list[int]:
        selected: list[int] = []
        if self.keep_game_audio and self.game_track is not None:
            selected.append(self.game_track)
        if self.keep_discord_audio and self.discord_track is not None:
            selected.append(self.discord_track)
        if not selected and self.mixed_track is not None and not self.remove_microphone:
            selected.append(self.mixed_track)
        return list(dict.fromkeys(selected))


class DetectionConfig(StrictModel):
    minimum_pull_seconds: float = Field(default=15.0, gt=0)
    merge_gap_seconds: float = Field(default=8.0, ge=0)
    pre_roll_seconds: float = Field(default=5.0, ge=0)
    post_roll_seconds: float = Field(default=8.0, ge=0)
    confidence_threshold: float = Field(default=0.70, ge=0, le=1)
    combat_log_offset_seconds: float = 0.0
    recording_started_at: datetime | None = None


class EditingConfig(StrictModel):
    include_trash_pulls: bool = True
    include_boss_wipes: bool = True
    include_boss_kills: bool = True
    include_run_backs: bool = False
    include_loot: bool = True
    transition_duration_seconds: float = Field(default=0.4, ge=0, le=5)


class MusicConfig(StrictModel):
    library: Path
    approved_track_ids: list[str] = Field(default_factory=list)


class PreviewConfig(StrictModel):
    resolution: str = "1280x720"
    fps: int = Field(default=30, gt=0, le=120)
    bitrate: str = "4M"
    hardware_encoding: bool = False

    @field_validator("resolution")
    @classmethod
    def resolution_must_be_dimensions(cls, value: str) -> str:
        parts = value.lower().split("x")
        if len(parts) != 2 or not all(part.isdigit() and int(part) > 0 for part in parts):
            raise ValueError("resolution must use WIDTHxHEIGHT, for example 1280x720")
        return value.lower()


class FinalConfig(StrictModel):
    resolution: str = "source"
    fps: str | int = "source"
    codec: str = "h264"
    hardware_encoding: bool = True


class ProjectConfig(StrictModel):
    project: ProjectMetadata
    input: InputConfig
    audio: AudioConfig
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    editing: EditingConfig = Field(default_factory=EditingConfig)
    music: MusicConfig
    preview: PreviewConfig = Field(default_factory=PreviewConfig)
    final: FinalConfig = Field(default_factory=FinalConfig)
