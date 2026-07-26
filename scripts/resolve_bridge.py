"""Python 3.13-only helper using Resolve's installed 20.3.2 scripting API.

This script creates a uniquely named project and timeline. It intentionally has
no render-job or upload code.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting")
MODULES = API_ROOT / "Modules"
LIBRARY = Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")


def fail(message: str, code: int = 2) -> int:
    print(json.dumps({"status": "error", "error": message}))
    return code


def main() -> int:
    if sys.version_info[:2] != (3, 13):
        return fail("Resolve 20.3.2 bridge must run under Python 3.13")
    if len(sys.argv) != 2:
        return fail("Expected a single bridge payload path")
    payload_path = Path(sys.argv[1]).resolve()
    try:
        payload: dict[str, Any] = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"Could not read bridge payload: {exc}")
    safety = payload.get("safety", {})
    if not safety.get("create_unique_project_only"):
        return fail("Payload lacks the unique-project safety flag")
    if safety.get("add_render_job") or safety.get("start_rendering") or safety.get("upload"):
        return fail("Payload requests a forbidden render or upload action")

    os.environ["RESOLVE_SCRIPT_API"] = str(API_ROOT)
    os.environ["RESOLVE_SCRIPT_LIB"] = str(LIBRARY)
    sys.path.insert(0, str(MODULES))
    try:
        import DaVinciResolveScript as dvr_script  # type: ignore[import-not-found]
    except Exception as exc:
        return fail(f"Could not import installed Resolve API: {exc}")
    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        return fail(
            "Resolve API connection unavailable. Start Resolve Studio and enable local external "
            "scripting. The installed non-Studio edition may not expose this API."
        )
    manager = resolve.GetProjectManager()
    if manager is None:
        return fail("Resolve returned no ProjectManager")
    project_name = str(payload["project_name"])
    existing = manager.GetProjectListInCurrentFolder() or []
    if project_name in existing:
        return fail(f"Refusing to modify existing Resolve project: {project_name}")
    project = manager.CreateProject(project_name)
    if project is None:
        return fail(
            "Resolve could not create the project. External scripting may require Resolve Studio."
        )
    media_pool = project.GetMediaPool()
    if media_pool is None:
        return fail("Resolve created a project but returned no MediaPool")
    imported = media_pool.ImportMedia([str(Path(payload["media_path"]).resolve())]) or []
    if len(imported) != 1:
        return fail("Resolve did not import exactly one microphone-free source")
    timeline = media_pool.CreateEmptyTimeline(str(payload["timeline_name"]))
    if timeline is None:
        return fail("Resolve could not create the timeline")
    project.SetCurrentTimeline(timeline)
    media_item = imported[0]
    for clip in payload["clips"]:
        appended = media_pool.AppendToTimeline(
            [
                {
                    "mediaPoolItem": media_item,
                    "startFrame": int(clip["start_frame"]),
                    "endFrame": int(clip["end_frame"]),
                    "recordFrame": int(clip["record_frame"]),
                }
            ]
        )
        if not appended:
            return fail(f"Resolve failed while appending {clip['label']}")
        timeline.AddMarker(
            int(clip["record_frame"]),
            "Blue",
            str(clip["label"]),
            f"{clip['type']}; pulls={','.join(clip['pull_ids'])}",
            1,
            "",
        )
    if not manager.SaveProject():
        return fail("Resolve project was created but could not be saved")
    print(
        json.dumps(
            {
                "status": "created",
                "project_name": project_name,
                "timeline_name": payload["timeline_name"],
                "clip_count": len(payload["clips"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
