"""Timeline JSON, FCPXML, subtitle-label, and chapter exports."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from raid_editor.models import TimelineDocument
from raid_editor.util.paths import atomic_write_json, atomic_write_text


def _frames(seconds: float, fps: float) -> int:
    return round(seconds * fps)


def _time(seconds: float, fps: float) -> str:
    return f"{_frames(seconds, fps)}/{round(fps)}s"


def write_timeline_json(timeline: TimelineDocument, destination: Path) -> None:
    payload = timeline.model_dump(mode="json")
    payload["condensed_duration_seconds"] = timeline.duration_seconds
    atomic_write_json(destination, payload)


def write_fcpxml(
    timeline: TimelineDocument,
    destination: Path,
    *,
    media_path: Path | None = None,
    width: int = 1920,
    height: int = 1080,
) -> None:
    """Write a conservative FCPXML 1.10 spine using a microphone-free sidecar."""

    fps = timeline.source_fps
    source = (media_path or timeline.source).resolve()
    root = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        id="r1",
        name=f"FFVideoFormat{height}p{round(fps)}",
        frameDuration=f"1/{round(fps)}s",
        width=str(width),
        height=str(height),
    )
    ET.SubElement(
        resources,
        "asset",
        id="r2",
        name=source.name,
        src=source.as_uri(),
        start="0s",
        duration=_time(timeline.source_duration_seconds, fps),
        hasVideo="1",
        hasAudio="1" if timeline.retained_audio_stream_indexes else "0",
        format="r1",
    )
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="WoW Raid Editor")
    project = ET.SubElement(event, "project", name=timeline.timeline_name)
    sequence = ET.SubElement(
        project,
        "sequence",
        format="r1",
        duration=_time(timeline.duration_seconds, fps),
        tcStart="0s",
        tcFormat="NDF",
    )
    spine = ET.SubElement(sequence, "spine")
    for clip in timeline.clips:
        element = ET.SubElement(
            spine,
            "asset-clip",
            ref="r2",
            offset=_time(clip.timeline_in, fps),
            name=clip.label,
            start=_time(clip.source_in, fps),
            duration=_time(clip.source_out - clip.source_in, fps),
        )
        ET.SubElement(element, "keyword", start="0s", duration="1s", value=clip.type)
        ET.SubElement(element, "marker", start="0s", value=clip.label)
    ET.indent(root, space="  ")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n'
    xml += ET.tostring(root, encoding="unicode")
    atomic_write_text(destination, xml + "\n")


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_labels_srt(timeline: TimelineDocument, destination: Path) -> None:
    blocks: list[str] = []
    for index, clip in enumerate(timeline.clips, start=1):
        start = clip.timeline_in
        end = min(start + 4.0, start + clip.source_out - clip.source_in)
        blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{clip.label}")
    atomic_write_text(destination, "\n\n".join(blocks) + ("\n" if blocks else ""))


def write_chapters(timeline: TimelineDocument, destination: Path) -> None:
    lines: list[str] = []
    for clip in timeline.clips:
        total_seconds = round(clip.timeline_in)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        stamp = f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"
        lines.append(f"{stamp} {clip.label}")
    atomic_write_text(destination, "\n".join(lines) + ("\n" if lines else ""))


def timeline_digest(timeline: TimelineDocument) -> str:
    return json.dumps(timeline.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
