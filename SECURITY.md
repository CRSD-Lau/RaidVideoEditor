---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Security Policy

## Supported versions

The current `main` branch and the latest tagged release receive security fixes.
Older snapshots are unsupported.

## Report a vulnerability

Do not open a public issue containing a vulnerability, recording, credential,
OAuth token, stream key, webhook, personal path, or private raid communication.

Report security issues through a private GitHub security advisory:

<https://github.com/CRSD-Lau/RaidVideoEditor/security/advisories/new>

Include the affected version, a minimal reproduction, impact, and suggested
remediation. Replace all live secrets with placeholders. If a credential may
already have been exposed, revoke or rotate it before sending the report.

## Implemented boundaries

- Ignore rules exclude source recordings, generated output, local project
  configuration, model/runtime downloads, and the `secrets/` directory. These
  rules are not a security boundary: inspect staged changes and built packages
  before sharing, and never force-add private files.
- YouTube OAuth clients and tokens use ignored purpose-specific files.
- CI scans the complete Git history with Gitleaks.
- Uploads, playlist changes, final renders, social exports, and archive copies
  require explicit approval.
- The shipped CLI does not delete source recordings. `archive --approved` is a
  copy-and-verify operation. Social/growth commands report retention holds and
  eligibility; they do not perform deletion. Any separately approved operator
  cleanup must verify exact allowlisted paths and publication/dependency holds.
- Optional speech/vision inference stays on the local workstation. Model
  downloads require explicit setup. The managed runtime binds to IPv4 loopback;
  it does not install a global service or startup entry.
- Full transcripts, decoded inference audio, inference frames, and free-form
  model responses are not persisted in analysis results. Review clips and
  approved highlights can contain voices and visible private information, so
  they still require human review before sharing.

Scores, source fingerprints, local approval files, and publication receipts are
application evidence, not proof against a malicious local user. Stream exclusion
also cannot remove a microphone voice already mixed into a retained track.

See [Security and privacy](docs/security-and-privacy.md) for the application
threat model and safe cleanup procedure.
