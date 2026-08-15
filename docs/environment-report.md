# Raid Video Editor workstation environment report

**Status:** Initial audited baseline  
**Audit date:** 2026-07-26  
**Host:** Windows 11 Pro workstation  
**Scope:** Local development, source inspection, combat-log processing, review rendering, and editor handoff

## 1. Executive assessment

The workstation is ready for the core Raid Video Editor workflow. It has ample CPU capacity, a capable NVIDIA GPU, working FFmpeg and FFprobe installations, a selectable CPython 3.12 runtime, and sufficient free space to process the audited source recording. Nothing in the host environment blocks media inspection, audio-stream sampling, legacy combat-log analysis, timeline generation, FCPXML generation, or a 720p review render.

Two narrower paths are not currently ready:

1. **Unattended external DaVinci Resolve automation is blocked.** The installed Resolve 20.3.2 appears to be the non-Studio edition, while its installed documentation explicitly requires Studio for external scripting. The scripting environment is also unconfigured, and the installed Python shim crashes Python 3.11 and 3.12 on this machine.
2. **The real recording does not have a separate microphone stem.** OBS routes both `Desktop Audio` and `Mic/Aux` to every track (`mixers=255`). Removing only the local microphone while retaining Discord is therefore an experimental source-separation problem and is intentionally blocked pending a user choice.

Neither limitation blocks the MVP as a whole. Resolve can remain a manual import/handoff target. The implemented legacy detector streams damage events into lower-confidence activity windows and overlays safer Skada boss metadata; the real file produced 31 review candidates that still require manual review.

## 2. Audited inventory

| Area | Audited state | Readiness and implication |
|---|---|---|
| Operating system | Windows 11 Pro 25H2, build `26200.8894` | Supported local development and production host |
| CPU | AMD Ryzen 9 7950X3D, 16 cores / 32 threads | Strong for parsing, hashing, software decode, and CPU render fallback |
| Memory | 32 GiB RAM | Adequate; avoid unbounded parallel transcodes and whole-file combat-log loading |
| Discrete GPU | NVIDIA GeForce RTX 4070, 12 GB VRAM, driver `610.62` | Preferred hardware video path |
| Integrated GPU | AMD integrated graphics | Secondary H.264/HEVC hardware encode path is available |
| Intel GPU | None | Intel Quick Sync Video is unavailable by hardware, not because of a missing package |
| System drive | `C:` with 226.7 GiB free | Sufficient for tools and project code; not the preferred large intermediate store |
| Data drive | `D:` with 835.2 GiB free | Preferred cache, temporary, render, and export location |
| Shell | PowerShell `7.6.4` | Ready |
| Version control | Git `2.51.2` | Ready |
| Python | CPython 3.12 is installed; `python` resolves to 3.11.9; `py` defaults to 3.13.13 | Ready only when the project selects 3.12 explicitly |
| Media tools | FFmpeg and FFprobe `8.1.1`, full Gyan build | Ready |
| Video editor | DaVinci Resolve `20.3.2` at `C:\Program Files\Blackmagic Design\DaVinci Resolve` | Installed for manual handoff; external automation is not ready |
| Capture software | OBS Studio `32.1.2` | Installed and actively producing multi-track recordings |
| Game/log source | Legacy WoW 3.3.5a HD environment, with DBM, Details, Skada, and WeakAuras present | Streaming damage-activity fallback and Skada metadata overlay are implemented; manual classification remains required |

The version numbers above are point-in-time audit facts. A future `raid-editor doctor` result should record the resolved executable path and version again rather than treating this document as live machine state.

## 3. FFmpeg codec and acceleration capability

The installed full Gyan FFmpeg build exposes the required inspection and processing surface. The following encoder paths were tested, not merely listed:

| Hardware path | H.264 | HEVC | AV1 | Project treatment |
|---|---:|---:|---:|---|
| NVIDIA NVENC | Verified | Verified | Verified | Primary hardware path |
| AMD AMF | Verified | Verified | Fails | H.264/HEVC fallback only; do not select AMF AV1 |
| Intel QSV | Unavailable | Unavailable | Unavailable | Omit from candidates because there is no Intel GPU |
| CPU/software | Available through the full FFmpeg build | Available through the full FFmpeg build | Build-dependent encoder choice | Deterministic fallback when hardware initialization fails |

NVENC is available for later performance work. The implemented MVP deliberately uses software `libx264` for a predictable review path and does not silently fall back among hardware encoders. `preview.hardware_encoding` is forward-looking configuration and does not change the current renderer.

The lack of QSV and failure of AMF AV1 are **not blockers**. The project does not need either capability, and NVENC already covers H.264, HEVC, and AV1.

## 4. Python runtime selection

The workstation has three relevant Python outcomes:

- The unqualified `python` command starts Python `3.11.9`.
- The unqualified Windows launcher `py` defaults to Python `3.13.13`.
- Python `3.12` is installed and available for explicit selection.

The application target is CPython 3.12, so setup and automation must never rely on either unqualified default. Create the project environment with an explicit launcher selection:

```powershell
py -3.12 -m venv .venv
```

After creation, scripts should invoke the virtual environment interpreter directly or activate it before running package commands. The resolved interpreter path and full version belong in `doctor` output and generated manifests.

This is a configuration requirement, not a missing dependency. Python 3.12 does not need to be installed again.

DaVinci Resolve's scripting shim is a separate compatibility boundary. On this host:

- importing the shim crashes Python 3.11;
- importing the shim crashes Python 3.12; and
- importing the shim succeeds in Python 3.13.

Accordingly, the main Python 3.12 process must not import `DaVinciResolveScript`. If Resolve automation is enabled later, it should run in an isolated Python 3.13 bridge process and exchange versioned files or JSON with the main application.

## 5. DaVinci Resolve and edition impact

Resolve 20.3.2 is installed at:

```text
C:\Program Files\Blackmagic Design\DaVinci Resolve
```

The locally installed scripting documentation is at:

```text
C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\README.txt
```

That documentation explicitly identifies DaVinci Resolve Studio as required for external scripting. The audited installation appears to be the non-Studio edition. Resolve's external scripting environment variables are unset, and Resolve was stopped during the audit, so no live API connection test was attempted.

### Practical edition boundary

| Workflow | Current impact |
|---|---|
| Generate a neutral timeline or Resolve handoff bundle | Not blocked |
| Generate FCPXML for manual import into Resolve | Not blocked by the scripting limitation; validate with a real import |
| Open Resolve and complete editorial work manually | Not blocked |
| Launch or drive Resolve unattended from `raid-editor` | Blocked by the apparent Free edition and unconfigured external API |
| Import the Resolve shim into the main Python 3.12 process | Unsupported on this host because it crashes |
| Use a Python 3.13 sidecar after obtaining Studio | Technically plausible, but still requires environment setup and a live integration test |

The baseline product should therefore advertise Resolve as a **manual handoff target**, not as a guaranteed automated target. It must not alter global scripting environment variables, start Resolve, or load the shim while performing normal inspection.

Before investing in external automation:

1. Confirm the edition in Resolve's own About/licensing UI.
2. Decide whether a Studio license is justified by unattended import requirements.
3. Configure the external API from the installed README in a process-local launcher.
4. Keep the bridge on Python 3.13.
5. Prove a minimal connect/create-project/import/disconnect cycle while Resolve is running.

Until all five steps pass, external Resolve automation remains optional and unavailable. Manual FCPXML import is the reliable MVP route.

## 6. OBS and audited source-media configuration

OBS Studio 32.1.2 is configured for:

- advanced output mode;
- MOV recording;
- HEVC video; and
- six audio tracks.

The latest audited recording is:

```text
C:\Users\YourName\Videos\2026-07-24 23-19-12.mov
```

Its audited characteristics are:

| Property | Value |
|---|---|
| File size | 11.03 GiB |
| Duration | 3:36:52 |
| Frame dimensions | 900 x 1600 |
| Orientation | Portrait by stored dimensions |
| Container / video | MOV / HEVC |
| Audio configuration | Six OBS tracks configured; actual streams must be taken from FFprobe |

The file is a useful realistic acceptance fixture. It also creates several mandatory behaviors:

- FFprobe output, not OBS settings or stream position assumptions, is authoritative for the streams actually present.
- The application must expose all detected audio streams, create short listening samples, and require an explicit role map before timeline generation.
- Microphone exclusion is a durable user decision. It must not be inferred from a track number or waveform.
- The 900 x 1600 geometry is source truth until probe metadata and a frame sample establish otherwise. The renderer must not silently rotate, crop, stretch, or reinterpret it as landscape.
- The original MOV is immutable. Inspection, proxies, remuxes, and extracts go to project output.

For future captures, crash-resistant recording to MKV followed by OBS remux can reduce the risk of losing an interrupted long recording. That is an optional user-side capture change, not a prerequisite for processing this completed MOV.

## 7. WoW client, addons, and combat-log condition

The audited game environment is legacy WoW 3.3.5a HD with DBM, Details, Skada, and WeakAuras installed. An accumulated combat log of approximately 300 MB is available.

Addon presence is useful context but is not itself timeline evidence. The project may only automate against timestamps and events actually present in the selected source files. It must not assume that a retail WoW encounter-event contract, DBM state, Details data, Skada data, or WeakAuras state was persisted in a form that can be correlated with the recording.

Before pull detection is considered ready, perform a read-only streaming inventory that records:

- the log's earliest and latest parseable timestamps;
- distinct event names and counts;
- parse-error counts and representative rejected lines;
- candidate encounter, boss, zone, death, and combat-state markers;
- apparent session boundaries or timestamp discontinuities; and
- whether the log time range overlaps the recording after an explicit synchronization offset is applied.

The 300 MB size is not a blocker. It argues for a streaming parser, bounded samples, and an index or cache keyed by the source hash. The accumulated nature of the file is the material issue: a recording must be aligned to one relevant time window without modifying or truncating the original log.

The available log has no `ENCOUNTER_START`, `ENCOUNTER_END`, or `PLAYER_REGEN_*` records. The implemented fallback clusters legacy hostile-damage activity, labels it `unknown`, and requires manual classification. A safe, non-executing Skada parser overlays boss names, timestamps, and outcomes where present. On the audited recording this produced 29 activity windows, one supported Lich King kill, and one short duplicate-like Skada segment excluded for review.

For future raids, starting a fresh combat log per session or archiving each session separately will materially simplify synchronization and provenance.

## 8. Automation-readiness matrix

| Capability | Status | Automated portion | Required human gate or fallback |
|---|---|---|---|
| Host/tool diagnosis | Ready | Resolve executables, versions, free space, encoder tests, and Python selection | None |
| Media inspection | Ready | FFprobe JSON, duration, dimensions, stream metadata, hashes | Review warnings for unusual metadata |
| Audio sampling | Ready | Extract bounded samples for every audio stream | Assign program, microphone, music, commentary, or exclude roles |
| Review rendering | Ready for separately mapped audio | 720p H.264 via software `libx264`, with pull labels | Choose a mixed-audio policy for the audited source and approve portrait framing |
| Combat-log inventory | Ready | Stream parse, event counts, time-range index, rejected-line report | Confirm correct session and sync anchors |
| Automatic pull detection | Ready for review candidates | Modern encounter events first; legacy damage clustering and Skada overlay otherwise | Correct classifications and boundaries in the HTML review |
| Timeline generation | Ready after inputs | Deterministic canonical timeline from accepted decisions | Approve pulls, audio map, framing, and music |
| FCPXML export | Implemented; live import unverified | Generate FCPXML from the neutral timeline and microphone-free sidecar | Import and inspect in Resolve |
| Resolve manual handoff | Available | Generate files and instructions | User imports or runs the handoff inside Resolve |
| Resolve external scripting | Blocked | None currently | Confirm Studio, configure API, run isolated Python 3.13 bridge |
| Automated final master | Out of MVP scope | None | Finish and render in the editor |

## 9. User configuration that must be explicit

The following values should be captured in project or user configuration rather than inferred anew on every run:

| Setting | Recommended audited-host default |
|---|---|
| Application Python | Project virtual environment created by `py -3.12` |
| FFmpeg / FFprobe | Resolve exact installed executable paths, then record their version and hash |
| Working/output root | A project folder on `D:` |
| Review encoder | Software `libx264` in the deterministic MVP |
| Review encoder fallback | None; fail clearly rather than silently changing encoder |
| Resolve mode | Manual handoff |
| Resolve external bridge | Disabled unless Studio and the Python 3.13 bridge pass integration tests |
| Source orientation/framing | Explicit choice after probe and frame review |
| Audio roles | Explicit per-source stream map; no positional defaults |
| Combat-log source window | Explicit selected window plus synchronization anchors |
| Originals policy | Read-only; all derivatives written beneath the output root |

Tool paths and hardware capability results should be machine-level preferences or diagnostic cache entries. Audio roles, log windows, sync anchors, framing, and pull corrections are project decisions and must travel with the project.

## 10. True blockers, constraints, and non-blockers

### True blockers for a specific capability

1. **External Resolve automation:** the apparent non-Studio edition conflicts with the installed documentation's Studio requirement. Even after licensing, the Python shim must be isolated to Python 3.13 and tested live.
2. **Selective microphone removal from the audited raid:** all six OBS tracks contain the microphone and desktop mix; no deterministic track exclusion can retain Discord while removing only the local voice.

### Required user decisions, not environment blockers

- Which mixed-audio policy to use for the audited recording: keep the mix, attempt experimental voice suppression, mute it, or use approved music.
- How the portrait source should fit the review and editor canvas.
- Which log window corresponds to the recording and what synchronization anchors are correct.
- Whether Studio-only unattended Resolve automation is valuable enough to license and maintain.

### Confirmed non-blockers

- Python 3.12 is already present despite different unqualified command defaults.
- FFmpeg and FFprobe are installed and current enough for the planned work.
- NVENC supplies all three audited modern encode families.
- AMF AV1 failure does not matter to the MVP.
- Missing Intel QSV does not matter because there is no Intel GPU and other hardware paths work.
- Resolve being stopped only prevented a live connection test; it does not affect file generation.
- The 300 MB combat-log size is manageable with streaming processing.
- The 11.03 GiB portrait MOV is processable; its geometry is a creative/configuration concern.
- Available storage is adequate, especially when large generated data is placed on `D:`.

## 11. Recommended next actions

1. Create and pin a Python 3.12 virtual environment; make `doctor` fail clearly when launched under another interpreter.
2. Add a future `doctor` command for exact FFmpeg/FFprobe paths, versions, encoder smoke tests, GPU path, free space, Python version, Resolve edition uncertainty, scripting environment state, and shim isolation.
3. Put cache, temporary media, review renders, and exports on `D:` by default while leaving source files in place.
4. Run FFprobe against the audited MOV and preserve the complete JSON before designing audio-role or orientation behavior around assumptions.
5. Generate listening samples for every detected audio stream and save an explicit accepted audio map.
6. Review and correct the generated legacy activity candidates; archive the accepted override file.
7. Verify the recording-to-log synchronization against visible gameplay while retaining the original accumulated log unchanged.
8. Choose the mixed-audio policy before rendering the real review.
9. Keep manual FCPXML/Resolve import as the MVP acceptance path and visually validate it.
10. Revisit external Resolve automation only after edition confirmation; if enabled, use a process-local scripting configuration and a Python 3.13 bridge.
11. For future capture sessions, consider MKV recording plus remux and a fresh combat log per raid.

The correct near-term posture is therefore: **use the local Python 3.12 CLI, FFprobe-first ingest, explicit user decisions, software review rendering, streaming legacy-log analysis, and manual editor handoff. Treat selective removal from the mixed real audio and Resolve external scripting as separate constrained integrations.**
