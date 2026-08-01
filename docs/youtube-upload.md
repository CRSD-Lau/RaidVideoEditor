# YouTube upload workflow

YouTube delivery is a separate, approval-gated step after the local final master
passes validation. A dry run never authenticates or transmits the video. An
approved upload defaults to **Private**, does not notify subscribers, and never
changes visibility after upload.

The implementation follows Google's official [Python upload
guide](https://developers.google.com/youtube/v3/guides/uploading_a_video), uses
the `youtube.upload` OAuth scope, sends the file with a [resumable upload
request](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol),
and applies the generated JPEG through
[`thumbnails.set`](https://developers.google.com/youtube/v3/docs/thumbnails/set).

## One-time Google setup

1. Create a dedicated Google Cloud project for the raid editor.
2. Enable **YouTube Data API v3**.
3. Configure Google Auth Platform for an **External** audience in testing and
   add the intended YouTube account as a test user.
4. Create an OAuth client with application type **Desktop app**.
5. Download its JSON file to
   `C:\Projects\RaidVideoEditor\secrets\youtube-client.local.json`.
6. Do not rename its JSON keys, commit it, paste it into chat, or share it.

The `secrets\` directory is git-ignored. On the first approved upload, Google
opens its own browser consent screen. After the operator grants only the
requested YouTube upload access, the refresh token is written to
`secrets\youtube-token.local.json`. No Google password is handled or stored by
the raid editor.

## Review before transmission

Generate the exact package that would be used:

```powershell
Set-Location C:\Projects\RaidVideoEditor
uv run raid-editor upload-youtube config\my-raid.local.yaml --dry-run
```

Inspect every file in `output\<project>\youtube\`:

- `metadata.json`: exact title, description, tags, category, audience flag, and
  requested visibility;
- `description.md`: copyable description with YouTube chapters;
- `chapters.txt`: chapter offsets including presentation intro/outro time;
- `thumbnail-source.jpg`: 1280x720 JPEG constrained to YouTube's 2 MB limit;
- `upload-checklist.md`: human review and processing checks;
- `video-source.txt`: absolute validated master selected for upload.

Change metadata only in the project YAML, rerun the dry run, and review the
regenerated package. Do not hand-edit `metadata.json`; it is reproducible output.

## Approved upload

For the safe default (`privacy_status: private`):

```powershell
uv run raid-editor upload-youtube config\my-raid.local.yaml --approved
```

Before authentication, the command confirms the final validation report passed.
It then computes the complete master SHA-256, uploads in chunks with bounded
retry/backoff, sets the custom thumbnail when the channel permits it, and writes
`youtube\upload-manifest.json` plus `reports\youtube-upload.md` with the returned
video ID and URL.

For deliberate immediate public visibility, both the YAML and command must opt
in:

```yaml
youtube:
  privacy_status: "public"
```

```powershell
uv run raid-editor upload-youtube config\my-raid.local.yaml --approved --public-approved
```

Prefer Private first. Wait for YouTube's HD/1440p processing, inspect the whole
video in YouTube Studio, and change visibility there only when ready.

## Duplicate and interruption safety

- A matching final-master hash and metadata hash returns the recorded YouTube
  URL without authentication or another upload.
- If the same master is already recorded but local metadata changed, the command
  refuses to upload a duplicate. Update the existing YouTube entry deliberately
  instead.
- Transient failures are retried while the command remains running.
- If the process ends after YouTube accepted the file but before the local
  manifest was written, inspect **YouTube Studio > Content** before retrying.
  This is the one interval where a local duplicate guard cannot prove whether
  the remote upload exists.
- A thumbnail permission error does not discard a successfully uploaded video;
  the report records the failure so the JPEG can be applied manually.

## Credential recovery and removal

If authorization is revoked or the token is corrupt, move only
`secrets\youtube-token.local.json` to a safe backup location and rerun the
approved command to start Google's consent flow again. Do not delete the OAuth
client JSON unless intentionally replacing the client.

To remove local access after the workflow is no longer needed, revoke the app in
the Google account's third-party access settings, then remove the exact two
local JSON files under `secrets\`. This does not delete an uploaded video. Delete
or change the video only in YouTube Studio after verifying its exact video ID.
