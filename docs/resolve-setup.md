# DaVinci Resolve setup

The reliable handoff from this MVP is a deterministic FCPXML file plus a
microphone-free media sidecar. Direct Resolve API control is optional and is not
known to work with the Resolve edition currently installed on this workstation.

## Audited host state

Checked on 2026-07-26:

| Item | Finding |
| --- | --- |
| Resolve executable | DaVinci Resolve 20.3.2.9 |
| Windows installed-product name | `DaVinci Resolve` (not `DaVinci Resolve Studio`) |
| Installed scripting SDK | Present |
| Installed SDK description | “Scripting API for DaVinci Resolve Studio” |
| Native library | `fusionscript.dll` 20.3.2 present |
| Compatible bridge interpreter | Python 3.13 x64 |
| Python 3.11/3.12 shim behavior | Not safe on this host; bridge does not use them |
| Live API project/import success | **Not demonstrated** |

Treat the installation as apparent non-Studio unless the Resolve UI/license
proves otherwise. Having SDK files on disk does not prove that the running
edition exposes external scripting.

## Build the handoff

After audio and pull review:

```powershell
uv run raid-editor build-timeline config\my-raid.local.yaml
```

The important artifacts are:

```text
output\<slug>\timeline\timeline.fcpxml
output\<slug>\generated-assets\source-microphone-free.mov
output\<slug>\timeline\timeline.json
output\<slug>\timeline\pull-labels.srt
output\<slug>\reports\chapters.txt
output\<slug>\resolve\create-project.json
```

`source-microphone-free.mov` is an FFmpeg stream-copy remux of the first video
stream and the explicitly retained game/Discord audio streams. It is not a
quality-changing transcode. The FCPXML references this sidecar, not the original
recording.

Do not move the sidecar between building and importing. If it must move, rebuild
the timeline so the FCPXML receives the correct file URI.

## Preferred manual import

A synthetic FCPXML import succeeded on this host on 2026-07-26. The following
remains the operator procedure for each real project and is not a general
compatibility claim:

1. Start Resolve normally.
2. Create a new, empty project with a unique name. Do not open or reuse an
   existing production project.
3. Use Resolve's timeline import command to select
   `output\<slug>\timeline\timeline.fcpxml`.
4. In any import/relink dialog, accept only the exact generated sidecar
   `output\<slug>\generated-assets\source-microphone-free.mov`.
5. Do not relink to the original OBS recording; it may contain the microphone.
6. Confirm one timeline exists and the clip order matches
   `timeline\timeline.json` or `reports\chapters.txt`.
7. Scrub the beginning, middle, and end of several clips.
8. Listen specifically for microphone leakage and confirm game/Discord audio is
   present.
9. Stop after inspection. Do not add a render job, open Quick Export, sign in to
   YouTube, or upload.

If the HEVC sidecar appears offline or cannot decode, stop. The current OBS
source is MOV/HEVC and the generated sidecar preserves that codec. Compatibility
with the apparent non-Studio edition is unproven. Do not silently relink to the
microphone-containing original.

See the guarded [Resolve computer-use
runbook](resolve-computer-use-runbook.md) before allowing an automation tool to
operate the UI.

## Optional API bridge

The main application runs on Python 3.12. The bridge intentionally launches:

```text
py -3.13 scripts\resolve_bridge.py <create-project.json>
```

Check the planned command first:

```powershell
py -3.13 --version
uv run raid-editor create-resolve-project config\my-raid.local.yaml --dry-run
```

`--dry-run` still builds the timeline, sidecar, and bridge payload. It only
prevents connection to Resolve.

The bridge requires:

- Resolve Studio running;
- the installed scripting SDK and `fusionscript.dll`;
- Resolve Preferences allowing external scripting from **Local**; and
- Python 3.13 x64 available through the Windows Python launcher.

Do not select network scripting. Local access is sufficient and has a smaller
security boundary.

Only if the edition and settings are confirmed should an operator consider:

```powershell
uv run raid-editor create-resolve-project config\my-raid.local.yaml
```

On the currently audited apparent non-Studio installation, expect this to fail
cleanly. A failure is not permission to alter global environment variables,
install unofficial shims, or retry against an existing project.

## Bridge safety behavior

The payload and helper enforce these boundaries:

- create a uniquely named project only;
- refuse to modify a project with the same name;
- import exactly one generated microphone-free sidecar;
- create one empty timeline and append planned clips;
- add markers;
- save the new project;
- never add a render job;
- never start rendering; and
- never upload.

The helper can leave a newly created partial project if a later import/timeline
step fails. Inspect and remove that unique test project manually if appropriate;
the CLI does not perform rollback or delete projects.

The project name is derived from raid/project name and raid date. If
`project.raid_date` is absent, the build date is used. A retry can therefore
collide with the partially created project and will be refused.

## What the Resolve handoff does not include

- Music is mixed only into the FFmpeg review preview. It is not represented in
  the FCPXML or bridge project.
- The FCPXML includes clip ranges, labels, markers, and keywords, but not a
  polished grade, titles package, final mix, or delivery preset.
- Configured preview fades are not a promise of equivalent Resolve transitions.
- There is no proxy or compatibility-transcode command.
- There is no final-render, Deliver-page, Quick Export, YouTube, or upload
  command.
- Synthetic H.264 import succeeded in Resolve 20.3.2. The real HEVC sidecar and
  external API bridge remain unproven.

## Approval sequence

Before any manual finishing:

1. Review and apply pull corrections.
2. Build the timeline and preview.
3. Run `validate`.
4. Watch the entire preview.
5. Review `reports\edit-summary.md`, `reports\validation.md`, and music reports.
6. Import into a new Resolve project.
7. Recheck audio and boundaries in Resolve.
8. Make any final/export decision manually outside this MVP.
