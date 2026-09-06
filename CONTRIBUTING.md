---
author: Neil Mitchell
last_modified_by: Neil Mitchell
---

# Contributing

This is a Windows-first project requiring Python 3.12 or newer. Keep changes deterministic,
review-first, and safe around recordings and credentials.

## Development setup

```powershell
git clone https://github.com/CRSD-Lau/RaidVideoEditor.git
Set-Location RaidVideoEditor
uv sync --extra dev --frozen
uv run --no-sync python scripts\generate-synthetic-fixture.py --force
```

## Validation

Install FFmpeg and FFprobe before running the synthetic fixture. Preserve an
existing compatible interpreter; a new environment can explicitly select
Python 3.12. Consult the [CI workflow](.github/workflows/ci.yml) for its tested
interpreter and dependency setup.

Run the quality checks before submitting a change:

```powershell
uv lock --check
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync mypy src
uv run --no-sync pytest -q
uv pip check
```

Use the synthetic fixture for reproducible integration checks. Never add a real
recording, combat log, local configuration, OAuth file, token, downloaded review
override, feedback, browser state, or generated `output/` or `reports/` directory
to a commit. Review staged paths and contents; ignore rules do not remove files
already tracked by Git. Do not include model weights or portable runtime binaries
in source commits or Python distributions.

## Optional runtime and media checks

The unit suite uses fixtures and mocks for model behavior. A passing mock test
does not establish that local speech recognition or model inference works.
Validate optional imports in a clean environment when their dependencies change:

```powershell
uv sync --extra dev --extra speech --extra intelligence --frozen
uv run --no-sync python -c "import vosk, faster_whisper, ctranslate2"
uv run --no-sync python scripts\smoke-native-portrait-highlights.py
```

The native smoke creates synthetic media and runs real FFmpeg checks. Its
fixture generation and numeric QA use NumPy, supplied by the intelligence
extra; production portrait synchronization itself uses the standard library
and FFmpeg. This smoke does not download or run models. Keep runtime/model smoke
tests separate and use [explicit local setup](docs/highlight-intelligence.md)
only when those downloads and inference are intended. GPU packages are optional
and do not belong in a mandatory CPU CI job.

For packaging changes, build a wheel and source distribution with `uv build`,
inspect their contents, and test the wheel's import and CLI in a fresh
environment without access to the source checkout. Check both base installation
and relevant optional extras. Preserve newer upstream lockfile versions when
integrating older work rather than silently downgrading unrelated dependencies.

## Change expectations

- Preserve source-media immutability in the editing CLI and explicit approval gates.
- Keep microphone exclusion fail-closed.
- Treat conflicting difficulty evidence as `UNKNOWN`.
- Document new commands and configuration fields.
- Add focused tests for behavior and failure boundaries.
- Update [CHANGELOG.md](CHANGELOG.md) for user-visible changes.

Brand artwork is not covered by the source-code license. See
[Third-party notices](THIRD_PARTY_NOTICES.md).
