# Contributing

This is a Windows-first Python 3.12 project. Keep changes deterministic,
review-first, and safe around recordings and credentials.

## Development setup

```powershell
git clone https://github.com/CRSD-Lau/RaidVideoEditor.git
Set-Location RaidVideoEditor
uv sync --python 3.12 --extra dev --frozen
uv run python scripts\generate-synthetic-fixture.py --force
```

## Validation

Run the same checks as CI before submitting a change:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest -q
```

Use the synthetic fixture for reproducible integration checks. Never add a real
recording, combat log, local configuration, OAuth file, token, downloaded review
override, or generated `output/` directory to a commit.

## Change expectations

- Preserve source-media immutability and explicit approval gates.
- Keep microphone exclusion fail-closed.
- Treat conflicting difficulty evidence as `UNKNOWN`.
- Document new commands and configuration fields.
- Add focused tests for behavior and failure boundaries.
- Update [CHANGELOG.md](CHANGELOG.md) for user-visible changes.

Brand artwork is not covered by the source-code license. See
[Third-party notices](THIRD_PARTY_NOTICES.md).
