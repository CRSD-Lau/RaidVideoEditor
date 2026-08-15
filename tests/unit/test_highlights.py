from __future__ import annotations

import json
from pathlib import Path

import pytest

from raid_editor.config.models import HighlightConfig
from raid_editor.highlights import detection as highlight_detection
from raid_editor.highlights.detection import (
    HighlightAnalysisError,
    Signal,
    analyse_highlights,
    build_highlight_candidates,
)
from raid_editor.highlights.render import (
    HighlightRenderError,
    render_vertical_highlights,
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


def test_fused_discord_and_motion_signal_is_review_only_funny_candidate() -> None:
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
    assert candidates[0].category == "funny"
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


def test_lich_king_climax_is_reserved_inside_a_bounded_candidate_list() -> None:
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
    assert any(candidate.encounter == "The Lich King" for candidate in candidates)
    assert all(candidate.include is False for candidate in candidates)


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
