# Resolve computer-use runbook

This runbook constrains any human or computer-use agent operating DaVinci
Resolve for the MVP handoff. Its goal is inspection of a newly imported
microphone-free timeline, not finishing, rendering, or publishing.

**Status:** the manual FCPXML route succeeded with the synthetic fixture on the
audited Resolve 20.3.2 apparent non-Studio installation on 2026-07-26. The API
route and real HEVC-sidecar import remain unproven.

## Scope

Allowed:

- launch Resolve;
- create a new empty project with a unique name;
- import the generated `timeline.fcpxml`;
- accept only the generated `source-microphone-free.mov`;
- inspect clip order, markers, playback, and audio;
- save the new test project; and
- close Resolve.

Not allowed:

- open or modify an existing production project;
- relink to the original OBS recording;
- import a microphone track;
- change global Resolve preferences except an operator-approved local scripting
  setting;
- enter the Deliver page, Quick Export, or render queue;
- add/start a render job;
- sign in to YouTube or another service;
- upload, publish, or share;
- delete any source/output file; or
- claim success without recorded evidence.

## Required inputs

Resolve these exact paths before launching the UI:

```text
CONFIG       C:\Projects\RaidVideoEditor\config\<project>.local.yaml
FCPXML       C:\Projects\RaidVideoEditor\output\<slug>\timeline\timeline.fcpxml
SIDECAR      C:\Projects\RaidVideoEditor\output\<slug>\generated-assets\source-microphone-free.mov
TIMELINE     C:\Projects\RaidVideoEditor\output\<slug>\timeline\timeline.json
CHAPTERS     C:\Projects\RaidVideoEditor\output\<slug>\reports\chapters.txt
VALIDATION   C:\Projects\RaidVideoEditor\output\<slug>\reports\validation.md
```

Do not infer the slug if command output gives a different directory. Confirm
each file exists and the sidecar is beneath the same exact project output.

## Preconditions

The operator must confirm:

- pull corrections have been applied through `input.manual_pulls`, if needed;
- `build-timeline`, `render-preview`, and `validate` completed;
- the complete preview was watched and accepted;
- validation did not report a microphone-stream or preview failure;
- the intended project name is unique;
- Resolve is not currently showing an important project; and
- no final render/upload action is authorized.

If any item is false, stop before controlling Resolve.

## Preflight commands

These commands prepare and inspect; they do not prove Resolve compatibility:

```powershell
Set-Location C:\Projects\RaidVideoEditor
uv run raid-editor build-timeline config\<project>.local.yaml
uv run raid-editor validate config\<project>.local.yaml
Get-Item -LiteralPath 'output\<slug>\timeline\timeline.fcpxml'
Get-Item -LiteralPath 'output\<slug>\generated-assets\source-microphone-free.mov'
```

If considering the API route, run only the bridge dry-run first:

```powershell
py -3.13 --version
uv run raid-editor create-resolve-project config\<project>.local.yaml --dry-run
```

The dry-run writes build artifacts but does not connect to Resolve. On the
audited apparent non-Studio edition, prefer the manual FCPXML route.

## Manual UI procedure

1. Launch DaVinci Resolve.
2. At Project Manager, confirm no production project will be reused.
3. Create a new empty project named for the test, raid, and date.
4. From the new project, choose the timeline-import action and select the exact
   FCPXML path.
5. If Resolve asks to import/relink media, inspect the full path.
6. Accept only `source-microphone-free.mov` from the exact generated-assets
   directory.
7. Reject or cancel if Resolve proposes the original OBS MOV, another similarly
   named file, a search across the whole drive, or an unexpected path.
8. Wait for import to finish without opening Deliver or Quick Export.
9. Compare timeline clip count/order with `timeline.json` and labels/chapters.
10. Play the start, middle, and end of at least three clips, including one boss
    segment and one voice-heavy segment.
11. Confirm game/Discord audio is present and the operator microphone is absent.
12. Inspect the first and last boundary and several adjacent cuts.
13. Save the new test project only if the evidence is correct.
14. Stop. Do not render or upload.

## Required evidence

Record a short result note outside Resolve containing:

- date/time;
- Resolve version and displayed edition;
- project name;
- exact FCPXML and sidecar paths;
- imported timeline name;
- observed clip count;
- whether media was online;
- whether game/Discord audio was present;
- whether microphone leakage was heard;
- boundary/marker observations;
- any warnings or manual relinks; and
- verdict: `PASS`, `FAIL`, or `BLOCKED`.

Do not write `PASS` unless audio and multiple clip ranges were actually played.
Do not describe API import as successful when only FCPXML was imported.

### Recorded synthetic import evidence

- Project: `WoW Raid Editor - Synthetic Training Hall - 2026-07-26`
- Timeline: `Deterministic Training Hall Condensed Review`
- Media: the generated `source-microphone-free.mov` only
- Observed: media online, 24-second duration, three distinct visual clip ranges
- Not claimed: full audio audition, API bridge success, real HEVC compatibility,
  final render, or upload
- Verdict: `BLOCKED` for full playback/audio acceptance; FCPXML structure and
  online-media import succeeded

## Abort conditions

Immediately stop and leave the current dialog without confirming if:

- Resolve shows the original OBS file as the media target;
- the sidecar is missing or offline;
- an existing project would be overwritten/modified;
- a prompt requests network scripting or account sign-in;
- the imported timeline has unexpected clips or durations;
- microphone speech is audible;
- the HEVC source cannot decode;
- Resolve becomes unstable;
- any Deliver/render/upload control is reached; or
- the automation cannot confidently identify a button or path.

Do not “fix forward” by guessing. Capture the blocker and return to the CLI or
operator.

## API route constraints

The bridge route is optional and currently unproven. If a future operator
confirms Resolve Studio and local external scripting:

1. Start Resolve Studio.
2. Set external scripting to **Local**, never network.
3. Inspect `resolve\create-project.json`; its safety flags must be:

   ```json
   {
     "create_unique_project_only": true,
     "add_render_job": false,
     "start_rendering": false,
     "upload": false
   }
   ```

4. Run `create-resolve-project` once.
5. If it fails after project creation, inspect the partial unique project.
6. Do not retry into that project; the bridge should refuse it.
7. Record the same evidence as for manual import.

Python 3.13 is a host-specific compatibility boundary for Resolve 20.3.2.
Python 3.12 remains the application runtime.

## Cleanup after a failed test

The CLI does not delete Resolve projects. With explicit operator approval, a
human may remove only the uniquely named failed test project from Resolve's
Project Manager after confirming it is not a production project.

Generated disk artifacts can be removed separately using the guarded exact-path
procedure in [security-and-privacy.md](security-and-privacy.md). Never delete
the source recording or relink a saved project to a path scheduled for removal.
