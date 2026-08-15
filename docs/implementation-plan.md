# Raid Video Editor implementation plan

> Historical planning artifact. The current implementation has advanced beyond
> several items originally marked deferred. Use [Architecture](architecture.md)
> and the [root README](../README.md) for current behavior.

> Historical planning artifact. The current implementation has advanced beyond
> several items originally marked deferred. Use [Architecture](architecture.md)
> and the [root README](../README.md) for current behavior.

**Status:** MVP implemented; this document separates delivered behavior from deferred hardening  
**Architecture:** [architecture.md](architecture.md)  
**Decisions:** [decision-log.md](decision-log.md)

## 1. Implemented MVP definition

The implemented milestone is a local Windows Python 3.12 workflow for one OBS recording. It succeeds when an operator can:

1. Inspect the recording and listen to samples from every audio stream.
2. Configure retained game/Discord streams and a separately excluded microphone stream.
3. Detect pull candidates from a combat log, legacy damage fallback, and optional Skada evidence, or provide a complete reviewed pull list.
4. Review thumbnails and short clips in static HTML.
5. Build a float-second neutral timeline and microphone-free MOV sidecar.
6. Generate timeline JSON, SRT labels, chapters, ElementTree FCPXML 1.10, and a safe Resolve payload.
7. Render a low-resolution preview with no final-render or upload path.
8. Run bounded validation and read the generated reports.

The MVP does not promise a persistent watermark, exact rational timing, full-file source identity, general cache recovery, or live NLE compatibility.

## 2. Landed source layout

```text
src/raid_editor/
  cli.py
  workflow.py
  models.py
  config/
    loader.py
    models.py
  ingestion/
    probe.py
  audio/
    tracks.py
    analysis.py
  detection/
    combat_log.py
    legacy.py
    manual.py
    pipeline.py
    skada.py
  review/
    html.py
  timeline/
    builder.py
    export.py
    merge.py
  music/
    library.py
  rendering/
    preview.py
  resolve/
    bridge.py
  reporting/
    pulls.py
    summary.py
  util/
    logging.py
    paths.py
scripts/
  resolve_bridge.py
  generate-synthetic-fixture.py
```

`workflow.py` composes the use cases behind the CLI. Pydantic models are the serialized boundaries. FFmpeg/FFprobe and the optional Resolve helper remain subprocess boundaries.

## 3. Delivered milestones

### Milestone A — Python CLI and strict configuration

**Status:** Implemented

Delivered:

- Python 3.12 package and `raid-editor` Typer entry point.
- Strict Pydantic YAML models with relative paths resolved from the YAML directory.
- One recording per project.
- Project output rooted at `output/<project-name-slug>/`.
- Structured normal/verbose logging.
- Reserved `details_export`, `preview.hardware_encoding`, and `final` fields that are not consumed by the MVP.

Verified by:

- Unknown-key rejection tests.
- Relative-path resolution tests.
- CLI integration through `render-preview --dry-run`.

### Milestone B — FFprobe inspection and audio review

**Status:** Implemented

Delivered:

- Normalized FFprobe format, video, and audio models.
- Quick recording fingerprint from path, size, nanosecond mtime, and first/last 4 MiB hashes.
- Reusable probe JSON with `--force` bypass.
- Three six-second PCM WAV samples per audio stream near 20%, 50%, and 80%.
- Static audio-role page using absolute FFprobe stream indexes.
- Title-keyword role suggestions that the operator must confirm.
- Validation of configured stream indexes and retained audio.

Verified by:

- Stream-role inference tests.
- Unknown-index and missing-microphone checks.
- Synthetic fixture inspection in the end-to-end dry-run journey.

### Milestone C — Separate microphone exclusion

**Status:** Implemented for dedicated tracks

Delivered:

- Pydantic rejection when a microphone stream is also a retained game/Discord stream.
- Downstream mapping checks before timeline creation.
- Stream-copy MOV sidecar containing the first video stream and only retained audio streams.
- Sidecar probe verifying expected audio-stream count.
- Preview graph references to retained indexes only.
- Source quick fingerprint comparison before/after the synthetic journey.

Limit:

- The tool cannot remove speech baked into a retained or mixed stream.

### Milestone D — Combat-log-first pull analysis

**Status:** Implemented

Delivered:

- Streaming selection of a recording-time window from an accumulated log with a 60-second margin.
- Explicit `recording_started_at`, OBS filename inference, and filesystem end-time estimate.
- One scalar `combat_log_offset_seconds`.
- Yearless timestamp resolution, New Year rollover, leap-day handling, UTF-8 BOM handling, and malformed-row reporting.
- Boss pulls from `ENCOUNTER_START`/`ENCOUNTER_END`.
- Trash windows from `PLAYER_REGEN_DISABLED`/`PLAYER_REGEN_ENABLED`.
- Boss precedence over overlapping trash markers.
- Minimum-duration and recording-bound filtering.

Verified by:

- Unit fixtures for kills, wipes, repeated attempts, missing ends, offsets, malformed rows, quoted metadata, bounds, New Year, leap day, and combat/boss overlap.

### Milestone E — Legacy damage fallback and Skada overlay

**Status:** Implemented

Delivered:

- Legacy 3.3.5 hostile-activity clustering when boundary parsing returns no pulls.
- Lower-confidence `unknown` candidates rather than unsupported boss assertions.
- A non-executing Skada saved-variable parser that reads top-level scalar segment metadata.
- Boss outcome overlay from Skada, replacing overlapping base candidates.
- Possible-duplicate Skada success detection and default exclusion.

Remaining manual responsibility:

- Confirm the recording-time mapping.
- Classify legacy activity candidates.
- Review every Skada replacement and possible duplicate.

### Milestone F — Static pull review and manual overrides

**Status:** Implemented

Delivered:

- Pull-candidate JSON and CSV.
- Uncertain-segment report.
- Per-pull thumbnail and short review MP4.
- Static HTML with include, title, start, end, and note editing.
- Downloadable `pull-overrides.json`.
- JSON and CSV manual loading.
- `input.manual_pulls` precedence over all automated detection.

Limit:

- Overrides replace the whole pull list. They are not hash-bound operations and the browser cannot apply them to YAML.

### Milestone G — Neutral timeline and exports

**Status:** Implemented

Delivered:

- Float-second `TimelineDocument` and `TimelineClip` models.
- Chronological inclusion policy, handles, trash-gap merging, overlap trimming, and contiguous placement.
- Timeline JSON with condensed duration.
- Four-second SRT pull labels and chapters.
- ElementTree-generated FCPXML 1.10 referencing the microphone-free sidecar.
- Frame conversion with `round(seconds * fps)`.
- Resolve payload with rounded inclusive frames and explicit no-render/no-upload safety flags.

Verified by:

- Timeline merge/overlap tests.
- Export timing, sidecar URI, label, and chapter tests.
- Resolve payload frame and safety tests.

Limit:

- Exact NTSC rational rates, VFR handling, DTD validation, and general NLE
  compatibility are not implemented or proven. The synthetic FCPXML has been
  imported successfully into Resolve 20.3.2.

### Milestone H — Licensed local music

**Status:** Implemented for preview use

Delivered:

- Strict local JSON registry.
- Track metadata, source URL, licence/version, date obtained, required attribution, and explicit use permissions.
- Full SHA-256 verification of selected local music files.
- Failure for missing IDs, files, permissions, hashes, or attribution text.
- Licence and attribution reports.
- First-approved-track selection as a low-level, looped preview bed.

Limit:

- Music is not placed in FCPXML or Resolve, and there are no expiry, revocation, receipt-file, or per-placement models.

### Milestone I — Review preview and final-render boundary

**Status:** Implemented

Delivered:

- FFmpeg filter graph for clip trims, configured scale/pad/FPS, per-clip title banners, fades, retained audio, and optional music.
- Software `libx264`, configured bitrate, AAC stereo, and `+faststart`.
- Disk-space estimate before rendering.
- Review-only filename, MP4 comment metadata, manifest flag, CLI wording, and reports.
- No final-render or upload command.

Limit:

- The preview is not persistently watermarked. Clip labels appear only during each clip’s first four seconds.
- `preview.hardware_encoding` and the entire `final` section are unused.

### Milestone J — Resolve isolation and bounded validation

**Status:** Implemented with an external-environment limitation

Delivered:

- `create-resolve-project` payload preparation.
- Out-of-process `py -3.13 scripts\resolve_bridge.py` invocation.
- Helper-local Resolve environment setup.
- Refusal to modify an existing project.
- Single sidecar import, timeline creation, clip appends, markers, and project save.
- No render-job, render-start, or upload calls.
- Validation reports for source metadata, pull bounds, timeline overlap, boss separation, sidecar audio count, readable preview, and absent final/upload paths.

Limit:

- The audited non-Studio Resolve 20.3.2 installation may block external
  scripting. Live project creation through the helper/API is not proven.
- The helper creates no custom bin and there is no PowerShell launcher.
- Manual GUI project creation and synthetic FCPXML import succeeded; that does
  not prove the external API bridge or real HEVC-sidecar compatibility.

## 4. Implemented command journey

### Guided

```powershell
uv run raid-editor wizard
```

The wizard can create `config/<slug>.local.yaml`, but it does not write browser-downloaded corrections back into configuration and does not create an approval record.

### Deliberate

```powershell
uv run raid-editor inspect config\my-raid.local.yaml --open-review
uv run raid-editor analyse config\my-raid.local.yaml
uv run raid-editor review config\my-raid.local.yaml
uv run raid-editor build-timeline config\my-raid.local.yaml
uv run raid-editor create-resolve-project config\my-raid.local.yaml --dry-run
uv run raid-editor render-preview config\my-raid.local.yaml --dry-run
uv run raid-editor render-preview config\my-raid.local.yaml
uv run raid-editor validate config\my-raid.local.yaml
```

The operator must stop after `review`, save the complete override list, reference it through `input.manual_pulls`, rerun review, and accept it before proceeding.

`--dry-run` is not read-only. It prevents the Resolve call or preview MP4 render, but prerequisite timeline, sidecar, payload, report, and filter files may still be generated.

## 5. Current reuse and recovery behavior

Implemented reuse is artifact-specific:

| Artifact | Reuse key |
|---|---|
| Media probe | Path, size, mtime, first/last 4 MiB SHA-256 |
| Pull candidates | Recording/log/Skada/manual quick fingerprints plus detection settings |
| Mic-free sidecar | Source size/mtime plus retained/excluded indexes |
| Preview | Timeline serialization, filter graph, command, and selected music hash |
| Review media | Expected file already exists |

Text and JSON writes are atomic sibling replacements. FFmpeg media generation writes managed output paths with `-y`. There is no project lock, general stage status, content-addressed cache, or stale-work recovery command.

## 6. Test baseline

The reconciled test run is:

```text
66 passed
```

Outside pytest, the synthetic project also completed a real non-dry-run
1280x720 `render-preview`; FFprobe read the result and `validate` passed.
Resolve 20.3.2 imported its FCPXML and microphone-free sidecar into a new
24-second, three-clip GUI project.

Automated coverage includes:

| Area | Covered behavior |
|---|---|
| Configuration | Strict keys, relative paths, preview dimensions |
| Audio | Role inference, microphone conflicts, retained indexes |
| Combat log | Boss/trash boundaries, offsets, malformed rows, rollover, bounds |
| Timeline | Handles, merging, boss separation, trash subtraction |
| Manual review | Wrapped JSON/CSV model loading |
| Music | Permissions, hash, attribution, missing IDs |
| Export | Timeline JSON, ElementTree FCPXML, SRT, chapters |
| Preview | Retained stream graph, mapped outputs, review metadata |
| Resolve | Rounded frames, safety flags, Python 3.13 isolation |
| Integration | Synthetic dry-run artifacts, mic-free sidecar, unchanged quick fingerprint |

## 7. Manual MVP acceptance still required

These are validation tasks, not implemented guarantees:

- [ ] Repeat the proven non-dry-run preview workflow with a representative real raid and watch it completely.
- [ ] Confirm every retained track is genuinely microphone-free.
- [ ] Verify the recording timestamp and combat-log offset against visible events.
- [ ] Review all legacy low-confidence and Skada-overlay candidates.
- [x] Import the synthetic FCPXML into Resolve and compare the three clip
  boundaries with the 7 s, 7 s, and 10 s generated timeline.
- [ ] Repeat the FCPXML import with the real MOV/HEVC sidecar after its audio
  policy is resolved.
- [ ] Test live Resolve project creation in a supported Studio environment.
- [ ] Confirm the real source’s portrait/landscape scaling, titles, transitions, and audio balance.
- [ ] Archive licence metadata and attribution alongside the delivered edit.

## 8. Deferred hardening plan

Everything in this section is **future work and not current behavior**.

### Deferred A — Strong source identity and stage coordination

- Stream a full recording SHA-256 once and store it as source identity.
- Introduce content-addressed stage keys and dependency invalidation.
- Add integrity checks, corrupt-entry quarantine, project locks, and explicit recovery.
- Replace existence-only review reuse and size/mtime-only sidecar reuse.
- Avoid direct FFmpeg overwrite by rendering to validated temporary siblings.

### Deferred B — Exact media time

- Preserve FFprobe rational frame rates and time bases.
- Replace float-second timeline fields with integer or rational time.
- Add explicit, tested frame-rounding rules.
- Detect VFR and either block export or build a deterministic CFR derivative.
- Validate long-duration NTSC drift.

### Deferred C — Synchronization and corrections

- Add multiple log/media anchors and explicit drift handling.
- Preserve combat-log byte offsets through the window iterator.
- Derive pull IDs from durable evidence rather than list position.
- Replace full-list overrides with hash-bound operations and replay/rebase diagnostics.

### Deferred D — NLE compatibility

- Validate FCPXML against a reviewed schema/DTD approach.
- Record the exact tested Final Cut and Resolve versions.
- Prove representative imports, audio roles, NTSC rates, markers, and Unicode paths.
- Add optional custom Resolve bin organization only if the API environment is supported.
- Generalize the Resolve interpreter/SDK compatibility matrix.

### Deferred E — Stronger review gating

- Add a persistent `REVIEW — NOT FINAL` visual watermark.
- Probe and inspect rendered output before accepting its manifest.
- Add a durable operator-approval record tied to timeline and source identity.
- Keep any future final renderer behind a separate accepted decision and explicit per-run gate.

### Deferred F — Advanced analysis

- CV/OCR fallback for missing logs.
- Audio classification, transcription, diarization, or source separation.
- Gameplay-aware highlight proposals.
- Beat analysis and deliberate music placement.
- Multi-recording sessions and proxy workflows.

Any future AI/CV output must remain a proposal with model/version provenance and human acceptance. It must not bypass microphone exclusion, music permission checks, or final-render boundaries.
