"""Pydantic models for project configuration."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

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


class DifficultyConfig(StrictModel):
    """Per-winning-pull difficulty classification and scoreline rules."""

    enabled: bool = True
    raid_size: Literal[10, 25] | None = None
    expected_bosses: int | None = Field(default=None, gt=0, le=100)
    title_raid_abbreviation: str | None = Field(default=None, min_length=1, max_length=20)
    require_confirmed_for_auto_title: bool = True


class HighlightConfig(StrictModel):
    """Review-first funny, reaction, movement, and intensity candidate generation."""

    enabled: bool = True
    manual_selection: Path | None = None
    review_clip_seconds: float = Field(default=45.0, gt=5, le=180)
    lead_in_seconds: float = Field(default=25.0, ge=0, le=120)
    lead_out_seconds: float = Field(default=15.0, ge=0, le=120)
    maximum_candidates: int = Field(default=12, ge=1, le=50)
    minimum_spacing_seconds: float = Field(default=30.0, ge=0, le=600)
    fusion_window_seconds: float = Field(default=8.0, gt=0, le=60)
    minimum_score: float = Field(default=0.30, ge=0, le=1)
    discord_rms_threshold_db: float = Field(default=-27.0, ge=-100, le=0)
    game_rms_threshold_db: float = Field(default=-20.0, ge=-100, le=0)
    motion_scene_threshold: float = Field(default=0.12, ge=0.001, le=1)
    motion_sample_fps: float = Field(default=2.0, gt=0, le=10)
    motion_keyframes_only: bool = True
    include_kill_climaxes: bool = True
    keep_game_audio: bool = True
    keep_discord_audio: bool = True
    keep_microphone_audio: bool = False
    vertical_resolution: str = "1080x1920"
    hardware_encoding: bool = True

    @field_validator("vertical_resolution")
    @classmethod
    def vertical_resolution_must_be_dimensions(cls, value: str) -> str:
        parts = value.lower().split("x")
        if len(parts) != 2 or not all(part.isdigit() and int(part) > 0 for part in parts):
            raise ValueError("vertical_resolution must use WIDTHxHEIGHT")
        if int(parts[0]) >= int(parts[1]):
            raise ValueError("vertical_resolution must be portrait")
        return value.lower()


class PreflightConfig(StrictModel):
    """Expected OBS and recording conditions for the Friday smoke check."""

    enabled: bool = True
    obs_profile_dir: str | None = "WoW_Raid_1440p60"
    obs_scene_collection_file: str | None = "WoW_Raid_Recording.json"
    expected_scene: str | None = "WoW Raid"
    expected_resolution: str = "2560x1440"
    expected_fps: int = Field(default=60, gt=0, le=120)
    minimum_free_space_gib: float = Field(default=150.0, ge=1)
    combat_log_max_age_minutes: float = Field(default=30.0, gt=0)
    require_fresh_combat_log: bool = True
    smoke_recording_max_age_minutes: float = Field(default=30.0, gt=0)
    smoke_recording_min_seconds: float = Field(default=5.0, gt=0, le=120)
    smoke_recording_max_seconds: float = Field(default=120.0, gt=5, le=600)
    required_recording_tracks: dict[int, str] = Field(
        default_factory=lambda: {
            1: "Full Mix",
            2: "WoW Game",
            3: "Discord",
            4: "Microphone",
        }
    )
    required_source_tracks: dict[str, list[int]] = Field(
        default_factory=lambda: {
            "WoW Audio": [1, 2],
            "Discord Audio": [1, 3],
            "Mic/Aux": [1, 4],
        }
    )
    required_scene_sources: list[str] = Field(
        default_factory=lambda: ["WoW", "WebCam", "WebCam Border"]
    )

    @field_validator("expected_resolution")
    @classmethod
    def expected_resolution_must_be_dimensions(cls, value: str) -> str:
        parts = value.lower().split("x")
        if len(parts) != 2 or not all(part.isdigit() and int(part) > 0 for part in parts):
            raise ValueError("expected_resolution must use WIDTHxHEIGHT")
        return value.lower()

    @field_validator("required_recording_tracks")
    @classmethod
    def recording_tracks_must_be_obs_track_numbers(cls, value: dict[int, str]) -> dict[int, str]:
        if any(track < 1 or track > 6 for track in value):
            raise ValueError("required recording track numbers must be between 1 and 6")
        return value

    @field_validator("required_source_tracks")
    @classmethod
    def source_tracks_must_be_obs_track_numbers(
        cls, value: dict[str, list[int]]
    ) -> dict[str, list[int]]:
        if any(track < 1 or track > 6 for tracks in value.values() for track in tracks):
            raise ValueError("required source track numbers must be between 1 and 6")
        return value

    @model_validator(mode="after")
    def smoke_duration_range_must_be_ordered(self) -> PreflightConfig:
        if self.smoke_recording_max_seconds <= self.smoke_recording_min_seconds:
            raise ValueError("smoke_recording_max_seconds must exceed the minimum")
        return self


class ArchiveConfig(StrictModel):
    """Copy-only verified archive settings. Source deletion is never supported."""

    enabled: bool = False
    destination: Path | None = None
    include_raw_recording: bool = True
    include_final_master: bool = True
    include_project_artifacts: bool = True
    require_public_1440p_verified: bool = True


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


class WatermarkConfig(StrictModel):
    image: Path
    x_fraction: float = Field(default=0.0, ge=0, le=1)
    y_fraction: float = Field(default=0.0, ge=0, le=1)
    width_fraction: float = Field(gt=0, le=1)
    height_fraction: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def must_fit_inside_frame(self) -> WatermarkConfig:
        if self.x_fraction + self.width_fraction > 1:
            raise ValueError("watermark x_fraction + width_fraction must not exceed 1")
        if self.y_fraction + self.height_fraction > 1:
            raise ValueError("watermark y_fraction + height_fraction must not exceed 1")
        return self


class PresentationConfig(StrictModel):
    intro_seconds: float = Field(default=5.0, ge=0, le=15)
    outro_seconds: float = Field(default=5.0, ge=0, le=15)
    intro_title: str = "PIZZA WARRIORS"
    intro_subtitle: str = "ICECROWN CITADEL"
    outro_title: str = "RAID COMPLETE"
    outro_subtitle: str = "PIZZA WARRIORS"
    boss_kicker: str = "PIZZA WARRIORS"


class PreviewConfig(StrictModel):
    resolution: str = "1280x720"
    fps: int = Field(default=30, gt=0, le=120)
    bitrate: str = "4M"
    hardware_encoding: bool = False
    review_clip_mode: Literal["sample", "full"] = "sample"
    review_clip_seconds: float = Field(default=10.0, gt=0, le=600)
    watermark: WatermarkConfig | None = None
    presentation: PresentationConfig | None = None

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
    constant_qp: int = Field(default=18, ge=0, le=51)
    preset: str = "p6"
    audio_bitrate: str = "320k"

    @field_validator("resolution")
    @classmethod
    def resolution_must_be_source_or_dimensions(cls, value: str) -> str:
        if value.casefold() == "source":
            return "source"
        parts = value.lower().split("x")
        if len(parts) != 2 or not all(part.isdigit() and int(part) > 0 for part in parts):
            raise ValueError("final resolution must be source or WIDTHxHEIGHT")
        return value.lower()

    @field_validator("fps")
    @classmethod
    def fps_must_be_source_or_positive(cls, value: str | int) -> str | int:
        if isinstance(value, str):
            if value.casefold() != "source":
                raise ValueError("final fps must be source or a positive integer")
            return "source"
        if value <= 0 or value > 120:
            raise ValueError("final fps must be source or a positive integer up to 120")
        return value


class YouTubeConfig(StrictModel):
    enabled: bool = False
    client_secrets: Path | None = None
    token: Path | None = None
    management_token: Path | None = None
    analytics_token: Path | None = None
    privacy_status: Literal["private", "unlisted", "public"] = "private"
    category_id: str = "20"
    category_name: str = Field(default="Gaming", max_length=100)
    game_title: str | None = Field(default=None, max_length=150)
    game_rating: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list, max_length=3)
    default_language: str = Field(default="en", min_length=2, max_length=20)
    made_for_kids: bool = False
    age_restricted: bool = False
    contains_synthetic_media: bool = False
    license: Literal["youtube", "creativeCommon"] = "youtube"
    allow_embedding: bool = True
    notify_subscribers: bool = True
    api_project_verified_for_public: bool = False
    forbid_em_dash: bool = True
    chunk_size_mib: int = Field(default=16, ge=1, le=256)
    thumbnail_variants: int = Field(default=3, ge=1, le=3)
    selected_thumbnail_variant: int = Field(default=1, ge=1, le=3)
    playlist_auto_add: bool = True
    playlist_id: str | None = Field(default=None, min_length=1, max_length=100)
    playlist_title: str | None = Field(default="Pizza Warriors Weekly ICC Clears", max_length=150)
    playlist_privacy_status: Literal["private", "unlisted", "public"] = "public"
    analytics_enabled: bool = True

    @field_validator("hashtags")
    @classmethod
    def hashtags_must_be_compact_and_prefixed(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for hashtag in value:
            compact = hashtag.strip()
            if not compact.startswith("#") or any(character.isspace() for character in compact):
                raise ValueError("YouTube hashtags must start with # and contain no spaces")
            normalized.append(compact)
        return normalized

    @model_validator(mode="after")
    def validate_youtube_workflow(self) -> YouTubeConfig:
        if self.enabled and (self.client_secrets is None or self.token is None):
            raise ValueError("enabled YouTube uploads require client_secrets and token paths")
        if self.game_title is not None and self.category_id != "20":
            raise ValueError("a YouTube game title requires the Gaming category ID 20")
        if self.selected_thumbnail_variant > self.thumbnail_variants:
            raise ValueError("selected_thumbnail_variant exceeds thumbnail_variants")
        if self.forbid_em_dash:
            copy_values = [
                self.title,
                self.description,
                self.category_name,
                self.game_title,
                self.game_rating,
                *self.tags,
                *self.hashtags,
            ]
            if any(value is not None and "—" in value for value in copy_values):
                raise ValueError("YouTube copy must not contain an em dash")
        return self


class ProjectConfig(StrictModel):
    project: ProjectMetadata
    input: InputConfig
    audio: AudioConfig
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    difficulty: DifficultyConfig = Field(default_factory=DifficultyConfig)
    highlights: HighlightConfig = Field(default_factory=HighlightConfig)
    preflight: PreflightConfig = Field(default_factory=PreflightConfig)
    editing: EditingConfig = Field(default_factory=EditingConfig)
    music: MusicConfig
    preview: PreviewConfig = Field(default_factory=PreviewConfig)
    final: FinalConfig = Field(default_factory=FinalConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    archive: ArchiveConfig = Field(default_factory=ArchiveConfig)
