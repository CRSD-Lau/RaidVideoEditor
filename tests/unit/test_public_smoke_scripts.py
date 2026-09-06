"""Public checkout script entry points must not need models merely to show help."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    "setup-highlight-intelligence.py",
    "smoke-highlight-intelligence.py",
    "smoke-highlight-editorial-cases.py",
    "smoke-native-portrait-highlights.py",
)


def _load(filename: str) -> ModuleType:
    name = "public_script_" + filename.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename", SCRIPTS)
def test_script_help_does_not_start_models_or_require_optional_imports(
    filename: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load(filename)
    if hasattr(module, "importlib"):
        monkeypatch.setattr(
            module.importlib, "import_module", lambda _name: pytest.fail("optional import on help")
        )
    with pytest.raises(SystemExit) as exc:
        module.main(["--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize("filename", SCRIPTS[1:])
def test_smoke_outputs_default_to_generic_local_reports_and_accept_override(
    filename: str, tmp_path: Path
) -> None:
    module = _load(filename)
    parser = module.argument_parser()
    default = parser.parse_args([]).output_dir
    assert default.parent == ROOT / "reports"
    assert "2026" not in str(default.relative_to(ROOT))
    override = tmp_path / "ci-smoke"
    assert parser.parse_args(["--output-dir", str(override)]).output_dir == override


def test_setup_missing_optional_extra_fails_before_download_or_directory_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load(SCRIPTS[0])
    monkeypatch.setattr(module.sys, "platform", "win32")

    def missing(_name: str) -> Any:
        raise ImportError("not installed")

    monkeypatch.setattr(module.importlib, "import_module", missing)
    monkeypatch.setattr(module, "urlopen", lambda *_args, **_kwargs: pytest.fail("download"))
    with pytest.raises(SystemExit) as exc:
        module.main(["--runtime-root", str(tmp_path)])
    assert exc.value.code == 2
    assert "uv sync --extra intelligence --frozen" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_setup_occupied_port_never_downloads_or_mutates_another_model_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load(SCRIPTS[0])
    monkeypatch.setattr(module.sys, "platform", "win32")
    calls: list[str] = []
    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            snapshot_download=lambda **_kwargs: pytest.fail("model pull")
        ),
    )
    monkeypatch.setattr(
        module.socket,
        "create_connection",
        lambda *_args, **_kwargs: SimpleNamespace(close=lambda: calls.append("closed")),
    )
    monkeypatch.setattr(module, "urlopen", lambda *_args, **_kwargs: pytest.fail("download"))
    monkeypatch.setattr(
        module, "managed_local_intelligence", lambda *_args: pytest.fail("server mutation")
    )
    with pytest.raises(SystemExit) as exc:
        module.main(["--runtime-root", str(tmp_path)])
    assert exc.value.code == 2
    assert "11435 is already in use" in capsys.readouterr().err
    assert calls == ["closed"]
    assert list(tmp_path.iterdir()) == []


def test_native_smoke_missing_numpy_has_actionable_error_without_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load(SCRIPTS[3])

    def missing(_name: str) -> Any:
        raise ImportError("not installed")

    monkeypatch.setattr(module.importlib, "import_module", missing)
    with pytest.raises(SystemExit) as exc:
        module.main(["--output-dir", str(tmp_path / "not-created")])
    assert exc.value.code == 2
    assert "--extra intelligence" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_positive_fixture_timing_must_match_generated_media_bytes(tmp_path: Path) -> None:
    module = _load(SCRIPTS[1])
    source = tmp_path / "synthetic-dialogue.mkv"
    source.write_bytes(b"authored synthetic fixture")
    timing = {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "voice": "Microsoft David Desktop - English (United States)",
        "speech_rate": 0,
        "expected_first_setup_seconds": 8.21,
        "expected_final_payoff_seconds": 40.69,
        "tolerance_seconds": 0.2,
    }
    source.with_name("fixture-timing.json").write_text(json.dumps(timing), encoding="utf-8")
    module._validate_fixture(source)
    source.write_bytes(b"different media")
    with pytest.raises(ValueError, match="changed"):
        module._validate_fixture(source)


@pytest.mark.parametrize("filename", SCRIPTS[1:3])
def test_speech_smoke_missing_authored_fixture_fails_before_inference(
    filename: str, tmp_path: Path
) -> None:
    module = _load(filename)
    with pytest.raises(SystemExit) as exc:
        module.main(["--fixture-dir", str(tmp_path), "--output-dir", str(tmp_path / "output")])
    assert exc.value.code == 2
    assert list(tmp_path.iterdir()) == []
