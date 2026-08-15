# Security, privacy, and generated-file removal

The MVP is designed to run locally and to leave source files unchanged. Local
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

`upload-youtube` transmits only after `--approved`, defaults to Private, and
requires the additional `--public-approved` flag if the YAML requests Public.
`sync-playlist` is a separate approval-gated YouTube mutation.
`youtube-analytics` is authenticated but read-only. Each purpose uses a separate
token file and least-purpose scope. The Resolve bridge remains isolated from
these paths and explicitly forbids render and upload requests.

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

The CLI writes beneath `output\<project-slug>\` and may create
`config\<project-slug>.local.yaml` through the wizard. It never intentionally
renames, moves, deletes, or overwrites the source inputs.

`archive` is the only command that writes outside the output tree. It requires
`--approved`, copies only to the exact configured destination, refuses an
existing project destination, and verifies every source/destination SHA-256.
There is no archive move, cleanup, or source deletion operation.

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
| `highlights\review\assets\*.mp4` | Full candidate windows with game and Discord audio |
| `highlights\vertical\*.mp4` | Approved portrait exports with potentially private raid comms |
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

## Local HTML

Review pages are static HTML with local relative media and no remote scripts,
fonts, analytics, or server. They can still reveal local content when copied
with their asset folder.

The browser cannot safely overwrite project files. It downloads
`audio-map.json`, `pull-overrides.json`, or `highlight-overrides.json`; the
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

The current Resolve installation is apparently non-Studio, and API success has
not been demonstrated. If Studio is later used:

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
Set-Location C:\Projects\RaidVideoEditor
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
