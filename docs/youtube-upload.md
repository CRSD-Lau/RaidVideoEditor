# YouTube upload workflow

YouTube delivery is a separate, approval-gated step after the local final master
passes validation. A dry run never authenticates or transmits the video. An
approved API upload defaults to **Private** and never changes visibility after
upload. A Public post from an unverified API project must use the generated
package in YouTube Studio because YouTube forces API uploads from those projects
to Private.

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

- `metadata.json`: exact title, description, tags, hashtags, category, game,
  audience flags, and requested visibility;
- `description.md`: copyable description with YouTube chapters;
- `chapters.txt`: chapter offsets including presentation intro/outro time;
- `thumbnail-source.jpg`: 1280x720 JPEG constrained to YouTube's 2 MB limit;
- `studio-details.md`: the exact Studio-only category, game rating, language,
  licence, embedding, and end-screen choices;
- `upload-checklist.md`: human review and processing checks;
- `video-source.txt`: absolute validated master selected for upload.

Change metadata only in the project YAML, rerun the dry run, and review the
regenerated package. Do not hand-edit `metadata.json`; it is reproducible output.

## Professional post settings

Keep the generated copy direct and human. The title leads with the raid name,
the description explains what was kept in the edit, and manual boss chapters
make the full clear easy to navigate. The package allows no more than three
focused hashtags and rejects em dashes when `forbid_em_dash` is enabled.

For a World of Warcraft raid, use:

```yaml
youtube:
  privacy_status: "public"
  category_id: "20"
  category_name: "Gaming"
  game_title: "World of Warcraft"
  game_rating: "Unrated"
  hashtags: ["#WorldOfWarcraft", "#WotLK", "#IcecrownCitadel"]
  default_language: "en"
  made_for_kids: false
  age_restricted: false
  contains_synthetic_media: false
  license: "youtube"
  allow_embedding: true
  notify_subscribers: true
  api_project_verified_for_public: false
  forbid_em_dash: true
```

In Studio, add **Subscribe** and **Best for viewer** over the final five-second
outro. Wait for Studio's checks before publishing, then verify the public watch
page. The 1440p version may continue processing after the video becomes public.

## Approved API upload

For the safe default (`privacy_status: private`):

```powershell
uv run raid-editor upload-youtube config\my-raid.local.yaml --approved
```

Before authentication, the command confirms the final validation report passed.
It then computes the complete master SHA-256, uploads in chunks with bounded
retry/backoff, sets the custom thumbnail when the channel permits it, and writes
`youtube\upload-manifest.json` plus `reports\youtube-upload.md` with the returned
video ID and URL.

For deliberate immediate public visibility through a Google API project that
has completed YouTube's compliance audit, all three settings must opt in:

```yaml
youtube:
  privacy_status: "public"
  api_project_verified_for_public: true
```

```powershell
uv run raid-editor upload-youtube config\my-raid.local.yaml --approved --public-approved
```

For an unverified API project, leave
`api_project_verified_for_public: false`. The command blocks the API upload and
the package checklist says **Required publishing route: YouTube Studio**. Upload
the exact file in `video-source.txt`, apply `thumbnail-source.jpg`, copy the
generated title and description, and set the fields in `studio-details.md`.

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
