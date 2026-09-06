---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# OBS recording setup

The editor removes a microphone by excluding an entire audio stream. It does not
separate voices from a mixed track. Its local editorial speech analysis and
optional exact `clip it` recognizer use isolated Discord and microphone stems.
A microphone-free game or Discord stream must already exist in the recording.

## Example capture profile

The following Pizza Warriors profile was audited on August 30, 2026. It is a
dated configuration example, not a probe of the reader's current OBS session.
The Friday shortcut expects this landscape geometry and labelled stems;
paths, scene names, sources, and bindings must match the actual installation.
Re-run preflight and make a fresh dual-output smoke recording before raid night.

| Setting | Value at the audit |
| --- | --- |
| Active profile | `WoW_Raid_1440p60` |
| Scene collection | `WoW_Raid_Recording.json` |
| Recording container | Hybrid MP4 |
| Video output | 2560x1440 at 60 fps |
| Recording path | `D:\RaidRecordings` |
| Recording tracks | 1 Full Mix, 2 WoW Game, 3 Discord, 4 Microphone |
| WoW source routing | Tracks 1 and 2 |
| Discord source routing | Tracks 1 and 3 |
| Mic/Aux routing | Tracks 1 and 4 |
| Aitum portrait canvas | 1080x1920 at 60 fps |
| Aitum portrait recording | `D:\RaidRecordings\Vertical`, Hybrid MP4, NVENC H.264 CQP 18 |
| Start all recordings | `Ctrl+Shift+F9` |
| Stop both recordings | `Ctrl+Shift+F10` |

This gives the editor a microphone-free game stem for the full movie and a
separate Discord stem for reviewed social highlights. Track 1 is still a
reference mix and must not be used when microphone exclusion is required.

That historical check found `Full Cam` active instead of `WoW Raid` and no fresh
combat event. Those observations are not current blockers on another session:
verify its actual program scene and use `/combatlog` before recording.

## Dual landscape and portrait capture

The landscape recording remains the authoritative editing master. It preserves
the complete 2560x1440 frame and isolated audio stems needed for the full raid,
manual reframing, and recovery from a poor portrait composition. The Aitum
`Vertical Raid Recording` output captures the existing `Vertical` canvas as a
second 1080x1920 Hybrid MP4. It is a ready-made social source, not a replacement
for the landscape master.

In the example profile, `Ctrl+Shift+F9` invokes Aitum **Start All Recordings**. That starts
the built-in landscape recording and `Vertical Raid Recording` together. The
portrait output uses its own NVENC H.264 CQP 18 encoder and writes tracks 1
through 4 so the same Full Mix, game, Discord, and microphone choices remain
available.

When streaming at the same time, do not use Aitum **Stop All Outputs**: that
control can also stop live streams and virtual cameras. At the end of the raid,
press `Ctrl+Shift+F10`. The same binding stops the built-in recording and the
`Vertical Raid Recording` output without targeting Twitch, TikTok, or either
virtual camera. Confirm that neither recording timer is still moving.

The weekly editor continues to choose the landscape master directly under
`D:\RaidRecordings`. New weekly configs separately bind a unique timestamp
match from `Vertical` for native highlight video, resetting any inherited
companion path and timing hint. Missing or ambiguous pairing leaves that
binding empty rather than blocking creation of the landscape project.

Native highlight review and export preserve the Aitum composition without blur
or added title overlays. They retain the verified landscape game, Discord, and
microphone stems, rather than replacing the audio mapping with the portrait
file's track numbers. Candidate times stay on the landscape clock. Local
shared-audio matching across separated samples must establish the offset;
filename proximity and the preflight duration check are not synchronization
proof. With a measured positive offset for a later portrait start,
`portrait_time = landscape_time - offset_seconds`.

`highlights.vertical_offset_hint_seconds` only centers the search. It cannot
bypass ambiguity, silence, inconsistent offsets, drift, or incomplete candidate
coverage. Requested native media fails clearly if these checks cannot pass,
without silently using landscape video. Review and export approval bind the
exact pair and mapping. Older dated configs retain their saved behavior; the
default for an omitted `highlights.video_source` remains `landscape`.

## Required routing

A practical four-track layout is:

| OBS source | Track 1 reference mix | Track 2 game | Track 3 Discord | Track 4 microphone |
| --- | ---: | ---: | ---: | ---: |
| WoW/game audio | Yes | Yes | No | No |
| Discord/raid comms | Yes | No | Yes | No |
| Mic/Aux | Yes | No | No | Yes |

Tracks 5 and 6 can remain unused or hold other deliberately isolated sources.
Track 1 is convenient for ordinary playback, but it includes the microphone and
must not be retained by the editor when microphone removal is required.

If game and Discord are both captured through one `Desktop Audio` source, use:

- Track 1: desktop plus microphone reference mix.
- Track 2: desktop only.
- Track 4: microphone only.
- No separate Discord track.

The project can retain Track 2 as `game_track` and leave `discord_track: null`.
It cannot independently rebalance game and Discord in that layout, but it can
exclude the microphone.

## Configure OBS 32.2.2

1. Open **Settings > Output**.
2. Set **Output Mode** to **Advanced**.
3. In **Recording**, enable the recording tracks you intend to use. Enabling a
   track only creates it; it does not decide which sources it contains.
4. Close Settings and open **Edit > Advanced Audio Properties**, or use the gear
   in the Audio Mixer.
5. Clear the all-tracks routing for each source.
6. Apply the matrix above. In particular, ensure `Mic/Aux` is unchecked on the
   game and Discord tracks.
7. In **Settings > Audio**, rename sources or track labels where useful, but do
   not rely on names alone. The editor verifies stream indexes from the file.
8. Record a short test with game audio, Discord speech, and several seconds of
   microphone speech at different times.

OBS's official [multiple audio track
guide](https://obsproject.com/kb/multiple-audio-track-recording-guide) describes
the two independent controls: recording-track enablement and the Advanced Audio
Properties routing matrix.

### Optional per-application capture

On supported Windows versions, add an **Application Audio Capture** source for
WoW and another for Discord, or enable audio capture on the applicable
Game/Window Capture source. Route those sources to separate tracks.

If you use per-application sources, avoid also capturing the same applications
through global `Desktop Audio`, or the result can contain doubled/echoed audio.
OBS's [Application Audio Capture
guide](https://obsproject.com/kb/application-audio-capture-guide) recommends
disabling global Desktop Audio when application sources replace it.

## MOV and HEVC

MOV/HEVC recordings can be inspected when the installed FFmpeg/FFprobe build
supports their codecs. This is separate from the Hybrid MP4 example above and
does not establish that a particular Resolve edition can decode or import the
generated sidecar. Probe the actual file and test the intended editor handoff.

Changing the OBS recording format is not required by the CLI. If recording
resilience matters, OBS recommends MKV for crash safety and remuxing later. Make
one short test after any format or encoder change; stream indexes and editor
compatibility can change.

## Verify the test recording

From the cloned repository root:

```powershell
uv run --no-sync raid-editor inspect 'C:\Users\YourName\Videos\OBS microphone routing test.mov' --open-review
```

The command prints absolute FFprobe stream indexes and generates three short WAV
samples for every audio stream beneath
`output\adhoc-obs-microphone-routing-test\review\audio-samples\`.

Listen to all three samples for each stream. Confirm:

- At least one retained stream contains game audio and no microphone.
- A separate stream contains the microphone.
- The proposed Discord stream contains Discord and no microphone.
- The full/reference mix is not mistaken for a safe retained stream.
- Empty or duplicate tracks are not selected.

The downloaded `audio-map.json` is a reference; there is no import command for
it. Copy its stream numbers into the project's `audio` section:

```yaml
audio:
  microphone_track: 4
  game_track: 2
  discord_track: 3
  mixed_track: 1
  keep_game_audio: true
  keep_discord_audio: true
  remove_microphone: true
```

These example numbers are not universal. FFprobe counts all streams, including
video, so always use the numbers printed for the actual recording.

The full movie excludes the microphone. For weekly reaction-clip review, opt in
only after confirming that track is the distinct labelled mic stem:

```yaml
highlights:
  keep_game_audio: true
  keep_discord_audio: true
  keep_microphone_audio: true
```

Then run the complete Friday preflight against that exact test file:

```powershell
uv run --no-sync raid-editor preflight config\my-raid.local.yaml `
  --smoke-recording 'D:\RaidRecordings\Friday smoke test.mp4' `
  --vertical-smoke-recording 'D:\RaidRecordings\Vertical\Friday smoke test.mp4'
```

The check fails closed on the expected profile, scene collection, program
scene, 2560x1440 at 60 fps, recording path, disk reserve, Hybrid MP4/MKV,
recording-track mask and labels, source routing, required visible sources, and
fresh combat log. It probes the smoke file for matching geometry and audio
labels. It never reads `service.json`, stream keys, or OAuth credentials.

The main `preflight` command deliberately validates the authoritative landscape
file. Also inspect the portrait companion from `D:\RaidRecordings\Vertical` and
confirm 1080x1920 geometry, 60 fps, audible game/Discord/microphone stems, and
the intended live crop. Both files must cover the same spoken clap or countdown
so timing alignment can be checked before the portrait file is used directly.
The smoke check's similar-duration result is only a capture check. Native
highlight synchronization must also pass for the actual raid pair. On the next
intact recording, verify representative native review clips at separated points
and an approved local export for picture, timing, crop, and landscape audio.
The September 4 originals were removed after approved cleanup, so they cannot
serve as a new real-pair validation.

## Fail-closed behavior

Timeline creation stops when:

- a configured stream index does not exist;
- microphone removal is requested for a multi-track recording but no microphone
  stream is identified;
- the microphone stream is also configured as retained game/Discord audio; or
- there is no retained game/Discord audio stream.

`mixed_track` is retained only when `remove_microphone: false` and no game or
Discord stream is selected. It is intentionally not a workaround for a mixed
track that contains microphone speech.

## Pre-raid checklist

- Switch the program scene to `WoW Raid`.
- Run `/combatlog` and create one fresh combat event.
- Press `Ctrl+Shift+F9` and make a 10–30 second dual-output test recording after
  changing any OBS profile.
- Press `Ctrl+Shift+F10` to stop both recording rows; never use **Stop All
  Outputs** while Twitch or TikTok is live.
- Run `preflight --smoke-recording` and resolve every failed row.
- Run `inspect` on both the landscape and portrait files and listen rather than
  trusting track names.
- Confirm mic isolation on at least one game/Discord stream.
- Confirm the OBS filename timestamp and Windows clock are correct.
- Check free disk space for the source, microphone-free sidecar, review clips,
  and preview.
- Confirm 2560x1440 landscape and 1080x1920 portrait dimensions with `inspect`.
- Start combat logging and, if used, Skada before the raid.
- Keep both source recordings until their publication and approved cleanup
  gates pass; the editor never rewrites either source.
