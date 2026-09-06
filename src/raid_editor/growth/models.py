"""Strict, platform-neutral models for the Pizza Warriors growth workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ContentLane = Literal["archive", "recap", "short", "boss_feature"]
SourceLineageKind = Literal[
    "landscape_authoritative_source",
    "native_portrait_source",
    "landscape_derived_portrait",
    "owner_recovered_derivative",
    "manual_owned_import",
]
ClaimKind = Literal[
    "full_clear",
    "progress",
    "overall_result",
    "heroic_count",
    "raid_size",
    "difficulty",
    "first",
    "milestone",
    "loot",
    "reward",
    "encounter",
    "recorded_range",
    "moment_description",
]
ClaimState = Literal["allowed", "rejected", "manual_review"]
AssetState = Literal[
    "planned",
    "review_required",
    "reviewed",
    "rendered",
    "upload_ready",
    "published",
    "verified_public",
    "skipped",
]
Destination = Literal[
    "youtube",
    "facebook",
    "instagram",
    "tiktok",
    "twitch",
    "discord",
]
DistributionState = Literal[
    "not_planned",
    "prepared",
    "reviewed",
    "awaiting_confirmation",
    "uploaded",
    "processing",
    "scheduled",
    "published",
    "verified_public",
    "failed",
    "blocked",
    "waived_by_neil",
]
ScheduleLock = Literal["proposed", "locked_remote", "terminal"]
RelatedTargetState = Literal[
    "not_applicable",
    "planned",
    "approved",
    "assignment_pending",
    "assigned_verified",
    "blocked_capability",
    "waived_by_neil",
]
MutationState = Literal[
    "not_applicable",
    "planned",
    "approved",
    "pending",
    "applied",
    "verified",
    "blocked_capability",
    "waived_by_neil",
]
ProductionStage = Literal[
    "preflight",
    "analysis",
    "boss_review",
    "highlight_review",
    "rendering",
    "packaging",
    "publication",
    "analytics",
    "cleanup",
]


class StrictModel(BaseModel):
    """Reject unknown fields so typos cannot silently weaken review gates."""

    model_config = ConfigDict(extra="forbid")


class SourceFingerprint(StrictModel):
    algorithm: Literal["sha256", "head_tail_sha256"]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    modified_ns: int | None = Field(default=None, ge=0)
    sampled_bytes_per_end: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def sampled_hash_discloses_its_window(self) -> SourceFingerprint:
        if self.algorithm == "head_tail_sha256" and self.sampled_bytes_per_end is None:
            raise ValueError("head_tail_sha256 requires sampled_bytes_per_end")
        if self.algorithm == "sha256" and self.sampled_bytes_per_end is not None:
            raise ValueError("a full sha256 fingerprint cannot include a sample window")
        return self


class SourceStream(StrictModel):
    index: int = Field(ge=0)
    kind: Literal["video", "audio"]
    codec: str = Field(min_length=1, max_length=80)
    audio_ordinal: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def audio_ordinal_matches_kind(self) -> SourceStream:
        if self.kind == "audio" and self.audio_ordinal is None:
            raise ValueError("audio source streams require audio_ordinal")
        if self.kind == "video" and self.audio_ordinal is not None:
            raise ValueError("video source streams cannot have audio_ordinal")
        return self


class SourceLineage(StrictModel):
    source_id: str = Field(min_length=1, max_length=160)
    path: Path
    kind: SourceLineageKind
    fingerprint: SourceFingerprint
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    streams: list[SourceStream] = Field(default_factory=list)
    paired_source_id: str | None = Field(default=None, max_length=160)
    authoritative_for: list[Literal["archive", "portrait", "audio"]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def orientation_matches_lineage(self) -> SourceLineage:
        stream_indexes = [stream.index for stream in self.streams]
        if len(stream_indexes) != len(set(stream_indexes)):
            raise ValueError("source stream indexes must be unique")
        if self.width is None or self.height is None:
            return self
        if self.kind == "native_portrait_source" and self.width >= self.height:
            raise ValueError("native_portrait_source must be portrait")
        if self.kind == "landscape_authoritative_source" and self.width <= self.height:
            raise ValueError("landscape_authoritative_source must be landscape")
        return self


class SourceRange(StrictModel):
    source_id: str = Field(min_length=1, max_length=160)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    purpose: Literal["recorded_coverage", "edited_asset", "highlight", "recap_beat"]
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def end_follows_start(self) -> SourceRange:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("source range end_seconds must follow start_seconds")
        return self


class CoverageRecord(StrictModel):
    expected_bosses: int = Field(default=12, ge=1, le=100)
    overall_bosses_killed: int = Field(default=0, ge=0)
    recorded_bosses: list[str] = Field(default_factory=list)
    edited_bosses: list[str] = Field(default_factory=list)
    heroic_bosses: list[str] = Field(default_factory=list)
    unknown_difficulty_bosses: list[str] = Field(default_factory=list)
    winning_ending_bosses: list[str] = Field(default_factory=list)
    confirmed_raid_size: Literal[10, 25] | None = None
    recording_state: Literal[
        "full",
        "partial",
        "interrupted",
        "multi_file",
        "late_start",
        "early_stop",
        "unknown",
    ] = "unknown"
    source_ranges: list[SourceRange] = Field(default_factory=list)
    known_missing_segments: list[str] = Field(default_factory=list)
    ending_evidence_complete: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator(
        "recorded_bosses",
        "edited_bosses",
        "heroic_bosses",
        "unknown_difficulty_bosses",
        "winning_ending_bosses",
    )
    @classmethod
    def boss_names_are_unique(cls, value: list[str]) -> list[str]:
        compact = [name.strip() for name in value if name.strip()]
        if len({name.casefold() for name in compact}) != len(compact):
            raise ValueError("boss names must be unique")
        return compact

    @model_validator(mode="after")
    def counts_and_sets_are_consistent(self) -> CoverageRecord:
        if self.overall_bosses_killed > self.expected_bosses:
            raise ValueError("overall_bosses_killed exceeds expected_bosses")
        edited = {name.casefold() for name in self.edited_bosses}
        recorded = {name.casefold() for name in self.recorded_bosses}
        heroic = {name.casefold() for name in self.heroic_bosses}
        unknown = {name.casefold() for name in self.unknown_difficulty_bosses}
        endings = {name.casefold() for name in self.winning_ending_bosses}
        if not edited.issubset(recorded):
            raise ValueError("edited_bosses must be a subset of recorded_bosses")
        if not heroic.issubset(recorded):
            raise ValueError("heroic_bosses must be a subset of recorded_bosses")
        if not unknown.issubset(recorded):
            raise ValueError("unknown_difficulty_bosses must be a subset of recorded_bosses")
        if not endings.issubset(edited):
            raise ValueError("winning_ending_bosses must be a subset of edited_bosses")
        if heroic & unknown:
            raise ValueError("a boss cannot be both Heroic and unknown difficulty")
        return self


class ClaimRecord(StrictModel):
    claim_id: str = Field(min_length=1, max_length=160)
    kind: ClaimKind
    text: str = Field(min_length=1, max_length=300)
    state: ClaimState
    reason: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list)
    automatic: bool = True
    manually_approved: bool = False

    @model_validator(mode="after")
    def manual_approval_is_explicit(self) -> ClaimRecord:
        if self.automatic and self.manually_approved:
            raise ValueError("automatic claims cannot also be manually approved")
        if self.state == "allowed" and not self.automatic and not self.manually_approved:
            raise ValueError("a non-automatic allowed claim requires manual approval")
        return self


class AudioReview(StrictModel):
    game: bool = True
    discord: bool = False
    microphone: bool = False
    added_music: bool = False
    clip_level_reviewed: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def voice_audio_requires_clip_review(self) -> AudioReview:
        if (self.discord or self.microphone) and not self.clip_level_reviewed:
            raise ValueError("Discord or microphone audio requires clip-level review")
        return self


class RelatedTarget(StrictModel):
    state: RelatedTargetState = "planned"
    target_content_id: str | None = Field(default=None, max_length=160)
    target_lane: Literal["archive", "recap"] | None = None
    target_url: str | None = None
    reviewed_at: datetime | None = None
    assignment_observed_at: datetime | None = None
    assignment_receipt: str | None = Field(default=None, max_length=1000)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity_matches_state(self) -> RelatedTarget:
        if self.state not in {"not_applicable", "waived_by_neil", "blocked_capability"} and (
            not self.target_content_id or self.target_lane is None
        ):
            raise ValueError("a related target requires content identity and lane")
        if self.state in {"approved", "assignment_pending", "assigned_verified"} and (
            self.reviewed_at is None
        ):
            raise ValueError("a reviewed related target requires reviewed_at")
        if self.state in {"blocked_capability", "waived_by_neil"} and self.reviewed_at is None:
            raise ValueError("a blocked or waived related target requires reviewed_at")
        if self.state == "assigned_verified" and (
            not self.target_url
            or self.assignment_observed_at is None
            or not self.assignment_receipt
        ):
            raise ValueError(
                "an assigned related target requires target URL, observation time, and receipt"
            )
        if self.state != "assigned_verified" and (
            self.assignment_observed_at is not None or self.assignment_receipt is not None
        ):
            raise ValueError("only an observed assignment may carry assignment receipt fields")
        return self


class CampaignAsset(StrictModel):
    content_id: str = Field(min_length=1, max_length=160)
    lane: ContentLane
    title: str = Field(min_length=1, max_length=200)
    state: AssetState = "planned"
    media_path: Path | None = None
    media_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_lineage_ids: list[str] = Field(min_length=1)
    coverage: CoverageRecord
    claims: list[ClaimRecord] = Field(default_factory=list)
    audio: AudioReview = Field(default_factory=AudioReview)
    related_target: RelatedTarget | None = None
    packaging_experiment_id: str | None = Field(default=None, max_length=160)
    review_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def lane_gates_are_enforced(self) -> CampaignAsset:
        if (self.media_path is None) != (self.media_sha256 is None):
            raise ValueError("media_path and media_sha256 must be supplied together")
        if self.state in {"rendered", "upload_ready", "published", "verified_public"} and (
            self.media_path is None or self.media_sha256 is None
        ):
            raise ValueError(f"{self.state} assets require hashed media")
        if self.lane == "short":
            if self.related_target is None:
                raise ValueError("Shorts require an explicit related-video target")
            if self.state in {"upload_ready", "published"} and (
                self.related_target.state
                not in {
                    "approved",
                    "assignment_pending",
                    "assigned_verified",
                    "waived_by_neil",
                }
            ):
                raise ValueError("an upload-ready Short requires a reviewed related-video target")
            if self.state == "verified_public" and self.related_target.state not in {
                "assigned_verified",
                "waived_by_neil",
            }:
                raise ValueError(
                    "a publication-complete Short requires an observed related-video assignment"
                )
        elif self.related_target is not None and self.related_target.state != "not_applicable":
            raise ValueError("only Shorts may carry a related-video target")
        return self


class RecapDecision(StrictModel):
    decision: Literal["pending", "candidate", "hold", "skipped"] = "pending"
    coherent_story: bool | None = None
    reason: str | None = Field(default=None, max_length=1000)
    reviewed_at: datetime | None = None
    candidate_beats: list[str] = Field(default_factory=list)
    source_ranges: list[SourceRange] = Field(default_factory=list)
    audio_plan: Literal[
        "not_applicable",
        "game_only",
        "voice_candidates_require_review",
        "reviewed_voice",
    ] = "not_applicable"
    estimated_active_minutes: float | None = Field(default=None, gt=0, le=1440)

    @model_validator(mode="after")
    def reviewed_decisions_have_evidence(self) -> RecapDecision:
        if self.decision == "pending":
            if self.reviewed_at is not None:
                raise ValueError("a pending recap decision cannot be reviewed")
            return self
        if self.reviewed_at is None or not self.reason:
            raise ValueError("a recap candidate, hold, or skip requires review time and reason")
        if self.decision == "candidate" and self.coherent_story is not True:
            raise ValueError("a recap candidate requires a coherent raid story")
        if self.decision == "candidate" and (
            not self.candidate_beats
            or not self.source_ranges
            or self.audio_plan == "not_applicable"
            or self.estimated_active_minutes is None
        ):
            raise ValueError(
                "a recap candidate requires beats, source ranges, an audio plan, "
                "and effort estimate"
            )
        return self


class CampaignManifest(StrictModel):
    schema_version: int = 1
    campaign_id: str = Field(min_length=1, max_length=160)
    created_at: datetime
    updated_at: datetime
    raid_name: str = Field(min_length=1, max_length=200)
    raid_date: str | None = None
    expected_bosses: int = Field(default=12, ge=1, le=100)
    sources: list[SourceLineage] = Field(min_length=1)
    assets: list[CampaignAsset] = Field(min_length=1)
    recap: RecapDecision = Field(default_factory=RecapDecision)
    pilot_limits_enforced: bool = True
    legacy_backfill: bool = False
    remote_mutations_permitted: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_and_pilot_limits_are_consistent(self) -> CampaignManifest:
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_id values must be unique")
        asset_ids = [asset.content_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("content_id values must be unique")
        known_sources = set(source_ids)
        for asset in self.assets:
            unknown = set(asset.source_lineage_ids) - known_sources
            if unknown:
                raise ValueError(f"asset references unknown source lineage: {sorted(unknown)}")
            range_sources = {
                source_range.source_id for source_range in asset.coverage.source_ranges
            }
            unknown_ranges = range_sources - known_sources
            if unknown_ranges:
                raise ValueError(
                    f"coverage ranges reference unknown source lineage: {sorted(unknown_ranges)}"
                )
        recap_range_sources = {source_range.source_id for source_range in self.recap.source_ranges}
        unknown_recap_ranges = recap_range_sources - known_sources
        if unknown_recap_ranges:
            raise ValueError(
                f"recap ranges reference unknown source lineage: {sorted(unknown_recap_ranges)}"
            )
        short_count = sum(
            asset.lane == "short" and asset.state != "skipped" for asset in self.assets
        )
        if self.pilot_limits_enforced and not self.legacy_backfill and short_count > 2:
            raise ValueError("new raid campaigns may select at most two Shorts")
        archive_count = sum(
            asset.lane == "archive" and asset.state != "skipped" for asset in self.assets
        )
        if not self.legacy_backfill and archive_count != 1:
            raise ValueError("a raid campaign requires exactly one accurate archive asset")
        if self.remote_mutations_permitted:
            raise ValueError(
                "campaign manifests are local artifacts and cannot authorize remote mutation"
            )
        return self


class DistributionEntry(StrictModel):
    content_id: str
    lane: ContentLane
    destination: Destination
    target_handle: str | None = Field(default=None, max_length=160)
    media_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    copy_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    scheduled_for: datetime | None = None
    published_at: datetime | None = None
    status: DistributionState = "not_planned"
    public_url: str | None = None
    remote_content_id: str | None = None
    source_manifest: Path | None = None
    notify_subscribers: bool | None = None
    mutation_intents: dict[
        Literal[
            "playlist",
            "related_video",
            "end_screen",
            "card",
            "comments",
            "community_post",
            "discord_post",
        ],
        MutationState,
    ] = Field(default_factory=dict)
    named_approvals: list[str] = Field(default_factory=list)
    review_approved: bool = False
    verification_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def public_states_require_identity(self) -> DistributionEntry:
        if self.status in {"published", "verified_public"} and (
            not self.public_url or not self.remote_content_id
        ):
            raise ValueError("public distribution states require URL and remote ID")
        return self


class ScheduleEntry(StrictModel):
    schedule_id: str = Field(min_length=1, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=160)
    content_id: str = Field(min_length=1, max_length=160)
    destination: Destination
    target_handle: str | None = Field(default=None, max_length=160)
    scheduled_for: datetime
    lock: ScheduleLock
    status: DistributionState
    source_manifest: Path | None = None
    displacement_of: str | None = None
    approved: bool = False

    @model_validator(mode="after")
    def locked_entries_cannot_be_displacements(self) -> ScheduleEntry:
        if self.lock != "proposed" and self.displacement_of is not None:
            raise ValueError("only proposals may displace an existing schedule entry")
        return self


class GlobalContentCalendar(StrictModel):
    schema_version: int = 1
    generated_at: datetime
    timezone: str = "America/Halifax"
    entries: list[ScheduleEntry] = Field(default_factory=list)
    locked_remote_entries_preserved: bool = True
    displacement_proposals: list[ScheduleEntry] = Field(default_factory=list, max_length=1)
    collision_warnings: list[str] = Field(default_factory=list)
    remote_mutations_performed: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def schedule_ids_are_unique_and_local_only(self) -> GlobalContentCalendar:
        all_rows = [*self.entries, *self.displacement_proposals]
        identities = [row.schedule_id for row in all_rows]
        if len(identities) != len(set(identities)):
            raise ValueError("calendar schedule_id values must be unique")
        if self.remote_mutations_performed:
            raise ValueError("calendar generation cannot perform remote mutations")
        if any(row.lock != "proposed" for row in self.displacement_proposals):
            raise ValueError("displacement proposals must remain proposed")
        return self


class CleanupDependency(StrictModel):
    dependency_id: str = Field(min_length=1, max_length=200)
    path: Path
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    required_by: list[str] = Field(default_factory=list)
    holds: list[str] = Field(default_factory=list)
    cleanup_eligible: bool = False

    @model_validator(mode="after")
    def eligibility_matches_holds(self) -> CleanupDependency:
        if self.cleanup_eligible and self.holds:
            raise ValueError("cleanup cannot be eligible while holds remain")
        return self


class GrowthDistributionManifest(StrictModel):
    schema_version: int = 1
    campaign_id: str = Field(min_length=1, max_length=160)
    generated_at: datetime
    campaign_manifest: Path
    entries: list[DistributionEntry] = Field(default_factory=list)
    schedule: list[ScheduleEntry] = Field(default_factory=list)
    cleanup_dependencies: list[CleanupDependency] = Field(default_factory=list)
    remote_mutations_performed: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def identities_are_unique_and_remote_safe(self) -> GrowthDistributionManifest:
        entry_keys = [(entry.destination, entry.content_id) for entry in self.entries]
        if len(entry_keys) != len(set(entry_keys)):
            raise ValueError("distribution destination/content identities must be unique")
        schedule_ids = [entry.schedule_id for entry in self.schedule]
        if len(schedule_ids) != len(set(schedule_ids)):
            raise ValueError("schedule_id values must be unique")
        if self.remote_mutations_performed:
            raise ValueError("local growth packaging cannot report a remote mutation")
        return self


class PackagingExperiment(StrictModel):
    experiment_id: str = Field(min_length=1, max_length=160)
    series_id: str | None = Field(default=None, min_length=1, max_length=160)
    supersedes_experiment_id: str | None = Field(default=None, max_length=160)
    campaign_id: str = Field(min_length=1, max_length=160)
    content_id: str = Field(min_length=1, max_length=160)
    created_at: datetime
    factor: Literal["title", "thumbnail", "opening", "description", "release_time"]
    hypothesis: str = Field(min_length=1, max_length=1000)
    control: str = Field(min_length=1, max_length=1000)
    treatment: str = Field(min_length=1, max_length=1000)
    primary_metric: str = Field(min_length=1, max_length=160)
    guardrail_metrics: list[str] = Field(default_factory=list)
    minimum_runtime_hours: int = Field(default=168, ge=1)
    status: Literal["planned", "running", "completed", "cancelled"] = "planned"
    decision: Literal["adopt", "revise", "retire", "inconclusive"] | None = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def result_only_exists_after_completion(self) -> PackagingExperiment:
        if self.series_id is None:
            self.series_id = self.experiment_id
        if self.supersedes_experiment_id == self.experiment_id:
            raise ValueError("an experiment version cannot supersede itself")
        if self.status == "completed" and self.decision is None:
            raise ValueError("completed experiments require a decision")
        if self.status != "completed" and self.decision is not None:
            raise ValueError("only completed experiments may carry a decision")
        if self.control.casefold() == self.treatment.casefold():
            raise ValueError("control and treatment must be meaningfully different")
        return self


class GrowthLedgerEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=200)
    recorded_at: datetime
    campaign_id: str = Field(min_length=1, max_length=160)
    event_type: Literal[
        "campaign_prepared",
        "recap_decided",
        "claim_reviewed",
        "asset_reviewed",
        "related_target_observed",
        "experiment_recorded",
        "schedule_imported",
        "schedule_proposed",
        "distribution_observed",
        "analytics_captured",
        "production_time_recorded",
        "cleanup_evaluated",
        "correction",
    ]
    subject_id: str = Field(min_length=1, max_length=200)
    actor: str = Field(default="raid-editor", min_length=1, max_length=160)
    reason: str | None = Field(default=None, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list)
    active_operator_minutes: float | None = Field(default=None, ge=0, le=1440)
    unattended_runtime_seconds: float | None = Field(default=None, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    supersedes_event_id: str | None = None

    @model_validator(mode="after")
    def corrections_reference_prior_identity(self) -> GrowthLedgerEvent:
        if self.event_type == "correction" and self.supersedes_event_id is None:
            raise ValueError("correction events must reference the superseded event")
        if self.event_type != "correction" and self.supersedes_event_id is not None:
            raise ValueError("only correction events may supersede another event")
        return self


class ProductionTimeEntry(StrictModel):
    entry_id: str = Field(min_length=1, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=160)
    recorded_at: datetime
    stage: ProductionStage
    content_id: str | None = Field(default=None, max_length=160)
    content_lane: ContentLane | None = None
    minutes: float = Field(default=0, ge=0, le=1440)
    unattended_runtime_seconds: float = Field(default=0, ge=0, le=604800)
    source: Literal["manual", "measured_command"] = "manual"
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def some_time_is_recorded(self) -> ProductionTimeEntry:
        if self.minutes == 0 and self.unattended_runtime_seconds == 0:
            raise ValueError("record active operator time, unattended runtime, or both")
        if self.content_id is not None and self.content_lane is None:
            raise ValueError("content-specific production time requires content_lane")
        return self


class AnalyticsSnapshot(StrictModel):
    snapshot_id: str = Field(min_length=1, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=160)
    content_id: str = Field(min_length=1, max_length=160)
    destination: Destination
    content_lane: ContentLane | None = None
    cohort: str | None = Field(default=None, max_length=160)
    published_at: datetime
    captured_at: datetime
    requested_checkpoint_hours: int = Field(gt=0)
    actual_age_seconds: float = Field(ge=0)
    tolerance_seconds: float = Field(default=3600, ge=0)
    outside_tolerance: bool
    surface: str | None = Field(default=None, max_length=160)
    metrics: dict[str, int | float | str | None]
    metric_definitions: dict[str, str] = Field(default_factory=dict)
    event_alignment: dict[str, int | float | str | None] | None = None
    source: Literal["manual_platform_export", "manual_studio_entry", "official_api"]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def age_matches_timestamps(self) -> AnalyticsSnapshot:
        expected = max(0.0, (self.captured_at - self.published_at).total_seconds())
        if abs(expected - self.actual_age_seconds) > 1.0:
            raise ValueError("actual_age_seconds does not match publication and capture timestamps")
        requested = self.requested_checkpoint_hours * 3600
        expected_outside = abs(self.actual_age_seconds - requested) > self.tolerance_seconds
        if expected_outside != self.outside_tolerance:
            raise ValueError("outside_tolerance does not match actual age")
        return self


class TimelineEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=200)
    kind: Literal[
        "intro_start",
        "intro_end",
        "first_payoff",
        "boss_card",
        "boss_action_start",
        "transition",
        "cta",
        "outro_start",
        "outro_end",
        "recap_beat",
    ]
    seconds: float = Field(ge=0)
    label: str = Field(min_length=1, max_length=200)
    source_clip_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
