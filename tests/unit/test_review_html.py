from pathlib import Path

from raid_editor.models import PullCandidate
from raid_editor.review import html as review_html


def test_full_pull_review_includes_lead_in_and_lead_out(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"generated")

    monkeypatch.setattr(review_html, "_run", fake_run)
    (tmp_path / "source.mp4").write_bytes(b"source")
    pull = PullCandidate(
        id="boss-1",
        start_seconds=20.0,
        end_seconds=50.0,
        type="boss_kill",
        encounter="Test Boss",
        result="kill",
        confidence=1.0,
        title="Test Boss",
    )

    assets = review_html.generate_pull_media(
        tmp_path / "source.mp4",
        [pull],
        tmp_path / "review",
        [2],
        max_preview_seconds=None,
        lead_in_seconds=5.0,
        lead_out_seconds=8.0,
        recording_duration_seconds=100.0,
    )

    preview = assets["boss-1"]["preview"]
    assert preview.name == "boss-1-full.webm"
    preview_command = commands[1]
    assert preview_command[preview_command.index("-ss") + 1] == "15.000"
    assert preview_command[preview_command.index("-t") + 1] == "43.000"
    assert preview_command[preview_command.index("-vf") + 1] == "scale=960:-2,fps=30"
    assert "0:2" in preview_command
    assert "libvpx-vp9" in preview_command
    assert "libopus" in preview_command
    assert "yuv420p" in preview_command

    destination = tmp_path / "review" / "pull-review.html"
    review_html.generate_pull_review_page([pull], assets, destination)
    page = destination.read_text(encoding="utf-8")
    assert "Full winning take" in page
    assert 'poster="assets/boss-1.jpg"' in page
    assert 'href="#boss-1"' in page
    assert "Adjust cut or notes" in page
    assert 'type="video/webm"' in page
    assert 'name="author" content="Neil Mitchell"' in page


def test_pull_review_cache_tracks_format_audio_bounds_and_source(tmp_path: Path, monkeypatch):
    commands = []

    def fake_run(command):
        commands.append(command)
        Path(command[-1]).write_bytes(b"generated")

    monkeypatch.setattr(review_html, "_run", fake_run)
    recording = tmp_path / "source.mp4"
    recording.write_bytes(b"source")
    pull = PullCandidate(
        id="boss", start_seconds=20, end_seconds=50, type="boss_kill", result="kill"
    )
    destination = tmp_path / "review"
    first = review_html.generate_pull_media(recording, [pull], destination, [2])
    review_html.generate_pull_media(recording, [pull], destination, [2])
    assert len(commands) == 2  # One poster and one video; unchanged call is cached.
    review_html.generate_pull_media(recording, [pull], destination, [3])
    assert len(commands) == 3
    changed = pull.model_copy(update={"start_seconds": 21.0})
    review_html.generate_pull_media(recording, [changed], destination, [3])
    assert len(commands) == 4
    recording.write_bytes(b"new source")
    review_html.generate_pull_media(recording, [changed], destination, [3])
    assert len(commands) == 5
    mp4 = review_html.generate_pull_media(
        recording, [changed], destination, [3], media_format="mp4"
    )
    assert len(commands) == 6
    assert mp4["boss"]["preview"].suffix == ".mp4"
    assert "libx264" in commands[-1] and "aac" in commands[-1]
    assert first["boss"]["preview"].is_file()  # Format switch never deletes old media.


def test_failed_pull_review_render_does_not_cache_partial_file(tmp_path: Path, monkeypatch):
    import pytest

    def fake_run(command):
        Path(command[-1]).write_bytes(b"partial")
        if "-t" in command:
            raise review_html.ReviewGenerationError("interrupted")

    monkeypatch.setattr(review_html, "_run", fake_run)
    recording = tmp_path / "source.mp4"
    recording.write_bytes(b"source")
    pull = PullCandidate(id="boss", start_seconds=20, end_seconds=50)
    with pytest.raises(review_html.ReviewGenerationError):
        review_html.generate_pull_media(recording, [pull], tmp_path / "review", [2])
    assert not list((tmp_path / "review" / "assets").glob("*.webm*"))
