"""Workflow integration checks with real manifests and no model/media execution."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import nullcontext
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from raid_editor import workflow
from raid_editor.config.loader import load_project_config
from raid_editor.config.models import ProjectConfig
from raid_editor.highlights import intelligence
from raid_editor.highlights.detection import build_highlight_candidates
from raid_editor.highlights.feedback import record_editorial_feedback
from raid_editor.highlights.intelligence import IntelligenceResult
from raid_editor.highlights.speech import SpeechTriggerEvent, SpeechTriggerResult
from raid_editor.ingestion.probe import AudioStream, MediaProbe, VideoStream
from raid_editor.models import HighlightCandidate, PullCandidate
from raid_editor.util.paths import quick_file_fingerprint
from raid_editor.weekly import create_weekly_project_config


def test_review_source_reference_is_stable_but_changes_with_the_recording(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.json"
    source: dict[str, object] = {"path": "raid.mkv", "head_tail_sha256": "first", "size": 100}
    original = workflow._highlight_source_reference(candidates, source)
    assert original == workflow._highlight_source_reference(
        candidates, dict(reversed(source.items()))
    )
    assert original.startswith(f"{candidates.resolve()}#recording=")
    assert original != workflow._highlight_source_reference(
        candidates, {**source, "head_tail_sha256": "replacement"}
    )


def _probe(recording: Path) -> MediaProbe:
    return MediaProbe(
        source=quick_file_fingerprint(recording),
        format_name="mov,mp4",
        duration_seconds=1200,
        size_bytes=recording.stat().st_size,
        video_streams=[VideoStream(index=0, codec="h264", width=2560, height=1440, frame_rate=60)],
        audio_streams=[
            AudioStream(index=index, audio_ordinal=index - 1, codec="aac", title=title)
            for index, title in enumerate(
                ["Full Mix", "WoW Game", "Discord", "Microphone"], start=1
            )
        ],
    )


def _candidate(*, semantic: bool = False, peak: float = 100) -> HighlightCandidate:
    return HighlightCandidate(
        id=f"{'semantic' if semantic else 'heuristic'}-{peak}",
        peak_seconds=peak,
        start_seconds=peak - 20,
        end_seconds=peak + 20,
        origin="semantic" if semantic else "heuristic",
        category="reaction" if semantic else "intense",
        score=0.85,
        confidence=0.88 if semantic else None,
        title="Supported reaction" if semantic else "Boss Finish Candidate",
        rationale="An event has a supported response." if semantic else "Finish evidence.",
        setup_seconds=peak - 15 if semantic else None,
        payoff_seconds=peak + 15 if semantic else None,
        signals=["semantic_speech"] if semantic else ["boss_kill:Example:25H", "scene_score:0.4"],
        include=False,
    )


@dataclass
class _Harness:
    config: ProjectConfig
    paths: workflow.ProjectPaths
    heuristic: Mock
    semantic: Mock
    speech: Mock
    managed: Mock
    runtime: dict[str, str]
    result: IntelligenceResult = field(
        default_factory=lambda: IntelligenceResult(status="complete")
    )

    def run(self) -> list[HighlightCandidate]:
        return workflow.analyse_highlights_project(self.config, create_review_media=False)[0]

    def read(self, name: str) -> object:
        return json.loads((self.paths.highlights / name).read_text(encoding="utf-8"))


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    recording = tmp_path / "2026-09-11 22-09-16.mp4"
    recording.write_bytes(b"source identity fixture; media tools must not decode this")
    speech_model = tmp_path / "models" / "whisper"
    speech_model.mkdir(parents=True)
    (speech_model / "model.bin").write_bytes(b"speech model version one")
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "Intelligence integration raid"},
            "input": {"recording": recording},
            "audio": {
                "game_track": 2,
                "discord_track": 3,
                "microphone_track": 4,
                "mixed_track": 1,
                "keep_discord_audio": False,
            },
            "music": {"library": tmp_path / "library.json"},
            "detection": {"recording_started_at": "2026-09-11T22:09:16-03:00"},
            "highlights": {
                "keep_microphone_audio": True,
                "speech_triggers": {"enabled": True, "model_path": tmp_path / "vosk"},
                "intelligence": {
                    "enabled": True,
                    "whisper_model_path": speech_model,
                    "feedback_path": tmp_path / "feedback.json",
                },
            },
        }
    )
    output = tmp_path / "output"
    paths = workflow.ProjectPaths(
        **{
            item.name: output if item.name == "root" else output / item.name
            for item in fields(workflow.ProjectPaths)
        }
    )
    pulls = [
        PullCandidate(
            id="pull-001",
            start_seconds=10,
            end_seconds=110,
            type="boss_kill",
            encounter="Example",
            result="kill",
            difficulty="25H",
        )
    ]
    monkeypatch.setattr(
        workflow, "analyse_project", Mock(return_value=(_probe(recording), pulls, paths))
    )
    managed = Mock(side_effect=lambda settings: nullcontext())
    monkeypatch.setattr(workflow, "managed_local_intelligence", managed)
    speech = Mock(
        return_value=SpeechTriggerResult(
            status="complete",
            requested_sources=("discord", "microphone"),
            completed_sources=("discord", "microphone"),
            backend="fixture",
            backend_version="1",
        )
    )
    monkeypatch.setattr(workflow, "detect_spoken_commands", speech)
    monkeypatch.setattr(
        workflow, "speech_runtime_signature", lambda settings: {"backend": "fixture"}
    )
    runtime = {"digest": "semantic-v1"}

    class LocalModelIdentity:
        def __init__(self, settings: object) -> None:
            del settings

        def model_digest(self) -> str:
            return runtime["digest"]

    # Exercise the actual runtime fingerprint construction, with only the local
    # service lookup replaced. No inference, server startup, or GPU is permitted.
    monkeypatch.setattr(intelligence, "LocalOllama", LocalModelIdentity)
    monkeypatch.setattr(intelligence, "dependency_version", lambda name: "fixture-1")
    semantic = Mock()
    heuristic = Mock()
    state = _Harness(config, paths, heuristic, semantic, speech, managed, runtime)

    def detect(
        recording: Path, pulls: list[PullCandidate], **kwargs: object
    ) -> list[HighlightCandidate]:
        del recording
        commands = build_highlight_candidates(
            kwargs["spoken_command_signals"],  # type: ignore[arg-type]
            pulls,
            recording_duration_seconds=1200,
            settings=config.highlights,
            limit_candidates=False,
        )
        return [_candidate(peak=100), _candidate(peak=500), *commands]

    heuristic.side_effect = detect
    semantic.side_effect = lambda *args, **kwargs: state.result
    monkeypatch.setattr(workflow, "analyse_highlights", heuristic)
    monkeypatch.setattr(workflow, "analyse_intelligent_highlights", semantic)
    return state


def test_completed_semantic_abstention_does_not_backfill_generic_kills(harness: _Harness) -> None:
    assert harness.run() == []
    baseline = harness.read("repaired-baseline.json")
    assert isinstance(baseline, dict) and len(baseline["highlights"]) == 2
    assert harness.read("candidates.json") == []
    status = harness.read("intelligence-status.json")
    assert isinstance(status, dict) and status["status"] == "complete"
    manifest = harness.read("analysis-manifest.json")
    assert isinstance(manifest, dict) and manifest["cacheable"] is True
    assert harness.heuristic.call_args.kwargs["limit_candidates"] is False


@pytest.mark.parametrize("confidence", [0.93, 0.65])
def test_completed_abstention_keeps_an_explicit_command_for_review(
    harness: _Harness, confidence: float
) -> None:
    harness.config.highlights.speech_triggers.minimum_word_confidence = 0.60
    event = SpeechTriggerEvent(
        start_seconds=899.5,
        end_seconds=900,
        phrase="clip it",
        source_role="microphone",
        confidence=confidence,
        backend="fixture",
        backend_version="1",
        model_identity="fixture",
    )
    harness.speech.return_value = SpeechTriggerResult(
        status="complete",
        events=(event,),
        requested_sources=("discord", "microphone"),
        completed_sources=("discord", "microphone"),
    )
    selected = harness.run()
    assert len(selected) == 1
    assert selected[0].origin == "speech"
    assert selected[0].peak_seconds == 900
    assert selected[0].confidence == confidence
    assert selected[0].include is False
    assert any(
        signal.startswith("speech_clip_command:microphone:") for signal in selected[0].signals
    )


@pytest.mark.parametrize("status", ["partial", "failed", "unavailable", "truncated"])
def test_degraded_intelligence_fallback_is_explicit_and_retried(
    harness: _Harness, status: str
) -> None:
    harness.result = IntelligenceResult(
        status=status,  # type: ignore[arg-type]
        diagnostics=["semantic_chunk_judgment_failed"],
        requested_streams={"discord": 3, "microphone": 4},
        completed_streams=["discord"] if status == "partial" else [],
    )
    selected = harness.run()
    assert len(selected) == 2
    assert all(candidate.origin == "heuristic" and not candidate.include for candidate in selected)
    saved_status = harness.read("intelligence-status.json")
    assert isinstance(saved_status, dict)
    assert saved_status["status"] == status
    assert saved_status["diagnostics"] == ["semantic_chunk_judgment_failed"]
    assert saved_status["cacheable"] is False
    manifest = harness.read("analysis-manifest.json")
    assert isinstance(manifest, dict) and manifest["cacheable"] is False
    report = (harness.paths.reports / "highlight-intelligence.md").read_text(encoding="utf-8")
    assert f"`{status}`" in report
    assert "degraded" in report.lower() and "heuristic" in report.lower()
    harness.run()
    assert harness.semantic.call_count == 2
    assert harness.heuristic.call_count == 2


def test_completed_same_signature_reuses_saved_proposals_without_inference(
    harness: _Harness,
) -> None:
    harness.result = IntelligenceResult(status="complete", candidates=[_candidate(semantic=True)])
    first = harness.run()
    second = harness.run()
    assert first == second
    assert len(first) == 1 and first[0].origin == "semantic"
    assert (
        harness.semantic.call_count
        == harness.heuristic.call_count
        == harness.speech.call_count
        == 1
    )


@pytest.mark.parametrize(
    "changed",
    ["semantic_digest", "speech_model", "configured_model", "feedback", "roles", "offset", "start"],
)
def test_discovery_cache_tracks_models_feedback_roles_and_timing(
    harness: _Harness, changed: str, tmp_path: Path
) -> None:
    if changed == "roles":
        # The review mix remains [2], so only analysis-role identity detects this.
        harness.config.highlights.keep_discord_audio = False
        harness.config.highlights.keep_microphone_audio = False
    harness.run()
    first_manifest = harness.read("analysis-manifest.json")
    assert isinstance(first_manifest, dict)
    if changed == "semantic_digest":
        harness.runtime["digest"] = "semantic-v2"
    elif changed == "speech_model":
        model = harness.config.highlights.intelligence.whisper_model_path
        assert model is not None
        (model / "model.bin").write_bytes(b"a different local speech model revision")
    elif changed == "configured_model":
        harness.config.highlights.intelligence.model = "another-local-model:4b"
    elif changed == "feedback":
        selection = tmp_path / "reviewed.json"
        reviewed = _candidate().model_copy(
            update={
                "review_rating": "reject",
                "rejection_reason": "ordinary_kill",
            }
        )
        selection.write_text(json.dumps({"highlights": [reviewed.model_dump(mode="json")]}))
        destination = harness.config.highlights.intelligence.feedback_path
        assert destination is not None
        record_editorial_feedback(selection, destination)
    elif changed == "roles":
        harness.config.audio.discord_track, harness.config.audio.microphone_track = 4, 3
    elif changed == "offset":
        harness.config.detection.combat_log_offset_seconds = 1.25
    elif changed == "start":
        anchor = harness.config.detection.recording_started_at
        assert anchor is not None
        harness.config.detection.recording_started_at = anchor + timedelta(seconds=3)
    harness.run()
    second_manifest = harness.read("analysis-manifest.json")
    assert isinstance(second_manifest, dict)
    assert first_manifest["signature"] != second_manifest["signature"]
    assert harness.semantic.call_count == harness.heuristic.call_count == 2
    if changed == "roles":
        assert first_manifest["signature"]["audio_streams"] == [2]
        assert second_manifest["signature"]["audio_streams"] == [2]
        assert harness.semantic.call_args.kwargs["source_streams"] == {
            "discord": 4,
            "microphone": 3,
        }


def test_missing_completed_status_does_not_claim_cached_semantic_coverage(
    harness: _Harness,
) -> None:
    harness.run()
    (harness.paths.highlights / "intelligence-status.json").unlink()
    harness.run()
    assert harness.semantic.call_count == 2


def test_manual_override_preserves_reviewed_selection_and_skips_runtime_and_models(
    harness: _Harness, tmp_path: Path
) -> None:
    harness.run()
    manual = _candidate(semantic=True, peak=220).model_copy(
        update={
            "id": "neils-choice",
            "title": "Reviewed personal choice",
            "include": True,
            "review_rating": "keep",
            "start_seconds": 185,
        }
    )
    selection = tmp_path / "approved.json"
    selection.write_text(json.dumps({"highlights": [manual.model_dump(mode="json")]}))
    original_bytes = selection.read_bytes()
    harness.config.highlights.manual_selection = selection
    result = harness.run()
    assert result == [manual]
    assert result[0].include is True
    assert selection.read_bytes() == original_bytes
    assert (
        harness.semantic.call_count
        == harness.heuristic.call_count
        == harness.speech.call_count
        == 1
    )
    assert harness.managed.call_count == 1
    feedback = harness.config.highlights.intelligence.feedback_path
    assert feedback is not None
    imported = json.loads(feedback.read_text(encoding="utf-8"))
    assert len(imported["records"]) == 1
    assert imported["records"][0]["rating"] == "keep"


def test_clearing_manual_override_does_not_reuse_its_approvals_as_automatic_output(
    harness: _Harness, tmp_path: Path
) -> None:
    assert harness.run() == []
    manual = _candidate(semantic=True, peak=220).model_copy(
        update={"id": "manual-choice", "include": True, "review_rating": "keep"}
    )
    selection = tmp_path / "manual.json"
    selection.write_text(json.dumps({"highlights": [manual.model_dump(mode="json")]}))
    # Isolate cache invalidation from the independently tested feedback fingerprint.
    harness.config.highlights.intelligence.feedback_path = None
    # Warm the matching automatic signature with feedback disabled.
    harness.run()
    prior_inferences = harness.semantic.call_count
    harness.config.highlights.manual_selection = selection
    assert harness.run() == [manual]
    assert harness.semantic.call_count == prior_inferences
    harness.config.highlights.manual_selection = None
    assert harness.run() == []
    assert harness.semantic.call_count == prior_inferences + 1


def test_new_weekly_config_enables_local_intelligence_without_rewriting_legacy_template(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "RaidVideoEditor"
    config_dir = project_root / "config"
    assets = project_root / "assets"
    config_dir.mkdir(parents=True)
    assets.mkdir()
    for name in (
        "pizza-warriors-lausudo-camera-cover-v1.png",
        "pizza-warriors-raid-presentation-v2-clean-1920x1080.png",
    ):
        (assets / name).write_bytes(b"placeholder asset; not rendered")
    old_recording = tmp_path / "2026-09-04 22-09-16.mp4"
    old_recording.write_bytes(b"older raid")
    template = config_dir / "pizza-warriors-2026-09-04.local.yaml"
    template.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "Older raid"},
                "input": {"recording": str(old_recording)},
                "audio": {"game_track": 2, "discord_track": 3, "microphone_track": 4},
                "music": {"library": str(project_root / "library.json")},
            }
        )
    )
    template_bytes = template.read_bytes()
    assert load_project_config(template).highlights.intelligence.enabled is False
    new_recording = tmp_path / "2026-09-11 22-09-16.mp4"
    new_recording.write_bytes(b"new raid")
    setup = create_weekly_project_config(
        new_recording,
        template_path=template,
        config_directory=config_dir,
        project_root=project_root,
        probe=_probe(new_recording),
    )
    generated = load_project_config(setup.config_path)
    settings = generated.highlights.intelligence
    assert setup.created is True
    assert settings.enabled is True and settings.required is False
    assert settings.whisper_model_path == project_root / ".models" / "faster-whisper-medium.en"
    assert settings.ollama_models_path == project_root / ".models" / "ollama"
    assert settings.ollama_executable is not None
    assert settings.ollama_executable.is_relative_to(project_root / ".tools")
    assert settings.ollama_url == "http://127.0.0.1:11435"
    assert settings.feedback_path == config_dir / "highlight-feedback.local.json"
    assert settings.visual_verification is True
    assert generated.highlights.manual_selection is None
    assert template.read_bytes() == template_bytes
    assert load_project_config(template).highlights.intelligence.enabled is False
    # OBS filenames use the capture machine's local clock, including on UTC CI hosts.
    assert generated.detection.recording_started_at == datetime(2026, 9, 11, 22, 9, 16).astimezone()


@pytest.mark.parametrize("status", ["unavailable", "partial", "truncated", "failed"])
def test_required_intelligence_records_failure_before_proposals_or_review_and_retries(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    harness.config.highlights.intelligence.required = True
    harness.result = IntelligenceResult(
        status=status,  # type: ignore[arg-type]
        candidates=[_candidate(semantic=True)],
        diagnostics=["semantic_chunk_judgment_failed"],
    )
    render = Mock()
    review = Mock()
    save_candidates = Mock()
    monkeypatch.setattr(workflow, "generate_highlight_review_media", render)
    monkeypatch.setattr(workflow, "generate_highlight_review_page", review)
    monkeypatch.setattr(workflow, "write_highlight_candidates", save_candidates)
    with pytest.raises(RuntimeError, match="(?i)(required|complete|coverage)"):
        workflow.analyse_highlights_project(harness.config, create_review_media=True)
    render.assert_not_called()
    review.assert_not_called()
    save_candidates.assert_not_called()
    assert not (harness.paths.highlights / "candidates.json").exists()
    saved_status = harness.read("intelligence-status.json")
    assert isinstance(saved_status, dict) and saved_status["status"] == status
    assert saved_status["cacheable"] is False
    manifest = harness.read("analysis-manifest.json")
    assert isinstance(manifest, dict) and manifest["cacheable"] is False
    assert isinstance(manifest["signature"], dict)
    harness.config.highlights.intelligence.required = False
    selected = harness.run()
    assert harness.semantic.call_count == 2
    assert harness.heuristic.call_count == 2
    assert selected and all(not candidate.include for candidate in selected)


def test_required_intelligence_failure_cannot_reach_vertical_encoder(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.config.highlights.intelligence.required = True
    harness.result = IntelligenceResult(status="unavailable")
    renderer = Mock()
    monkeypatch.setattr(workflow, "render_vertical_highlights", renderer)
    with pytest.raises(RuntimeError, match="(?i)(required|complete|coverage)"):
        workflow.render_highlights_project(harness.config, approved=True, dry_run=True)
    renderer.assert_not_called()


@pytest.mark.parametrize(
    "corruption",
    ["baseline_source", "manifest_source", "signature_list", "signature_null", "manifest_list"],
)
def test_comparison_rejects_mismatched_or_malformed_source_before_rendering(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    harness.result = IntelligenceResult(status="complete", candidates=[_candidate(semantic=True)])
    harness.run()
    if corruption == "baseline_source":
        destination = harness.paths.highlights / "repaired-baseline.json"
        payload = harness.read("repaired-baseline.json")
        assert isinstance(payload, dict)
        payload["source"] = {"path": "unrelated-recording.mp4"}
    else:
        destination = harness.paths.highlights / "analysis-manifest.json"
        payload = harness.read("analysis-manifest.json")
        assert isinstance(payload, dict)
        if corruption == "manifest_source":
            payload["signature"]["recording"] = {"path": "unrelated-recording.mp4"}
        elif corruption == "signature_list":
            payload["signature"] = []
        elif corruption == "signature_null":
            payload["signature"] = None
        else:
            payload = []
    destination.write_text(json.dumps(payload), encoding="utf-8")
    renderer = Mock()
    monkeypatch.setattr(workflow, "generate_highlight_review_media", renderer)
    with pytest.raises(ValueError, match="(?i)(fingerprint|source|recording)"):
        workflow.prepare_highlight_comparison(harness.config)
    renderer.assert_not_called()


def test_comparison_uses_opaque_media_paths_without_changing_saved_selections(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.result = IntelligenceResult(
        status="complete", candidates=[_candidate(semantic=True, peak=250)]
    )
    harness.run()
    candidates_path = harness.paths.highlights / "candidates.json"
    baseline_path = harness.paths.highlights / "repaired-baseline.json"
    candidate_bytes = candidates_path.read_bytes()
    baseline_bytes = baseline_path.read_bytes()
    destinations: list[Path] = []

    def render(
        recording: Path, candidates: list[HighlightCandidate], destination: Path, **kwargs: object
    ) -> dict[str, Path]:
        del recording, kwargs
        assert all(not candidate.include for candidate in candidates)
        destinations.append(destination)
        return {
            candidate.id: destination / "assets" / f"{candidate.id}.webm"
            for candidate in candidates
        }

    monkeypatch.setattr(workflow, "generate_highlight_review_media", render)
    page = workflow.prepare_highlight_comparison(harness.config)
    assert {path.name for path in destinations} == {
        hashlib.sha256(name.encode()).hexdigest()[:16]
        for name in ("repaired_heuristics", "current_recommendations")
    }
    assert all(path.parent.name == "variants" for path in destinations)
    visible = page.read_text(encoding="utf-8").split("<script>")[0]
    sources = re.findall(r'(?:src|href)="([^"]+)"', visible)
    assert sources
    assert not any(
        "repaired_heuristics" in source or "current_recommendations" in source for source in sources
    )
    assert candidates_path.read_bytes() == candidate_bytes
    assert baseline_path.read_bytes() == baseline_bytes


def test_disabled_highlights_skip_runtime_recognition_and_semantic_calls(harness: _Harness) -> None:
    harness.config.highlights.enabled = False
    assert harness.run() == []
    harness.managed.assert_not_called()
    harness.speech.assert_not_called()
    harness.semantic.assert_not_called()
    harness.heuristic.assert_not_called()
    assert harness.read("candidates.json") == []
    status = harness.read("intelligence-status.json")
    assert isinstance(status, dict) and status["status"] == "disabled"
    manifest = harness.read("analysis-manifest.json")
    assert isinstance(manifest, dict) and manifest["cacheable"] is False


def test_disabled_highlights_replace_stale_review_without_rendering_media(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = harness.paths.highlights / "review" / "index.html"
    page.parent.mkdir(parents=True)
    page.write_text("STALE PREVIOUS APPROVED CHOICES", encoding="utf-8")
    harness.config.highlights.enabled = False
    renderer = Mock()
    monkeypatch.setattr(workflow, "generate_highlight_review_media", renderer)
    candidates, _ = workflow.analyse_highlights_project(harness.config, create_review_media=True)
    assert candidates == []
    renderer.assert_not_called()
    harness.managed.assert_not_called()
    harness.speech.assert_not_called()
    harness.semantic.assert_not_called()
    content = page.read_text(encoding="utf-8")
    assert "STALE PREVIOUS APPROVED CHOICES" not in content
    assert "Editorial intelligence: disabled" in content
    assert '<article class="candidate"' not in content
