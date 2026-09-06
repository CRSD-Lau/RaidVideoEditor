"""Native source binding, time conversion, and independent audio evidence."""

from __future__ import annotations

import json
import math
import os
import random
import time
from pathlib import Path
from typing import Any

import pytest

from raid_editor.config.models import ProjectConfig
from raid_editor.highlights import _portrait_sync as sync
from raid_editor.highlights import portrait
from raid_editor.ingestion.probe import AudioStream, MediaProbe, VideoStream


def source(offset: float = 2.35) -> portrait.PortraitSource:
    return portrait.PortraitSource(
        recording=Path("portrait.mkv"),
        landscape_recording=Path("landscape.mkv"),
        offset_seconds=offset,
        landscape_duration_seconds=100,
        duration_seconds=95,
        signature={},
    )


@pytest.mark.parametrize("offset,portrait_seek", [(2.35, "7.650000"), (-1.25, "11.250000")])
def test_input_times_preserve_landscape_audio_and_offset_sign(
    offset: float, portrait_seek: str
) -> None:
    bound = source(offset)
    assert bound.input_args(10, 20) == [
        "-ss",
        portrait_seek,
        "-i",
        "portrait.mkv",
        "-ss",
        "10.000000",
        "-i",
        "landscape.mkv",
    ]


@pytest.mark.parametrize(
    "start,end",
    [
        (0, 5),
        (95, 99),
        (-1, 10),
        (10, 9),
        (10, float("inf")),
        (float("nan"), 12),
    ],
)
def test_complete_highlight_window_must_fit_video_coverage(start: float, end: float) -> None:
    with pytest.raises(ValueError):
        source().validate_window(start, end)


def test_negative_offset_limits_final_coverage() -> None:
    bound = source(-2)
    bound.validate_window(0, 93)
    with pytest.raises(ValueError, match="coverage"):
        bound.validate_window(0, 93.1)


def test_discovery_requires_unique_close_filename_not_newest(tmp_path: Path) -> None:
    original = tmp_path / "2026-09-11 19-00-00.mkv"
    original.write_bytes(b"source")
    directory = tmp_path / "Vertical"
    directory.mkdir()
    unrelated = directory / "2026-09-11 20-00-00.mkv"
    unrelated.write_bytes(b"newer")
    assert portrait.discover_portrait_recording(original) is None
    match = directory / "2026-09-11 19-00-02.mkv"
    match.write_bytes(b"paired")
    assert portrait.discover_portrait_recording(original) == match.resolve()
    (directory / "2026-09-11 19-00-03.mp4").write_bytes(b"ambiguous")
    with pytest.raises(ValueError, match="Multiple"):
        portrait.discover_portrait_recording(original)


def test_discovery_does_not_infer_start_from_mtime_or_escape_directory(tmp_path: Path) -> None:
    original = tmp_path / "arbitrary-name.mkv"
    original.write_bytes(b"source")
    assert portrait.discover_portrait_recording(original) is None
    with pytest.raises(ValueError, match="within"):
        portrait.discover_portrait_recording(original, "../Other")


def _noise(length: int, seed: int = 7) -> list[float]:
    generator = random.Random(seed)  # noqa: S311 - reproducible synthetic audio, not cryptography
    return [0.2 + generator.random() for _ in range(length)]


def test_correlation_finds_unique_shift_and_rejects_wrong_pair() -> None:
    pattern = _noise(1200)
    search = _noise(100) + pattern + _noise(150, seed=9)
    shift, best, second = sync.correlate_envelopes(pattern, search)
    assert shift == 100 and best > 0.99
    assert best - second > sync.MIN_PEAK_MARGIN
    with pytest.raises(ValueError, match="does not clearly match"):
        sync.correlate_envelopes(pattern, _noise(1450, seed=123))


def test_correlation_rejects_silence_and_repetitive_audio() -> None:
    with pytest.raises(ValueError, match="silent"):
        sync.correlate_envelopes([0.0] * 800, [0.0] * 1200)
    period = [0.4 + 0.2 * math.sin(index * math.tau / 100) for index in range(100)]
    with pytest.raises(ValueError, match="ambiguous"):
        sync.correlate_envelopes(period * 8, period * 24)


@pytest.mark.parametrize("offset", [2.35, -1.27])
def test_separated_samples_recover_both_offset_signs(
    monkeypatch: pytest.MonkeyPatch,
    offset: float,
) -> None:
    timeline = _noise(18000)
    calls: list[tuple[Path, int, float, float]] = []

    def decode(path: Path, index: int, start: float, seconds: float) -> list[float]:
        calls.append((path, index, start, seconds))
        position = start + (offset if path.name == "portrait" else 0) + 20
        first = round(position * sync.ENVELOPE_RATE)
        return timeline[first : first + round(seconds * sync.ENVELOPE_RATE)]

    monkeypatch.setattr(sync, "decode_envelope", decode)
    measured, observations = sync.measure_audio_offset(
        Path("landscape"),
        Path("portrait"),
        landscape_stream_index=1,
        portrait_stream_index=3,
        landscape_duration=100,
        portrait_duration=95,
        hint_seconds=0,
    )
    assert measured == pytest.approx(offset, abs=0.011)
    assert len(observations) == 3
    assert observations[-1].landscape_start_seconds - observations[0].landscape_start_seconds > 60
    assert {index for path, index, _, _ in calls if path.name == "portrait"} == {3}


def test_well_correlated_windows_must_agree_on_constant_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sync, "decode_envelope", lambda _path, _index, start, _seconds: [start])
    offsets = iter([2.35, 2.42, 2.52])

    def correlate(reference: list[float], search: list[float]) -> tuple[int, float, float]:
        return round((reference[0] - search[0] - next(offsets)) * sync.ENVELOPE_RATE), 0.99, 0.2

    monkeypatch.setattr(sync, "correlate_envelopes", correlate)
    with pytest.raises(ValueError, match="drifts or disagrees"):
        sync.measure_audio_offset(
            Path("landscape"),
            Path("portrait"),
            landscape_stream_index=1,
            portrait_stream_index=2,
            landscape_duration=100,
            portrait_duration=95,
            hint_seconds=0,
        )


def _probe(path: Path, *, native: bool, role_index: int = 2) -> MediaProbe:
    return MediaProbe(
        source=portrait.quick_file_fingerprint(path),
        format_name="matroska",
        duration_seconds=120,
        size_bytes=path.stat().st_size,
        video_streams=[
            VideoStream(
                index=0,
                codec="h264",
                width=1080 if native else 1920,
                height=1920 if native else 1080,
                frame_rate=60,
                duration_seconds=95 if native else 100,
            )
        ],
        audio_streams=[
            AudioStream(
                index=role_index if native else 1,
                audio_ordinal=0,
                codec="aac",
                title="WoW Game",
            )
        ],
    )


@pytest.fixture
def project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ProjectConfig, Path, list[Any]]:
    landscape = tmp_path / "2026-09-11 19-00-00.mkv"
    native = tmp_path / "2026-09-11 19-00-02.mkv"
    for path in (landscape, native):
        path.write_bytes(path.name.encode())
        os.utime(path, (time.time() - 180, time.time() - 180))
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "Portrait test"},
            "input": {"recording": landscape, "vertical_recording": native},
            "audio": {"game_track": 1},
            "music": {"library": tmp_path / "music.json"},
            "highlights": {"video_source": "native_vertical"},
        }
    )
    calls: list[Any] = []
    monkeypatch.setattr(portrait.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(portrait, "probe_media", lambda path: _probe(path, native=path == native))

    def measure(*args: Any, **kwargs: Any) -> tuple[float, list[sync.SyncObservation]]:
        calls.append((args, kwargs))
        return 2.35, [sync.SyncObservation(start, 12, 2.35, 0.99, 0.2) for start in (5, 40, 80)]

    monkeypatch.setattr(portrait, "measure_audio_offset", measure)
    return config, tmp_path / "binding", calls


def test_resolver_cache_binds_both_sources_audio_roles_and_verified_offset(
    project: tuple[ProjectConfig, Path, list[Any]],
) -> None:
    config, destination, calls = project
    bound = portrait.resolve_portrait_source(config, destination)
    assert bound is not None
    assert bound.offset_seconds == 2.35 and bound.duration_seconds == 95
    assert calls[0][1]["landscape_stream_index"] == 1
    assert calls[0][1]["portrait_stream_index"] == 2
    assert calls[0][1]["hint_seconds"] == 2
    assert portrait.resolve_portrait_source(config, destination) == bound
    assert len(calls) == 1
    manifest = json.loads((destination / "portrait-source.json").read_text())
    assert manifest["author"] == manifest["last_modified_by"] == "Neil Mitchell"
    assert manifest["signature"]["status"] == "verified"
    assert manifest["signature"]["landscape"] == portrait.quick_file_fingerprint(
        config.input.recording
    )
    assert "samples" not in manifest["signature"]["sync"]


def test_resolver_discovers_configured_vertical_subdirectory(
    project: tuple[ProjectConfig, Path, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, destination, calls = project
    assert config.input.vertical_recording is not None
    native = config.input.vertical_recording
    custom_directory = native.parent / "NativePortrait"
    custom_directory.mkdir()
    discovered = custom_directory / native.name
    native.rename(discovered)
    config.input.vertical_recording = None
    config.preflight.vertical_recording_subdirectory = "NativePortrait"
    monkeypatch.setattr(
        portrait, "probe_media", lambda path: _probe(path, native=path == discovered)
    )
    bound = portrait.resolve_portrait_source(config, destination)
    assert bound is not None and bound.recording == discovered.resolve()
    assert len(calls) == 1


def test_cache_invalidation_for_hint_and_portrait_replacement(
    project: tuple[ProjectConfig, Path, list[Any]],
) -> None:
    config, destination, calls = project
    portrait.resolve_portrait_source(config, destination)
    config.highlights.vertical_offset_hint_seconds = 2.1
    portrait.resolve_portrait_source(config, destination)
    assert len(calls) == 2
    assert config.input.vertical_recording is not None
    config.input.vertical_recording.write_bytes(b"different recording")
    os.utime(config.input.vertical_recording, (time.time() - 180, time.time() - 180))
    portrait.resolve_portrait_source(config, destination)
    assert len(calls) == 3


def test_corrupted_verified_cache_does_not_bypass_sampling(
    project: tuple[ProjectConfig, Path, list[Any]],
) -> None:
    config, destination, calls = project
    portrait.resolve_portrait_source(config, destination)
    cache = destination / "portrait-source.json"
    payload = json.loads(cache.read_text())
    payload["signature"]["offset_seconds"] = 99
    cache.write_text(json.dumps(payload))
    result = portrait.resolve_portrait_source(config, destination)
    assert result is not None and result.offset_seconds == 2.35 and len(calls) == 2


def test_landscape_mode_does_not_probe_or_require_native_file(
    project: tuple[ProjectConfig, Path, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, destination, _calls = project
    config.highlights.video_source = "landscape"
    monkeypatch.setattr(portrait, "probe_media", lambda _path: pytest.fail("must not probe"))
    assert portrait.resolve_portrait_source(config, destination) is None
    assert not destination.exists()


@pytest.mark.parametrize("problem", ["missing", "fresh", "wrong_landscape_role", "ambiguous_role"])
def test_unverified_pair_never_writes_binding(
    project: tuple[ProjectConfig, Path, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    config, destination, _calls = project
    assert config.input.vertical_recording is not None
    if problem == "missing":
        config.input.vertical_recording.unlink()
    elif problem == "fresh":
        os.utime(config.input.vertical_recording, None)
    elif problem == "wrong_landscape_role":
        config.audio.game_track = 7
    else:
        original = portrait.probe_media

        def ambiguous(path: Path) -> MediaProbe:
            result = original(path)
            if path == config.input.vertical_recording:
                result.audio_streams.append(
                    AudioStream(
                        index=5,
                        audio_ordinal=1,
                        codec="aac",
                        title="WoW Game",
                    )
                )
            return result

        monkeypatch.setattr(portrait, "probe_media", ambiguous)
    with pytest.raises(ValueError):
        portrait.resolve_portrait_source(config, destination)
    assert not (destination / "portrait-source.json").exists()


def test_source_mutation_during_sampling_is_rejected(
    project: tuple[ProjectConfig, Path, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, destination, _calls = project
    original = portrait.measure_audio_offset

    def mutate(*args: Any, **kwargs: Any) -> Any:
        config.input.recording.write_bytes(b"changed while validating")
        return original(*args, **kwargs)

    monkeypatch.setattr(portrait, "measure_audio_offset", mutate)
    with pytest.raises(ValueError, match="changed during"):
        portrait.resolve_portrait_source(config, destination)
    assert not (destination / "portrait-source.json").exists()


def test_video_tail_limits_coverage_when_audio_outlasts_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "native.mkv"
    path.write_bytes(b"fixture")
    probe = _probe(path, native=True)
    probe.video_streams[0].duration_seconds = None
    probe.duration_seconds = 120
    monkeypatch.setattr(
        portrait,
        "_ffprobe_packets",
        lambda _path, _start: [
            {"pts_time": "94.966667", "duration_time": "0.016667"},
            {"pts_time": "94.983333", "duration_time": "0.016667"},
        ],
    )
    assert portrait._video_duration(path, probe) == pytest.approx(95)


def test_geometry_rejects_landscape_native_or_mismatched_fps(tmp_path: Path) -> None:
    path = tmp_path / "file.mkv"
    path.write_bytes(b"fixture")
    original, native = _probe(path, native=False), _probe(path, native=True)
    native.video_streams[0].frame_rate = 30
    with pytest.raises(ValueError, match="frame rates"):
        portrait._geometry(original, native, "1080x1920")
    native.video_streams[0].frame_rate = 60
    native.video_streams[0].width = 1920
    with pytest.raises(ValueError, match="must be"):
        portrait._geometry(original, native, "1080x1920")
