"""Native presentation approval and render integration regressions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from raid_editor import workflow
from raid_editor.config.models import ProjectConfig
from raid_editor.highlights.portrait import PortraitSource
from raid_editor.highlights.render import (
    HighlightRenderError,
    _filter_graph,
    portrait_presentation_reference,
    render_vertical_highlights,
)
from raid_editor.highlights.review import (
    HighlightReviewError,
    generate_highlight_comparison_page,
    generate_highlight_review_media,
    generate_highlight_review_page,
)
from raid_editor.models import HighlightCandidate


def candidate() -> HighlightCandidate:
    return HighlightCandidate(
        id="highlight-001",
        start_seconds=43,
        end_seconds=49,
        peak_seconds=46,
        category="reaction",
        title="Native candidate",
        score=0.8,
        include=True,
    )


def source(tmp_path: Path, offset: float = 2.35) -> PortraitSource:
    return PortraitSource(
        recording=tmp_path / "portrait.mp4",
        landscape_recording=tmp_path / "landscape.mp4",
        offset_seconds=offset,
        landscape_duration_seconds=100,
        duration_seconds=95,
        signature={
            "portrait": {"head_tail_sha256": "new-native"},
            "landscape": {"head_tail_sha256": "landscape"},
        },
    )


def config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "project": {"name": "Native gate fixture"},
            "input": {"recording": tmp_path / "landscape.mp4"},
            "audio": {"game_track": 2, "discord_track": 3, "microphone_track": 4},
            "music": {"library": tmp_path / "unused.json"},
            "highlights": {"video_source": "native_vertical", "keep_microphone_audio": True},
        }
    )


def test_native_graph_preserves_picture_and_uses_landscape_stems() -> None:
    graph = _filter_graph(
        candidate(), width=1080, height=1920, audio_stream_indexes=[2, 3, 4], native_portrait=True
    )
    assert "[0:v:0]setpts=PTS-STARTPTS" in graph
    assert all(f"[1:{index}]asetpts=PTS-STARTPTS" in graph for index in (2, 3, 4))
    assert "gblur" not in graph and "drawtext" not in graph and "overlay" not in graph


def test_presentation_identity_tracks_picture_offset_mix_and_resolution(tmp_path: Path) -> None:
    original = source(tmp_path)

    def ref(
        value: PortraitSource, audio: list[int] | None = None, resolution: str = "1080x1920"
    ) -> str:
        return portrait_presentation_reference(
            value, audio_stream_indexes=audio or [2, 3, 4], resolution=resolution
        )

    reference = ref(original)
    assert reference == ref(source(tmp_path))
    assert reference != ref(source(tmp_path, offset=2.40))
    changed = source(tmp_path)
    changed.signature["portrait"] = {"head_tail_sha256": "replacement"}
    assert reference != ref(changed)
    assert reference != ref(original, audio=[2, 3])
    assert reference != ref(original, resolution="720x1280")


@pytest.mark.parametrize("old_reference", [None, "old-landscape-review", "old-native-picture"])
def test_native_export_rejects_unbound_or_stale_selection_before_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_reference: str | None,
) -> None:
    project = config(tmp_path)
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {"presentation_reference": old_reference, "highlights": [candidate().model_dump()]}
        )
    )
    project.highlights.manual_selection = selection
    monkeypatch.setattr(workflow, "project_output_dir", lambda _: tmp_path / "output")
    monkeypatch.setattr(workflow, "resolve_portrait_source", lambda *_: source(tmp_path))
    analyse, render = Mock(), Mock()
    monkeypatch.setattr(workflow, "analyse_highlights_project", analyse)
    monkeypatch.setattr(workflow, "render_vertical_highlights", render)
    with pytest.raises(ValueError, match="current picture, timing and audio"):
        workflow.render_highlights_project(project, approved=True)
    analyse.assert_not_called()
    render.assert_not_called()


def test_native_export_passes_exact_bound_source_and_intervals(tmp_path: Path) -> None:
    project = config(tmp_path)
    native = source(tmp_path)
    root = tmp_path / "exports"
    render_vertical_highlights(
        project.input.recording,
        [candidate()],
        root,
        audio_stream_indexes=[2, 3, 4],
        microphone_stream_index=4,
        settings=project.highlights,
        approved=True,
        dry_run=True,
        portrait_source=native,
    )
    clip = json.loads((root / "manifest.json").read_text())["clips"][0]
    args = clip["command"]
    inputs = [args[i + 1] for i, arg in enumerate(args) if arg == "-i"]
    assert inputs == [str(native.recording), str(project.input.recording)]
    assert clip["portrait_start_seconds"] == pytest.approx(40.65)
    assert clip["portrait_end_seconds"] == pytest.approx(46.65)
    assert clip["audio_recording"] == str(project.input.recording.resolve())
    assert clip["source_binding"] == native.signature
    assert clip["output_fingerprint"] is None
    assert clip["rendered"] is False


def test_native_review_download_binds_presentation_and_keeps_clock_explicit(tmp_path: Path) -> None:
    path = tmp_path / "index.html"
    generate_highlight_review_page(
        [candidate().model_copy(update={"include": False})],
        {"highlight-001": tmp_path / "test.webm"},
        path,
        includes_game=True,
        includes_discord=True,
        includes_microphone=True,
        native_portrait=True,
        presentation_reference="f" * 64,
    )
    page = path.read_text()
    assert "Native portrait recording with aligned landscape audio" in page
    assert "Times below use the landscape recording clock" in page
    assert 'const presentationReference = "' + "f" * 64 + '"' in page
    assert "presentation_reference: presentationReference" in page
    assert 'class="include" type="checkbox" >' in page


def test_native_media_rejects_mismatched_landscape_argument(tmp_path: Path) -> None:
    native = source(tmp_path)
    with pytest.raises(HighlightRenderError, match="different landscape"):
        render_vertical_highlights(
            tmp_path / "wrong.mp4",
            [candidate()],
            tmp_path / "export",
            audio_stream_indexes=[2, 3, 4],
            microphone_stream_index=4,
            settings=config(tmp_path).highlights,
            approved=True,
            dry_run=True,
            portrait_source=native,
        )
    with pytest.raises(HighlightReviewError, match="different landscape"):
        generate_highlight_review_media(
            tmp_path / "wrong.mp4",
            [candidate()],
            tmp_path / "review",
            audio_stream_indexes=[2, 3, 4],
            portrait_source=native,
        )
    assert not (tmp_path / "export").exists()
    assert not (tmp_path / "review").exists()


def test_comparison_feedback_identity_and_manifest_track_presentation(tmp_path: Path) -> None:
    original = candidate().model_copy(update={"review_identity": "c" * 64})
    variants = {"example": ([original], {original.id: tmp_path / "clip.webm"})}
    refs = [
        workflow._review_source_reference(tmp_path / "candidates.json", {"id": 1}, value)
        for value in ("a" * 64, "b" * 64)
    ]
    assert refs[0] != refs[1]
    manifests = [
        generate_highlight_comparison_page(
            variants,
            tmp_path / f"comparison-{index}.html",
            source_reference=reference,
            includes_game=True,
            includes_discord=False,
            includes_microphone=False,
            presentation_reference=("a" if index == 0 else "b") * 64,
        )
        for index, reference in enumerate(refs)
    ]
    assert manifests[0]["clips"] != manifests[1]["clips"]
    assert manifests[0]["source_reference"] != manifests[1]["source_reference"]
    assert manifests[0]["presentation_reference"] == "a" * 64
