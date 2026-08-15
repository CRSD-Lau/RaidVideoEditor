# Combat-log and Skada setup

The MVP uses deterministic local evidence. It prefers explicit WoW combat
boundary events, can supplement them with safely parsed Skada segment metadata,
and falls back to clustering hostile damage events for legacy 3.3.5 logs that
lack modern boundary events.

Every result still needs a human review.

## Current legacy log

The audited workstation has this accumulated log:

```text
D:\world of warcraft 3.3.5a hd\Logs\WoWCombatLog.txt
```

On 2026-07-26 it was 300,044,869 bytes (about 300 MB decimal). The detector
streams the file line by line and keeps only rows in the recording interval,
with a one-minute margin. It does not load the full file into memory.

Scanning still reads through the accumulated file, so it can take time. Do not
truncate, rotate, or edit the log while analysis is running. Prefer stopping
combat logging or closing WoW before analysis.

### Audited current-recording result

The read-only analysis of the 2026-07-24 recording produced 31 provisional
candidates:

- 29 damage-activity clusters at confidence `0.72`;
- one Skada-backed `The Lich King` kill at confidence `0.96`; and
- one short possible duplicate `The Lich King` segment at confidence `0.45`,
  excluded by default.

This proves the fallback can extract deterministic candidates from the available
legacy evidence. It does not approve their classifications or boundaries. The
29 activity clusters remain `unknown` and require manual review.

## Enable and preserve combat logging

Before the raid, use the legacy client's combat-log command and confirm that the
file's modified time advances when combat occurs. The commonly used command is:

```text
/combatlog
```

Client builds and addons can differ. Verify the file rather than trusting the
chat confirmation alone. Preserve the original log; the editor opens it
read-only.

## Project input

Use a single-quoted Windows path or forward slashes:

```yaml
input:
  recording: 'C:\Users\YourName\Videos\2026-07-26 20-00-00.mov'
  combat_log: 'D:\world of warcraft 3.3.5a hd\Logs\WoWCombatLog.txt'
  details_export: null
  skada_export: null
  manual_pulls: null
```

`combat_log` is required unless `manual_pulls` is set. `skada_export` supplements
a combat log; it is not currently a stand-alone substitute. `details_export` is
a reserved field and is not read by this MVP.

## Time synchronization

The safest mapping uses an explicit recording start with a UTC offset:

```yaml
detection:
  recording_started_at: "2026-07-26T20:00:00-03:00"
  combat_log_offset_seconds: 0
```

If `recording_started_at` is `null`, the detector:

1. Looks for `YYYY-MM-DD HH-MM-SS` or `YYYY-MM-DD_HH-MM-SS` in the recording
   filename.
2. Otherwise estimates the start as file modification time minus media
   duration.

The OBS profile currently uses the matching timestamped filename pattern, but
an explicit timestamp is still easier to audit. WoW log rows can omit the year;
the parser resolves them relative to the recording start and handles New Year
rollover.

`combat_log_offset_seconds` shifts mapped log events on the video timeline:

- Positive values move detected events later in the video.
- Negative values move them earlier.

Example: a visible pull starts at 125.0 seconds, but the generated candidate
starts at 121.5. Set the offset to `3.5`, rerun `analyse`, and check more than
one pull before accepting the alignment.

## Detection precedence

The implemented order is:

1. If `input.manual_pulls` is set, load that file and skip combat-log, damage,
   and Skada detection.
2. Otherwise parse explicit boundary events from the combat log.
3. If no boundary-derived pulls exist, cluster legacy hostile damage activity.
4. If `input.skada_export` is set, add its valid boss segments and replace
   overlapping generic activity windows.
5. Sort the combined result and assign stable run-local `pull-####` IDs.

### Explicit combat boundaries

The primary parser recognizes:

- `ENCOUNTER_START` / `ENCOUNTER_END` for boss attempts and kill/wipe outcome.
- `PLAYER_REGEN_DISABLED` / `PLAYER_REGEN_ENABLED` for non-boss combat windows.

Boss pulls have confidence `1.0`. Non-boss windows shorter than
`minimum_pull_seconds` are discarded. Malformed relevant rows are reported in
`analysis\combat-log-issues.json`; parsing continues when safe.

### Legacy damage-activity fallback

Some 3.3.5 logs contain neither encounter nor player-regen boundary events. When
the primary parser produces no pulls, the fallback clusters hostile events such
as swing, spell, periodic, environmental, and ranged damage, misses, kills, and
unit deaths.

Clusters are separated when their event gap exceeds
`detection.merge_gap_seconds`. Short clusters are removed using
`minimum_pull_seconds`.

Fallback candidates:

- have type and result `unknown`;
- are titled `Combat activity N`;
- carry `legacy_3.3.5_damage_activity` evidence;
- have confidence `0.72` at 100 or more hostile events, otherwise `0.58`; and
- include a note that manual classification is required.

This is activity detection, not boss recognition. A dense trash fight can look
like a boss, and downtime inside a fight can split one attempt. Review every
candidate with legacy evidence even if `0.72` is above the default `0.70`
uncertainty threshold.

## Optional Skada evidence

Set `skada_export` to a copied or stable `SkadaStorage.lua`:

```yaml
input:
  skada_export: 'D:\path\to\WTF\Account\<account>\<realm>\<character>\SavedVariables\SkadaStorage.lua'
```

The parser does **not** execute Lua. It reads only top-level scalar fields from
non-aggregate segments:

- `starttime`
- `endtime`
- `mobname`
- optional `success`
- optional `type`

Nested actor and damage tables are ignored. Valid segments are mapped from Unix
epoch seconds to recording time. `success: true` becomes a boss kill,
`success: false` becomes a boss wipe, and a missing value becomes an unresolved
boss attempt. These candidates use confidence `0.96`.

Normal kill/wipe segments use confidence `0.96`; segments without a `success`
value become unresolved attempts at `0.82`. A short successful segment less than
ten minutes after a prior kill of the same name is treated as a possible
duplicate: it is changed to `unknown`, assigned confidence `0.45`, and excluded
pending manual review.

Skada is useful evidence, not proof. Saved variables can be stale, a segment can
cover the wrong session, and the recording clock can be misaligned. Review the
thumbnail/video, title, outcome, and both boundaries, including every excluded
possible duplicate. Copy the SavedVariables file after WoW has flushed it; do
not edit an actively written file.

## Analyze and inspect evidence

```powershell
uv run raid-editor analyse config\my-raid.local.yaml
uv run raid-editor review config\my-raid.local.yaml
```

Review:

- `output\<slug>\analysis\pull-candidates.json`
- `output\<slug>\analysis\pull-candidates.csv`
- `output\<slug>\analysis\combat-log-issues.json`
- `output\<slug>\reports\uncertain-segments.md`
- `output\<slug>\review\pull-review.html`

The local HTML page can adjust inclusion, title, start, end, and notes. It
downloads `pull-overrides.json`; browser security prevents it from updating the
project directly.

## Apply manual corrections

1. Download `pull-overrides.json` from the review.
2. Move it to a durable local path, for example
   `config\my-raid-pull-overrides.json`.
3. Set that file in YAML:

   ```yaml
   input:
     manual_pulls: "my-raid-pull-overrides.json"
   ```

4. Rerun `analyse` and `review`.
5. Confirm the corrected candidates before `build-timeline`.

Paths are relative to the YAML file. When `manual_pulls` is present it is the
complete authoritative list; later combat-log or Skada changes are ignored until
the setting is cleared.

The manual file may be a JSON array, `{"pulls": [...]}`, or CSV. The review
download is the easiest valid form. Advanced manual JSON can also correct
`type`, `result`, and `encounter` using supported values:

```json
{
  "pulls": [
    {
      "id": "pull-0001",
      "start_seconds": 125.0,
      "end_seconds": 244.5,
      "type": "boss_wipe",
      "encounter": "Example Boss",
      "result": "wipe",
      "confidence": 1.0,
      "evidence": ["manual video review"],
      "include": true,
      "title": "Example Boss — Attempt 1",
      "notes": "Corrected against the visible pull timer."
    }
  ]
}
```

If `duration_seconds` is present, it must equal end minus start within 0.05
seconds. Start must be non-negative and end must be greater than start.

## Review gate

Do not build a timeline merely because candidate generation completed. Confirm:

- the recording start and offset against several visible events;
- every legacy activity cluster's classification;
- Skada segment names and outcomes;
- kill versus wipe;
- pre-roll and post-roll;
- accidental overlap or missing attempts; and
- all candidates below the configured confidence threshold.

Only then run:

```powershell
uv run raid-editor build-timeline config\my-raid.local.yaml
uv run raid-editor render-preview config\my-raid.local.yaml
```
