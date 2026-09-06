---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Documentation

Use this index to find the shortest path for the task at hand.

## Operate the workflow

| Goal | Guide |
| --- | --- |
| Prepare OBS for Friday | [OBS recording setup](obs-recording-setup.md) |
| Enable and verify evidence | [Combat-log and Skada setup](combat-log-setup.md) |
| Run the editor safely | [Root README](../README.md) |
| Enable spoken `clip it` proposals | [Spoken highlight triggers](spoken-highlight-triggers.md) |
| Improve and compare highlight selections | [Local highlight intelligence](highlight-intelligence.md) |
| Use the recorded Aitum composition | [Native portrait review and export](highlight-intelligence.md#use-the-recorded-portrait-composition) |
| Add OBS countdown and ending scenes | [OBS browser assets](../assets/obs/README.md) |
| Publish to YouTube | [YouTube workflow](youtube-upload.md) |
| Package and publish social clips | [Cross-platform social publishing](social-publishing.md) |
| Run the weekly growth layer | [Omnichannel growth workflow](growth-workflow.md) |
| Import into Resolve | [Resolve setup](resolve-setup.md) |
| Diagnose a failure | [Troubleshooting](troubleshooting.md) |

## Understand the system

| Topic | Guide |
| --- | --- |
| Components and data flow | [Architecture](architecture.md) |
| Security and privacy boundaries | [Security and privacy](security-and-privacy.md) |
| Design decisions | [Decision log](decision-log.md) |
| Original implementation scope | [Implementation plan](implementation-plan.md) |
| Historical July 26 workstation evidence | [Environment report](environment-report.md) |
| Historical synthetic performance results | [Benchmark report](benchmark-report.md) |
| Music approval and attribution | [Music licensing](music-licensing.md) |
| Guarded Resolve UI fallback | [Resolve computer-use runbook](resolve-computer-use-runbook.md) |

## Repository policies

- [Contributing](../CONTRIBUTING.md)
- [Security policy](../SECURITY.md)
- [Changelog](../CHANGELOG.md)
- [Third-party notices](../THIRD_PARTY_NOTICES.md)

Generated review media, reports, and local project files are intentionally not
version controlled.

Command examples assume you have completed the README's dependency setup.
`uv run --no-sync` uses that prepared environment and preserves its explicitly
installed optional extras; after changing dependencies, run the appropriate
`uv sync --frozen` command with every extra you intend to retain.

Use the root README and operational guides for current setup. Historical reports
and the original implementation plan describe their dated scope; they are not
live machine status, current dependency requirements, or a performance promise.
