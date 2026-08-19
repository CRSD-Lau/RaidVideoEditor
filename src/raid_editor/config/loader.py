"""Load and normalize YAML project configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from raid_editor.config.models import ProjectConfig
from raid_editor.util.paths import slugify

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class ConfigError(ValueError):
    """Expected, user-actionable configuration failure."""


def _absolute(base: Path, value: Path | None) -> Path | None:
    if value is None:
        return None
    return value if value.is_absolute() else (base / value).resolve()


def load_project_config(path: Path) -> ProjectConfig:
    config_path = path.expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"Project configuration does not exist: {config_path}")
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Could not read YAML configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Project configuration must contain a YAML mapping")
    try:
        config = ProjectConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc

    base = config_path.parent
    normalized_input = config.input.model_copy(
        update={
            "recording": _absolute(base, config.input.recording),
            "combat_log": _absolute(base, config.input.combat_log),
            "details_export": _absolute(base, config.input.details_export),
            "skada_export": _absolute(base, config.input.skada_export),
            "manual_pulls": _absolute(base, config.input.manual_pulls),
        }
    )
    normalized_music = config.music.model_copy(
        update={"library": _absolute(base, config.music.library)}
    )
    normalized_highlights = config.highlights.model_copy(
        update={
            "manual_selection": _absolute(base, config.highlights.manual_selection),
        }
    )
    normalized_preview = config.preview
    preview_updates: dict[str, object] = {}
    if config.preview.watermark is not None:
        preview_updates["watermark"] = config.preview.watermark.model_copy(
            update={"image": _absolute(base, config.preview.watermark.image)}
        )
    presentation = config.preview.presentation
    if presentation is not None and presentation.background_image is not None:
        preview_updates["presentation"] = presentation.model_copy(
            update={
                "background_image": _absolute(base, presentation.background_image),
            }
        )
    if preview_updates:
        normalized_preview = config.preview.model_copy(update=preview_updates)
    normalized_youtube = config.youtube
    if any(
        path is not None
        for path in (
            config.youtube.client_secrets,
            config.youtube.token,
            config.youtube.management_token,
            config.youtube.analytics_token,
        )
    ):
        normalized_youtube = config.youtube.model_copy(
            update={
                "client_secrets": (
                    _absolute(base, config.youtube.client_secrets)
                    if config.youtube.client_secrets is not None
                    else None
                ),
                "token": (
                    _absolute(base, config.youtube.token)
                    if config.youtube.token is not None
                    else None
                ),
                "management_token": (
                    _absolute(base, config.youtube.management_token)
                    if config.youtube.management_token is not None
                    else None
                ),
                "analytics_token": (
                    _absolute(base, config.youtube.analytics_token)
                    if config.youtube.analytics_token is not None
                    else None
                ),
            }
        )
    normalized_archive = config.archive.model_copy(
        update={"destination": _absolute(base, config.archive.destination)}
    )
    return config.model_copy(
        update={
            "input": normalized_input,
            "music": normalized_music,
            "highlights": normalized_highlights,
            "preview": normalized_preview,
            "youtube": normalized_youtube,
            "archive": normalized_archive,
        }
    )


def project_slug(config: ProjectConfig) -> str:
    return slugify(config.project.name)


def project_output_dir(config: ProjectConfig) -> Path:
    return PROJECT_ROOT / "output" / project_slug(config)
