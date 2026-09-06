---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Spoken `clip it` highlight triggers

The weekly workflow can propose a highlight whenever either isolated voice stem
clearly contains the exact English phrase `clip it`. Detection runs after the
raid, entirely on the local workstation. It is a review aid, not an approval:
every resulting candidate starts with `include: false` and must be watched in
the same highlight review page as energy, motion, death-pressure, and kill
climax proposals.

## One-time workstation setup

From the cloned repository root, install the locked optional recognizer:

```powershell
uv sync --extra speech --frozen
```

If editorial intelligence or development tools are already installed, include
their extras in that sync too, for example
`uv sync --extra dev --extra speech --extra intelligence --frozen`. Otherwise
sync can remove optional packages that were not requested.

Download the small US-English Vosk model from the upstream Alpha Cephei model
registry, verify the pinned archive hash, and expand it into the Git-ignored
local model directory:

```powershell
$raidModelRoot = Join-Path (Get-Location).Path '.models'
$raidModelZip = Join-Path ([System.IO.Path]::GetTempPath()) 'vosk-model-small-en-us-0.15.zip'
New-Item -ItemType Directory -Force -Path $raidModelRoot | Out-Null
Invoke-WebRequest `
  -Uri 'https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip' `
  -OutFile $raidModelZip

$raidExpectedHash = '30F26242C4EB449F948E42CB302DD7A686CB29A3423A8367F99FF41780942498'
$raidActualHash = (Get-FileHash -LiteralPath $raidModelZip -Algorithm SHA256).Hash
if ($raidActualHash -ne $raidExpectedHash) {
  throw "Unexpected Vosk model archive hash: $raidActualHash"
}

Expand-Archive -LiteralPath $raidModelZip -DestinationPath $raidModelRoot
```

The Friday config generator enables the feature and writes the absolute model
path automatically. For a hand-written config stored under `config\`, use:

```yaml
highlights:
  speech_triggers:
    enabled: true
    required: false
    backend: "vosk"
    model_path: "../.models/vosk-model-small-en-us-0.15"
    phrases: ["clip it"]
    source_roles: ["discord", "microphone"]
    minimum_word_confidence: 0.80
    maximum_word_gap_seconds: 0.50
    dedupe_seconds: 6
    maximum_matches: 50
    sample_rate_hz: 16000
```

`required: false` is deliberate. A missing runtime, model, or single voice stem
does not erase the existing heuristic suggestions, but the run is labelled
`unavailable`, `partial`, or `failed` rather than falsely reporting zero spoken
commands. Set `required: true` only when a missing speech pass should stop the
whole highlight analysis.

## Recognition contract

- Only the configured absolute Discord and microphone stream indexes are
  decoded. Game and Full Mix are never speech inputs by default.
- FFmpeg streams mono 16-bit PCM at 16 kHz into Vosk. No multi-hour WAV is
  written.
- Vosk uses the constrained grammar `["clip it", "[unk]"]` and returns word
  timing/confidence. Matching is case- and punctuation-insensitive but otherwise
  exact; `click it`, `clip this`, low-confidence words, and words separated by
  more than the configured gap do not qualify.
- Cross-stem echoes within six seconds collapse deterministically to the
  strongest event, preferring the isolated microphone on a tie.
- The command is anchored at its final word. The normal 25-second lead-in and
  15-second lead-out therefore preserve the action that prompted someone to say
  `clip it`.
- Command-backed proposals bypass the heuristic minimum score, 30-second
  spacing, and 12-candidate quota. They still respect the separate 50-command
  safety ceiling, with explicit truncation reporting.
- The full-raid render remains game-only. Voice-track analysis does not imply
  voice retention; the highlight review/export mix remains controlled by
  `keep_discord_audio` and `keep_microphone_audio`.

## Evidence and privacy

The workflow writes:

- `highlights\speech-trigger-status.json` with coverage status and only matched
  spans;
- `highlights\speech-trigger-manifest.json` with the local cache identity; and
- `reports\speech-trigger-status.md` with a readable summary.

The persisted event contains the canonical phrase, source role, start/end,
confidence, backend version, and model identity. It does not contain a full or
partial transcript, surrounding dialogue, decoded audio, or a cloud request.
Changing the recording, stream indexes, phrase settings, recognizer version, or
model fingerprint invalidates the speech cache.

## Review and calibration

Run the usual command:

```powershell
uv run --no-sync raid-editor analyse-highlights config\my-raid.local.yaml --open
```

Read the spoken coverage banner before approving clips. Discord compression,
overlapping voices, noise suppression, and delivery can cause misses; similar
speech can still cause a false proposal. Tune confidence or de-duplication only
against representative private raid audio, and keep every result review-only.
