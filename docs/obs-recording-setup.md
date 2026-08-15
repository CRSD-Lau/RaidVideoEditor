# OBS recording setup

The editor removes a microphone by excluding an entire audio stream. It does not
perform speech recognition or source separation. A microphone-free game or
Discord stream must already exist in the recording.

## Audited host configuration

The read-only preflight inspected the active OBS configuration on 2026-08-15.

| Setting | Verified value |
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

This gives the editor a microphone-free game stem for the full movie and a
separate Discord stem for reviewed social highlights. Track 1 is still a
reference mix and must not be used when microphone exclusion is required.

The same check found two operational blockers, not configuration corruption:
`Full Cam` was active instead of `WoW Raid`, and the combat log had not received
a fresh event. Switch scenes and run `/combatlog` before recording.

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

## Configure OBS 32.1.2

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

The current MOV/HEVC recording profile is a supported inspection input for the
installed FFmpeg/FFprobe. It does not prove that the apparent non-Studio Resolve
installation will decode or import the generated MOV/HEVC sidecar.

Changing the OBS recording format is not required by the CLI. If recording
resilience matters, OBS recommends MKV for crash safety and remuxing later. Make
one short test after any format or encoder change; stream indexes and editor
compatibility can change.

## Verify the test recording

From `C:\Projects\RaidVideoEditor`:

```powershell
uv run raid-editor inspect 'C:\Users\YourName\Videos\OBS microphone routing test.mov' --open-review
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

Then run the complete Friday preflight against that exact test file:

```powershell
uv run raid-editor preflight config\my-raid.local.yaml `
  --smoke-recording 'D:\RaidRecordings\Friday smoke test.mp4'
```

The check fails closed on the expected profile, scene collection, program
scene, 2560x1440 at 60 fps, recording path, disk reserve, Hybrid MP4/MKV,
recording-track mask and labels, source routing, required visible sources, and
fresh combat log. It probes the smoke file for matching geometry and audio
labels. It never reads `service.json`, stream keys, or OAuth credentials.

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
- Make a 10–30 second test recording after changing any OBS profile.
- Run `preflight --smoke-recording` and resolve every failed row.
- Run `inspect` and listen rather than trusting track names.
- Confirm mic isolation on at least one game/Discord stream.
- Confirm the OBS filename timestamp and Windows clock are correct.
- Check free disk space for the source, microphone-free sidecar, review clips,
  and preview.
- Confirm the saved recording's orientation and dimensions with `inspect`.
- Start combat logging and, if used, Skada before the raid.
- Keep the source recording after the raid; the editor never rewrites it.
