"""Safe boundary to the Resolve API, isolated in the compatible Python runtime."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from raid_editor.models import TimelineDocument
from raid_editor.util.paths import atomic_write_json

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESOLVE_API_ROOT = Path(
    r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
)
RESOLVE_LIBRARY = Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")


class ResolveIntegrationError(RuntimeError):
    """Expected Resolve edition, availability, or API failure."""


def build_bridge_payload(
    timeline: TimelineDocument,
    *,
    project_name: str,
    media_path: Path,
) -> dict[str, Any]:
    fps = timeline.source_fps
    return {
        "schema_version": 1,
        "project_name": project_name,
        "timeline_name": timeline.timeline_name,
        "media_path": str(media_path.resolve()),
        "fps": fps,
        "clips": [
            {
                "start_frame": round(clip.source_in * fps),
                "end_frame": max(
                    round(clip.source_in * fps),
                    round(clip.source_out * fps) - 1,
                ),
                "record_frame": round(clip.timeline_in * fps),
                "label": clip.label,
                "type": clip.type,
                "pull_ids": clip.pull_ids,
            }
            for clip in timeline.clips
        ],
        "safety": {
            "create_unique_project_only": True,
            "add_render_job": False,
            "start_rendering": False,
            "upload": False,
        },
    }


def write_bridge_payload(
    timeline: TimelineDocument,
    destination: Path,
    *,
    project_name: str,
    media_path: Path,
) -> dict[str, Any]:
    payload = build_bridge_payload(timeline, project_name=project_name, media_path=media_path)
    atomic_write_json(destination, payload)
    return payload


def run_resolve_bridge(payload_path: Path, *, dry_run: bool = False) -> list[str]:
    """Run only through Python 3.13; 3.11/3.12 crash with Resolve 20.3.2's shim."""

    helper = PROJECT_ROOT / "scripts" / "resolve_bridge.py"
    command = ["py", "-3.13", str(helper), str(payload_path.resolve())]
    if dry_run:
        return command
    if not RESOLVE_API_ROOT.is_dir() or not RESOLVE_LIBRARY.is_file():
        raise ResolveIntegrationError("Installed Resolve scripting SDK or library was not found")
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise ResolveIntegrationError("Python launcher or Resolve bridge is unavailable") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stdout or "") + "\n" + (exc.stderr or "")
        raise ResolveIntegrationError(detail.strip() or "Resolve bridge failed") from exc
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ResolveIntegrationError("Resolve bridge returned invalid status JSON") from exc
    if result.get("status") != "created":
        raise ResolveIntegrationError(str(result.get("error", "Resolve project was not created")))
    return command
