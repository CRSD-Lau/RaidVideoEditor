# Troubleshooting

Run commands from `C:\Projects\RaidVideoEditor` and add global `--verbose` before
the subcommand when more context is useful:

```powershell
uv run raid-editor --verbose analyse config\my-raid.local.yaml
```

## Environment checks

```powershell
py -3.12 --version
uv --version
uv run python --version
ffmpeg -version
ffprobe -version
uv run raid-editor --help
```

The first and third Python versions should be 3.12. Resolve integration is the
only component that uses `py -3.13`.

### `ffmpeg` or `ffprobe` is not installed or not available on PATH

Install a Windows build that includes both executables, open a new PowerShell
session, and rerun the checks. The CLI does not download tools or search
arbitrary directories.

### `uv sync` selects the wrong Python

```powershell
uv sync --python 3.12 --extra dev --frozen
uv run python --version
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
uv run raid-editor inspect config\my-raid.local.yaml --force --open-review
```

Listen to samples and set the absolute FFprobe stream index. Do not use the OBS
track label or audio ordinal by assumption.

### Every track contains the microphone

The current OBS profile routes `Desktop Audio` and `Mic/Aux` to all six tracks.
No config setting can remove voice already mixed into every stream. Stop and
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
timeline, sidecar, reports, filter script, and command.

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
uv run raid-editor create-resolve-project config\my-raid.local.yaml --dry-run
```

The main Python 3.12 environment cannot substitute for the host-specific 3.13
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

## Still blocked

Collect:

- the exact command;
- terminal output with `--verbose`;
- `uv run python --version`;
- first lines of `ffmpeg -version` and `ffprobe -version`;
- config with private paths/names redacted;
- relevant `analysis` or `reports` file; and
- whether the source is the current MOV/HEVC six-track recording.

Do not attach raw microphone samples, combat logs, Skada files, tokens, or
private license documents unless deliberately redacted.
