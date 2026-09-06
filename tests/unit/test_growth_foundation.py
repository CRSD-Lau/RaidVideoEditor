from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from raid_editor.growth.analytics import record_growth_analytics
from raid_editor.growth.calendar import propose_open_slots, write_global_calendar
from raid_editor.growth.claims import validate_coverage_claims
from raid_editor.growth.events import build_timeline_events
from raid_editor.growth.ledger import (
    GrowthLedgerError,
    append_growth_event,
    append_packaging_experiment,
    append_production_time,
    load_growth_events,
    load_packaging_experiments,
)
from raid_editor.growth.models import (
    CampaignAsset,
    CampaignManifest,
    ClaimRecord,
    CoverageRecord,
    DistributionEntry,
    GrowthDistributionManifest,
    GrowthLedgerEvent,
    PackagingExperiment,
    ProductionTimeEntry,
    RecapDecision,
    RelatedTarget,
    ScheduleEntry,
    SourceFingerprint,
    SourceLineage,
    SourceRange,
)
from raid_editor.growth.package import (
    _load_prior_campaign_proposals,
    _preserve_reviewed_projection,
    approve_growth_package,
    record_manual_claim_approval,
    record_packaging_experiment_state,
    record_related_target_state,
)
from raid_editor.models import TimelineClip, TimelineDocument
from raid_editor.util.paths import atomic_write_json, full_file_sha256
from raid_editor.youtube.upload import _write_thumbnail_mobile_preview


def _coverage(count: int, *, overall: int | None = None) -> CoverageRecord:
    bosses = [f"Boss {number}" for number in range(1, count + 1)]
    return CoverageRecord(
        expected_bosses=12,
        overall_bosses_killed=count if overall is None else overall,
        recorded_bosses=bosses,
        edited_bosses=bosses,
        heroic_bosses=bosses[: min(7, count)],
        winning_ending_bosses=bosses,
        confirmed_raid_size=25,
        ending_evidence_complete=True,
        evidence_ids=[f"pull-{number}" for number in range(1, count + 1)],
    )


def _archive() -> CampaignAsset:
    coverage = _coverage(12)
    return CampaignAsset(
        content_id="raid-archive",
        lane="archive",
        title="ICC 25M 12/12 7HC Full Clear",
        source_lineage_ids=["landscape"],
        coverage=coverage,
        claims=validate_coverage_claims(coverage, content_id="raid-archive"),
    )


def _short(number: int) -> CampaignAsset:
    return CampaignAsset(
        content_id=f"short-{number}",
        lane="short",
        title=f"Short {number}",
        source_lineage_ids=["landscape"],
        coverage=_coverage(1),
        related_target=RelatedTarget(
            state="planned",
            target_content_id="raid-archive",
            target_lane="archive",
        ),
    )


def _source() -> SourceLineage:
    return SourceLineage(
        source_id="landscape",
        path=Path("raid.mp4"),
        kind="landscape_authoritative_source",
        fingerprint=SourceFingerprint(
            algorithm="head_tail_sha256",
            digest="a" * 64,
            size_bytes=100,
            modified_ns=1,
            sampled_bytes_per_end=4,
        ),
        width=2560,
        height=1440,
        duration_seconds=100,
        authoritative_for=["archive", "audio"],
    )


def _write_growth_pair(tmp_path: Path) -> Path:
    growth = tmp_path / "growth"
    growth.mkdir()
    campaign_path = growth / "campaign-manifest.json"
    now = datetime.now(UTC)
    short_path = tmp_path / "short.mp4"
    short_path.write_bytes(b"reviewed synthetic short")
    short_sha256 = full_file_sha256(short_path)
    short = CampaignAsset(
        content_id="short-1",
        lane="short",
        title="Funny pull",
        state="upload_ready",
        media_path=short_path,
        media_sha256=short_sha256,
        source_lineage_ids=["landscape"],
        coverage=_coverage(1),
        related_target=RelatedTarget(
            state="approved",
            target_content_id="raid-archive",
            target_lane="archive",
            reviewed_at=now,
        ),
    )
    campaign = CampaignManifest(
        campaign_id="raid-night",
        created_at=now,
        updated_at=now,
        raid_name="Icecrown Citadel",
        sources=[_source()],
        assets=[_archive(), short],
    )
    distribution = GrowthDistributionManifest(
        campaign_id=campaign.campaign_id,
        generated_at=now,
        campaign_manifest=campaign_path,
        entries=[
            DistributionEntry(
                content_id="raid-archive",
                lane="archive",
                destination="youtube",
                status="reviewed",
                review_approved=True,
            ),
            DistributionEntry(
                content_id="short-1",
                lane="short",
                destination="youtube",
                status="reviewed",
                media_sha256=short_sha256,
                mutation_intents={"related_video": "planned"},
                review_approved=True,
            ),
        ],
    )
    atomic_write_json(campaign_path, campaign.model_dump(mode="json"))
    atomic_write_json(
        growth / "distribution-manifest.json",
        distribution.model_dump(mode="json"),
    )
    return campaign_path


def test_full_clear_claim_requires_complete_recorded_and_edited_footage() -> None:
    complete = validate_coverage_claims(_coverage(12), content_id="archive")
    assert next(claim for claim in complete if claim.kind == "full_clear").state == "allowed"

    partial_footage = _coverage(9, overall=12)
    partial = validate_coverage_claims(partial_footage, content_id="archive")
    full_clear = next(claim for claim in partial if claim.kind == "full_clear")
    assert full_clear.state == "rejected"
    assert "recorded footage covers 9/12" in full_clear.reason
    assert "overall result is" not in full_clear.reason

    missing_ending = _coverage(12)
    missing_ending.winning_ending_bosses = missing_ending.winning_ending_bosses[:-1]
    ending_claims = validate_coverage_claims(missing_ending, content_id="archive")
    ending_full_clear = next(claim for claim in ending_claims if claim.kind == "full_clear")
    assert ending_full_clear.state == "rejected"
    assert "winning endings are missing" in ending_full_clear.reason


def test_unknown_difficulty_never_produces_an_exact_heroic_total() -> None:
    coverage = _coverage(2)
    coverage.heroic_bosses = ["Boss 1"]
    coverage.unknown_difficulty_bosses = ["Boss 2"]
    claims = validate_coverage_claims(coverage, content_id="archive")
    heroic = next(claim for claim in claims if claim.kind == "heroic_count")
    second = next(
        claim for claim in claims if claim.kind == "difficulty" and claim.text.startswith("Boss 2:")
    )
    assert heroic.state == "manual_review"
    assert heroic.text == "at least 1HC"
    assert second.state == "manual_review"


def test_new_campaign_rejects_more_than_two_shorts() -> None:
    with pytest.raises(ValidationError, match="at most two Shorts"):
        CampaignManifest(
            campaign_id="raid-night",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            raid_name="Icecrown Citadel",
            sources=[_source()],
            assets=[_archive(), _short(1), _short(2), _short(3)],
        )


@pytest.mark.parametrize("short_count", [0, 1, 2])
def test_archive_only_zero_one_and_two_short_campaigns_validate(short_count: int) -> None:
    campaign = CampaignManifest(
        campaign_id=f"raid-{short_count}",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        raid_name="Icecrown Citadel",
        sources=[_source()],
        assets=[_archive(), *[_short(index) for index in range(short_count)]],
    )
    assert sum(asset.lane == "short" for asset in campaign.assets) == short_count


def test_upload_ready_short_requires_reviewed_related_target() -> None:
    with pytest.raises(ValidationError, match="reviewed related-video target"):
        CampaignAsset(
            content_id="short-1",
            lane="short",
            title="Funny pull",
            state="upload_ready",
            media_path=Path("short.mp4"),
            media_sha256="b" * 64,
            source_lineage_ids=["landscape"],
            coverage=_coverage(1),
            related_target=RelatedTarget(
                state="planned",
                target_content_id="raid-archive",
                target_lane="archive",
            ),
        )


def test_publication_complete_short_requires_observed_native_assignment() -> None:
    with pytest.raises(ValidationError, match="publication-complete Short"):
        CampaignAsset(
            content_id="short-1",
            lane="short",
            title="Funny pull",
            state="verified_public",
            media_path=Path("short.mp4"),
            media_sha256="b" * 64,
            source_lineage_ids=["landscape"],
            coverage=_coverage(1),
            related_target=RelatedTarget(
                state="approved",
                target_content_id="raid-archive",
                target_lane="archive",
                reviewed_at=datetime.now(UTC),
            ),
        )

    asset = CampaignAsset(
        content_id="short-1",
        lane="short",
        title="Funny pull",
        state="verified_public",
        media_path=Path("short.mp4"),
        media_sha256="b" * 64,
        source_lineage_ids=["landscape"],
        coverage=_coverage(1),
        related_target=RelatedTarget(
            state="assigned_verified",
            target_content_id="raid-archive",
            target_lane="archive",
            target_url="https://www.youtube.com/watch?v=archive",
            reviewed_at=datetime.now(UTC),
            assignment_observed_at=datetime.now(UTC),
            assignment_receipt="Observed the native related-video chip on the public Short.",
        ),
    )
    assert asset.related_target.state == "assigned_verified"


def test_manual_claim_cannot_become_allowed_without_explicit_manual_approval() -> None:
    with pytest.raises(ValidationError, match="requires manual approval"):
        ClaimRecord(
            claim_id="short:first",
            kind="first",
            text="Our first Shadowmourne",
            state="allowed",
            reason="Owner-supplied milestone",
            evidence_ids=["clip-1"],
            automatic=False,
        )
    approved = ClaimRecord(
        claim_id="short:first",
        kind="first",
        text="Our first Shadowmourne",
        state="allowed",
        reason="Owner reviewed guild evidence",
        evidence_ids=["clip-1"],
        automatic=False,
        manually_approved=True,
    )
    assert approved.manually_approved is True


def test_manual_claim_command_path_records_named_approval_and_ledger(tmp_path: Path) -> None:
    campaign_path = _write_growth_pair(tmp_path)
    campaign = record_manual_claim_approval(
        campaign_path,
        content_id="raid-archive",
        claim_id="raid-archive:first-kill",
        kind="milestone",
        text="Our first clean one-shot night",
        reason="Neil reviewed the complete winning-pull timeline.",
        evidence_ids=["pull-001", "pull-012"],
        approved=True,
    )
    claim = next(
        claim for claim in campaign.assets[0].claims if claim.claim_id == "raid-archive:first-kill"
    )
    assert claim.manually_approved is True
    distribution = GrowthDistributionManifest.model_validate_json(
        campaign_path.with_name("distribution-manifest.json").read_text(encoding="utf-8")
    )
    archive = next(entry for entry in distribution.entries if entry.content_id == "raid-archive")
    assert "claim:raid-archive:first-kill" in archive.named_approvals
    assert load_growth_events(campaign_path.with_name("growth-ledger.jsonl"))[-1].event_type == (
        "claim_reviewed"
    )


def test_related_target_receipt_completes_short_only_after_observation(tmp_path: Path) -> None:
    campaign_path = _write_growth_pair(tmp_path)
    pending = record_related_target_state(
        campaign_path,
        content_id="short-1",
        state="assignment_pending",
        reason="Short is public; Studio assignment is the next named mutation.",
        short_remote_id="short-remote-1",
        short_public_url="https://youtube.com/shorts/short-remote-1",
        approved=True,
    )
    pending_short = next(asset for asset in pending.assets if asset.content_id == "short-1")
    assert pending_short.state == "published"

    completed = record_related_target_state(
        campaign_path,
        content_id="short-1",
        state="assigned_verified",
        reason="The native related-video chip was observed on the public Short.",
        short_remote_id="short-remote-1",
        short_public_url="https://youtube.com/shorts/short-remote-1",
        target_url="https://www.youtube.com/watch?v=archive-1",
        assignment_receipt="Manual Studio and public-playback observation.",
        approved=True,
    )
    completed_short = next(asset for asset in completed.assets if asset.content_id == "short-1")
    assert completed_short.state == "verified_public"
    assert completed_short.related_target.state == "assigned_verified"
    distribution = GrowthDistributionManifest.model_validate_json(
        campaign_path.with_name("distribution-manifest.json").read_text(encoding="utf-8")
    )
    youtube = next(entry for entry in distribution.entries if entry.content_id == "short-1")
    assert youtube.status == "verified_public"
    assert youtube.mutation_intents["related_video"] == "verified"


def test_local_growth_approval_does_not_approve_remote_mutation_intents(
    tmp_path: Path,
) -> None:
    campaign_path = _write_growth_pair(tmp_path)
    approve_growth_package(campaign_path, approved=True)
    distribution = GrowthDistributionManifest.model_validate_json(
        campaign_path.with_name("distribution-manifest.json").read_text(encoding="utf-8")
    )
    short = next(entry for entry in distribution.entries if entry.content_id == "short-1")
    assert short.mutation_intents["related_video"] == "planned"
    assert "growth_package_review" in short.named_approvals


def test_regeneration_preserves_reviewed_decisions_when_source_identity_matches() -> None:
    now = datetime.now(UTC)
    old_short = _short(1)
    old_short.state = "reviewed"
    old_short.related_target = RelatedTarget(
        state="approved",
        target_content_id="raid-archive",
        target_lane="archive",
        reviewed_at=now,
    )
    old_short.claims.append(
        ClaimRecord(
            claim_id="short-1:milestone",
            kind="milestone",
            text="Reviewed guild milestone",
            state="allowed",
            reason="Reviewed against guild evidence",
            evidence_ids=["clip-1"],
            automatic=False,
            manually_approved=True,
        )
    )
    existing = CampaignManifest(
        campaign_id="raid-night",
        created_at=now,
        updated_at=now,
        raid_name="Icecrown Citadel",
        sources=[_source()],
        assets=[_archive(), old_short],
        recap=RecapDecision(
            decision="skipped",
            coherent_story=False,
            reason="No coherent story this week",
            reviewed_at=now,
        ),
    )
    assets, recap = _preserve_reviewed_projection(
        existing,
        sources=[_source()],
        assets=[_archive(), _short(1)],
    )
    regenerated_short = next(asset for asset in assets if asset.content_id == "short-1")
    assert regenerated_short.state == "reviewed"
    assert regenerated_short.related_target.state == "approved"
    assert any(claim.manually_approved for claim in regenerated_short.claims)
    assert recap.decision == "skipped"


def test_prior_local_schedule_proposals_remain_stable_on_regeneration(tmp_path: Path) -> None:
    release = datetime.now(UTC) + timedelta(days=5)
    path = tmp_path / "global-content-calendar.json"
    proposal = ScheduleEntry(
        schedule_id="proposal-1",
        campaign_id="raid-night",
        content_id="short-1",
        destination="youtube",
        scheduled_for=release,
        lock="proposed",
        status="prepared",
    )
    write_global_calendar(path, existing=[], proposals=[proposal])
    replay = _load_prior_campaign_proposals(
        path,
        campaign_id="raid-night",
        valid_keys={("youtube", "short-1")},
    )
    assert replay == [proposal]


def test_recap_candidate_requires_story_beats_ranges_audio_and_effort() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="requires beats"):
        RecapDecision(
            decision="candidate",
            coherent_story=True,
            reason="A progression story exists",
            reviewed_at=now,
        )
    candidate = RecapDecision(
        decision="candidate",
        coherent_story=True,
        reason="A progression story exists",
        reviewed_at=now,
        candidate_beats=["Early wipe", "Recovery", "Kill"],
        source_ranges=[
            SourceRange(
                source_id="landscape",
                start_seconds=10,
                end_seconds=30,
                purpose="recap_beat",
            )
        ],
        audio_plan="voice_candidates_require_review",
        estimated_active_minutes=75,
    )
    assert candidate.estimated_active_minutes == 75


def test_append_only_growth_ledger_is_idempotent_and_rejects_identity_reuse(
    tmp_path: Path,
) -> None:
    path = tmp_path / "growth-ledger.jsonl"
    event = GrowthLedgerEvent(
        event_id="event-1",
        recorded_at=datetime.now(UTC),
        campaign_id="raid-night",
        event_type="campaign_prepared",
        subject_id="raid-night",
        payload={"remote_mutations": 0},
    )
    append_growth_event(path, event)
    append_growth_event(path, event)
    assert load_growth_events(path) == [event]

    changed = event.model_copy(update={"payload": {"remote_mutations": 1}})
    with pytest.raises(GrowthLedgerError, match="already exists"):
        append_growth_event(path, changed)


def test_only_one_packaging_variable_can_be_active_per_asset(tmp_path: Path) -> None:
    path = tmp_path / "experiments.jsonl"
    first = PackagingExperiment(
        experiment_id="thumbnail-1",
        campaign_id="raid-night",
        content_id="raid-archive",
        created_at=datetime.now(UTC),
        factor="thumbnail",
        hypothesis="Boss action earns more qualified clicks.",
        control="clean score badge",
        treatment="boss action frame",
        primary_metric="watch_time_per_impression",
    )
    append_packaging_experiment(path, first)
    with pytest.raises(GrowthLedgerError, match="Only one packaging variable"):
        append_packaging_experiment(
            path,
            first.model_copy(
                update={
                    "experiment_id": "title-1",
                    "factor": "title",
                    "hypothesis": "A story title earns more qualified clicks.",
                    "control": "accurate archive title",
                    "treatment": "story-led title",
                }
            ),
        )

    completed = first.model_copy(
        update={
            "experiment_id": "thumbnail-1:completed",
            "series_id": "thumbnail-1",
            "supersedes_experiment_id": "thumbnail-1",
            "status": "completed",
            "decision": "inconclusive",
        }
    )
    append_packaging_experiment(path, completed)
    next_test = first.model_copy(
        update={
            "experiment_id": "title-2",
            "series_id": "title-2",
            "factor": "title",
            "hypothesis": "A story title earns more qualified clicks.",
            "control": "accurate archive title",
            "treatment": "story-led title",
        }
    )
    append_packaging_experiment(path, next_test)


def test_production_time_separates_operator_and_unattended_runtime(tmp_path: Path) -> None:
    path = tmp_path / "growth-ledger.jsonl"
    entry = ProductionTimeEntry(
        entry_id="time-1",
        campaign_id="raid-night",
        recorded_at=datetime.now(UTC),
        stage="rendering",
        content_id="short-1",
        content_lane="short",
        minutes=5,
        unattended_runtime_seconds=1800,
    )
    append_production_time(path, entry)
    event = load_growth_events(path)[0]
    assert event.active_operator_minutes == 5
    assert event.unattended_runtime_seconds == 1800
    assert event.payload["content_lane"] == "short"


def test_experiment_state_receipt_appends_a_version_without_rewriting_plan(
    tmp_path: Path,
) -> None:
    campaign_path = _write_growth_pair(tmp_path)
    experiment_path = campaign_path.with_name("packaging-experiments.jsonl")
    initial = PackagingExperiment(
        experiment_id="raid-night-thumbnail-01",
        campaign_id="raid-night",
        content_id="raid-archive",
        created_at=datetime.now(UTC),
        factor="thumbnail",
        hypothesis="Boss action earns more qualified clicks.",
        control="clean score badge",
        treatment="boss action frame",
        primary_metric="watch_time_per_impression",
    )
    append_packaging_experiment(experiment_path, initial)
    version = record_packaging_experiment_state(
        campaign_path,
        series_id="raid-night-thumbnail-01",
        status="completed",
        decision="inconclusive",
        reason="Not enough comparable impressions.",
        approved=True,
    )
    history = load_packaging_experiments(experiment_path)
    assert [row.status for row in history] == ["planned", "completed"]
    assert version.supersedes_experiment_id == initial.experiment_id
    assert history[0].decision is None


def test_calendar_keeps_remote_social_schedule_locked_and_proposes_youtube_separately(
    tmp_path: Path,
) -> None:
    future = datetime.now(UTC) + timedelta(days=30)
    existing = [
        ScheduleEntry(
            schedule_id="locked-facebook",
            campaign_id="backfill",
            content_id="old-short",
            destination="facebook",
            scheduled_for=future,
            lock="locked_remote",
            status="scheduled",
            approved=True,
        )
    ]
    proposals = propose_open_slots(
        campaign_id="new-raid",
        content_ids=["new-archive"],
        destinations=["youtube"],
        existing=existing,
        cadence_days=1,
    )
    assert proposals[0].scheduled_for < future
    calendar = write_global_calendar(
        tmp_path / "calendar.json",
        existing=existing,
        proposals=proposals,
    )
    assert calendar.entries[0].lock == "locked_remote"
    assert calendar.entries[1].lock == "proposed"
    assert calendar.remote_mutations_performed is False


def test_calendar_reports_same_platform_account_time_collisions(tmp_path: Path) -> None:
    release = datetime.now(UTC) + timedelta(days=3)
    rows = [
        ScheduleEntry(
            schedule_id=f"facebook-{index}",
            campaign_id="raid-night",
            content_id=f"short-{index}",
            destination="facebook",
            target_handle="Pizza Warriors",
            scheduled_for=release,
            lock="proposed",
            status="prepared",
        )
        for index in (1, 2)
    ]
    calendar = write_global_calendar(
        tmp_path / "calendar.json",
        existing=[],
        proposals=rows,
    )
    assert len(calendar.collision_warnings) == 1
    assert "short-1, short-2" in calendar.collision_warnings[0]


def test_analytics_uses_actual_publication_age_and_surface(tmp_path: Path) -> None:
    metrics = tmp_path / "metrics.json"
    metrics.write_text(json.dumps({"views": 100, "average_view_duration": 20}))
    published = datetime(2026, 8, 1, 12, tzinfo=UTC)
    captured = published + timedelta(hours=25, minutes=30)
    snapshot, destination = record_growth_analytics(
        tmp_path / "analytics",
        campaign_id="raid-night",
        content_id="short-1",
        destination="youtube",
        published_at=published,
        requested_checkpoint_hours=24,
        metrics_path=metrics,
        source="manual_studio_entry",
        surface="shorts_feed",
        captured_at=captured,
        tolerance_seconds=3600,
    )
    assert snapshot.actual_age_seconds == 25.5 * 3600
    assert snapshot.outside_tolerance is True
    assert snapshot.surface == "shorts_feed"
    assert snapshot.event_alignment is None
    assert destination.is_file()


def test_timeline_events_mark_intro_boss_actions_transitions_and_outro(tmp_path: Path) -> None:
    timeline = TimelineDocument(
        timeline_name="Raid",
        source=tmp_path / "raid.mp4",
        source_duration_seconds=120,
        source_fps=60,
        retained_audio_stream_indexes=[2],
        clips=[
            TimelineClip(
                source_in=10,
                source_out=30,
                timeline_in=0,
                label="Marrowgar",
                type="boss_kill",
                result="kill",
                pull_ids=["pull-1"],
            ),
            TimelineClip(
                source_in=50,
                source_out=80,
                timeline_in=20,
                label="Deathwhisper",
                type="boss_kill",
                result="kill",
                pull_ids=["pull-2"],
            ),
        ],
    )
    events = build_timeline_events(
        timeline,
        intro_seconds=5,
        boss_card_seconds=1.5,
        outro_seconds=5,
    )
    kinds = [event.kind for event in events]
    assert kinds.count("boss_action_start") == 2
    assert "transition" in kinds
    assert next(event.seconds for event in events if event.event_id == "boss-01-action") == 6.5
    assert next(event.seconds for event in events if event.kind == "outro_start") == 55


def test_thumbnail_mobile_review_uses_feed_size_simulation(tmp_path: Path) -> None:
    candidates = (tmp_path / "one.jpg", tmp_path / "two.jpg")
    destination = tmp_path / "mobile.html"
    _write_thumbnail_mobile_preview(candidates, destination)
    page = destination.read_text(encoding="utf-8")
    assert "Compact feed simulation" in page
    assert "width:160px" in page
    assert "width:320px" in page
    assert candidates[0].as_uri() in page
