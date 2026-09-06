---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Security, privacy, and generated-file removal

The editing CLI is designed to run locally and to leave source files unchanged. Local
does not mean non-sensitive: generated samples, clips, reports, and timelines can
contain voice, player names, file paths, and licensing evidence.

## Source repository boundary

The Git repository excludes recordings, generated output, local YAML/JSON
configuration, OAuth clients, OAuth tokens, private keys, and the complete
`secrets/` directory. The committed CI workflow scans full history with
Gitleaks. Before a release or new remote push, scan both Git history and the
staged snapshot; never rely on filename exclusions alone.

Repository presentation assets contain no recording frames or private player
data. The social preview uses the user-supplied guild mark over an original
background. Brand artwork is outside the source-code license; see
[Third-party notices](../THIRD_PARTY_NOTICES.md).

## Network and publication boundary

Normal inspection, analysis, highlight ranking, review, timeline, preview,
validation, final render, preflight, and archive planning remain local. They do
not implement cloud storage, telemetry, authentication, upload, or remote music
acquisition. Browser-opening review commands open a local `file:` page.

Optional editorial inference sends temporary text and sparse frames only to a
validated IPv4 loopback HTTP endpoint, default `127.0.0.1:11435`. The adapter
uses direct `HTTPConnection` requests rather than environment proxy/redirect
handling. It does not invoke a cloud model, transmit voices to a provider, or
download a missing model during analysis. The separately invoked
`scripts/setup-highlight-intelligence.py` downloads the pinned portable runtime,
speech weights, and requested local editorial model (default `qwen3.5:9b`) from
their upstream distribution
services. Setup records provenance and sends no raid content.

The managed portable runtime uses process-local environment settings, disables
cloud mode, suppresses diagnostic stdout/stderr and visible console windows,
and terminates only the server process it started. It may reuse an already
healthy local endpoint; that process retains its own lifecycle and configuration.
There is no global service, startup entry, driver installation, or persistent
environment edit. Optional NVIDIA wheels are confined to the project virtual
environment. Local processing is not a guarantee against an operating-system
crash dump, paging, or another application's logging; this pipeline itself does
not persist inference conversation or raw model output.

`upload-youtube` transmits only after `--approved`, defaults to Private, and
requires the additional `--public-approved` flag if the YAML requests Public.
`sync-playlist` is a separate approval-gated YouTube mutation.
`youtube-analytics` is authenticated but read-only. Each purpose uses a separate
token file and least-purpose scope. The Resolve bridge remains isolated from
these paths and explicitly forbids render and upload requests.

`prepare-social`, `approve-social`, `confirm-social-publication`,
`social-status`, and `social-analytics` are local package and evidence commands.
They never read browser cookies, passwords, recovery codes, two-factor codes,
stream keys, or platform access tokens. Posting is performed only in the
official signed-in interfaces. Preserve personal Facebook and Instagram
identities, verify the visible destination account before every post, and stop
for operator input whenever a platform requests a credential, CAPTCHA, or
identity check.

The operator can still share files manually or use another application's
network features. Keep the review workspace outside synchronized folders unless
that sharing is intentional.

## Source safety

Read-only inputs include:

- the OBS recording;
- the combat log;
- the optional Skada SavedVariables file;
- the optional manual-pull file;
- the music library and music files.

Normal analysis/render commands write beneath `output\<project-slug>\`.
The wizard and Friday workflow can create local configuration; feedback import
writes its explicit destination, defaulting to `config\highlight-feedback.local.json`.
The separate inference setup script writes the ignored local `.tools/` and
`.models/` directories. These operations do not rename, move, delete, or overwrite
the recording or combat-log inputs.

`archive` writes to a separately configured archive destination. It requires
`--approved`, copies only to that exact destination, refuses an
existing project destination, and verifies every source/destination SHA-256.
It has no archive move or source-deletion operation. Separately approved weekly
cleanup is an operator script boundary, not part of model setup or inference.

`validate` checks that the source size and nanosecond modification time still
match the cached probe. The media fingerprint hashes bounded head/tail chunks,
not the complete recording. Treat this as a practical change detector, not a
cryptographic proof that every source byte remained unchanged.

`validate` and both `--dry-run` paths are not read-only. They can rebuild
timeline, sidecar, report, manifest, payload, and filter files.

## Sensitive generated content

| Artifact | Possible sensitive content |
| --- | --- |
| `analysis\media-probe.json` | Absolute recording path, timestamps, stream metadata, bounded fingerprint |
| `analysis\analysis-manifest.json` | Bounded fingerprints for recording, combat log, Skada, and manual pulls |
| `analysis\combat-log-issues.json` | Raw malformed log rows, character/unit IDs and names |
| `review\audio-samples\*.wav` | Every inspected stream, including microphone speech |
| `review\assets\*.mp4` | Gameplay and retained voice comms |
| `review\pull-review.html` | Encounter names, notes, evidence, local media references |
| `highlights\review\assets\*.mp4` | Full candidate windows with configured game, Discord, and optional microphone audio |
| `highlights\vertical\*.mp4` | Approved portrait exports with potentially private raid comms and creator microphone |
| `social\distribution-manifest.json` | Target account names, media/copy hashes, release times, remote IDs and public URLs |
| `social\receipts\*.jsonl` | Append-only publication-state history and verification evidence |
| `social\analytics\*` | Platform metrics entered manually or obtained through an official API/export |
| `highlights\speech-trigger-status.json` | Exact matched phrase spans, source role, confidence, and local recognizer/model provenance; never a full transcript |
| `highlights\intelligence-status.json` | Coverage, fixed-class candidate labels/rationales, timing, safe diagnostics, model identities and visual verdicts; no dialogue or raw model response |
| `highlights\discovery-pool.json`, `repaired-baseline.json` | Unapproved source-time proposals and source fingerprints for comparison |
| `highlights\comparison\*` | Local full-context clips, blinded review and variant membership; may contain the configured voice mix |
| `config\highlight-feedback.local.json` | Explicit reviewed ratings/reasons, source references, category/origin, timing and short reviewed titles; no review notes or transcript |
| `timeline\timeline.fcpxml` | Absolute `file:` URI to the generated sidecar |
| `resolve\create-project.json` | Absolute sidecar path, clip labels and ranges |
| `reports\*` | Raid titles, detected encounters, audio names, music/license evidence |
| `preview\*.mp4` | Condensed gameplay, Discord/raid comms, optional music |
| `final\*.mp4` | Approved high-quality master selected for upload |
| `youtube\metadata.json` | Intended title, description, tags, audience, and visibility |
| `youtube\upload-manifest.json` | Full master hash, metadata hash, YouTube video ID and URL |
| `analytics\*` | Channel-owner views, retention, CTR entered from Studio, and video ID |
| external archive destination | Raw source, final master, project artifacts, and hashes |

OAuth client and token files live under the repository's ignored `secrets\`
directory, outside the generated output tree. They must never be committed,
shared, logged, or copied into reports. Upload, playlist management, and
analytics use separate tokens and scopes; the application stores no Google
password.

The audio-identification page intentionally samples every stream because the
operator must identify the mic. Do not share it as proof of a “mic-free” result.

The optional Vosk recognizer is local-only. It scans only explicitly configured
Discord and microphone stream indexes, and `.models\` is ignored by Git. No
cloud speech service, automatic model download, full transcript, partial-result
log, or surrounding conversation is persisted. `complete` with zero matches is
different from `unavailable`, `partial`, `failed`, or `truncated`; the review
page exposes that distinction so missing recognition is never presented as a
clean scan.

The separate faster-whisper/vision path decodes inference audio and frames into
memory. Timed utterances are transient local model input. Model responses must
match strict fixed-class schemas and exact available evidence IDs; free-form
dialogue-conditioned prose is not accepted into persistent results. Titles and
rationales are generated from application templates, with checks against
verbatim source dialogue. Errors use fixed safe diagnostic codes. Prompts treat
spoken content and feedback as untrusted data rather than operational
instructions; no model tool execution or approval capability is exposed.

Configured Qwen requests disable the model's thinking mode with `think: false`.
The discovery/boundary assessment fields remain transient local inference
material; neither those assessments nor raw model replies are saved in reports.

Editorial feedback requires an explicit rating, approval, rejection reason or
recognized all-declined decision. Unchecked/unreviewed clips are omitted. It is
bounded and deduplicated by source/candidate identity; invalid data fails before
writing. The semantic rubric consumes aggregates and structural examples rather
than titles or notes. Positive feedback is not export approval. Comparison
downloads always keep `include: false` and expose memberships only in their
metadata/page source, not in visible labels; blinding is not access control.

## Local HTML

Review pages are static HTML with local relative media and no remote scripts,
fonts, analytics, or server. They can still reveal local content when copied
with their asset folder.

The browser cannot safely overwrite project files. It downloads
`audio-map.json`, `pull-overrides.json`, `highlight-overrides.json`, or comparison
ratings; the
operator must inspect and apply those files. Treat downloaded JSON as untrusted
input until the CLI validates it.

## Combat and Skada parsing

Combat logs are read as text with replacement for invalid UTF-8. Malformed
relevant rows can be copied into the issue report.

The Skada parser does not execute Lua or import the SavedVariables file as code.
It scans structural braces and a restricted set of top-level scalar assignments.
Nested actor/damage content is ignored. Keep untrusted SavedVariables files
local; this restricted parser does not make the rest of the file safe to execute
elsewhere.

## Resolve scripting

The July 26, 2026 host audit found an apparent non-Studio installation; successful
live API project/import remains unproven. Verify the actual edition and local
compatibility before using the optional bridge. For a supported Studio setup:

- enable external scripting for **Local** only, not network access;
- use the isolated Python 3.13 bridge;
- inspect the JSON payload before execution;
- operate in a new unique project; and
- stop before Deliver, Quick Export, render queue, sign-in, or upload controls.

The bridge sets API environment variables only in its own process. It does not
need persistent machine/user environment changes.

A bridge failure after project creation can leave a partial Resolve project.
The CLI never deletes it automatically.

## Safely remove generated files

There is no `clean` command. Close browser tabs and Resolve first. Determine the
exact output folder from the project's `project.name` or from command output.
Then use a guarded PowerShell deletion.

Example for `output\pizza-warriors-raid`:

```powershell
# Run from the cloned repository root.
$outputRoot = (Resolve-Path -LiteralPath '.\output').Path
$generated = (Resolve-Path -LiteralPath '.\output\pizza-warriors-raid').Path
$requiredPrefix = $outputRoot + [IO.Path]::DirectorySeparatorChar
if (-not $generated.StartsWith($requiredPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove a path outside the output directory: $generated"
}
Get-ChildItem -LiteralPath $generated -Force
Remove-Item -LiteralPath $generated -Recurse -Force
```

This removes only that generated project folder. It does not remove:

- the recording, combat log, Skada file, music, or other source;
- `config\<slug>.local.yaml` created by the wizard;
- `secrets\youtube-client.local.json` and `secrets\youtube-token.local.json`;
- a downloaded `audio-map.json` or `pull-overrides.json` saved elsewhere;
- a manual-pull file moved beside the config;
- a Resolve project created through the API or UI; or
- files copied out of the output folder.

Remove those separately only after resolving and verifying their exact paths.
Do not use `Remove-Item .\output\* -Recurse`, a broad drive path, an unresolved
environment variable, or a wildcard derived from project input.

The shipped `archive` command only copies and verifies files. Social/growth
commands report cleanup eligibility and dependency holds; this public repository
does not ship a source-deletion command or an operator's private weekly cleanup
helper. Any separately approved cleanup process must review an exact path
allowlist and obey those holds before removing files.

Treat social portrait media as a separate retention class. Keep each small
social master until its required platform packages are `verified_public` or
an explicit waiver is recorded. A prepared, uploaded, processing, scheduled,
failed, or uncertain post is not cleanup proof. Preserve originals needed for
planned review or comparison even when publication has completed.

Generated output is reproducible from the current config and inputs, but manual
correction files and license evidence are user decisions. Back those up before
deleting anything.

## Before sharing a preview

- Watch the full preview for microphone leakage.
- Confirm it contains no private overlays, chat, account details, or accidental
  desktop capture.
- Check whether Discord participants consent to sharing.
- Verify music permission and attribution.
- Remove local-path JSON/XML and raw audio samples from any share package.
- Share only the intended MP4 and deliberately selected reports.
- Remember that the preview is not a final master and has no persistent visual
  watermark.

## Before an approved YouTube upload

- Read the complete generated metadata and chapter list.
- View the generated thumbnail and confirm it contains no unintended private UI.
- Confirm `privacy_status: private` unless immediate publication is deliberate.
- Verify the final-validation report is passed and the selected path is the
  intended final master.
- Keep the terminal running during the upload. If it exits before a URL is
  recorded, check YouTube Studio before retrying.
- After upload, wait for 1440p processing and watch the Private result before
  changing visibility.
