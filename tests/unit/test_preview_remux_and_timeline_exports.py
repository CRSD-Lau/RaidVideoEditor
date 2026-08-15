from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import raid_editor.audio.tracks as audio_tracks
from raid_editor.audio.tracks import AudioMappingError, create_mic_free_remux
from raid_editor.config.models import PresentationConfig, WatermarkConfig
from raid_editor.ingestion.probe import AudioStream, MediaProbe
from raid_editor.models import TimelineClip, TimelineDocument
from raid_editor.rendering.preview import (
    FinalRenderError,
    build_filter_graph,
    build_final_command,
    build_preview_command,
    render_final,
)
from raid_editor.timeline.export import (
    write_chapters,
    write_fcpxml,
    write_labels_srt,
    write_timeline_json,
)
from raid_editor.util.paths import quick_file_fingerprint


def _timeline(source: Path) -> TimelineDocument:
    return TimelineDocument(
        timeline_name="Deterministic Condensed Review",
        source=source,
        source_duration_seconds=30.0,
        source_fps=30.0,
        retained_audio_stream_indexes=[2, 3],
        excluded_microphone_stream_index=4,
        clips=[
            TimelineClip(
                source_in=1.0,
                source_out=4.0,
                timeline_in=0.0,
                label="Trash pull",
                type="trash_pull",
                result="unknown",
                transition_out="fade",
                pull_ids=["trash-1"],
            ),
            TimelineClip(
                source_in=10.0,
                source_out=12.0,
                timeline_in=3.0,
                label="Boss — Wipe",
                type="boss_wipe",
                result="wipe",
                encounter="Lord Marrowgar",
                difficulty="25H",
                transition_in="fade",
                pull_ids=["boss-1"],
            ),
        ],
    )


def _verified_sidecar_probe() -> MediaProbe:
    return MediaProbe(
        source={"path": "sidecar.mov"},
        format_name="mov",
        duration_seconds=30.0,
        size_bytes=128,
        video_streams=[],
        audio_streams=[
            AudioStream(index=1, audio_ordinal=0, codec="aac"),
            AudioStream(index=2, audio_ordinal=1, codec="aac"),
        ],
    )


def test_preview_filter_graph_includes_only_retained_audio_streams(tmp_path: Path) -> None:
    # Arrange
    timeline = _timeline(tmp_path / "source.mkv")

    # Act
    graph = build_filter_graph(
        timeline,
        width=1280,
        height=720,
        fps=30,
        transition_seconds=0.2,
        music=None,
    )

    # Assert
    assert graph.count("[0:2]atrim=") == len(timeline.clips)
    assert graph.count("[0:3]atrim=") == len(timeline.clips)
    assert "[0:4]" not in graph
    assert "amix=inputs=2" in graph
    assert "HEROIC" in graph
    assert "25 PLAYER" in graph


def test_preview_command_maps_only_filtered_review_outputs(tmp_path: Path) -> None:
    # Arrange
    timeline = _timeline(tmp_path / "source recording.mkv")
    filter_script = tmp_path / "review.filters.txt"
    destination = tmp_path / "review copy.mp4"

    # Act
    command = build_preview_command(
        timeline,
        filter_script,
        destination,
        bitrate="1M",
        music=None,
    )

    # Assert
    mapped_values = [
        command[index + 1] for index, argument in enumerate(command[:-1]) if argument == "-map"
    ]
    assert mapped_values == ["[vout]", "[aout]"]
    assert command.count("-i") == 1
    assert "-/filter_complex" in command
    assert command[command.index("-c:v") + 1] == "libx264"
    assert str(timeline.source) in command
    assert str(destination) == command[-1]
    assert "Review render only" in " ".join(command)


def test_preview_command_can_use_nvenc_for_a_configured_review(tmp_path: Path) -> None:
    command = build_preview_command(
        _timeline(tmp_path / "source.mkv"),
        tmp_path / "review.filters.txt",
        tmp_path / "review.mp4",
        bitrate="4M",
        music=None,
        hardware_encoding=True,
    )

    assert command[command.index("-c:v") + 1] == "h264_nvenc"


def test_preview_graph_and_command_overlay_a_camera_cover(tmp_path: Path) -> None:
    cover = tmp_path / "camera-cover.png"
    cover.write_bytes(b"image placeholder")
    watermark = WatermarkConfig(
        image=cover,
        x_fraction=0,
        y_fraction=0.75,
        width_fraction=0.25,
        height_fraction=0.25,
    )

    graph = build_filter_graph(
        _timeline(tmp_path / "source.mkv"),
        width=1280,
        height=720,
        fps=30,
        transition_seconds=0.2,
        music=None,
        watermark=watermark,
    )
    command = build_preview_command(
        _timeline(tmp_path / "source.mkv"),
        tmp_path / "review.filters.txt",
        tmp_path / "review.mp4",
        bitrate="4M",
        music=None,
        watermark=cover,
    )

    assert "scale=320:180" in graph
    assert "overlay=x=0:y=540" in graph
    assert command[command.index("-loop") + 1] == "1"
    assert str(cover) in command


def test_preview_command_loops_an_animated_gif_watermark(tmp_path: Path) -> None:
    logo = tmp_path / "pizza-warriors.gif"
    logo.write_bytes(b"animated image placeholder")

    command = build_preview_command(
        _timeline(tmp_path / "source.mkv"),
        tmp_path / "review.filters.txt",
        tmp_path / "review.mp4",
        bitrate="4M",
        music=None,
        watermark=logo,
    )

    assert command[command.index("-stream_loop") + 1] == "-1"
    assert command[command.index("-ignore_loop") + 1] == "1"
    assert "-loop" not in command
    assert str(logo) in command


def test_preview_graph_adds_branded_intro_outro_and_boss_titles(tmp_path: Path) -> None:
    logo = tmp_path / "pizza-warriors.gif"
    logo.write_bytes(b"animated image placeholder")
    watermark = WatermarkConfig(
        image=logo,
        x_fraction=0,
        y_fraction=0.75,
        width_fraction=0.25,
        height_fraction=0.25,
    )
    presentation = PresentationConfig(
        intro_seconds=5,
        outro_seconds=5,
        intro_title="PIZZA WARRIORS",
        intro_subtitle="ICECROWN CITADEL",
        outro_title="RAID COMPLETE",
        outro_subtitle="PIZZA WARRIORS",
        boss_kicker="PIZZA WARRIORS — ICC",
    )

    graph = build_filter_graph(
        _timeline(tmp_path / "source.mkv"),
        width=1280,
        height=720,
        fps=30,
        transition_seconds=0.2,
        music=None,
        watermark=watermark,
        presentation=presentation,
    )

    assert "split=3[watermark_raw][intro_logo_raw][outro_logo_raw]" in graph
    assert "text='PIZZA WARRIORS'" in graph
    assert "text='ICECROWN CITADEL'" in graph
    assert "text='RAID COMPLETE'" in graph
    assert "text='PIZZA WARRIORS — ICC'" in graph
    assert "fontfile='C\\:/Windows/Fonts/georgiab.ttf'" in graph
    assert "concat=n=3:v=1:a=0" in graph
    assert graph.count("anullsrc=r=48000:cl=stereo") == 2
    assert "concat=n=3:v=0:a=1[aout]" in graph


def test_branded_graph_scales_title_geometry_for_1440p(tmp_path: Path) -> None:
    logo = tmp_path / "pizza-warriors.gif"
    logo.write_bytes(b"animated image placeholder")

    graph = build_filter_graph(
        _timeline(tmp_path / "source.mkv"),
        width=2560,
        height=1440,
        fps=60,
        transition_seconds=0.2,
        music=None,
        watermark=WatermarkConfig(
            image=logo,
            x_fraction=0,
            y_fraction=0.75,
            width_fraction=0.25,
            height_fraction=0.25,
        ),
        presentation=PresentationConfig(),
    )

    assert "drawbox=x=48:y=36:w=1408:h=188" in graph
    assert "fontcolor=0xF2D28B:fontsize=64:x=96:y=56" in graph
    assert "drawbox=x=(iw-1240)/2:y=800:w=1240:h=4" in graph
    assert "fontcolor=0xF2D28B:fontsize=88" in graph


def test_final_command_uses_constant_quality_and_never_uploads(tmp_path: Path) -> None:
    logo = tmp_path / "pizza-warriors.gif"
    logo.write_bytes(b"animated image placeholder")

    command = build_final_command(
        _timeline(tmp_path / "source.mkv"),
        tmp_path / "final.filters.txt",
        tmp_path / "final.rendering.mp4",
        codec="h264",
        constant_qp=18,
        preset="p6",
        audio_bitrate="320k",
        music=None,
        hardware_encoding=True,
        watermark=logo,
    )

    assert command[command.index("-c:v") + 1] == "h264_nvenc"
    assert command[command.index("-rc") + 1] == "constqp"
    assert command[command.index("-qp") + 1] == "18"
    assert command[command.index("-b:a") + 1] == "320k"
    assert "-stream_loop" in command
    assert not any("upload" in argument.casefold() for argument in command)


def test_final_render_requires_explicit_approval(tmp_path: Path) -> None:
    with pytest.raises(FinalRenderError, match="requires explicit approval"):
        render_final(
            _timeline(tmp_path / "source.mkv"),
            tmp_path / "final.mp4",
            resolution="2560x1440",
            fps=60,
            codec="h264",
            constant_qp=18,
            preset="p6",
            audio_bitrate="320k",
            transition_seconds=0.2,
        )


def test_mic_free_remux_maps_retained_streams_and_never_changes_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    source = tmp_path / "source raid.mkv"
    source.write_bytes(b"immutable source recording")
    destination = tmp_path / "generated" / "source-microphone-free.mov"
    before = quick_file_fingerprint(source, chunk_bytes=8)
    observed: dict[str, list[str]] = {}

    def fake_ffmpeg(command: list[str]) -> None:
        observed["command"] = command
        Path(command[-1]).write_bytes(b"verified sidecar")

    monkeypatch.setattr(audio_tracks, "_run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(audio_tracks, "probe_media", lambda _: _verified_sidecar_probe())

    # Act
    result = create_mic_free_remux(
        source,
        retained_stream_indexes=[2, 3],
        microphone_stream_index=4,
        destination=destination,
    )

    # Assert
    command = observed["command"]
    mapped_values = [
        command[index + 1] for index, argument in enumerate(command[:-1]) if argument == "-map"
    ]
    assert result == destination
    assert mapped_values == ["0:v:0", "0:2", "0:3"]
    assert "0:4" not in command
    assert command[command.index("-c") + 1] == "copy"
    assert quick_file_fingerprint(source, chunk_bytes=8) == before
    assert destination.with_suffix(".mov.json").is_file()


@pytest.mark.parametrize(
    ("retained", "microphone", "message"),
    [
        ([], 4, "without retained audio tracks"),
        ([2, 4], 4, "Refusing to retain the configured microphone stream"),
    ],
)
def test_mic_free_remux_rejects_unsafe_mapping_before_running_ffmpeg(
    retained: list[int],
    microphone: int,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    source = tmp_path / "source.mkv"
    source.write_bytes(b"immutable")
    destination = tmp_path / "sidecar.mov"

    def unexpected_ffmpeg(_: list[str]) -> None:
        raise AssertionError("FFmpeg must not run for an unsafe mapping")

    monkeypatch.setattr(audio_tracks, "_run_ffmpeg", unexpected_ffmpeg)

    # Act / Assert
    with pytest.raises(AudioMappingError, match=message):
        create_mic_free_remux(
            source,
            retained_stream_indexes=retained,
            microphone_stream_index=microphone,
            destination=destination,
        )
    assert source.read_bytes() == b"immutable"
    assert not destination.exists()


def test_timeline_exports_preserve_clip_timing_and_use_the_mic_free_asset(
    tmp_path: Path,
) -> None:
    # Arrange
    source = tmp_path / "source.mkv"
    sidecar = tmp_path / "source-microphone-free.mov"
    timeline = _timeline(source)
    timeline_json = tmp_path / "timeline.json"
    fcpxml = tmp_path / "timeline.fcpxml"
    labels = tmp_path / "pull-labels.srt"
    chapters = tmp_path / "chapters.txt"

    # Act
    write_timeline_json(timeline, timeline_json)
    write_fcpxml(timeline, fcpxml, media_path=sidecar, width=1920, height=1080)
    write_labels_srt(timeline, labels)
    write_chapters(timeline, chapters)

    # Assert
    payload = json.loads(timeline_json.read_text(encoding="utf-8"))
    root = ET.parse(fcpxml).getroot()  # noqa: S314 - generated locally by the test
    asset = root.find("./resources/asset")
    clips = root.findall("./library/event/project/sequence/spine/asset-clip")
    assert payload["condensed_duration_seconds"] == 5.0
    assert payload["retained_audio_stream_indexes"] == [2, 3]
    assert payload["excluded_microphone_stream_index"] == 4
    assert asset is not None
    assert asset.attrib["src"] == sidecar.resolve().as_uri()
    assert asset.attrib["hasAudio"] == "1"
    assert [clip.attrib["start"] for clip in clips] == ["30/30s", "300/30s"]
    assert [clip.attrib["offset"] for clip in clips] == ["0/30s", "90/30s"]
    assert "00:00:00,000 --> 00:00:03,000\nTrash pull" in labels.read_text(encoding="utf-8")
    assert chapters.read_text(encoding="utf-8").splitlines() == [
        "00:00 Trash pull",
        "00:03 Boss — Wipe",
    ]
