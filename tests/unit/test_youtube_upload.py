from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from raid_editor.config.models import ProjectConfig
from raid_editor.models import PullCandidate, TimelineClip, TimelineDocument
from raid_editor.youtube import upload as youtube_upload
from raid_editor.youtube.upload import YouTubeUploadError


def _config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "project": {
                "name": "Pizza Warriors ICC",
                "game": "World of Warcraft",
                "expansion": "Wrath of the Lich King",
                "raid": "Icecrown Citadel",
                "raid_date": "2026-07-31",
            },
            "input": {"recording": str(tmp_path / "raid.mp4")},
            "audio": {"game_track": 2, "microphone_track": 4},
            "music": {"library": str(tmp_path / "music.json")},
            "preview": {
                "presentation": {
                    "intro_seconds": 5,
                    "outro_seconds": 5,
                    "intro_title": "PIZZA WARRIORS",
                    "outro_title": "RAID COMPLETE",
                }
            },
            "youtube": {
                "enabled": True,
                "client_secrets": str(tmp_path / "youtube-client.local.json"),
                "token": str(tmp_path / "youtube-token.local.json"),
                "title": "Icecrown Citadel Full Raid Clear | Pizza Warriors WoW WotLK",
                "privacy_status": "private",
                "tags": ["World of Warcraft", "Pizza Warriors"],
                "category_name": "Gaming",
                "game_title": "World of Warcraft",
                "game_rating": "Unrated",
                "hashtags": ["#WorldOfWarcraft", "#WotLK", "#IcecrownCitadel"],
            },
        }
    )


def _timeline(tmp_path: Path) -> TimelineDocument:
    return TimelineDocument(
        timeline_name="Pizza Warriors ICC",
        source=tmp_path / "raid.mp4",
        source_duration_seconds=1200,
        source_fps=60,
        retained_audio_stream_indexes=[2],
        excluded_microphone_stream_index=4,
        clips=[
            TimelineClip(
                source_in=10,
                source_out=70,
                timeline_in=0,
                label="Lord Marrowgar",
                type="boss_kill",
                result="kill",
            ),
            TimelineClip(
                source_in=200,
                source_out=290,
                timeline_in=60,
                label="Lady Deathwhisper",
                type="boss_kill",
                result="kill",
            ),
        ],
    )


def _package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> youtube_upload.YouTubePackage:
    video = tmp_path / "raid.mp4"
    video.write_bytes(b"approved-final-master")

    def fake_thumbnail(_video: Path, destination: Path, **_options: object) -> None:
        destination.write_bytes(b"jpeg")

    monkeypatch.setattr(youtube_upload, "_create_thumbnail", fake_thumbnail)
    return youtube_upload.write_youtube_package(
        _config(tmp_path),
        _timeline(tmp_path),
        video,
        tmp_path / "youtube",
    )


def test_youtube_package_contains_title_chapters_and_private_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package(tmp_path, monkeypatch)

    metadata = json.loads(package.metadata.read_text(encoding="utf-8"))
    chapters = package.chapters.read_text(encoding="utf-8").splitlines()

    assert metadata["title"] == ("Icecrown Citadel Full Raid Clear | Pizza Warriors WoW WotLK")
    assert metadata["privacy_status"] == "private"
    assert metadata["made_for_kids"] is False
    assert metadata["category_id"] == "20"
    assert metadata["category_name"] == "Gaming"
    assert metadata["game_title"] == "World of Warcraft"
    assert metadata["game_rating"] == "Unrated"
    assert metadata["hashtags"] == [
        "#WorldOfWarcraft",
        "#WotLK",
        "#IcecrownCitadel",
    ]
    assert chapters == [
        "00:00 Intro",
        "00:05 Lord Marrowgar",
        "01:05 Lady Deathwhisper",
        "02:35 Outro",
    ]
    assert "Pizza Warriors take on Icecrown Citadel" in metadata["description"]
    assert "Boss chapters" in metadata["description"]
    assert "—" not in metadata["title"]
    assert "—" not in metadata["description"]
    studio_details = package.studio_details.read_text(encoding="utf-8")
    assert "World of Warcraft" in studio_details
    assert "Current YouTube Studio may not expose" in studio_details
    assert "YouTube Data API" in package.root.joinpath("upload-checklist.md").read_text(
        encoding="utf-8"
    )
    assert package.thumbnail.read_bytes() == b"jpeg"


def test_youtube_package_builds_confirmed_scoreline_chapters_and_three_thumbnails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "raid.mp4"
    video.write_bytes(b"approved-final-master")
    bosses = [
        "Lord Marrowgar",
        "Lady Deathwhisper",
        "Gunship Battle",
        "Deathbringer Saurfang",
        "Festergut",
        "Rotface",
        "Professor Putricide",
        "Blood Prince Council",
        "Blood-Queen Lana'thel",
        "Valithria Dreamwalker",
        "Sindragosa",
        "The Lich King",
    ]
    heroic_indexes = {1, 3, 4, 5, 6, 8, 10}
    pulls = [
        PullCandidate(
            id=f"pull-{index:03d}",
            start_seconds=index * 100,
            end_seconds=index * 100 + 80,
            type="boss_kill",
            encounter=boss,
            result="kill",
            difficulty="25H" if index in heroic_indexes else "25N",
            difficulty_confidence="high",
        )
        for index, boss in enumerate(bosses, start=1)
    ]

    def fake_thumbnail(_video: Path, destination: Path, **_options: object) -> None:
        destination.write_bytes(b"jpeg")

    monkeypatch.setattr(youtube_upload, "_create_thumbnail", fake_thumbnail)
    base = _config(tmp_path)
    config = base.model_copy(
        update={
            "youtube": base.youtube.model_copy(update={"title": None}),
            "difficulty": base.difficulty.model_copy(
                update={"expected_bosses": 12, "title_raid_abbreviation": "ICC"}
            ),
        }
    )
    package = youtube_upload.write_youtube_package(
        config,
        _timeline(tmp_path),
        video,
        tmp_path / "youtube-auto",
        pulls=pulls,
    )

    metadata = json.loads(package.metadata.read_text(encoding="utf-8"))
    chapters = package.chapters.read_text(encoding="utf-8")

    assert metadata["title"] == ("ICC 25M 12/12 7HC Full Clear | Pizza Warriors WoW WotLK | Jul 31")
    assert metadata["title_ready"] is True
    assert "00:05 Lord Marrowgar (Heroic)" in chapters
    assert "01:05 Lady Deathwhisper (Normal)" in chapters
    assert len(package.thumbnail_candidates) == 3
    assert all(path.read_bytes() == b"jpeg" for path in package.thumbnail_candidates)


def test_youtube_upload_refuses_to_authenticate_without_explicit_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package(tmp_path, monkeypatch)

    with pytest.raises(YouTubeUploadError, match="requires explicit approval"):
        youtube_upload.upload_youtube_video(
            _config(tmp_path),
            package,
            approved=False,
        )


def test_youtube_package_appends_required_music_attribution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    reports.joinpath("youtube-attribution.txt").write_text(
        "Example Track — Example Artist (CC BY 4.0)\n",
        encoding="utf-8",
    )

    package = _package(tmp_path, monkeypatch)
    metadata = json.loads(package.metadata.read_text(encoding="utf-8"))

    assert "Music attribution" in metadata["description"]
    assert "Example Track - Example Artist (CC BY 4.0)" in metadata["description"]
    assert package.root.joinpath("attribution.txt").read_text(encoding="utf-8") == (
        "Example Track — Example Artist (CC BY 4.0)\n"
    )


def test_publication_confirmation_records_operator_checked_1440p_for_archiving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = tmp_path / "raid.mp4"
    video.write_bytes(b"approved-final-master")
    base = _config(tmp_path)
    config = base.model_copy(
        update={"youtube": base.youtube.model_copy(update={"privacy_status": "public"})}
    )

    def fake_thumbnail(_video: Path, destination: Path, **_options: object) -> None:
        destination.write_bytes(b"jpeg")

    monkeypatch.setattr(youtube_upload, "_create_thumbnail", fake_thumbnail)
    package = youtube_upload.write_youtube_package(
        config,
        _timeline(tmp_path),
        video,
        tmp_path / "youtube-public",
    )

    with pytest.raises(YouTubeUploadError, match="explicit approval"):
        youtube_upload.record_publication_confirmation(
            config,
            package,
            video_id="public123",
            maximum_quality="1440p60",
            approved=False,
            report_destination=tmp_path / "publication.md",
        )

    payload = youtube_upload.record_publication_confirmation(
        config,
        package,
        video_id="public123",
        maximum_quality="1440p60",
        approved=True,
        report_destination=tmp_path / "publication.md",
    )

    assert payload["privacy_status"] == "public"
    assert payload["public_playback_confirmed"] is True
    assert payload["maximum_quality_confirmed"] == "1440p60"
    assert payload["publication_confirmation_method"] == "operator_verified_watch_page"
    assert "Remote verification: not performed" in tmp_path.joinpath("publication.md").read_text(
        encoding="utf-8"
    )


def test_matching_upload_manifest_prevents_duplicate_upload_and_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    package = _package(tmp_path, monkeypatch)
    metadata = json.loads(package.metadata.read_text(encoding="utf-8"))
    metadata_sha256 = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    package.manifest.write_text(
        json.dumps(
            {
                "video_id": "existing123",
                "privacy_status": "private",
                "source_sha256": hashlib.sha256(package.video.read_bytes()).hexdigest(),
                "metadata_sha256": metadata_sha256,
            }
        ),
        encoding="utf-8",
    )

    def unexpected_credentials(_config: ProjectConfig) -> object:
        raise AssertionError("duplicate guard should return before authentication")

    monkeypatch.setattr(youtube_upload, "_credentials", unexpected_credentials)

    result = youtube_upload.upload_youtube_video(config, package, approved=True)

    assert result.video_id == "existing123"
    assert result.skipped_existing is True
    assert result.url == "https://youtu.be/existing123"


def test_changed_metadata_for_uploaded_master_is_blocked_as_a_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    package = _package(tmp_path, monkeypatch)
    package.manifest.write_text(
        json.dumps(
            {
                "video_id": "existing123",
                "privacy_status": "private",
                "source_sha256": hashlib.sha256(package.video.read_bytes()).hexdigest(),
                "metadata_sha256": "different-metadata-hash",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(YouTubeUploadError, match="Refusing to create a duplicate"):
        youtube_upload.upload_youtube_video(config, package, approved=True)


def test_unverified_api_project_refuses_public_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path).model_copy(
        update={
            "youtube": _config(tmp_path).youtube.model_copy(update={"privacy_status": "public"})
        }
    )
    package = _package(tmp_path, monkeypatch)

    with pytest.raises(YouTubeUploadError, match="not verified for Public uploads"):
        youtube_upload.upload_youtube_video(config, package, approved=True)


def test_successful_upload_sets_private_metadata_and_custom_thumbnail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    package = _package(tmp_path, monkeypatch)
    observed: dict[str, object] = {}

    class FakeVideoRequest:
        def next_chunk(self) -> tuple[None, dict[str, str]]:
            return None, {"id": "fresh123"}

    class FakeVideos:
        def insert(self, **kwargs: object) -> FakeVideoRequest:
            observed["insert"] = kwargs
            return FakeVideoRequest()

    class FakeThumbnailRequest:
        def execute(self) -> dict[str, object]:
            observed["thumbnail_executed"] = True
            return {"items": []}

    class FakeThumbnails:
        def set(self, **kwargs: object) -> FakeThumbnailRequest:
            observed["thumbnail"] = kwargs
            return FakeThumbnailRequest()

    class FakeYouTube:
        def videos(self) -> FakeVideos:
            return FakeVideos()

        def thumbnails(self) -> FakeThumbnails:
            return FakeThumbnails()

    monkeypatch.setattr(youtube_upload, "_credentials", lambda _config: object())
    monkeypatch.setattr(
        youtube_upload,
        "build",
        lambda *_args, **_kwargs: FakeYouTube(),
    )
    monkeypatch.setattr(
        youtube_upload,
        "MediaFileUpload",
        lambda *_args, **kwargs: {"options": kwargs},
    )

    result = youtube_upload.upload_youtube_video(config, package, approved=True)

    insert = observed["insert"]
    assert isinstance(insert, dict)
    body = insert["body"]
    assert isinstance(body, dict)
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert body["status"]["containsSyntheticMedia"] is False
    assert body["status"]["license"] == "youtube"
    assert body["snippet"]["defaultLanguage"] == "en"
    assert insert["notifySubscribers"] is True
    assert observed["thumbnail_executed"] is True
    assert result.video_id == "fresh123"
    assert result.thumbnail_applied is True
    manifest = json.loads(package.manifest.read_text(encoding="utf-8"))
    assert manifest["upload_complete"] is True
    assert manifest["thumbnail_applied"] is True
