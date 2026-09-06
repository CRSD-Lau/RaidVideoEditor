---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Local highlight intelligence

The highlight lane proposes complete, distinctive moments for human review.
It combines repaired gameplay evidence with temporary local speech analysis
and optional visual checks. It can return no strong semantic suggestions.
An exact spoken `clip it` request still reserves a review candidate. Neither
the model, a positive review rating, nor this command approves an export.

## What changed

The September 4 review contained fourteen proposals and no accepted highlights.
Ten proposals contained a boss-kill signal, and eight scores were tied at 1.0.
A subsequent combat-log audit found that the old death detector could count an
NPC or totem death as a player death because it inspected every field, including
the empty source GUID. Its Deathwhisper “clutch” evidence was four totems and
zero player deaths. The repaired detector checks destination player identity.

The heuristic stage now uses a monotonic score without hard saturation at 1.0,
relative voice-energy evidence, limited routine-kill coverage, and overlap
suppression. Volume plus motion does not earn a Funny label, and a boss finish
plus deaths does not establish a Clutch. These signals identify places to
inspect; their ranking is not a probability of a good highlight.

Semantic shortlisting always prioritizes the higher score. Exact score ties
favor moments farther from those already selected, so the beginning of a raid
does not consume the queue simply because several model scores are equal.

Semantic discovery scans overlapping speech windows across the recording,
including between-boss conversation. It is not restricted to the old top twelve
heuristic proposals. Its fixed moment classes cover a developed joke,
unexpected reaction, recovery, unusual gameplay, surprising loot, and a guild
milestone. The model receives compact utterances containing an ID, source role,
start/end time and text. It selects existing evidence IDs rather than inventing
timestamps or copying a word-timing array. The original word times remain local
and determine the first/last evidence bounds within configured duration limits.

A second, bounded review of shortlisted candidates checks whether the evidence
contains the complete connected exchange. It can remove routine conversation
before the setup or after the final response. This extra review is still a model
judgment, not a guarantee of correct trimming. Invalid/missing refinement or a
refiner error produces partial, noncacheable coverage rather than silently
claiming a complete editorial pass. The normal partial-coverage fallback and
`required` behavior still apply.
Sparse visual checks can support, contradict, or leave the gameplay context
uncertain; they do not prove that a joke is funny or a rescue was exceptional.

The persisted title and rationale come from fixed application templates for
these classes. Free-form model prose, dialogue quotations, full transcripts,
decoded inference audio, and inference frames are not written into the result.
This deliberately limits the detail of explanations in the review page.

## Install the optional local runtime

Run setup explicitly from the cloned repository root on the workstation that
will process the raid:

```powershell
uv sync --extra dev --extra speech --extra intelligence --frozen
uv run --no-sync python scripts\setup-highlight-intelligence.py
```

The `speech` extra supplies the separate exact-command Vosk recognizer. Follow
[spoken highlight setup](spoken-highlight-triggers.md) for its model. The
`intelligence` extra supplies faster-whisper and its Python dependencies. The
explicit setup script downloads these inference assets:

| Asset | Source and recorded identity | Local destination |
| --- | --- | --- |
| Portable Ollama 0.33.3 | Official Ollama GitHub release; ZIP SHA-256 `52cb36a62e7e501f61514f60212dec7117b6c098811357585e02fffe32d2fcd7` | `.tools/ollama-0.33.3/` |
| English speech model | `Systran/faster-whisper-medium.en`, revision `a29b04bd15381511a9af671baec01072039215e3` | `.models/faster-whisper-medium.en/` |
| Local editorial/vision model | Ollama library `qwen3.5:9b`; installed model digest recorded after download | `.models/ollama/` |

The `dev` extra is useful when running the accompanying regression checks; it
can be omitted for normal operation. Include it when syncing an environment
whose development tools you want to retain. The commands below use `--no-sync`
after setup to keep the explicitly installed optional extras available.

The Qwen tag is resolved during explicit setup; the downloaded digest is
recorded in `.models/highlight-intelligence-provenance.json`. Do not describe a
tag alone as immutable model identity. Runtime checks also include installed
dependency versions and local model fingerprints.

Setup accepts an optional `--model MODEL_TAG` to explicitly download a different
local editorial model for an evaluation. Running setup without that option
continues to use the configured default, `qwen3.5:9b`. Selecting or installing a
larger model is not evidence of better selections; evaluate the actual resulting
queue and record the resolved model digest. Match the project configuration to
the model being evaluated instead of silently treating different tags as the
same experiment.

Setup also accepts `--runtime-root PATH` to keep `.models/` and `.tools/` under
an explicitly chosen local directory. Match the project's configured paths to
that directory. A runtime installed for another checkout is not copied into
this repository or downloaded automatically during analysis.

The evaluated default is Qwen3.5-9B in Ollama's `Q4_K_M` form, approximately
6.594 GB on disk, with model digest
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`.
That is the recorded evaluated model, not a promise that an upstream mutable
tag will always resolve to those bytes. See the [official model card](https://huggingface.co/Qwen/Qwen3.5-9B)
and [Ollama library entry](https://ollama.com/library/qwen3.5:9b).

The default was selected after targeted local checks of ordinary speech
abstention, a distinctive exchange surrounded by routine conversation, and ICC
image context, with the existing thresholds and validation guards retained.
The earlier evaluated `gemma3:4b` is not the default after its negative-control
failure. These checks support the model choice for this implementation; they
do not establish reliable raid-wide recall or improved human acceptance.
Configured Qwen requests send `think: false`. Discovery and boundary assessment
text is still transient and is discarded rather than persisted in results.

For optional NVIDIA speech acceleration, install the environment-local wheels:

```powershell
uv sync --extra dev --extra speech --extra intelligence --extra intelligence-gpu --frozen
```

The GPU extra adds NVIDIA libraries to this project's environment. It does not
install a global CUDA toolkit, change the graphics driver, or modify persistent
PATH variables. `whisper_device: auto` attempts acceleration and falls back to
CPU when supported startup/inference failures occur; the status records that
fallback. Explicit `cuda` configuration does not silently become a CPU request.
Whisper and editorial/vision inference run sequentially, releasing speech-model
memory before the editorial pass. Actual runtime, GPU memory use, and speech
accuracy depend on the installed hardware and recording; no throughput promise
is implied by the model choice.

These commands preserve the project's compatible interpreter selection. Do not
force a different Python minor version into an existing working environment
merely to install the optional extras.

The managed portable server binds to `127.0.0.1:11435`. Analysis can start it
without a visible console and terminates only the process it created. It can
reuse an already healthy local endpoint without terminating that other process.
There is no Windows service, startup entry, global Ollama installation, or
persistent environment change. Normal analysis never downloads models. Explicit
setup contacts upstream distribution services to obtain assets; it does not
send raid audio, frames, or dialogue to those services.

Read the separate [third-party notices](../THIRD_PARTY_NOTICES.md) before using
the optional assets. Project MIT licensing does not relicense model weights or
NVIDIA binaries.

### Optional synthetic model checks

After explicitly installing the models, the following checks use authored
fictional dialogue rather than private raid footage:

```powershell
powershell -NoProfile -File scripts\generate-highlight-speech-smoke.ps1
uv run --no-sync python scripts\smoke-highlight-intelligence.py
uv run --no-sync python scripts\smoke-highlight-editorial-cases.py
```

The speech fixture requires the installed Windows voice
`Microsoft David Desktop - English (United States)` at the fixed rate used by
the generator. It writes `fixture-timing.json`; the model checks verify that
manifest and media identity before applying their timing assertions. Missing
voice or model assets are a prerequisite failure, not a passing result.

The generator accepts `-OutputDirectory PATH`. Both model checks accept
`--fixture-dir PATH`, `--runtime-root PATH`, and `--output-dir PATH`, so their
inputs and outputs do not depend on a dated local report directory. Their
defaults use generated fixtures under `samples/generated/` and summaries under
ignored `reports/` directories. These checks run real local inference and can
take time; they are separate from the default unit suite and do not establish
full-raid editorial accuracy.

## Use the recorded portrait composition

New weekly configurations use the Aitum companion for highlight video. The
landscape master remains the source for analysis, combat-log alignment, archive
editing, and all candidate timestamps. Review and export combine the original
portrait composition with the already mapped landscape game, Discord, and
microphone stems. Native video does not receive the landscape blur treatment
or an added title overlay.

```yaml
input:
  recording: 'D:\RaidRecordings\2026-09-11 22-00-00.mp4'
  vertical_recording: 'D:\RaidRecordings\Vertical\2026-09-11 22-00-02.mp4'
highlights:
  video_source: native_vertical
  vertical_offset_hint_seconds: null
  vertical_sync_audio_role: game
```

The weekly setup searches the configured `Vertical` subdirectory for a unique
filename timestamp within ten seconds of the landscape file. This identifies a
possible companion; filenames and similar durations do not establish sync. A
missing or ambiguous match leaves the new binding empty so config creation and
the landscape archive can proceed. Previous weeks' paths and offset hints are
never inherited. Existing dated configs are preserved, and configs without
`video_source` keep the legacy `landscape` behavior.

Before native media is created, local shared-audio matching checks separated
samples using `vertical_sync_audio_role`. Ambiguous matches, silence, and
inconsistent offsets or drift cannot establish a valid mapping. A configured
`vertical_offset_hint_seconds` is only a search center, never permission to use
an unverified offset. For the measured offset, the mapping is
`portrait_time = landscape_time - offset_seconds`: a portrait file that starts
two seconds later uses portrait second 58 for landscape second 60. The entire
proposed window must fit in both sources; it is not shortened to hide a gap.

Native mode fails clearly when the companion, synchronization, or coverage is
unusable. It does not silently fall back to landscape. Review media and export
approval are bound to the source pair and measured mapping; changing the pair
or source mode requires reviewing the resulting media again. The normal WebM
full-context players and explicit export selection remain in force.

For the next intact dual recording, inspect and listen to representative native
review clips near the beginning, middle, and end. Confirm action/reaction sync,
the complete setup/payoff, the recorded crop, and the retained landscape audio.
Check an approved local export against its reviewed source. No surviving
September 4 pair is available for this check after approved cleanup; synthetic
alignment tests do not replace that real-session validation.

## Run and interpret the review

New `friday` configurations enable local intelligence, keep exact-command
recognition, use a discovery pool of up to 200 heuristic candidates, and request
at most eight ordinary final suggestions. Exact-command reservations remain
separate. Existing dated configurations and their approved selections are not
migrated automatically.

For an existing unreviewed project, configure `highlights.intelligence` using
the paths above, then run:

```powershell
uv run --no-sync raid-editor analyse-highlights config\my-raid.local.yaml --open
```

The important persisted artifacts are under `output/PROJECT/highlights/`:

| Artifact | Meaning |
| --- | --- |
| `discovery-pool.json` | Broad gameplay candidate pool before final queue limits |
| `repaired-baseline.json` | Corrected heuristic shortlist with source fingerprint |
| `intelligence-status.json` | Coverage, safe diagnostics, model identities, fixed-class proposals and visual verdicts |
| `candidates.json` | Current unapproved recommendations or explicitly loaded manual selection |
| `portrait-source-status.json` | Native source verification or a media blocker; legacy landscape mode is labelled |
| `review/index.html` | Full-context review players, evidence, ratings and separate export checkboxes |

Read both the editorial-intelligence and spoken-command coverage banners:

- **Complete:** semantic discovery completed. The queue contains accepted
  semantic proposals and exact-command reservations. If semantic discovery
  abstains, routine heuristic clips do not refill the queue.
- **Partial, unavailable, failed, or truncated:** the queue can combine available
  semantic suggestions with the repaired heuristic fallback. This is degraded
  coverage, not proof that unexamined footage contained nothing interesting.
- **Disabled:** the corrected heuristic/command workflow remains available.

Ordinary defaults are fail-soft. `highlights.intelligence.required: true`
requires complete semantic coverage; inspect failures before proceeding.
Review confidence is a model self-assessment and is not calibrated against
Neil's future decisions. The recommendation count is a ceiling, not a quota.

Choose **Worth keeping**, **Maybe**, or **Reject**, and record a rejection reason
when useful. Unreviewed and unchecked do not mean rejected. A keep rating alone
does not check **Approve for vertical export**. Rejecting a clip clears that
approval. Watch the complete setup and payoff and adjust the window if needed.
The peak is editable and must stay inside that window; otherwise the page
explains the validation error and pauses the download. Machine setup/payoff
markers excluded by a human trim are cleared from the exported candidate.

Import the downloaded decisions as local feedback:

```powershell
uv run --no-sync raid-editor import-highlight-feedback path\highlight-overrides.json
```

Feedback stores at most 500 explicit source/candidate decisions and a bounded
rubric. New candidates use a stable `review_identity`, so rescanning and
renumbering display IDs does not overwrite feedback for a different moment.
Human trimming or rerating preserves that identity and updates its existing
decision. Legacy candidates without it retain their original ID identity.
Reimporting the same decision does not increase its weight. It retains
source references, timing, rating/reason, category/origin and a short reviewed
title, but no notes or dialogue. Semantic prompts consume aggregate and
structural examples, not free-form titles or notes. This is an advisory rubric,
not a trained personal preference model. Structural examples alternate the
newest keep/reject/maybe decisions so one recently declined week does not crowd
out all positive examples. Aggregate counts remain exact; sample balance and
any reason weights are advisory, not invented decisions or learned preferences.
A prior explicit all-declined decision
can count as rejection; generic unchecked proposals cannot.

To render, apply the reviewed override through `highlights.manual_selection`
and use the existing separate `render-highlights --approved` gate. Feedback
imports do not set that configuration, approve a render, or publish anything.
The growth workflow still permits zero to two selected Shorts without a quota.

## Compare approaches before claiming better selections

With an intact source and completed highlight analysis:

```powershell
uv run --no-sync raid-editor compare-highlights config\my-raid.local.yaml --open
```

The command checks that the saved corrected baseline and current recommendations
match the recording fingerprint, renders local comparison clips in separate
variant directories, and creates `highlights/comparison/index.html`. Approach
names, scores, and rationales are hidden in the visible review. Equal source
windows appear once and retain both approach memberships in the manifest.
Order is stable for the same seed. Membership remains in the page source; this
is editorial blinding rather than access control.

Comparison downloads always contain `include: false`, including positively
rated clips, and can be imported as feedback. Elapsed time means page-open
time and includes idle time. Compare accepted distinctive moments, irrelevant
suggestions, boundary corrections and review burden. Also review independently
identified good moments and random footage outside both candidate lists:
candidate-only comparisons cannot establish recall.

For the next intact raid, record the evaluation before tuning to its results:

1. Keep the original recording and isolated voice stems until comparison is
   complete. Check semantic, boundary-review and visual coverage before treating
   the run as complete; record partial/fallback runs separately.
2. Review the blinded corrected-baseline/current queue and record explicit
   keep/maybe/reject decisions. Check that the first necessary setup and the
   final connected response both fit. Flag routine chatter attached to either
   end, an exchange cut mid-response, and repeated versions of the same moment.
3. Compare distinctive accepted moments, irrelevant suggestions, required manual
   trim changes and review time. Supplement both queues with independently
   remembered good moments and random outside-pool windows to investigate misses.
4. Keep the chosen settings fixed for a later raid before claiming improvement.
   A successful compact-input or boundary fixture establishes only that tested
   case; it does not establish that the default 9B model handles busy raid
   conversation consistently or matches Neil's preferences.

Subjective improvement has not been established merely by implementing this
pipeline or passing its tests. September 4 raw recordings and review clips were
removed after approved publication cleanup. Its saved decisions and reports
support diagnosis, but cannot support a fresh full-media comparison. Evaluate
the next intact raid, then check a later raid before treating a tuning result as
reliable. Keep approved source media until any planned comparison is complete.
