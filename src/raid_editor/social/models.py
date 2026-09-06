"""Strict models for deterministic social packages and publication evidence."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SocialPlatform = Literal["facebook", "instagram", "tiktok", "twitch"]
ContentPillar = Literal[
    "guild_milestone",
    "intensity",
    "comedy",
    "reaction",
    "raid_comms",
    "loot",
    "personality",
    "other",
]
PublicationStatus = Literal[
    "prepared",
    "reviewed",
    "awaiting_confirmation",
    "uploaded",
    "processing",
    "scheduled",
    "published",
    "verified_public",
    "failed",
    "blocked_account",
    "blocked_capability",
    "remote_state_uncertain",
    "waived_by_neil",
]


class StrictModel(BaseModel):
    """Reject unknown fields so a typo cannot weaken a publication gate."""

    model_config = ConfigDict(extra="forbid")


class SocialMediaDetails(StrictModel):
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_rate: float = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    video_codec: str
    pixel_format: str | None = None
    audio_codec: str
    audio_sample_rate: int | None = Field(default=None, gt=0)
    audio_channels: int | None = Field(default=None, gt=0)
    provenance: Literal[
        "raid_editor_render",
        "youtube_takeout_owner_export",
        "youtube_studio_owner_download",
        "manual_owned_import",
    ]
    quality_class: Literal["canonical_master", "owner_recovered_derivative"]
    quality_exception: str | None = None

    @model_validator(mode="after")
    def portrait_media_and_exception_are_consistent(self) -> SocialMediaDetails:
        if self.width >= self.height:
            raise ValueError("social media must be portrait")
        if self.quality_class == "owner_recovered_derivative" and not self.quality_exception:
            raise ValueError("a recovered derivative requires an explicit quality_exception")
        if self.quality_class == "canonical_master" and self.quality_exception:
            raise ValueError("a canonical master cannot carry a quality_exception")
        return self


class SocialAudioPolicy(StrictModel):
    game: bool = True
    discord: bool = True
    microphone: bool = True
    added_music: bool = False
    reviewed: bool = True


class SocialAsset(StrictModel):
    content_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    content_pillar: ContentPillar = "other"
    source_highlight_id: str | None = None
    youtube_video_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{11}$")
    youtube_url: str | None = None
    media: SocialMediaDetails
    audio_policy: SocialAudioPolicy = Field(default_factory=SocialAudioPolicy)
    approved: bool = True

    @model_validator(mode="after")
    def youtube_identity_is_complete(self) -> SocialAsset:
        if (self.youtube_video_id is None) != (self.youtube_url is None):
            raise ValueError("youtube_video_id and youtube_url must be supplied together")
        return self


class SocialPost(StrictModel):
    content_id: str
    platform: SocialPlatform
    content_type: Literal["reel", "short_video", "upload"]
    target_handle: str = Field(min_length=1, max_length=120)
    target_account_id: str | None = Field(default=None, max_length=200)
    media_path: Path
    media_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=160)
    caption: str = Field(max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=5)
    cover_frame_seconds: float = Field(default=1.0, ge=0)
    visibility: Literal["public"] = "public"
    comments_enabled: bool = True
    remix_enabled: bool = False
    duet_enabled: bool = False
    stitch_enabled: bool = False
    download_enabled: bool = False
    scheduled_for: datetime
    status: PublicationStatus = "prepared"
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    copy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    remote_content_id: str | None = None
    public_url: str | None = None
    published_at: datetime | None = None
    verified_at: datetime | None = None
    verification_checks: dict[str, bool | str | float | int | None] = Field(default_factory=dict)
    last_error: str | None = None

    @field_validator("hashtags")
    @classmethod
    def hashtags_are_compact(cls, value: list[str]) -> list[str]:
        if any(not item.startswith("#") or any(char.isspace() for char in item) for item in value):
            raise ValueError("hashtags must start with # and contain no spaces")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def public_states_require_remote_identity(self) -> SocialPost:
        if self.status in {"published", "verified_public"} and (
            not self.remote_content_id or not self.public_url
        ):
            raise ValueError("published posts require a remote_content_id and public_url")
        if self.status == "verified_public" and self.verified_at is None:
            raise ValueError("verified_public posts require verified_at")
        return self


class DistributionManifest(StrictModel):
    schema_version: int = 1
    campaign_id: str = Field(min_length=1, max_length=160)
    created_at: datetime
    package_version: str = "1"
    release_timezone: str = "America/Halifax"
    cadence_days: int = Field(default=2, ge=1, le=30)
    assets: list[SocialAsset] = Field(min_length=1)
    posts: list[SocialPost] = Field(min_length=1)
    review_approved: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_and_keys_are_unique(self) -> DistributionManifest:
        asset_ids = [asset.content_id for asset in self.assets]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("asset content_id values must be unique")
        known = set(asset_ids)
        pairs: set[tuple[str, str]] = set()
        keys: set[str] = set()
        for post in self.posts:
            if post.content_id not in known:
                raise ValueError(f"post references unknown content_id {post.content_id}")
            pair = (post.platform, post.content_id)
            if pair in pairs:
                raise ValueError(f"duplicate platform/content post: {pair}")
            if post.idempotency_key in keys:
                raise ValueError("post idempotency_key values must be unique")
            pairs.add(pair)
            keys.add(post.idempotency_key)
        return self


class SocialAnalyticsSnapshot(StrictModel):
    platform: SocialPlatform
    content_id: str
    label: str = Field(min_length=1, max_length=40)
    captured_at: datetime
    metrics: dict[str, int | float | str | None]
    normalized: dict[str, float | None] = Field(default_factory=dict)
    source: Literal["manual_platform_export", "manual_studio_entry", "official_api"]
    notes: list[str] = Field(default_factory=list)


def json_payload(model: BaseModel) -> dict[str, Any]:
    """Return a JSON-compatible model payload for atomic serialization."""

    return model.model_dump(mode="json")
