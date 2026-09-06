"""Native review cache and approval isolation without media or model execution."""

from __future__ import annotations

import json
import re
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from raid_editor import workflow
from raid_editor.config.models import ProjectConfig
from raid_editor.highlights import review
from raid_editor.highlights.portrait import PortraitSource
from raid_editor.highlights.render import portrait_presentation_reference
from raid_editor.ingestion.probe import AudioStream, MediaProbe, VideoStream
from raid_editor.models import HighlightCandidate
from raid_editor.util.paths import quick_file_fingerprint


def _candidate() -> HighlightCandidate:
    return HighlightCandidate(
        id="highlight-001",
        start_seconds=60,
        peak_seconds=75,
        end_seconds=90,
        category="reaction",
        score=0.8,
        title="A complete reaction",
        origin="semantic",
        rationale="The setup and response belong to the same exchange.",
        setup_seconds=65,
        payoff_seconds=85,
        confidence=0.85,
        review_identity="a" * 64,
    )


def _binding(landscape: Path, portrait: Path, offset: float = 2) -> PortraitSource:
    return PortraitSource(
        recording=portrait,
        landscape_recording=landscape,
        offset_seconds=offset,
        landscape_duration_seconds=600,
        duration_seconds=598,
        signature={
            "landscape": quick_file_fingerprint(landscape),
            "portrait": quick_file_fingerprint(portrait),
            "status": "verified",
            "offset_seconds": offset,
            "sync": {"audio_role": "game"},
        },
    )


@pytest.fixture
def native_source(tmp_path: Path) -> PortraitSource:
    landscape = tmp_path / "raid.mkv"
    portrait = tmp_path / "Vertical" / "raid.mkv"
    portrait.parent.mkdir()
    landscape.write_bytes(b"landscape source fixture, not decodable media")
    portrait.write_bytes(b"portrait source fixture, not decodable media")
    return _binding(landscape, portrait)


def _review_probe(path: Path) -> MediaProbe:
    return MediaProbe(
        source=quick_file_fingerprint(path),
        format_name="matroska,webm",
        duration_seconds=30,
        size_bytes=path.stat().st_size,
        video_streams=[VideoStream(index=0, codec="vp9", width=540, height=960, frame_rate=30)],
        audio_streams=[AudioStream(index=1, audio_ordinal=0, codec="opus")],
    )


@pytest.fixture
def media_stubs(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    def render(command: list[str]) -> None:
        Path(command[-1]).write_bytes(f"review render {runner.call_count}".encode())

    runner = Mock(side_effect=render)
    prober = Mock(side_effect=_review_probe)
    monkeypatch.setattr(review, "_run", runner)
    monkeypatch.setattr(review, "probe_media", prober)
    return runner, prober


@pytest.mark.parametrize("change", ["portrait", "offset", "audio", "landscape"])
def test_native_review_cache_reuses_identical_inputs_and_rebuilds_changed_binding(
    tmp_path: Path,
    native_source: PortraitSource,
    media_stubs: tuple[Mock, Mock],
    change: str,
) -> None:
    runner, prober = media_stubs
    destination = tmp_path / "review"
    candidate = _candidate()
    audio = [2, 3, 4]

    def render(source: PortraitSource, streams: list[int]) -> dict[str, Path]:
        return review.generate_highlight_review_media(
            source.landscape_recording,
            [candidate],
            destination,
            audio_stream_indexes=streams,
            portrait_source=source,
        )

    first = render(native_source, audio)
    first_bytes = first[candidate.id].read_bytes()
    assert render(native_source, audio) == first
    assert runner.call_count == prober.call_count == 1
    command = runner.call_args.args[0]
    first_input = command.index("-i")
    second_input = command.index("-i", first_input + 1)
    assert command[first_input - 1] == "58.000000"
    assert command[first_input + 1] == str(native_source.recording)
    assert command[second_input - 1] == "60.000000"
    assert command[second_input + 1] == str(native_source.landscape_recording)
    assert command[command.index("-t") + 1] == "30.000"
    graph = command[command.index("-filter_complex") + 1]
    assert "scale=540:960" in graph
    assert all(f"[1:{stream}]" in graph for stream in audio)
    assert "gblur" not in graph and "drawtext" not in graph
    assert prober.call_args.args[0].name == ".highlight-001.rendering.webm"
    assert not prober.call_args.args[0].exists()

    if change == "portrait":
        native_source.recording.write_bytes(b"different native picture in the same pathname")
        updated = _binding(native_source.landscape_recording, native_source.recording)
    elif change == "landscape":
        native_source.landscape_recording.write_bytes(b"different landscape audio source content")
        updated = _binding(native_source.landscape_recording, native_source.recording)
    elif change == "offset":
        # The explicit measured offset is independently part of the media cache.
        updated = replace(native_source, offset_seconds=3)
    else:
        updated = native_source
        audio = [2, 3]

    assert render(updated, audio) == first
    assert runner.call_count == prober.call_count == 2
    assert first[candidate.id].read_bytes() != first_bytes
    assert render(updated, audio) == first
    assert runner.call_count == prober.call_count == 2
    manifest = json.loads((destination / "render-manifest.json").read_text(encoding="utf-8"))
    assert manifest["portrait_source"] == updated.signature
    assert manifest["portrait_offset_seconds"] == updated.offset_seconds
    assert manifest["audio_stream_indexes"] == audio
    assert manifest["composition"] == "preserve-native-no-overlays-v1"


@pytest.mark.parametrize("failure", ["landscape_geometry", "short_duration", "missing_audio"])
def test_native_review_qa_failure_keeps_previous_clip_and_manifest(
    tmp_path: Path,
    native_source: PortraitSource,
    media_stubs: tuple[Mock, Mock],
    failure: str,
) -> None:
    _, prober = media_stubs
    destination = tmp_path / "review"
    candidate = _candidate()
    assets = review.generate_highlight_review_media(
        native_source.landscape_recording,
        [candidate],
        destination,
        audio_stream_indexes=[2, 3, 4],
        portrait_source=native_source,
    )
    clip = assets[candidate.id]
    original_bytes = clip.read_bytes()
    manifest = destination / "render-manifest.json"
    manifest_bytes = manifest.read_bytes()

    def bad_probe(path: Path) -> MediaProbe:
        probe = _review_probe(path)
        if failure == "landscape_geometry":
            return probe.model_copy(
                update={
                    "video_streams": [
                        VideoStream(index=0, codec="vp9", width=960, height=540, frame_rate=30)
                    ]
                }
            )
        if failure == "short_duration":
            return probe.model_copy(update={"duration_seconds": 20})
        return probe.model_copy(update={"audio_streams": []})

    prober.side_effect = bad_probe
    with pytest.raises(review.HighlightReviewError, match="geometry/duration QA"):
        review.generate_highlight_review_media(
            native_source.landscape_recording,
            [candidate],
            destination,
            audio_stream_indexes=[2, 3, 4],
            portrait_source=replace(native_source, offset_seconds=3),
        )

    assert clip.read_bytes() == original_bytes
    assert manifest.read_bytes() == manifest_bytes
    assert not list(clip.parent.glob("*.rendering.webm"))


@pytest.mark.parametrize("previous_binding", ["legacy", "portrait", "offset", "audio", "matching"])
def test_rereview_preserves_editorial_decisions_but_clears_changed_presentation_approval(
    tmp_path: Path,
    native_source: PortraitSource,
    media_stubs: tuple[Mock, Mock],
    monkeypatch: pytest.MonkeyPatch,
    previous_binding: str,
) -> None:
    old_source = native_source
    old_audio = [2, 3, 4]
    if previous_binding == "portrait":
        old_portrait = tmp_path / "old-portrait.mkv"
        old_portrait.write_bytes(b"previously reviewed portrait")
        old_source = _binding(native_source.landscape_recording, old_portrait)
    elif previous_binding == "offset":
        old_source = _binding(native_source.landscape_recording, native_source.recording, 3)
    elif previous_binding == "audio":
        old_audio = [2, 3]
    old_reference = (
        None
        if previous_binding == "legacy"
        else portrait_presentation_reference(
            old_source, audio_stream_indexes=old_audio, resolution="1080x1920"
        )
    )
    original = [
        _candidate().model_copy(update={"include": True, "review_rating": "keep"}),
        _candidate().model_copy(
            update={"id": "highlight-002", "review_identity": "b" * 64, "review_rating": "maybe"}
        ),
        _candidate().model_copy(
            update={
                "id": "highlight-003",
                "review_identity": "c" * 64,
                "review_rating": "reject",
                "rejection_reason": "wrong_timing",
            }
        ),
    ]
    selection = tmp_path / "reviewed-overrides.json"
    selection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "author": "Neil Mitchell",
                "last_modified_by": "Neil Mitchell",
                "presentation_reference": old_reference,
                "highlights": [row.model_dump(mode="json") for row in original],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    before = selection.read_bytes()
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "Native manual review"},
            "input": {
                "recording": native_source.landscape_recording,
                "vertical_recording": native_source.recording,
            },
            "audio": {"game_track": 2, "discord_track": 3, "microphone_track": 4},
            "music": {"library": tmp_path / "library.json"},
            "highlights": {
                "video_source": "native_vertical",
                "manual_selection": selection,
                "keep_microphone_audio": True,
                "intelligence": {"enabled": False, "feedback_path": None},
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
    probe = MediaProbe(
        source=quick_file_fingerprint(native_source.landscape_recording),
        format_name="matroska",
        duration_seconds=600,
        size_bytes=native_source.landscape_recording.stat().st_size,
        video_streams=[VideoStream(index=0, codec="h264", width=2560, height=1440, frame_rate=60)],
        audio_streams=[
            AudioStream(index=index, audio_ordinal=index - 1, codec="aac", title=title)
            for index, title in enumerate(
                ["Full Mix", "WoW Game", "Discord", "Microphone"], start=1
            )
        ],
    )
    monkeypatch.setattr(workflow, "analyse_project", Mock(return_value=(probe, [], paths)))
    monkeypatch.setattr(workflow, "resolve_portrait_source", Mock(return_value=native_source))
    managed = Mock(side_effect=AssertionError("Manual review must not start model inference"))
    speech = Mock(side_effect=AssertionError("Manual review must not scan speech"))
    feedback = Mock(side_effect=AssertionError("Feedback is disabled for this review"))
    monkeypatch.setattr(workflow, "managed_local_intelligence", managed)
    monkeypatch.setattr(workflow, "detect_spoken_commands", speech)
    monkeypatch.setattr(workflow, "record_editorial_feedback", feedback)

    rows, actual_paths = workflow.analyse_highlights_project(config, create_review_media=True)

    assert actual_paths == paths
    assert [row.include for row in rows] == [previous_binding == "matching", False, False]
    assert [row.model_dump(exclude={"include"}) for row in rows] == [
        row.model_dump(exclude={"include"}) for row in original
    ]
    assert selection.read_bytes() == before
    assert config.highlights.manual_selection == selection
    assert config.highlights.video_source == "native_vertical"
    saved = json.loads((paths.highlights / "candidates.json").read_text(encoding="utf-8"))
    assert saved == [row.model_dump(mode="json") for row in rows]
    page = (paths.highlights / "review" / "index.html").read_text(encoding="utf-8")
    reference_match = re.search(r"const presentationReference = (.*?);", page)
    assert reference_match is not None
    expected_reference = portrait_presentation_reference(
        native_source, audio_stream_indexes=[2, 3, 4], resolution="1080x1920"
    )
    assert json.loads(reference_match.group(1)) == expected_reference
    if previous_binding != "matching":
        assert expected_reference != old_reference
    original_match = re.search(r"const original = (.*?);\s+const sourceReference", page, re.S)
    assert original_match is not None
    assert json.loads(original_match.group(1)) == saved
    include_controls = re.findall(r'<input class="include"[^>]*>', page)
    assert len(include_controls) == 3
    assert sum("checked" in control for control in include_controls) == (
        1 if previous_binding == "matching" else 0
    )
    managed.assert_not_called()
    speech.assert_not_called()
    feedback.assert_not_called()
    assert media_stubs[0].call_count == media_stubs[1].call_count == 3
