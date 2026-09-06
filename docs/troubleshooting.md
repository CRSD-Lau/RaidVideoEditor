---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Troubleshooting

Run commands from the cloned repository root and add global `--verbose` before
the subcommand when more context is useful:

```powershell
uv run --no-sync raid-editor --verbose analyse config\my-raid.local.yaml
```

## Environment checks

```powershell
uv --version
uv run --no-sync python --version
ffmpeg -version
ffprobe -version
uv run --no-sync raid-editor --help
```

The project interpreter must be Python 3.12 or newer and compatible with the
locked dependencies. Preserve a working compatible environment. The Resolve
bridge separately uses `py -3.13` for its recorded host compatibility boundary.

### `ffmpeg` or `ffprobe` is not installed or not available on PATH

Install a Windows build that includes both executables, open a new PowerShell
session, and rerun the checks. The CLI does not download media tools or search
arbitrary directories.

### `uv sync` selects the wrong Python

For a new environment, explicitly selecting a supported interpreter can help:

```powershell
uv sync --python 3.12 --extra dev --frozen
uv run --no-sync python --version
```

If an incompatible `.venv` already exists, inspect it before removal. Do not
delete unrelated environments outside this project.

## YAML and path problems

### `Project configuration does not exist`

The command argument is resolved from the current shell. Use an absolute path or
run from the repository root.

### Windows path produces a YAML escape error

Use single quotes or forward slashes:

```yaml
recording: 'D:\Raid Videos\2026-07-26 20-00-00.mov'
combat_log: "D:/world of warcraft 3.3.5a hd/Logs/WoWCombatLog.txt"
```

All relative paths inside YAML are resolved against the YAML's directory.

### Validation reports an unknown/extra key

Config models are strict. Compare against `config\project.example.yaml`.
`skada_export` belongs under `input`; `recording_started_at` and
`combat_log_offset_seconds` belong under `detection`.

## Audio problems

### `No retained audio streams are configured`

Set at least one valid `game_track` or `discord_track` and leave its corresponding
`keep_*` flag true. With `remove_microphone: true`, `mixed_track` is not retained.

### `Microphone removal is requested but microphone_track is not identified`

Run:

```powershell
uv run --no-sync raid-editor inspect config\my-raid.local.yaml --force --open-review
```

Listen to samples and set the absolute FFprobe stream index. Do not use the OBS
track label or audio ordinal by assumption.

### Every track contains the microphone

If `Desktop Audio` and `Mic/Aux` are routed to every enabled track, those tracks
can all contain the same mixed voices. Verify by listening; do not infer the
routing from an earlier recording. No config setting can remove voice already
mixed into every stream. Stop and
make a new recording after applying the separate-track setup in
[obs-recording-setup.md](obs-recording-setup.md).

### Stream index no longer exists

OBS profile, container, or source changes can reorder streams. Rerun `inspect
--force`, listen again, and update the config. Never carry numbers from another
recording without verifying them.

### Review clips have no sound

`analyse` uses the first retained stream for short review clips. Confirm the
mapping, then regenerate the project output if stale cached clips remain.

## Pull detection problems

### `No combat log or manual pull file is configured`

Set `input.combat_log` or `input.manual_pulls`. `skada_export` alone is not a
stand-alone input in this MVP.

### The 300 MB legacy log seems slow

The detector streams the accumulated file without loading it into memory, but it
must still scan it. Run after WoW has stopped writing, avoid network drives, and
wait for the pass to complete. Do not make a truncated working copy unless you
can preserve the correct timestamps and evidence.

### Pulls are absent or all called `Combat activity`

Legacy 3.3.5 logs may lack `ENCOUNTER_*` and `PLAYER_REGEN_*` boundaries. When no
primary pulls exist, the fallback clusters hostile damage and labels the
result `unknown`. This is expected lower-confidence behavior.

Add a stable `skada_export` if available, or manually classify/correct a
downloaded pull file. Review all legacy clusters even when their confidence is
`0.72` and the default threshold is `0.70`.

### Skada segments are missing

Check that:

- the path points to `SkadaStorage.lua`, not another addon's file;
- WoW was closed or had flushed SavedVariables before the copy;
- segments contain top-level `starttime`, `endtime`, and `mobname`;
- segment epoch times overlap the recording window after offset; and
- `combat_log` is also configured.

The parser ignores aggregate segment `[0]` and nested actor data. It does not
execute Lua.

A short successful segment near a prior kill of the same boss is deliberately
marked as a possible duplicate with confidence `0.45` and `include: false`.
Inspect it before manually including or discarding it.

### Pulls are consistently early or late

Set an explicit ISO 8601 `recording_started_at`, then adjust
`combat_log_offset_seconds`. Positive moves pulls later; negative moves them
earlier. Validate against several visible events rather than one.

### The browser changes did not affect the next command

The review page only downloads `pull-overrides.json`. Move it to a stable
location, set `input.manual_pulls`, and rerun `analyse`. The wizard does not
automatically import browser downloads.

### All detected pulls were excluded

Check each pull's `include`, then the editing policy:

- `include_trash_pulls`
- `include_boss_wipes`
- `include_boss_kills`
- `include_run_backs`
- `include_loot`

Unknown legacy activity is included by default unless manually excluded.

## Timeline and preview problems

### `No pull candidates are available for the timeline`

Run `analyse`, inspect parser issues, and apply manual pulls if evidence is
insufficient.

### FFmpeg fails creating `source-microphone-free.mov`

This full-length copy is needed only for an explicit Resolve/FCPXML export
(`build-timeline` or `create-resolve-project`). The normal FFmpeg preview,
validation, final-render, upload and analytics paths do not create it. Do not
run `build-timeline` as a prerequisite for those commands when disk is tight.
They build their timeline directly from the original recording and select the
retained audio streams without duplicating the source.

Check:

- the retained stream indexes;
- free space;
- source readability with `ffprobe`;
- write access to the exact project output directory; and
- that the source file is no longer being moved or replaced.

The sidecar is stream-copied, not transcoded. Some codec/container combinations
may not remux to MOV.

### `render-preview --dry-run` created files

Expected. Dry-run prevents the preview MP4 process but still prepares the
timeline, reports, filter script, and command. It does not create the full-length
Resolve sidecar.

### Preview rendering is slow despite `hardware_encoding: true`

`preview.hardware_encoding` is a reserved field in this MVP. Preview video is
encoded with software `libx264`, preset `medium`. Leave the documented 720p
settings for predictable review performance.

### The portrait recording is small with black side padding

Expected for the audited 900x1600 source. The renderer scales to fit inside
1280x720 while preserving aspect ratio, then pads with black. It does not rotate,
crop, or track gameplay automatically. Make any creative reframing manually in
an editor after the review gate.

### Old thumbnails or preview still appear

Several artifacts are reused when present or when a manifest signature matches.
First rerun `inspect --force` for probe changes. If a clean rebuild is needed,
close the browser/Resolve and remove only the exact
`output\<project-slug>` folder using the guarded procedure in
[security-and-privacy.md](security-and-privacy.md).

### `validate` fails `preview_rendered`

Run `render-preview` without `--dry-run`, then `validate`. Validation rebuilds
timeline-side artifacts but does not render a missing preview.

### `validate` fails `source_not_modified`

The recording's size or modification time differs from the cached probe. Do not
force past this silently. Confirm whether the file was replaced or modified,
then rerun `inspect --force`, re-review audio and pulls, and rebuild.

## Music problems

### Approved ID is absent

The spelling in `approved_track_ids` must exactly match a library `id`.

### File missing or SHA-256 mismatch

Restore the exact reviewed file or re-evaluate its license and update the record
with the new hash. Do not silently point the old ID at a different recording.

### More than one approved track is listed

Approve one. The MVP validates/reports every listed ID but mixes only the first
one into the preview.

## Resolve problems

### `Installed Resolve scripting SDK or library was not found`

The bridge expects Blackmagic's standard Windows paths. Use the FCPXML fallback
if the installed files are absent. Do not download an unofficial DLL.

### `Python launcher or Resolve bridge is unavailable`

Check:

```powershell
py -3.13 --version
uv run --no-sync raid-editor create-resolve-project config\my-raid.local.yaml --dry-run
```

The main application environment cannot substitute for the host-specific 3.13
bridge.

### `Resolve API connection unavailable`

Resolve must be running and Studio may be required. The audited installation is
apparent non-Studio, and its installed docs describe the API as Studio. Use
`timeline.fcpxml` with `source-microphone-free.mov`; do not claim or assume API
success.

### Resolve refuses an existing project name

This is intentional. Inspect the existing/partial project; do not let the bridge
modify it. Change the project/raid name or date only if that accurately
represents a new project.

### Imported media is offline

The FCPXML points to the generated sidecar. Confirm it remains at the original
absolute path. Current MOV/HEVC compatibility with the apparent non-Studio
edition is unproven. Never relink to the original OBS source merely to make the
timeline online; that source can contain microphone audio.

## Spoken highlight trigger problems

### Review says `unavailable`

Run `uv sync --extra speech`, confirm
`.models\vosk-model-small-en-us-0.15\am\final.mdl` exists, and verify
`highlights.speech_triggers.model_path`. An unavailable run is deliberately not
cached as a successful zero-match scan.

### Review says `partial` or `failed`

Read `reports\speech-trigger-status.md`. Confirm that the Discord and microphone
roles point to distinct absolute FFprobe stream indexes and that the source was
not moved while FFmpeg was reading it. The ordinary highlight heuristics still
run when `required: false`.

### Someone said `clip it`, but no proposal appears

Confirm the phrase is audible on either isolated voice stem, not only Full Mix.
Overlapping voices, compression, quiet delivery, or a word confidence below
`minimum_word_confidence` can cause a miss. Do not enable fuzzy matching merely
to force a result; add the moment manually in the review override or calibrate
the threshold against private representative samples.

### Too many spoken proposals

Raise `minimum_word_confidence` or shorten `dedupe_seconds` only after listening
to the false positives. If `maximum_matches` is reached, status becomes
`truncated`; the workflow never describes that as complete coverage.

## Review timer advances but the picture stays black

Individual boss and highlight reviews now default to VP9/Opus WebM via
`preview.review_media_format: webm`. This is independent of the assembled movie
preview, 1440p master, and H.264/AAC social delivery files. The September 4 review
MP4s contained valid gameplay when decoded independently, but appeared black in
the in-app player; a WebM sample was the working compatibility option. The exact
browser decoder/compositor cause was not established.

Regenerate the affected review lane and reload its page. Full boss windows and
highlight audio routing are preserved. The format and encoding settings are in
the review-cache signatures, so switching formats cannot silently reuse old MP4
proxies. Previous media is retained. `mp4` remains an explicit compatibility
option for other browsers. A direct clip link and visible media-error message
are available on both review pages. Always confirm actual browser playback;
successful FFmpeg decoding alone does not prove that a browser displayed it.

## Final validation is unbound or the final master changed

YouTube packaging and upload, including `--dry-run`, require a passed
`final-validation.json` bound to the selected final file's absolute path and
full SHA-256. An older report containing only `status: passed` cannot validate
a substituted or newly discovered MP4. The final renderer also refuses to reuse
an old master whose render manifest lacks a matching artifact binding.

For an existing approved master, inspect the exact MP4 in the project's `final`
directory without rendering or changing it:

```powershell
uv run --no-sync raid-editor validate-final config\my-raid.local.yaml `
  --video 'output\my-raid\final\my-raid-final-1440p60.mp4'
```

The command reads the complete video, checks it against the saved edit records,
and prints its SHA-256 plus a ready-to-copy approval command. Watch the complete
existing master and check picture, sound, and edit, then use that command with
`--approved --expected-sha256 HASH_FROM_INSPECTION`. A new final render is not
required. This is fresh approval of the inspected file; it does not prove that
an unbound legacy file has never changed.

The approved render sidecar, saved `timeline/timeline.json`, and saved
`analysis/media-probe.json` must be present and consistent. Approval also needs
`analysis/pull-candidates.json` for downstream chapters and raid claims. The raw
recording, old preview, music files, and original presentation artwork do not
need to be available. Source preservation is not reverified, and saved stream
mapping is distinguished from the operator's audible-content review.

Acceptance preserves exact copies of the old sidecar and validation reports in
`reports/final-validation-history/`, retains the original render signature, and
writes the matching artifact bindings. The MP4's bytes and timestamp remain
unchanged. An ordinary write failure restores the prior reports; after a process
interruption, inspect again before retrying. Conflicting hashes or unrelated
changes always block recovery. Do not hand-edit fingerprints.

After acceptance, `upload-youtube CONFIG --dry-run` uses the bound saved timeline,
pulls, and recorded presentation timing without reading the raw recording.
Upload and Public visibility still require their separate approvals. Existing
publication receipts remain intact. `validate CONFIG` continues to check the
preview; `validate-final` is the separate existing-master workflow.

## Native portrait review cannot be prepared

Inspect `highlights/portrait-source-status.json` and the native-source detail
before changing the config. An unbound or ambiguous companion needs the exact
same-session `input.vertical_recording`; choosing the newest portrait file is
not sufficient. Candidate timestamps stay on the landscape clock.

Shared-audio verification must pass at separated points, and every full clip
must fit in both recordings. An offset hint only centers the search; it cannot
override silence, ambiguity, drift, or missing coverage. Native mode does not
fall back automatically. Continue eligible landscape work independently and
resolve the media blocker before approving native clips. See the
[native portrait contract](highlight-intelligence.md#use-the-recorded-portrait-composition).

Changing the source pair, measured offset, or audio mix requires reviewing the
new presentation. Saved editorial ratings can survive that change while export
checkboxes are cleared; this preserves preferences without approving different
footage.

## Still blocked

Collect:

- the exact command;
- terminal output with `--verbose`;
- `uv run --no-sync python --version`;
- first lines of `ffmpeg -version` and `ffprobe -version`;
- config with private paths/names redacted;
- relevant `analysis` or `reports` file; and
- the actual source container, video codec/geometry, and audio stream layout.

Do not attach raw microphone samples, combat logs, Skada files, tokens, or
private license documents unless deliberately redacted.
