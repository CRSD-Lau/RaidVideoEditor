# Raid Video Editor decision log

This log describes the landed MVP. Accepted entries are implemented. Entries marked **Deferred** are proposals only and must not be read as current behavior.

## ADR-0001 — Use a Windows Python 3.12 CLI

**Date:** 2026-07-26  
**Status:** Accepted

### Context

The workflow processes large local files and must remain scriptable without requiring a custom desktop application.

### Decision

Use the `raid_editor` Python 3.12 package with the `raid-editor` Typer entry point. Use Pydantic for strict serialized/configuration models and PyYAML `safe_load` for project YAML. Keep the main edit workflow local.

### Consequences

The commands are testable and composable from PowerShell. A native GUI is not part of the MVP. The supported command surface is limited to `inspect`, `analyse`, `review`, `build-timeline`, `create-resolve-project`, `render-preview`, `validate`, and `wizard`.

## ADR-0002 — Model one recording with one YAML project

**Date:** 2026-07-26  
**Status:** Accepted

### Context

The initial use case is one long OBS raid recording with local evidence and explicit operator choices.

### Decision

Each project YAML references one recording, optional combat/Skada/manual evidence, audio indexes, edit settings, music registry, and preview settings. Resolve relative paths from the YAML directory. Write generated artifacts under `output/<project-name-slug>/`.

### Consequences

Projects are understandable and portable when their referenced paths are preserved. Two projects with the same display name share an output root. Multi-file stitching is deferred.

## ADR-0003 — Store a normalized FFprobe model

**Date:** 2026-07-26  
**Status:** Accepted

### Context

OBS track indexes, codecs, dimensions, and metadata vary and cannot safely be inferred from filenames or profile settings.

### Decision

Call FFprobe JSON and store a normalized Pydantic model containing the format, first-class video fields, and all audio streams with both absolute index and audio ordinal. Use the first video stream and its average frame rate for the MVP.

### Consequences

Audio mapping and exports share one media description. The complete raw FFprobe payload, exact rational frame rate, nominal-rate comparison, and VFR classification are not retained.

## ADR-0004 — Use bounded recording fingerprints for reuse

**Date:** 2026-07-26  
**Status:** Accepted

### Context

Repeatedly hashing an 11 GB recording would slow every command, while path/mtime alone would be too weak for ordinary replacement detection.

### Decision

Identify a recording for probe and analysis reuse with canonical path, size, nanosecond modification time, and SHA-256 over at most the first and last 4 MiB. Use related quick fingerprints for combat logs, Skada exports, and manual pull files.

### Consequences

Common file replacement or modification is detected cheaply. This is not a full content identity and does not support a content-addressed cache or strict source-integrity claim.

## ADR-0005 — Require explicit dedicated-track microphone exclusion

**Date:** 2026-07-26  
**Status:** Accepted

### Context

A default or mixed OBS audio stream can contain the operator’s microphone.

### Decision

Sample every audio stream, display absolute FFprobe indexes, and require explicit YAML mapping. Reject a microphone stream retained as game/Discord audio. Build a stream-copy sidecar containing only the first video stream and retained audio, probe its audio count, and use it for NLE exports. Address only retained streams in preview filters.

### Consequences

A dedicated microphone track can be excluded without transcoding the source video. The tool cannot remove speech already mixed into a retained stream. Generated sidecars use managed output paths and FFmpeg overwrite behavior rather than a general atomic-media writer.

## ADR-0006 — Detect pulls from combat evidence before weaker fallbacks

**Date:** 2026-07-26  
**Status:** Accepted

### Context

Legacy and modern WoW logs can contain explicit boss/combat-state boundaries, but the real legacy 3.3.5 file is a large accumulated log and some sessions lack those markers.

### Decision

Stream only the recording-time window plus a 60-second margin. Use `ENCOUNTER_START`/`ENCOUNTER_END` for boss attempts and `PLAYER_REGEN_DISABLED`/`PLAYER_REGEN_ENABLED` for non-boss combat. Give boss windows precedence over overlapping trash. If no pulls result, cluster recognized legacy damage activity into lower-confidence `unknown` candidates.

### Consequences

Strong evidence remains distinguishable from fallback activity. The fallback does not claim boss identity or outcome and requires human classification.

## ADR-0007 — Use one recording start and one combat-log offset

**Date:** 2026-07-26  
**Status:** Accepted

### Context

Legacy logs lack year/time-zone data and must be aligned to media time.

### Decision

Use an explicit `recording_started_at` when configured. Otherwise infer it from an OBS-style filename, then fall back to filesystem completion time minus media duration. Apply one configured `combat_log_offset_seconds` value and record the selected synchronization mode in evidence/reports.

### Consequences

The workflow handles the current accumulated legacy log without an interactive synchronization subsystem. Filename/filesystem inference can be wrong, and there is no clock-drift correction or multiple-anchor model.

## ADR-0008 — Overlay optional Skada boss segments without executing Lua

**Date:** 2026-07-26  
**Status:** Accepted

### Context

Skada saved variables can provide boss names, timestamps, and results when the combat log is incomplete, but executing or generally parsing addon Lua would be unsafe.

### Decision

Read only top-level scalar fields from recognized Skada segment tables. Map valid segments to recording seconds, replace overlapping base candidates, and classify success/failure with higher confidence. Exclude suspicious short repeated successes as possible duplicates pending review.

### Consequences

Skada improves boss labeling while remaining a non-executing evidence overlay. Misalignment and addon-specific data quirks remain possible and require review.

## ADR-0009 — Make manual review a complete-list override

**Date:** 2026-07-26  
**Status:** Accepted

### Context

The static browser page cannot safely write local project files, but operators need to change inclusion, titles, bounds, and notes.

### Decision

Generate static HTML that downloads a full `pull-overrides.json`. Accept a complete list of `PullCandidate` records from JSON or CSV through `input.manual_pulls`. When present, that file bypasses combat-log and Skada analysis.

### Consequences

The correction loop is simple and inspectable. Overrides are not hash-bound operations, have no replay/rebase semantics, and can become stale if source evidence changes.

## ADR-0010 — Use float seconds and round at frame exports

**Date:** 2026-07-26  
**Status:** Accepted

### Context

The MVP needs a shared representation for JSON, FFmpeg, FCPXML, and Resolve without introducing a time library.

### Decision

Store source/timeline positions and FPS as floats. Build non-overlapping chronological clips in seconds. Convert to frames with Python `round(seconds * fps)`; use rounded FPS as the FCPXML denominator.

### Consequences

The model is easy to inspect and works for the current integer-FPS synthetic fixture. It is not exact for NTSC rates, long-duration drift, or VFR media.

## ADR-0011 — Generate conservative ElementTree FCPXML

**Date:** 2026-07-26  
**Status:** Accepted

### Context

An editor-independent fallback is needed even when Resolve external scripting is unavailable.

### Decision

Generate an FCPXML 1.10 library/event/project/spine using `xml.etree.ElementTree`. Reference the microphone-free sidecar and emit one asset clip, keyword, and marker per timeline clip. Verify well-formedness in tests.

### Consequences

The export has no additional XML dependency and is inspectable. It is not
validated against an Apple DTD. A synthetic H.264 export imported successfully
through the Resolve 20.3.2 GUI on 2026-07-26; Final Cut and real HEVC-sidecar
compatibility remain unproven.

## ADR-0012 — Isolate Resolve automation in Python 3.13

**Date:** 2026-07-26  
**Status:** Accepted

### Context

On the audited machine, importing Resolve 20.3.2’s shim crashes Python 3.11/3.12, and the apparent non-Studio edition may not expose external scripting.

### Decision

Keep the shim out of the Python 3.12 process. Write a JSON payload, then invoke `py -3.13 scripts/resolve_bridge.py`. Have the helper set its own Resolve environment variables, refuse an existing project, import one sidecar, create a timeline, append clips, add markers, and save. Forbid render and upload requests in the payload/helper.

### Consequences

The main CLI remains stable if the Resolve shim is incompatible. There is no
PowerShell launcher or custom bin. Project creation through the external API
depends on a running compatible Resolve Studio environment and is not yet
proven; manual GUI project creation and synthetic FCPXML import succeeded.

## ADR-0013 — Make preview rendering the only render path

**Date:** 2026-07-26  
**Status:** Accepted

### Context

The operator needs a watchable review but should not mistake the automated output for an approved final master.

### Decision

Expose only `render-preview`. Build a configurable scale/pad/FPS filter graph, label each clip for four seconds, fade transitions, mix retained audio, and optionally add the first approved music track. Encode with software `libx264`, configured bitrate, AAC stereo, and review-only filename/metadata/manifest. Leave the `final` configuration section unused and provide no final or upload command.

### Consequences

The output is clearly treated as a preview in workflow metadata and naming. It is not persistently watermarked, and there is no machine-enforced approval record before timeline or preview generation.

## ADR-0014 — Gate music with registry permissions and a full music hash

**Date:** 2026-07-26  
**Status:** Accepted

### Context

A local audio filename alone does not show that it is approved for synchronization or monetized YouTube use.

### Decision

Require selected registry entries to include explicit YouTube, monetization, and derivative-synchronization permission; local file; full SHA-256; and attribution text when required. Use only explicitly approved IDs and verify the file hash immediately before use.

### Consequences

Preview music has stronger identity than source-video caching. Only the first approved track is looped in the preview. The registry does not manage receipt files, expiry, revocation, or NLE music placement.

## ADR-0015 — Use scoped idempotency instead of a stage engine

**Date:** 2026-07-26  
**Status:** Accepted

### Context

Probe, detection, sidecar creation, and preview rendering are expensive enough to benefit from reuse, but the MVP does not need a general workflow database.

### Decision

Use artifact-specific manifests: quick fingerprint for probes, evidence/settings signature for analysis, source metadata plus track mapping for sidecars, and timeline/filter/command/music signature for previews. Reuse expected review files by existence. Use atomic temporary-sibling replacement for application-written text and JSON.

### Consequences

Common reruns are faster and text/JSON writes survive interruption cleanly. There is no content-addressed cache, project lock, stale-lock recovery, corrupt-cache quarantine, general dependency graph, or uniform atomic guarantee for FFmpeg media.

## ADR-0016 — Define determinism as repeatable decisions, not byte identity

**Date:** 2026-07-26  
**Status:** Accepted

### Context

The editor must make predictable choices, but FFmpeg output and formatted artifacts can vary across toolchain versions.

### Decision

Test deterministic ordering, pull/window rules, selected stream indexes, frame rounding, generated command structure, and safety flags for the current models and toolchain. Do not claim byte-stable media or strict cross-version JSON/XML identity. Do not record external executable hashes in the MVP.

### Consequences

Tests can protect editorial and safety behavior without overstating reproducibility. Strong supply-chain and byte-for-byte reproducibility remain separate hardening work.

## ADR-0017 — Keep validation bounded and explicit

**Date:** 2026-07-26  
**Status:** Accepted

### Context

Operators need a final local checklist, but the MVP lacks full source hashing, content analysis, and live NLE validation.

### Decision

Validate source size/mtime, pull bounds, non-overlapping timeline windows, distinct boss clips, sidecar audio-stream count, readable preview, and the absence of final-render/upload paths. Report each check as pass/fail.

### Consequences

Validation catches common workflow mistakes. It does not prove microphone absence inside retained mixes, full source identity, persistent watermarking, subjective quality, or NLE compatibility.

## ADR-0100 — Add full source identity and content-addressed stages

**Date:** 2026-07-26  
**Status:** Deferred — not implemented

### Context

Bounded fingerprints can theoretically miss modifications and do not coordinate concurrent commands.

### Proposal

Add full recording SHA-256, content-addressed stage keys, dependency invalidation, project locks, integrity verification, quarantine, and explicit recovery.

### Consequences

This would strengthen provenance and resume behavior at the cost of initial hashing time and significantly more state-management code.

## ADR-0101 — Add exact rational time and VFR policy

**Date:** 2026-07-26  
**Status:** Deferred — not implemented

### Context

Float seconds and rounded FPS are insufficient for exact NTSC/VFR interchange.

### Proposal

Preserve FFprobe rational rates/time bases, adopt integer or rational timeline time, define exact rounding, detect VFR, and block or create a deterministic CFR derivative.

### Consequences

Exports would be more reliable across long recordings and NLEs, but schemas and all exporters would need migration.

## ADR-0102 — Add anchored synchronization and correction operations

**Date:** 2026-07-26  
**Status:** Deferred — not implemented

### Context

One offset cannot model clock drift, and complete-list overrides have weak stale-state behavior.

### Proposal

Introduce multiple log/media anchors, piecewise mapping, byte-offset-derived evidence IDs, and hash-bound correction operations with replay/rebase diagnostics.

### Consequences

Review would be safer and more durable, but the UI, schemas, parser provenance, and migration behavior would become substantially more complex.

## ADR-0103 — Validate NLE exports against supported environments

**Date:** 2026-07-26  
**Status:** Deferred — not implemented

### Context

ElementTree well-formedness and mocked Resolve calls do not prove import compatibility.
On 2026-07-26, Resolve 20.3.2 successfully imported the synthetic H.264
three-pull FCPXML and microphone-free sidecar into a new GUI project. That is a
useful fixture result, not a compatibility matrix or proof for the real HEVC
sidecar.

### Proposal

Adopt a reviewed DTD/schema-validation approach, preserve exact rates, and maintain a tested Final Cut/Resolve compatibility matrix with live import fixtures.

### Consequences

Compatibility claims would become evidence-based. This requires access to supported NLE environments and careful handling of Apple/Blackmagic version changes.

## ADR-0104 — Add persistent review marking and durable approval

**Date:** 2026-07-26  
**Status:** Deferred — not implemented

### Context

Filename, metadata, manifest, and CLI wording can be lost when a preview is copied.

### Proposal

Burn a persistent `REVIEW — NOT FINAL` mark into every preview and tie a durable operator approval to source and timeline identity before any future final-render path.

### Consequences

Review artifacts would be harder to misuse, but the overlay asset, runtime verification, accessibility, and approval lifecycle require design and tests.

## ADR-0105 — Keep CV and AI advisory if introduced

**Date:** 2026-07-26  
**Status:** Deferred — not implemented

### Context

CV/OCR, speech, and multimodal models could help when logs are absent, but their output is probabilistic.

### Proposal

Allow future models to emit proposals with model/version provenance, confidence, and evidence. Require human acceptance and prohibit bypassing audio exclusions, music permissions, or final-render boundaries.

### Consequences

Advanced automation could improve efficiency without becoming an unaudited authority. No CV or AI component exists in the MVP.
