"""Portable model-server lifecycle without running a process, network, or GPU."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from raid_editor.config.models import HighlightIntelligenceConfig
from raid_editor.highlights import local_runtime


def _settings(tmp_path: Path) -> HighlightIntelligenceConfig:
    executable = tmp_path / "portable" / "ollama.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"test placeholder; must never execute")
    return HighlightIntelligenceConfig(
        enabled=True,
        ollama_executable=executable,
        ollama_models_path=tmp_path / "models",
        ollama_url="http://127.0.0.1:11435",
    )


def test_owned_server_is_hidden_process_scoped_and_stopped_after_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    before = dict(os.environ)
    healthy = Mock(side_effect=[False, True])
    process = Mock()
    process.poll.return_value = None
    launch = Mock(return_value=process)
    monkeypatch.setattr(local_runtime, "_healthy", healthy)
    monkeypatch.setattr(local_runtime.subprocess, "Popen", launch)
    with local_runtime.managed_local_intelligence(settings):
        process.terminate.assert_not_called()
        assert dict(os.environ) == before
    launch.assert_called_once()
    assert launch.call_args.args[0] == [str(settings.ollama_executable), "serve"]
    options = launch.call_args.kwargs
    assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert options["env"]["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert options["env"]["OLLAMA_NO_CLOUD"] == "1"
    assert options["env"]["OLLAMA_DEBUG"] == "0"
    assert options["env"]["OLLAMA_NUM_PARALLEL"] == "1"
    assert options["env"]["OLLAMA_MAX_LOADED_MODELS"] == "1"
    assert options["env"]["OLLAMA_MODELS"] == str(settings.ollama_models_path)
    assert options["env"] is not os.environ
    assert dict(os.environ) == before
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)
    process.kill.assert_not_called()


def test_existing_server_is_reused_and_never_stopped_even_when_analysis_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    launch = Mock()
    monkeypatch.setattr(local_runtime, "_healthy", Mock(return_value=True))
    monkeypatch.setattr(local_runtime.subprocess, "Popen", launch)
    with (
        pytest.raises(RuntimeError, match="analysis failure"),
        local_runtime.managed_local_intelligence(settings),
    ):
        raise RuntimeError("analysis failure")
    launch.assert_not_called()


@pytest.mark.parametrize("disabled", [True, False])
def test_disabled_or_missing_runtime_does_not_launch_or_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disabled: bool,
) -> None:
    settings = HighlightIntelligenceConfig(
        enabled=not disabled, ollama_executable=tmp_path / "missing.exe"
    )
    healthy = Mock(return_value=False)
    launch = Mock()
    monkeypatch.setattr(local_runtime, "_healthy", healthy)
    monkeypatch.setattr(local_runtime.subprocess, "Popen", launch)
    with local_runtime.managed_local_intelligence(settings):
        pass
    launch.assert_not_called()
    if disabled:
        healthy.assert_not_called()
    assert not (tmp_path / "missing.exe").exists()


def test_owned_process_is_cleaned_up_when_analysis_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(local_runtime, "_healthy", Mock(side_effect=[False, True]))
    monkeypatch.setattr(local_runtime.subprocess, "Popen", Mock(return_value=process))
    with (
        pytest.raises(RuntimeError, match="semantic failure"),
        local_runtime.managed_local_intelligence(_settings(tmp_path)),
    ):
        raise RuntimeError("semantic failure")
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)


def test_owned_process_timeout_kills_only_the_returned_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("owned-ollama", 10), 0]
    launch = Mock(return_value=process)
    monkeypatch.setattr(local_runtime, "_healthy", Mock(side_effect=[False, True]))
    monkeypatch.setattr(local_runtime.subprocess, "Popen", launch)
    with local_runtime.managed_local_intelligence(_settings(tmp_path)):
        pass
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert [call.kwargs for call in process.wait.call_args_list] == [
        {"timeout": 10},
        {"timeout": 5},
    ]
    launch.assert_called_once()


def test_already_exited_owned_process_is_not_terminated_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock()
    process.poll.return_value = 1
    monkeypatch.setattr(local_runtime, "_healthy", Mock(return_value=False))
    monkeypatch.setattr(local_runtime.subprocess, "Popen", Mock(return_value=process))
    with local_runtime.managed_local_intelligence(_settings(tmp_path)):
        pass
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


def test_launch_failure_yields_fixed_warning_without_exposing_exception_or_escaping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(local_runtime, "_healthy", Mock(return_value=False))
    monkeypatch.setattr(
        local_runtime.subprocess, "Popen", Mock(side_effect=OSError("PRIVATE exception detail"))
    )
    with (
        caplog.at_level("WARNING", logger="raid_editor.highlights.local_runtime"),
        local_runtime.managed_local_intelligence(_settings(tmp_path)),
    ):
        reached = True
    assert reached
    assert len(caplog.records) == 1
    assert "PRIVATE" not in caplog.text
    assert "could not start" in caplog.text


@pytest.mark.parametrize("body", [b"[]", b"null", b'"string"', b"not-json"])
def test_health_check_refuses_malformed_payload_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    response = Mock(status=200)
    response.read.return_value = body
    connection = Mock()
    connection.getresponse.return_value = response
    connect = Mock(return_value=connection)
    monkeypatch.setattr(local_runtime.http.client, "HTTPConnection", connect)
    assert local_runtime._healthy(HighlightIntelligenceConfig()) is False
    connect.assert_called_once_with("127.0.0.1", 11435, timeout=1)
    connection.request.assert_called_once_with("GET", "/api/version")
    connection.close.assert_called_once()
