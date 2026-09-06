"""Native campaign lineage follows verified render evidence, without media execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from raid_editor.config.models import ProjectConfig
from raid_editor.growth import package
from raid_editor.ingestion.probe import AudioStream, MediaProbe, VideoStream
from raid_editor.util.paths import atomic_write_json, quick_file_fingerprint


@dataclass
class CampaignFiles:
    root: Path
    landscape: Path
    portrait: Path
    output: Path
    config: ProjectConfig

    def row(self, *, offset: float = 2) -> dict[str, Any]:
        return {
            "id": "highlight-001",
            "title": "Reviewed moment",
            "output": str(self.output),
            "rendered": True,
            "video_source": "native_vertical",
            "video_recording": str(self.portrait),
            "audio_recording": str(self.landscape),
            "source_start_seconds": 20,
            "source_end_seconds": 40,
            "portrait_start_seconds": 20 - offset,
            "portrait_end_seconds": 40 - offset,
            "presentation_reference": "d" * 64,
            "output_fingerprint": quick_file_fingerprint(self.output),
            "source_binding": {
                "schema_version": 1,
                "status": "verified",
                "landscape": quick_file_fingerprint(self.landscape),
                "portrait": quick_file_fingerprint(self.portrait),
                "offset_seconds": offset,
                "landscape_duration_seconds": 120,
                "portrait_duration_seconds": 118,
            },
        }

    def manifest(self, rows: list[dict[str, Any]], *, approved: bool = True) -> None:
        atomic_write_json(
            self.root / "highlights" / "vertical" / "manifest.json",
            {
                "approved": approved,
                "author": "Neil Mitchell",
                "last_modified_by": "Neil Mitchell",
                "clips": rows,
            },
        )


def _probe(path: Path) -> MediaProbe:
    portrait = path.name != "landscape.mp4"
    return MediaProbe(
        source=quick_file_fingerprint(path),
        format_name="mp4",
        duration_seconds=118 if portrait else 120,
        size_bytes=path.stat().st_size,
        video_streams=[
            VideoStream(
                index=0,
                codec="h264",
                width=1080 if portrait else 2560,
                height=1920 if portrait else 1440,
                frame_rate=60,
            )
        ],
        audio_streams=[AudioStream(index=1, audio_ordinal=0, codec="aac", title="Game")],
    )


@pytest.fixture
def campaign_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CampaignFiles:
    root = tmp_path / "raid-night"
    landscape = tmp_path / "landscape.mp4"
    portrait = tmp_path / "portrait.mp4"
    output = tmp_path / "short.mp4"
    for path in (landscape, portrait, output):
        path.write_bytes(path.name.encode("utf-8"))
    config = ProjectConfig.model_validate(
        {
            "project": {"name": "Raid night", "raid": "Icecrown Citadel"},
            "input": {"recording": landscape},
            "audio": {"game_track": 1},
            "music": {"library": tmp_path / "music.json"},
        }
    )
    monkeypatch.setattr(package, "project_output_dir", lambda _config: root)
    monkeypatch.setattr(package, "probe_media", _probe)
    return CampaignFiles(root, landscape, portrait, output, config)


def test_supplied_portrait_does_not_relabel_legacy_owned_short(
    campaign_files: CampaignFiles,
) -> None:
    files = campaign_files
    row = {
        "id": "owned-short",
        "title": "Owned approved media",
        "output": str(files.output),
        "source_start_seconds": 20,
        "source_end_seconds": 40,
    }
    files.manifest([row])
    campaign, _, _ = package.prepare_growth_package(
        files.config, portrait_source=files.portrait, social_manifest_paths=[]
    )
    raw = next(source for source in campaign.sources if source.path == files.portrait)
    rendered = next(source for source in campaign.sources if source.path == files.output)
    short = next(asset for asset in campaign.assets if asset.lane == "short")
    assert raw.kind == "native_portrait_source"
    assert raw.paired_source_id is None
    assert rendered.kind == "landscape_derived_portrait"
    assert raw.source_id not in short.source_lineage_ids
    assert [interval.source_id for interval in short.coverage.source_ranges] == [
        "landscape-authoritative"
    ]


@pytest.mark.parametrize("offset", [2, -3.25])
def test_native_render_manifest_drives_lineage_and_both_source_intervals(
    campaign_files: CampaignFiles,
    offset: float,
) -> None:
    files = campaign_files
    files.manifest([files.row(offset=offset)])
    campaign, _, _ = package.prepare_growth_package(files.config, social_manifest_paths=[])
    raw = next(source for source in campaign.sources if source.path == files.portrait)
    rendered = next(source for source in campaign.sources if source.path == files.output)
    short = next(asset for asset in campaign.assets if asset.lane == "short")
    assert raw.paired_source_id == "landscape-authoritative"
    assert rendered.kind == "native_portrait_source"
    assert rendered.paired_source_id == raw.source_id
    assert short.source_lineage_ids == [
        "landscape-authoritative",
        raw.source_id,
        rendered.source_id,
    ]
    assert [
        (interval.source_id, interval.start_seconds, interval.end_seconds)
        for interval in short.coverage.source_ranges
    ] == [("landscape-authoritative", 20, 40), (raw.source_id, 20 - offset, 40 - offset)]


@pytest.mark.parametrize("approved,rendered", [(False, True), (True, False)])
def test_unapproved_or_dry_run_manifest_does_not_package_short(
    campaign_files: CampaignFiles,
    approved: bool,
    rendered: bool,
) -> None:
    files = campaign_files
    row = files.row()
    row["rendered"] = rendered
    files.manifest([row], approved=approved)
    campaign, _, _ = package.prepare_growth_package(files.config, social_manifest_paths=[])
    assert all(asset.lane != "short" for asset in campaign.assets)


@pytest.mark.parametrize(
    "change",
    [
        "missing_rendered",
        "missing_output_fingerprint",
        "wrong_output_path",
        "missing_binding",
        "unverified",
        "missing_portrait_fingerprint",
        "missing_landscape_fingerprint",
        "wrong_portrait_path",
        "wrong_audio_path",
        "missing_reference",
        "wrong_offset",
        "out_of_coverage",
        "nonfinite",
    ],
)
def test_native_lineage_rejects_incomplete_or_inconsistent_evidence(
    campaign_files: CampaignFiles,
    change: str,
) -> None:
    files = campaign_files
    row = files.row()
    if change == "missing_rendered":
        row.pop("rendered")
    elif change == "missing_output_fingerprint":
        row.pop("output_fingerprint")
    elif change == "wrong_output_path":
        row["output_fingerprint"]["path"] = str(files.portrait)
    elif change == "missing_binding":
        row.pop("source_binding")
    elif change == "unverified":
        row["source_binding"]["status"] = "ambiguous"
    elif change == "missing_portrait_fingerprint":
        row["source_binding"].pop("portrait")
    elif change == "missing_landscape_fingerprint":
        row["source_binding"].pop("landscape")
    elif change == "wrong_portrait_path":
        row["source_binding"]["portrait"]["path"] = str(files.output)
    elif change == "wrong_audio_path":
        row["audio_recording"] = str(files.portrait)
    elif change == "missing_reference":
        row.pop("presentation_reference")
    elif change == "wrong_offset":
        row["source_binding"]["offset_seconds"] = -2
    elif change == "out_of_coverage":
        row["source_binding"]["portrait_duration_seconds"] = 30
    else:
        row["portrait_end_seconds"] = float("nan")
    files.manifest([row])
    with pytest.raises(package.GrowthPackageError):
        package.prepare_growth_package(files.config, social_manifest_paths=[])
    assert not (files.root / "growth" / "campaign-manifest.json").exists()


@pytest.mark.parametrize("changed_file", ["landscape", "portrait", "output"])
def test_native_lineage_rejects_replaced_media(
    campaign_files: CampaignFiles,
    changed_file: str,
) -> None:
    files = campaign_files
    files.manifest([files.row()])
    getattr(files, changed_file).write_bytes(b"a different recording or rendered Short")
    with pytest.raises(package.GrowthPackageError, match="identity"):
        package.prepare_growth_package(files.config, social_manifest_paths=[])


def test_verified_native_output_can_be_packaged_after_raw_cleanup(
    campaign_files: CampaignFiles,
) -> None:
    files = campaign_files
    files.manifest([files.row()])
    atomic_write_json(
        files.root / "analysis" / "media-probe.json",
        _probe(files.landscape).model_dump(mode="json"),
    )
    files.landscape.unlink()
    files.portrait.unlink()
    campaign, _, _ = package.prepare_growth_package(files.config, social_manifest_paths=[])
    raw = next(source for source in campaign.sources if source.path == files.portrait)
    assert raw.fingerprint.digest
    assert "unavailable" in raw.notes[0]
    assert next(asset for asset in campaign.assets if asset.lane == "short").state == "rendered"


def test_cleanup_cannot_hide_wrong_landscape_identity(campaign_files: CampaignFiles) -> None:
    files = campaign_files
    row = files.row()
    row["source_binding"]["landscape"]["head_tail_sha256"] = "a" * 64
    files.manifest([row])
    atomic_write_json(
        files.root / "analysis" / "media-probe.json",
        _probe(files.landscape).model_dump(mode="json"),
    )
    files.landscape.unlink()
    files.portrait.unlink()
    with pytest.raises(package.GrowthPackageError, match="campaign evidence"):
        package.prepare_growth_package(files.config, social_manifest_paths=[])
