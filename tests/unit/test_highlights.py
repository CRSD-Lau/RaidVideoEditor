from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from raid_editor.config.models import HighlightConfig, SpeechTriggerConfig
from raid_editor.highlights import detection as highlight_detection
from raid_editor.highlights import review as highlight_review
from raid_editor.highlights.detection import (
    HighlightAnalysisError,
    Signal,
    analyse_highlights,
    build_highlight_candidates,
    combat_pressure_signals,
    select_highlight_candidates,
)
from raid_editor.highlights.render import (
    HighlightRenderError,
    render_vertical_highlights,
)
from raid_editor.highlights.speech import (
    RecognizedWord,
    SpeechTriggerEvent,
    detect_spoken_commands,
    match_phrase_words,
)
from raid_editor.models import HighlightCandidate, PullCandidate


def _pull() -> PullCandidate:
    return PullCandidate(
        id="pull-001",
        start_seconds=50,
        end_seconds=140,
        type="boss_kill",
        encounter="Lord Marrowgar",
        result="kill",
        difficulty="25H",
    )


def _candidate(*, include: bool) -> HighlightCandidate:
    return HighlightCandidate(
        id="highlight-001",
        peak_seconds=100,
        start_seconds=80,
        end_seconds=120,
        category="funny",
        score=0.82,
        signals=["discord_rms:-12.0dB", "scene_score:0.450"],
        encounter="Lord Marrowgar",
        include=include,
        title="Marrowgar Spin Moment",
    )


def test_fused_discord_and_motion_signal_does_not_claim_to_understand_humor() -> None:
    candidates = build_highlight_candidates(
        [
            Signal(100, "discord", 0.9, "discord_rms:-12.0dB"),
            Signal(102, "motion", 0.8, "scene_score:0.450"),
        ],
        [_pull()],
        recording_duration_seconds=300,
        settings=HighlightConfig(
            minimum_score=0.2,
            minimum_spacing_seconds=0,
            lead_in_seconds=20,
            lead_out_seconds=15,
        ),
    )

    assert len(candidates) == 1
    assert candidates[0].category == "reaction"
    assert "Voice Activity Candidate" in candidates[0].title
    assert candidates[0].encounter == "Lord Marrowgar"
    assert candidates[0].include is False
    assert candidates[0].start_seconds < candidates[0].peak_seconds


def test_default_motion_scan_uses_keyframes_for_long_recordings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def fake_run(command: list[str]) -> str:
        observed.extend(command)
        return ""

    monkeypatch.setattr(highlight_detection, "_run_ffmpeg", fake_run)

    signals = highlight_detection.motion_signals(
        tmp_path / "raid.mp4",
        threshold=0.12,
        sample_fps=2,
        keyframes_only=True,
    )

    assert signals == []
    assert observed[observed.index("-skip_frame") + 1] == "nokey"


def test_ordinary_lich_king_finish_does_not_displace_stronger_events() -> None:
    candidates = build_highlight_candidates(
        [
            Signal(10, "discord", 1.0, "discord_rms:-8.0dB"),
            Signal(10, "motion", 1.0, "scene_score:0.900"),
            Signal(100, "raid_deaths", 1.0, "player_deaths_8s:8"),
            Signal(100, "kill_climax", 1.0, "boss_kill:Festergut:25H"),
            Signal(200, "kill_climax", 0.76, "boss_kill:The Lich King:25N"),
        ],
        [
            _pull(),
            PullCandidate(
                id="pull-lk",
                start_seconds=160,
                end_seconds=207,
                type="boss_kill",
                encounter="The Lich King",
                result="kill",
                difficulty="25N",
            ),
        ],
        recording_duration_seconds=300,
        settings=HighlightConfig(
            maximum_candidates=2,
            minimum_score=0.2,
            minimum_spacing_seconds=20,
        ),
    )

    assert len(candidates) == 2
    assert all(candidate.encounter != "The Lich King" for candidate in candidates)
    assert all(candidate.category not in {"funny", "clutch"} for candidate in candidates)
    assert all(candidate.include is False for candidate in candidates)


def test_spoken_clip_commands_bypass_heuristic_spacing_and_candidate_limit() -> None:
    candidates = build_highlight_candidates(
        [
            Signal(100, "speech_clip_command", 0.95, "speech_clip_command:microphone:clip it:0.95"),
            Signal(107, "speech_clip_command", 0.91, "speech_clip_command:discord:clip it:0.91"),
            Signal(200, "discord", 1.0, "discord_rms:-8.0dB"),
            Signal(200, "motion", 1.0, "scene_score:0.900"),
        ],
        [_pull()],
        recording_duration_seconds=300,
        settings=HighlightConfig(
            maximum_candidates=1,
            minimum_score=0.2,
            minimum_spacing_seconds=30,
        ),
    )

    spoken = [
        candidate
        for candidate in candidates
        if any(signal.startswith("speech_clip_command:") for signal in candidate.signals)
    ]
    assert [candidate.peak_seconds for candidate in spoken] == [100, 107]
    assert all(candidate.category == "reaction" for candidate in spoken)
    assert all(candidate.include is False for candidate in spoken)
    assert all("review the preceding moment" in candidate.notes for candidate in spoken)


@pytest.mark.parametrize(
    ("payload", "is_player"),
    [
        ('UNIT_DIED,0x0000000000000000,nil,0x80000000,0x06000000006655CA,"Raider",0x514', True),
        ('UNIT_DIED,0x0000000000000000,nil,0x80000000,0x0000000000123456,"Raider",0x514', True),
        ('UNIT_DIED,0x0000000000000000,nil,0x80000000,Player-123-ABCDEF,"Raider",0x514', True),
        (
            'UNIT_DIED,false,0x0000000000000000,nil,0x80000000,0x0,Player-123-ABCDEF,"Raider",0x514,0x0',
            True,
        ),
        ('UNIT_DIED,0x0000000000000000,nil,0x80000000,0xF13000799D00018D,"Totem",0x1114', False),
        ('UNIT_DIED,0x0000000000000000,nil,0x80000000,0xF130000584016E78,"Squirrel",0xa28', False),
        ('UNIT_DIED,0x0000000000000000,nil,0x80000000,0xF130000584016E78,"Boss",0x10a48', False),
        ('UNIT_DIED,Player-123-ABCDEF,"Raider",0x514,Pet-0-123,"Pet",0x1114', False),
        ("UNIT_DIED,0x0000000000000000,nil,0x80000000,0x0000000000000000,nil,0x514", False),
        ('UNIT_DIED,0x0000000000000000,nil,0x80000000,Player-123-ABCDEF,"Pet",0x1114', False),
        (
            'UNIT_DIED,0x0000000000000000,nil,0x80000000,0xF13000799D00018D,"Wrong type",0x514',
            False,
        ),
        ('UNIT_DIED,0x0000000000000000,nil,0x80000000,Player-123-ABCDEF,"Conflict",0xc00', False),
        (
            'UNIT_DIED,0x0000000000000000,nil,0x80000000,Player-123-ABCDEF,"Malformed",invalid',
            False,
        ),
        ('UNIT_DIED,Player-123-ABCDEF,"Ambiguous partial row"', False),
        ('UNIT_DIED,unknown,0,nil,0,0,Player-123-ABCDEF,"Wrong layout",0x514,0', False),
    ],
)
def test_player_death_signals_validate_destination_and_known_raw_log_layout(
    tmp_path: Path, payload: str, is_player: bool
) -> None:
    log = tmp_path / "combat.txt"
    log.write_text(
        f"9/4 22:09:26.100  {payload}\n9/4 22:09:27.100  {payload}\n",
        encoding="utf-8",
    )
    result = combat_pressure_signals(
        log,
        recording_started_at=datetime.fromisoformat("2026-09-04T22:09:16-03:00"),
        recording_duration_seconds=60,
        recording_offset_seconds=0,
    )
    assert len(result) == int(is_player)
    if result:
        assert result[0].detail == "player_deaths_8s:2"
        assert result[0].seconds == pytest.approx(10.6)


def test_death_cluster_excludes_four_totems_and_out_of_recording_margin(tmp_path: Path) -> None:
    log = tmp_path / "combat.txt"
    null_source = "UNIT_DIED,0x0000000000000000,nil,0x80000000"
    player = f'{null_source},0x06000000006655CA,"Raider",0x514'
    totem = f'{null_source},0xF13000799D00018D,"Flametongue Totem VIII",0x1114'
    rows = [
        f"9/4 22:09:15.100  {player}",
        f"9/4 22:09:15.200  {player}",
        *(f"9/4 22:09:26.{i}00  {totem}" for i in range(4)),
        f"9/4 22:10:20.100  {player}",
        f"9/4 22:10:20.200  {player}",
        f"9/4 22:10:22.100  {player}",
        f"9/4 22:10:22.200  {player}",
    ]
    log.write_text("\n".join(rows), encoding="utf-8")
    result = combat_pressure_signals(
        log,
        recording_started_at=datetime.fromisoformat("2026-09-04T22:09:16-03:00"),
        recording_duration_seconds=65,
        recording_offset_seconds=0,
    )
    assert len(result) == 1
    assert result[0].detail == "player_deaths_8s:2"
    assert result[0].seconds == pytest.approx(64.15)
    assert 0 <= result[0].seconds <= 65


def test_unsaturated_scores_rank_stronger_later_event_above_earlier_event() -> None:
    signals = [
        Signal(seconds, kind, strength, f"{kind}:evidence")
        for seconds, strength in [(100, 0.85), (200, 1.0)]
        for kind in ("discord", "game", "motion", "raid_deaths", "kill_climax")
    ]
    settings = HighlightConfig(maximum_candidates=1)
    pool = build_highlight_candidates(
        signals, [], recording_duration_seconds=300, settings=settings, limit_candidates=False
    )
    assert 0 < pool[0].score < pool[1].score < 1
    selected = select_highlight_candidates(pool, settings=settings)
    assert [candidate.peak_seconds for candidate in selected] == [200]
    assert selected[0].include is False


def test_spoken_intent_keeps_confidence_separate_and_survives_score_and_pool_limits() -> None:
    candidates = build_highlight_candidates(
        [
            Signal(t, "speech_clip_command", 0.1, f"speech_clip_command:microphone:clip it:{t}")
            for t in range(20, 35)
        ],
        [],
        recording_duration_seconds=60,
        settings=HighlightConfig(minimum_score=0.99, maximum_candidates=1, candidate_pool_size=12),
        limit_candidates=False,
    )
    # Explicit commands are the documented exception if their count exceeds the pool budget.
    assert len(candidates) == 15
    assert all(candidate.origin == "speech" for candidate in candidates)
    assert all(candidate.confidence == 0.1 for candidate in candidates)
    assert all(candidate.score < candidate.confidence for candidate in candidates)
    assert all(not candidate.include for candidate in candidates)
    assert len(select_highlight_candidates(candidates, settings=HighlightConfig())) == 15


def test_discovery_pool_preserves_weak_kills_before_final_routine_suppression() -> None:
    signals = [Signal(t, "kill_climax", 0.8, "boss_kill:Example:25H") for t in range(50, 1550, 50)]
    settings = HighlightConfig(
        maximum_candidates=8, maximum_routine_kills=2, candidate_pool_size=12, minimum_score=0
    )
    pool = build_highlight_candidates(
        signals, [], recording_duration_seconds=1600, settings=settings, limit_candidates=False
    )
    assert len(pool) == 12
    assert len(select_highlight_candidates(pool, settings=settings)) == 2
    assert (
        select_highlight_candidates(
            pool, settings=settings.model_copy(update={"minimum_score": 0.99})
        )
        == []
    )


def test_union_selection_removes_overlap_but_preserves_two_explicit_command_anchors() -> None:
    heuristic = _candidate(include=False).model_copy(
        update={"start_seconds": 70, "end_seconds": 130, "score": 0.5}
    )
    semantic = heuristic.model_copy(
        update={"origin": "semantic", "peak_seconds": 120, "score": 0.8, "title": "Story"}
    )
    commands = [
        heuristic.model_copy(
            update={
                "origin": "speech",
                "peak_seconds": t,
                "start_seconds": 200,
                "end_seconds": 240,
                "score": 0.1,
            }
        )
        for t in (220, 225)
    ]
    selected = select_highlight_candidates(
        [heuristic, semantic, *commands],
        settings=HighlightConfig(maximum_candidates=1, minimum_spacing_seconds=0),
    )
    assert [candidate.origin for candidate in selected] == ["semantic", "speech", "speech"]
    assert selected[0].title == "Story"


def test_review_identity_survives_new_earlier_candidate_and_changed_ranking() -> None:
    settings = HighlightConfig()
    original = select_highlight_candidates([_candidate(include=False)], settings=settings)[0]
    assert original.id == "highlight-001"
    assert original.review_identity is not None and len(original.review_identity) == 64
    earlier = _candidate(include=False).model_copy(
        update={
            "id": "new-proposal",
            "peak_seconds": 40,
            "start_seconds": 20,
            "end_seconds": 60,
        }
    )
    rescanned = _candidate(include=False).model_copy(
        update={
            "id": "different-detector-id",
            "score": 0.4,
            "title": "Revised display title",
            "signals": ["discord_rms:-16.0dB", "scene_score:0.350"],
        }
    )
    reranked = select_highlight_candidates([rescanned, earlier], settings=settings)
    same_moment = next(candidate for candidate in reranked if candidate.peak_seconds == 100)
    assert same_moment.id == "highlight-002"
    assert same_moment.review_identity == original.review_identity
    assert len({candidate.review_identity for candidate in reranked}) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "semantic"},
        {"start_seconds": 79.0},
        {"end_seconds": 121.0},
        {"peak_seconds": 101.0},
        {"signals": ["semantic_kind:developed_joke"]},
    ],
)
def test_distinct_original_moments_get_distinct_review_identities(
    change: dict[str, object],
) -> None:
    candidate = _candidate(include=False)
    numbered = highlight_detection._number_candidates(
        [
            candidate,
            candidate.model_copy(update=change),
        ]
    )
    assert numbered[0].review_identity != numbered[1].review_identity


def test_human_trim_round_trip_preserves_original_review_identity() -> None:
    original = highlight_detection._number_candidates([_candidate(include=False)])[0]
    payload = original.model_dump(mode="json")
    payload.update(start_seconds=90, end_seconds=130, title="My corrected boundaries")
    reviewed = HighlightCandidate.model_validate_json(json.dumps(payload))
    renumbered = highlight_detection._number_candidates([reviewed])[0]
    assert renumbered.review_identity == original.review_identity
    assert (renumbered.start_seconds, renumbered.end_seconds) == (90, 130)
    assert json.loads(renumbered.model_dump_json())["review_identity"] == original.review_identity


@pytest.mark.parametrize("identity", ["", "a" * 63, "a" * 65, "x" * 64])
def test_review_identity_rejects_non_sha256_values(identity: str) -> None:
    payload = _candidate(include=False).model_dump()
    payload["review_identity"] = identity
    with pytest.raises(ValidationError, match="review_identity"):
        HighlightCandidate.model_validate(payload)


def test_spoken_command_keeps_its_identity_and_gets_supported_setup_and_payoff() -> None:
    command = _candidate(include=False).model_copy(
        update={
            "origin": "speech",
            "score": 0.2,
            "confidence": 0.92,
            "signals": ["speech_clip_command:microphone:clip it:0.92"],
            "title": "Raid Clip-It Moment",
            "rationale": "Explicit voice command.",
        }
    )
    context = HighlightCandidate(
        id="semantic-story",
        peak_seconds=85,
        start_seconds=55,
        end_seconds=110,
        setup_seconds=60,
        payoff_seconds=105,
        category="funny",
        origin="semantic",
        score=0.9,
        confidence=0.88,
        title="A supported story",
        include=False,
        rationale="A mistake receives a clear group response.",
        signals=["semantic_kind:mistake_and_aftermath", "speech_evidence:microphone:60-105"],
    )
    selected = select_highlight_candidates(
        [command, context], settings=HighlightConfig(maximum_candidates=1)
    )
    assert len(selected) == 1
    result = selected[0]
    assert result.origin == "speech"
    assert result.title == command.title
    assert result.peak_seconds == command.peak_seconds
    assert result.confidence == command.confidence
    assert result.score == command.score
    assert (result.start_seconds, result.end_seconds) == (55, 120)
    assert (result.setup_seconds, result.payoff_seconds) == (60, 105)
    assert context.rationale in result.rationale
    assert set(command.signals + context.signals).issubset(result.signals)
    assert any(signal.startswith("semantic_context:") for signal in result.signals)
    assert result.include is False


def test_oversized_semantic_context_stays_separate_without_losing_command_or_story() -> None:
    command = _candidate(include=False).model_copy(
        update={
            "origin": "speech",
            "confidence": 0.92,
            "signals": ["speech_clip_command:microphone:clip it:0.92"],
        }
    )
    context = HighlightCandidate(
        id="semantic-story",
        peak_seconds=85,
        start_seconds=25,
        end_seconds=115,
        setup_seconds=30,
        payoff_seconds=110,
        category="reaction",
        origin="semantic",
        score=0.9,
        confidence=0.88,
        title="A longer story",
        include=False,
        rationale="A supported setup and response.",
        signals=["semantic_kind:reaction"],
    )
    selected = select_highlight_candidates(
        [command, context], settings=HighlightConfig(maximum_candidates=1)
    )
    assert len(selected) == 2
    result = next(candidate for candidate in selected if candidate.origin == "speech")
    assert (result.start_seconds, result.end_seconds) == (80, 120)
    assert result.setup_seconds is None
    assert any(candidate.rationale == context.rationale for candidate in selected)
    assert all(not candidate.include for candidate in selected)


def test_weak_semantic_context_does_not_change_an_explicit_command() -> None:
    command = _candidate(include=False).model_copy(
        update={
            "origin": "speech",
            "confidence": 0.92,
            "signals": ["speech_clip_command:microphone:clip it:0.92"],
        }
    )
    weak = command.model_copy(
        update={
            "origin": "semantic",
            "confidence": 0.1,
            "score": 0.1,
            "signals": ["semantic_kind"],
            "start_seconds": 55,
            "setup_seconds": 60,
            "payoff_seconds": 110,
            "rationale": "Uncertain interpretation.",
        }
    )
    selected = select_highlight_candidates([command, weak], settings=HighlightConfig())
    assert len(selected) == 1
    assert selected[0].start_seconds == command.start_seconds
    assert selected[0].rationale == command.rationale


@pytest.mark.parametrize("peak", [0.0, 50.0, 100.0])
def test_contextual_window_keeps_anchor_inside_short_clip_and_source(peak: float) -> None:
    result = build_highlight_candidates(
        [Signal(peak, "kill_climax", 1, "boss_kill:Example:25H")],
        [],
        recording_duration_seconds=100,
        settings=HighlightConfig(
            lead_in_seconds=120,
            lead_out_seconds=120,
            review_clip_seconds=6,
            minimum_score=0,
        ),
    )
    assert len(result) == 1
    assert 0 <= result[0].start_seconds <= peak <= result[0].end_seconds <= 100
    assert result[0].end_seconds - result[0].start_seconds <= 6


def test_kill_anchor_does_not_drift_with_scene_change_density() -> None:
    result = build_highlight_candidates(
        [Signal(t, "motion", 1, "scene_score:0.5") for t in range(90, 101, 2)]
        + [Signal(100, "kill_climax", 0.64, "boss_kill:Example:25H")],
        [],
        recording_duration_seconds=200,
        settings=HighlightConfig(minimum_score=0, review_clip_seconds=45),
    )
    assert len(result) == 1
    assert result[0].peak_seconds == 100
    assert result[0].start_seconds <= 90
    assert result[0].end_seconds >= 100


def test_local_audio_background_suppresses_constant_loudness_and_keeps_a_relative_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata = "\n".join(
        f"frame:{t} pts_time:{t}\nlavfi.astats.Overall.RMS_level={-4 if t == 30 else -12}"
        for t in range(61)
    )
    monkeypatch.setattr(highlight_detection, "_run_ffmpeg", lambda command: metadata)
    relative = highlight_detection.audio_energy_signals(
        tmp_path / "raid.mp4", stream_index=4, threshold_db=-27, kind="microphone"
    )
    absolute = highlight_detection.audio_energy_signals(
        tmp_path / "raid.mp4",
        stream_index=4,
        threshold_db=-27,
        kind="microphone",
        relative_energy=False,
    )
    assert [signal.seconds for signal in relative] == [30]
    assert "local_rise:8.0dB" in relative[0].detail
    assert len(absolute) > len(relative)


@pytest.mark.parametrize("microphone_enabled", [False, True])
def test_microphone_energy_is_a_distinct_opt_in_signal_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, microphone_enabled: bool
) -> None:
    observed: list[tuple[int, str]] = []

    def fake_audio(recording: Path, **kwargs: object) -> list[Signal]:
        del recording
        observed.append((int(str(kwargs["stream_index"])), str(kwargs["kind"])))
        return []

    monkeypatch.setattr(highlight_detection, "audio_energy_signals", fake_audio)
    monkeypatch.setattr(highlight_detection, "motion_signals", lambda *args, **kwargs: [])
    analyse_highlights(
        tmp_path / "raid.mp4",
        [],
        game_stream_index=2,
        discord_stream_index=3,
        microphone_stream_index=4,
        combat_log=None,
        recording_started_at=None,
        recording_duration_seconds=100,
        recording_offset_seconds=0,
        settings=HighlightConfig(keep_microphone_audio=microphone_enabled),
    )
    assert observed == [(3, "discord"), (2, "game")] + (
        [(4, "microphone")] if microphone_enabled else []
    )


def test_exact_spoken_phrase_matching_rejects_near_words_low_confidence_and_long_gaps() -> None:
    words = [
        RecognizedWord("click", 1.0, 1.2, 0.99),
        RecognizedWord("it", 1.21, 1.4, 0.99),
        RecognizedWord("clip", 2.0, 2.2, 0.79),
        RecognizedWord("it", 2.21, 2.4, 0.99),
        RecognizedWord("clip", 3.0, 3.2, 0.99),
        RecognizedWord("it", 3.8, 4.0, 0.99),
        RecognizedWord("CLIP!", 5.0, 5.2, 0.93),
        RecognizedWord("it.", 5.25, 5.5, 0.91),
    ]

    matches = match_phrase_words(
        words,
        phrases=["clip it"],
        minimum_word_confidence=0.80,
        maximum_word_gap_seconds=0.50,
    )

    assert matches == [("clip it", 5.0, 5.5, 0.91)]


def test_spoken_command_detection_deduplicates_cross_stem_echoes_without_transcript(
    tmp_path: Path,
) -> None:
    class FakeRecognizer:
        backend = "fake"
        backend_version = "1.0"
        model_identity = "fixture:model"

        def recognize(
            self,
            recording: Path,
            *,
            stream_index: int,
            source_role: str,
            settings: SpeechTriggerConfig,
        ) -> list[SpeechTriggerEvent]:
            del recording, stream_index, settings
            confidence = 0.96 if source_role == "microphone" else 0.90
            offset = 0.0 if source_role == "microphone" else 0.8
            return [
                SpeechTriggerEvent(
                    start_seconds=99.5 + offset,
                    end_seconds=100.0 + offset,
                    phrase="clip it",
                    source_role=source_role,  # type: ignore[arg-type]
                    confidence=confidence,
                    backend=self.backend,
                    backend_version=self.backend_version,
                    model_identity=self.model_identity,
                )
            ]

    result = detect_spoken_commands(
        tmp_path / "raid.mp4",
        source_streams={"discord": 3, "microphone": 4},
        settings=SpeechTriggerConfig(
            enabled=True,
            model_path=tmp_path,
            dedupe_seconds=6,
        ),
        recognizer=FakeRecognizer(),
    )

    assert result.status == "complete"
    assert len(result.events) == 1
    assert result.events[0].source_role == "microphone"
    assert result.events[0].confidence == 0.96
    assert result.to_dict()["transcript_persisted"] is False
    assert "transcript" not in result.to_dict()["events"][0]


def test_missing_local_speech_model_is_visible_and_retryable(tmp_path: Path) -> None:
    result = detect_spoken_commands(
        tmp_path / "raid.mp4",
        source_streams={"discord": 3, "microphone": 4},
        settings=SpeechTriggerConfig(
            enabled=True,
            model_path=tmp_path / "missing-model",
        ),
    )

    assert result.status == "unavailable"
    assert result.cacheable is False
    assert result.events == ()
    assert "missing" in result.diagnostics[0].casefold()


def test_highlight_analysis_refuses_any_audio_role_that_is_the_microphone(
    tmp_path: Path,
) -> None:
    with pytest.raises(HighlightAnalysisError, match="must not include the microphone"):
        analyse_highlights(
            tmp_path / "raid.mp4",
            [_pull()],
            game_stream_index=2,
            discord_stream_index=4,
            microphone_stream_index=4,
            combat_log=None,
            recording_started_at=None,
            recording_duration_seconds=300,
            recording_offset_seconds=0,
            settings=HighlightConfig(),
        )


def test_review_media_cache_rebuilds_when_the_audio_mix_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = tmp_path / "raid.mp4"
    recording.write_bytes(b"synthetic recording")
    destination = tmp_path / "review"
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(str(len(commands)).encode())

    monkeypatch.setattr(highlight_review, "_run", fake_run)

    first = highlight_review.generate_highlight_review_media(
        recording,
        [_candidate(include=False)],
        destination,
        audio_stream_indexes=[2, 3],
    )
    highlight_review.generate_highlight_review_media(
        recording,
        [_candidate(include=False)],
        destination,
        audio_stream_indexes=[2, 3],
    )
    rebuilt = highlight_review.generate_highlight_review_media(
        recording,
        [_candidate(include=False)],
        destination,
        audio_stream_indexes=[2, 3, 4],
    )

    manifest = json.loads(destination.joinpath("render-manifest.json").read_text(encoding="utf-8"))
    assert len(commands) == 2
    assert first == rebuilt
    assert rebuilt["highlight-001"].read_bytes() == b"2"
    assert manifest["audio_stream_indexes"] == [2, 3, 4]
    assert "[0:4]" in commands[-1][commands[-1].index("-filter_complex") + 1]


@pytest.mark.parametrize(
    ("media_format", "video_codec", "audio_codec"),
    [(None, "libvpx-vp9", "libopus"), ("mp4", "libx264", "aac")],
)
def test_review_media_format_preserves_full_window_audio_mix_and_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    media_format: str | None,
    video_codec: str,
    audio_codec: str,
) -> None:
    recording = tmp_path / "raid.mp4"
    recording.write_bytes(b"synthetic recording")
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"rendered review")

    monkeypatch.setattr(highlight_review, "_run", fake_run)
    candidate = _candidate(include=False)
    original = candidate.model_dump()
    if media_format == "mp4":
        outputs = highlight_review.generate_highlight_review_media(
            recording,
            [candidate],
            tmp_path / "review",
            audio_stream_indexes=[2, 3, 4],
            media_format="mp4",
        )
    else:
        outputs = highlight_review.generate_highlight_review_media(
            recording,
            [candidate],
            tmp_path / "review",
            audio_stream_indexes=[2, 3, 4],
        )

    command = commands[0]
    expected_format = media_format or "webm"
    assert outputs[candidate.id].suffix == f".{expected_format}"
    assert command[-1].endswith(f".rendering.{expected_format}")
    assert command[command.index("-c:v") + 1] == video_codec
    assert command[command.index("-c:a") + 1] == audio_codec
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-ss") + 1] == "80.000"
    assert command[command.index("-t") + 1] == "40.000"
    assert command[command.index("-vf") + 1] == "scale=960:-2,fps=30"
    graph = command[command.index("-filter_complex") + 1]
    assert all(f"[0:{stream}]" in graph for stream in (2, 3, 4))
    assert "[0:1]" not in graph
    assert ("-movflags" in command) is (media_format == "mp4")
    assert candidate.model_dump() == original
    manifest = json.loads((tmp_path / "review/render-manifest.json").read_text(encoding="utf-8"))
    assert manifest["media_format"] == expected_format
    assert video_codec in manifest["encoding_args"]
    assert "artist=Neil Mitchell" in manifest["encoding_args"]


def test_review_media_cache_invalidates_when_format_or_encoding_policy_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = tmp_path / "raid.mp4"
    recording.write_bytes(b"synthetic recording")
    destination = tmp_path / "review"
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(str(len(commands)).encode())

    monkeypatch.setattr(highlight_review, "_run", fake_run)
    candidate = _candidate(include=False)
    mp4_outputs = highlight_review.generate_highlight_review_media(
        recording, [candidate], destination, audio_stream_indexes=[2, 3, 4], media_format="mp4"
    )
    webm_outputs = highlight_review.generate_highlight_review_media(
        recording, [candidate], destination, audio_stream_indexes=[2, 3, 4]
    )
    highlight_review.generate_highlight_review_media(
        recording, [candidate], destination, audio_stream_indexes=[2, 3, 4]
    )
    assert len(commands) == 2
    assert mp4_outputs[candidate.id].read_bytes() == b"1"
    assert webm_outputs[candidate.id].read_bytes() == b"2"

    revised_args = highlight_review.review_encoding_args("webm")
    revised_args[revised_args.index("-crf") + 1] = "34"
    monkeypatch.setattr(highlight_review, "review_encoding_args", lambda _: revised_args)
    highlight_review.generate_highlight_review_media(
        recording, [candidate], destination, audio_stream_indexes=[2, 3, 4]
    )
    assert len(commands) == 3
    assert webm_outputs[candidate.id].read_bytes() == b"3"
    manifest = json.loads(destination.joinpath("render-manifest.json").read_text(encoding="utf-8"))
    assert manifest["encoding_args"] == [
        *revised_args,
        "-metadata",
        "author=Neil Mitchell",
        "-metadata",
        "last_modified_by=Neil Mitchell",
    ]


def test_review_media_failed_format_conversion_keeps_prior_manifest_and_clips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = tmp_path / "raid.mp4"
    recording.write_bytes(b"synthetic recording")
    destination = tmp_path / "review"

    def fake_run(command: list[str]) -> None:
        Path(command[-1]).write_bytes(b"rendered review")

    monkeypatch.setattr(highlight_review, "_run", fake_run)
    candidate = _candidate(include=False)
    outputs = highlight_review.generate_highlight_review_media(
        recording, [candidate], destination, audio_stream_indexes=[2, 3, 4], media_format="mp4"
    )
    manifest_path = destination / "render-manifest.json"
    old_manifest = manifest_path.read_bytes()

    def fail_render(command: list[str]) -> None:
        Path(command[-1]).write_bytes(b"partial")
        raise highlight_review.HighlightReviewError("synthetic encoding failure")

    monkeypatch.setattr(highlight_review, "_run", fail_render)
    with pytest.raises(highlight_review.HighlightReviewError, match="synthetic encoding failure"):
        highlight_review.generate_highlight_review_media(
            recording, [candidate], destination, audio_stream_indexes=[2, 3, 4]
        )
    assert manifest_path.read_bytes() == old_manifest
    assert outputs[candidate.id].read_bytes() == b"rendered review"
    assert not list(destination.joinpath("assets").glob("*.webm"))


@pytest.mark.parametrize("extension", ["webm", "mp4"])
def test_highlight_review_page_declares_media_type_and_preserves_unapproved_state(
    tmp_path: Path,
    extension: str,
) -> None:
    candidate = _candidate(include=False)
    destination = tmp_path / "index.html"
    highlight_review.generate_highlight_review_page(
        [candidate],
        {candidate.id: tmp_path / "assets" / f"{candidate.id}.{extension}"},
        destination,
        includes_game=True,
        includes_discord=True,
        includes_microphone=True,
    )
    document = destination.read_text(encoding="utf-8")
    assert f'type="video/{extension}"' in document
    assert f'src="assets/{candidate.id}.{extension}"' in document
    assert 'name="author" content="Neil Mitchell"' in document
    assert 'name="last-modified-by" content="Neil Mitchell"' in document
    assert "Open full review clip" in document
    assert "could not load or decode this clip" in document
    assert 'class="include" type="checkbox" >' in document
    assert '"include": false' in document
    assert "game included, Discord included, microphone included" in document


def test_vertical_export_requires_approval_and_selected_candidates(tmp_path: Path) -> None:
    with pytest.raises(HighlightRenderError, match="explicit approval"):
        render_vertical_highlights(
            tmp_path / "raid.mp4",
            [_candidate(include=True)],
            tmp_path / "vertical",
            audio_stream_indexes=[2, 3],
            microphone_stream_index=4,
            settings=HighlightConfig(hardware_encoding=False),
            approved=False,
        )

    with pytest.raises(HighlightRenderError, match="No highlight candidates"):
        render_vertical_highlights(
            tmp_path / "raid.mp4",
            [_candidate(include=False)],
            tmp_path / "vertical",
            audio_stream_indexes=[2, 3],
            microphone_stream_index=4,
            settings=HighlightConfig(hardware_encoding=False),
            approved=True,
        )


def test_vertical_dry_run_keeps_game_and_discord_but_excludes_microphone(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "vertical"
    outputs = render_vertical_highlights(
        tmp_path / "raid.mp4",
        [_candidate(include=True)],
        destination,
        audio_stream_indexes=[2, 3],
        microphone_stream_index=4,
        settings=HighlightConfig(hardware_encoding=False),
        approved=True,
        dry_run=True,
    )

    manifest = json.loads(destination.joinpath("manifest.json").read_text(encoding="utf-8"))
    command = manifest["clips"][0]["command"]
    graph = command[command.index("-filter_complex") + 1]

    assert outputs == [destination / "01-marrowgar-spin-moment-vertical.mp4"]
    assert manifest["clips"][0]["audio_stream_indexes"] == [2, 3]
    assert manifest["clips"][0]["excluded_microphone_stream_index"] == 4
    assert "[0:2]" in graph
    assert "[0:3]" in graph
    assert "[0:4]" not in graph


def test_vertical_dry_run_can_include_microphone_when_explicitly_enabled(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "vertical"
    render_vertical_highlights(
        tmp_path / "raid.mp4",
        [_candidate(include=True)],
        destination,
        audio_stream_indexes=[2, 3, 4],
        microphone_stream_index=4,
        settings=HighlightConfig(
            keep_microphone_audio=True,
            hardware_encoding=False,
        ),
        approved=True,
        dry_run=True,
    )

    manifest = json.loads(destination.joinpath("manifest.json").read_text(encoding="utf-8"))
    clip = manifest["clips"][0]
    graph = clip["command"][clip["command"].index("-filter_complex") + 1]

    assert clip["audio_stream_indexes"] == [2, 3, 4]
    assert clip["microphone_stream_index"] == 4
    assert clip["microphone_included"] is True
    assert clip["excluded_microphone_stream_index"] is None
    assert "[0:2]" in graph
    assert "[0:3]" in graph
    assert "[0:4]" in graph


def test_vertical_title_with_apostrophe_is_safe_for_ffmpeg_drawtext(tmp_path: Path) -> None:
    candidate = _candidate(include=True).model_copy(
        update={"title": "Pizza Warriors' FIRST Shadowmourne!"}
    )
    destination = tmp_path / "vertical"

    render_vertical_highlights(
        tmp_path / "raid.mp4",
        [candidate],
        destination,
        audio_stream_indexes=[2, 3, 4],
        microphone_stream_index=4,
        settings=HighlightConfig(
            keep_microphone_audio=True,
            hardware_encoding=False,
        ),
        approved=True,
        dry_run=True,
    )

    manifest = json.loads(destination.joinpath("manifest.json").read_text(encoding="utf-8"))
    command = manifest["clips"][0]["command"]
    graph = command[command.index("-filter_complex") + 1]

    assert "Pizza Warriors’ FIRST Shadowmourne!" in graph
    assert "Pizza Warriors\\' FIRST Shadowmourne" not in graph
    assert manifest["clips"][0]["title"] == "Pizza Warriors' FIRST Shadowmourne!"
