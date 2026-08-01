# Music licensing workflow

The MVP never searches for or downloads music. It will use only a local file
whose identity and permitted uses are explicitly recorded in
`music\music-library.json`.

This is an evidence gate, not legal advice and not an independent license
verification service.

## Safe acquisition

Before adding a track:

1. Obtain it from a source you trust.
2. Read the license terms for synchronization with video, YouTube use, and
   monetized YouTube use.
3. Confirm whether derivative use, looping, fades, and mixing under game audio
   are permitted.
4. Save the source page URL, license name/version, date obtained, exact required
   attribution, and a local copy or screenshot of the terms.
5. Store the audio file beneath `music\files\` or another local controlled
   folder.
6. Do not approve a track while any permission is unknown.

The application does not fetch the source page later. Preserve evidence in case
the page changes.

## Hash the exact file

From PowerShell:

```powershell
(Get-FileHash -LiteralPath 'C:\Projects\RaidVideoEditor\music\files\example.flac' -Algorithm SHA256).Hash.ToLowerInvariant()
```

Record that 64-character hash. Re-encoding or editing the file changes the hash
and requires a new review and library entry/update.

## Register the track

The library starts empty:

```json
{
  "schema_version": 1,
  "tracks": []
}
```

Add a complete record:

```json
{
  "schema_version": 1,
  "tracks": [
    {
      "id": "example-artist-example-track",
      "title": "Example Track",
      "artist": "Example Artist",
      "source": "Example music library",
      "source_page": "https://example.invalid/track-page",
      "licence": "Example License",
      "licence_version": "1.0",
      "attribution_required": true,
      "date_obtained": "2026-07-26",
      "local_file": "files/example.flac",
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "youtube_use_permitted": true,
      "monetized_youtube_permitted": true,
      "derivative_synchronization_permitted": true,
      "required_description_text": "Music: Example Track by Example Artist — licensed under Example License 1.0.",
      "tags": ["instrumental", "raid"],
      "tempo_bpm": 120,
      "energy": 0.65,
      "integrated_loudness_lufs": -16,
      "instrumental": true,
      "duration_seconds": 180
    }
  ]
}
```

`local_file` is resolved relative to the library JSON unless it is absolute.
The source page must be a valid HTTP(S) URL. Optional analysis fields may be
`null`.

The MVP rejects an approved track if:

- its ID is absent;
- the local file is missing;
- the SHA-256 differs;
- any of YouTube use, monetized YouTube use, or derivative synchronization is
  not explicitly `true`; or
- attribution is required but the text is blank.

It does not maintain a list of acceptable licenses or determine whether the
operator's interpretation of the license is correct.

## Approve explicitly per project

Keep the default empty list for no music:

```yaml
music:
  library: "../music/music-library.json"
  approved_track_ids: []
```

To use a track in the preview:

```yaml
music:
  library: "../music/music-library.json"
  approved_track_ids:
    - "example-artist-example-track"
```

Approve exactly one track in the MVP. The renderer uses only the first approved
track as a low-level continuous bed, loops it if necessary, applies a gain of
`0.16`, and fades it in/out over two seconds. Multiple IDs are validated and
reported, but only the first is actually mixed; approving more than one can make
the generated reports misleading.

Music is added only to `render-preview`. It is not embedded in
`timeline.fcpxml`, `source-microphone-free.mov`, or the Resolve bridge project.

## Review generated evidence

Running:

```powershell
uv run raid-editor render-preview config\my-raid.local.yaml
```

writes:

- `reports\music-licence-report.md`
- `reports\youtube-attribution.txt`
- `reports\music-plan.md`
- the review MP4

Check that:

- the actual audible track matches the record;
- the game and Discord mix remains intelligible;
- looping and fades comply with the license;
- the attribution text is complete;
- the source URL and date are correct; and
- the intended use, channel, territory, and monetization status are permitted.

Verify the exact generated attribution text before approving an upload. The
YouTube packaging step automatically appends non-empty
`reports\youtube-attribution.txt` content to the generated description; it does
not silently edit metadata for a video that is already uploaded.

## Removal and revocation

To stop using a track:

1. Remove its ID from `approved_track_ids`.
2. Rerun `render-preview`.
3. Confirm `music-plan.md` says no music will be applied.
4. Listen to the new preview.
5. Remove old generated preview/report files by deleting only the exact project
   output folder if necessary.

Removing an ID does not alter an already rendered preview. Deleting the source
audio file before clearing the ID causes validation to fail, which is safer than
silently substituting another track.

## Privacy and license records

Library entries expose local paths, source URLs, dates, and attribution. Output
reports repeat much of this evidence. Review those files before sharing a whole
project folder. Keep purchase receipts, account details, and private license
documents outside the repository unless they have been deliberately redacted.
