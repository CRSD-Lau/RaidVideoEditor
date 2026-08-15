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

## Repository guarantees

- Source recordings, generated output, local project configuration, and the
  `secrets/` directory are excluded from Git.
- YouTube OAuth clients and tokens use ignored purpose-specific files.
- CI scans the complete Git history with Gitleaks.
- Uploads, playlist changes, final renders, social exports, and archive copies
  require explicit approval.
- No command deletes source recordings.

See [Security and privacy](docs/security-and-privacy.md) for the application
threat model and safe cleanup procedure.
