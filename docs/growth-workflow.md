---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Omnichannel growth workflow

This layer turns one reviewed raid into a coherent Pizza Warriors campaign
without weakening any publication gate. YouTube remains the canonical home for
the accurate archive. A recap is optional and exists only when the raid has a
coherent story. Each new raid contributes zero to two strong portrait moments,
not a quota of repetitive clips.

## What is implemented

`prepare-growth` writes local, strict artifacts under `output\PROJECT\growth`:

- `campaign-manifest.json` records archive, recap, Short, and boss-feature lanes;
- every source has an explicit landscape, native portrait, derived portrait, or
  owner-recovery lineage, stream inventory, and disclosed full or sampled fingerprint;
- overall raid result, recorded footage, and edited footage remain separate;
- every title-worthy statement is allowed, rejected, or held for manual review;
- a recorded `Full Clear` requires all expected bosses in both recording and
  edit, per-boss winning-ending evidence, and a complete overall result;
- every new Short requires a reviewed related archive or recap target before it
  can become upload-ready and an observed native assignment receipt before it is
  publication-complete;
- `distribution-manifest.json` consolidates YouTube, Facebook, Instagram,
  TikTok, Twitch, and Discord states and receipts;
- `global-content-calendar.json` preserves accepted remote releases as locked
  and keeps fresh dates as unapproved local proposals;
- `packaging-experiments.jsonl` stores one interpretable title, thumbnail,
  opening, description, or release-time test at a time;
- `timeline-events.json` maps intro, boss cards, action starts, transitions,
  CTA, and outro for retention analysis;
- cleanup dependencies hold raw recordings, masters, and portrait derivatives
  until the distributions that require them are terminal;
- the local review page presents claims, source lineage, schedules, and holds in
  one place.

No function in the growth package has a platform client. A growth approval is
not permission to upload, publish, schedule, change visibility, apply related
videos, add cards or end screens, post to Discord, or delete media.

## Friday sequence

Start with the existing review-first command:

```powershell
uv run --no-sync raid-editor friday
```

Approve boss cuts and difficulty, review highlights, render the accurate
archive and zero to two portrait moments, and prepare the existing YouTube and
social packages. Then consolidate them:

```powershell
uv run --no-sync raid-editor prepare-growth config\pizza-warriors-YYYY-MM-DD.local.yaml `
  --short-id SHORT_1 --short-id SHORT_2
```

For an archive-only week:

```powershell
uv run --no-sync raid-editor prepare-growth config\pizza-warriors-YYYY-MM-DD.local.yaml --no-shorts
```

Add `--portrait-source` only for a matching, inspected native 1080x1920 OBS
recording. It never replaces the 2560x1440 archive source.

Review `growth\review\index.html`, record the recap decision, and approve only
the local package:

```powershell
uv run --no-sync raid-editor growth-recap output\PROJECT\growth\campaign-manifest.json `
  --decision skipped --reason "No coherent recap story this week" --approved
uv run --no-sync raid-editor approve-growth output\PROJECT\growth\campaign-manifest.json --approved
uv run --no-sync raid-editor growth-status output\PROJECT\growth\campaign-manifest.json
```

A `candidate` decision additionally requires repeated `--beat` options,
`--source-ranges-json` containing reviewed `recap_beat` ranges that reference a
campaign source ID, `--audio-plan`, and `--estimated-minutes`. `hold` and
`skipped` require only the reviewed reason.

Remote actions still use their existing destination-specific approval and
receipt commands.

When a custom milestone, first, loot, reward, encounter, or moment claim is
needed, approve the exact evidence separately. Coverage and scoreline claims
cannot use this manual path to bypass footage validation:

```powershell
uv run --no-sync raid-editor growth-claim output\PROJECT\growth\campaign-manifest.json `
  --content-id SHORT_ID --claim-id SHORT_ID:first-shadowmourne --kind milestone `
  --text "Pizza Warriors' first Shadowmourne" --evidence-id SHORT_ID `
  --reason "Reviewed against the approved clip and guild history" --approved
```

After the Short has a YouTube identity, record the separate native assignment.
`assignment_pending` and `assigned_verified` require the observed Short ID and
public URL; verification also requires the exact target URL and a receipt note:

```powershell
uv run --no-sync raid-editor growth-related-target output\PROJECT\growth\campaign-manifest.json `
  --content-id SHORT_ID --state assigned_verified `
  --short-remote-id VIDEO_ID --short-public-url "https://youtube.com/shorts/VIDEO_ID" `
  --target-url "https://www.youtube.com/watch?v=ARCHIVE_ID" `
  --assignment-receipt "Native related-video chip observed on public playback" `
  --reason "Assigned to this raid's exact archive" --approved
```

This command records local evidence only; it never operates YouTube Studio.

## Packaging experiments

The three thumbnail candidates test different ideas:

1. clean scoreline context;
2. boss-action curiosity;
3. Pizza Warriors guild identity.

Judge all three in `thumbnail-mobile-preview.html`. Run one Studio Test &
Compare at a time only when impressions are sufficient. Prefer watch time per
impression, with average view duration and early retention as guardrails. Do
not adopt a winner from a tiny sample or compare traffic surfaces with different
intent as though they were equivalent.

Record the observed test lifecycle append-only. This does not start or alter
Studio Test & Compare:

```powershell
uv run --no-sync raid-editor growth-experiment output\PROJECT\growth\campaign-manifest.json `
  --series-id PROJECT-thumbnail-01 --status completed --decision inconclusive `
  --reason "Insufficient comparable impressions after the planned window" --approved
```

## Three-raid pilot

For each suitable raid:

1. create the accurate archive;
2. record whether a coherent recap story exists;
3. publish a recap only after its own timeline and audio review;
4. select zero to two strong Shorts;
5. review Short to recap to archive linkage;
6. run at most one packaging experiment;
7. capture production minutes and audience results.

After three suitable raids and at least one useful 28-day window, keep recaps
only if their audience value justifies the effort. Retire losing packaging,
avoid twelve repetitive boss uploads, and use an archive-only week whenever the
raid has no discovery-worthy story.

## Analytics

Use `growth-analytics` with an observed timezone-aware `published_at`, a 24-hour,
48-hour, 7-day, or 28-day checkpoint, a platform surface, and the raw metrics export.
The snapshot computes actual age and marks captures outside tolerance. Compare
within platform, surface, age, and content lane. Never read CTR without watch
duration.

Use `growth-time` after meaningful stages so production economics are visible:

```powershell
uv run --no-sync raid-editor growth-time output\PROJECT\growth\campaign-manifest.json `
  --stage rendering --minutes 4 --unattended-seconds 1800
```

## Cleanup

The public CLI reports cleanup eligibility and consolidated dependency holds;
it does not delete sources. An external operator cleanup process must separately
verify public playback, the required final quality, the exact allowlisted paths,
and explicit deletion approval. A non-terminal required destination holds the
corresponding raw, archive, or portrait asset. Preserve compact evidence,
configs, approvals, manifests, hashes, thumbnails, reports, and public URLs.
