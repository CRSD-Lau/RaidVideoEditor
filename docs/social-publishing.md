---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Cross-platform social publishing

This guide turns approved Pizza Warriors portrait highlights into a controlled
release across Facebook Reels, Instagram Reels, TikTok, and Twitch Video
Producer Uploads. The editor prepares, checks, schedules, and records evidence.
The official signed-in platform interfaces perform the actual account and post
actions.

## What the workflow protects

- Neil's personal Facebook profile and personal Instagram account stay intact.
- Pizza Warriors uses its own Facebook Page and public Instagram Creator account.
- Passwords, browser cookies, two-factor codes, recovery codes, stream keys, and
  access tokens never enter project files or terminal output.
- Every clip is watched with sound before release. A recovered YouTube owner
  download is clearly marked as a quality exception and is never called a master.
- Public publication is a separate deliberate action after local review.
- Media is not considered safely disposable until every intended destination is
  verified public or explicitly waived.

## One-time account setup

Use the same brand identity on every destination:

| Field | Value |
| --- | --- |
| Display name | Pizza Warriors |
| Instagram username | `pizzawarriorswow` |
| Existing TikTok username | `lausudo` |
| Category | Gaming video creator or Video creator |
| Bio | World of Warcraft raid nights, guild milestones, and the moments between pulls. Full raids on YouTube. Live on Twitch. |
| Primary link | `https://www.youtube.com/channel/UCWXOkRuqBMTlKgxyCOZ1BKg` |

Keep the Instagram account public and set it to Creator. Connect only the Pizza
Warriors Instagram account to the Pizza Warriors Facebook Page. Do not enable
contact-detail display, contact syncing, public phone numbers, addresses, or
business hours. On Facebook, add a Watch Video or Learn More button that points
to YouTube, review Page access, enable spam/comment moderation and notifications,
and leave public Reel remix disabled. Do not mass invite Neil's personal friends.

On TikTok, confirm the visible Pizza Warriors destination account before each
upload. On Twitch, use Video Producer Uploads and place the videos in a
`Pizza Warriors Raid Highlights` collection. External files are not Twitch
Clips.

## Friday night operating sequence

1. Before the raid, press `Ctrl+Shift+F9` to start both the 2560x1440 landscape
   master and the Aitum 1080x1920 portrait recording. Record a fresh 10-second
   smoke test, press `Ctrl+Shift+F10` to stop both recordings, inspect both files, and
   run `preflight` against the landscape file.
2. After the raid, run `uv run --no-sync raid-editor friday`. It selects the latest complete
   1440p60 recording from the root recording folder, verifies the four isolated
   audio tracks, and opens both review lanes. Keep the matching portrait file as
   an optional social source until its timing, crop, and stems pass review.
3. Review full winning boss pulls. Apply the downloaded pull overrides and rerun
   analysis until every retained cut is correct.
4. Review the full highlight candidates with game, Discord, and microphone audio.
   Spoken `clip it` matches are proposals, not automatic approvals. Apply the
   downloaded highlight overrides.
5. Render and watch approved portrait clips. Confirm crop, title safe area,
   watermark, audio sync, and ending. Reject private chat, accidental desktop,
   dead air, broken audio, or a joke that needs missing context.
6. Render and validate the full raid, prepare the YouTube package, and publish it
   through its separate approval flow.
7. Prepare the social package:

   ```powershell
   uv run --no-sync raid-editor prepare-social config\PROJECT.local.yaml `
     --facebook-handle 'Pizza Warriors' `
     --instagram-handle pizzawarriorswow `
     --tiktok-handle lausudo `
     --twitch-handle lausudo `
     --start-at '2026-09-04T19:00:00-03:00' `
     --cadence-days 2
   ```

   Then run `prepare-growth` with zero to two approved highlight IDs. The growth
   command imports remotely accepted schedules as locked, aligns any unreviewed
   local social package to the first open global slots, and writes the
   consolidated cross-platform manifest. It does not contact a platform.

8. Open `output\PROJECT\social\review\index.html`. Verify every target account,
   clip, caption, hashtag set, cover frame, and release time. Then record review:

   ```powershell
   uv run --no-sync raid-editor approve-social `
     output\PROJECT\social\distribution-manifest.json --approved
   ```

9. Use the official platform interfaces. Publish one strongest clip as a pilot,
   verify public playback on every destination, then schedule the remaining
   approved clips at the manifest times. Use the same release day across all
   four destinations so fixed-age comparisons remain meaningful.
10. Record each observed state and public URL. A publication receipt is evidence,
    not the publishing mechanism:

    ```powershell
    uv run --no-sync raid-editor confirm-social-publication `
      output\PROJECT\social\distribution-manifest.json `
      --platform instagram `
      --content-id CONTENT_ID `
      --status verified_public `
      --remote-id REMOTE_ID `
      --url PUBLIC_URL `
      --checks-json checks.json `
      --approved
    ```

11. Run `social-status`. Do not clean social media while any destination remains
    prepared, uploaded, processing, scheduled, failed, blocked, or uncertain.
12. Capture analytics at publication verification, 24 hours, 7 days, and 28 days.
    Use 48 hours only to investigate an unusual first-day result. Prefer
    `growth-analytics` for pilot comparisons because it records observed
    publication time, actual age, platform surface, and optional edit-event
    alignment instead of trusting an age label alone.

## Platform checklist

### Facebook Reels

- Destination is the Pizza Warriors Page, not Neil's profile.
- Visibility is Public.
- Description and Page link are correct.
- Comments are enabled; remix is disabled unless deliberately tested later.
- Cover is readable on mobile and the subject is not hidden by UI controls.
- Public Reel URL opens while logged out or in a clean session.

### Instagram Reels

- Destination is the Pizza Warriors Creator account.
- Also share to the connected Pizza Warriors Facebook Page only when that does
  not create a duplicate of the separately scheduled Facebook package.
- Upload at the highest available quality.
- Comments are enabled; remix and download are disabled by default.
- Profile grid crop, Reel cover, captions, and link-in-bio wording are correct.

### TikTok

- Destination account is visibly Pizza Warriors.
- Run the platform copyright/sound check before publishing.
- Visibility is Everyone. Comments are enabled.
- Duet, Stitch, and downloads are disabled by default.
- Confirm the title remains inside the portrait safe area and is not too wide.

### Twitch

- Use Creator Dashboard, Content, Video Producer, Upload.
- Set the title and World of Warcraft category.
- Publish as an Upload and add it to `Pizza Warriors Raid Highlights`.
- Confirm the direct vertical file displays acceptably before considering any
  Twitch-specific wrapper. A wrapper is a new derivative and needs fresh review.

## Packaging and growth system

Use a strongest-first release rather than dumping seven clips at once. The
default cadence is one clip every two days at 7 PM Atlantic. A pilot milestone
clip goes first, then intensity, surprise, personality, raid-comms, and reaction
moments. This spacing creates enough observations to learn without leaving the
channels idle.

Each clip should have one clear hook visible immediately, readable text inside
the safe area, no unexplained setup longer than needed, and a decisive ending.
Write natural platform-specific copy. Use three focused hashtags rather than a
large repeated block. Keep the title short enough for mobile, and let the clip
deliver the joke or milestone instead of explaining everything in the caption.

Maintain a balanced set of content pillars:

- guild milestones and progression;
- clutch or intense gameplay;
- funny comms and reactions;
- personality and recurring guild characters;
- loot and celebration;
- occasional useful raid insight.

Compare each platform only with its own previous posts at the same age. Preserve
raw definitions and mark unavailable values as unavailable. The most useful
cross-post measures are completion or average watch percentage, shares per 1,000
views, saves per 1,000 views, comments per 1,000 views, follows per 1,000 views,
profile visits, link clicks, and any mute or restriction. Views alone are not a
growth diagnosis.

Every four weeks, keep the best-performing hooks and pillars, rewrite weak title
patterns, and test only one meaningful packaging variable at a time. Do not
change the clip, hook, caption, cover, and release time together because the
result will not explain what helped.

## Recovery and provenance

Prefer the original approved 1080x1920 portrait master. If it no longer exists,
recover Neil's own upload through Google Takeout first. YouTube Studio owner
download is an acceptable fallback for a previously approved Pizza Warriors
Short, but the manifest must record `youtube_studio_owner_download`,
`owner_recovered_derivative`, the public YouTube identity, and the exact quality
exception. Never use a public third-party downloader.

## Failure handling

- If an upload times out after submission, inspect the platform library before
  retrying. Record `remote_state_uncertain` until the remote state is known.
- If Neil has already published the exact approved media manually, record
  `waived_by_neil` directly from the reviewed state with a manual-publication
  check. Do not invent an account failure and do not upload a duplicate.
- If account identity is wrong, stop before Publish and record `blocked_account`.
- If Twitch or another destination rejects the format, record
  `blocked_capability`; do not silently substitute a different product type.
- If a public post has the wrong crop, audio, audience, or account, make the
  smallest reversible correction in the platform UI and record a new receipt.
- Never delete a local social master to free space while its post state is
  uncertain.
