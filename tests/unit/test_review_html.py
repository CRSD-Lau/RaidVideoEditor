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
    assert preview.name == "boss-1-full.mp4"
    preview_command = commands[1]
    assert preview_command[preview_command.index("-ss") + 1] == "15.000"
    assert preview_command[preview_command.index("-t") + 1] == "43.000"
    assert preview_command[preview_command.index("-vf") + 1] == "scale=960:-2,fps=30"
    assert "0:2" in preview_command

    destination = tmp_path / "review" / "pull-review.html"
    review_html.generate_pull_review_page([pull], assets, destination)
    page = destination.read_text(encoding="utf-8")
    assert "Full winning take" in page
    assert 'poster="assets/boss-1.jpg"' in page
    assert 'href="#boss-1"' in page
    assert "Adjust cut or notes" in page
