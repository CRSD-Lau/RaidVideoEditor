"""Own a portable loopback model server only for the duration of local analysis."""

from __future__ import annotations

import http.client
import json
import logging
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit

from raid_editor.config.models import HighlightIntelligenceConfig


def _healthy(settings: HighlightIntelligenceConfig) -> bool:
    endpoint = urlsplit(settings.ollama_url)
    if endpoint.hostname != "127.0.0.1":
        return False
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port or 80, timeout=1)
    try:
        connection.request("GET", "/api/version")
        response = connection.getresponse()
        body = response.read(4096)
        payload = json.loads(body)
        return (
            response.status == 200
            and isinstance(payload, dict)
            and isinstance(payload.get("version"), str)
        )
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


@contextmanager
def managed_local_intelligence(settings: HighlightIntelligenceConfig) -> Iterator[None]:
    """Reuse an existing server; terminate only the process created by this call.

    Missing installation remains a visible intelligence coverage limitation.
    There are no installations, network model pulls, or persistent environment changes.
    """

    if not settings.enabled or _healthy(settings):
        yield
        return
    executable = settings.ollama_executable
    if executable is None or not executable.is_file():
        yield
        return
    endpoint = urlsplit(settings.ollama_url)
    environment = os.environ.copy()
    environment.update(
        OLLAMA_HOST=settings.ollama_url.removeprefix("http://"),
        OLLAMA_NO_CLOUD="1",
        OLLAMA_DEBUG="0",
        OLLAMA_MAX_LOADED_MODELS="1",
        OLLAMA_NUM_PARALLEL="1",
    )
    if settings.ollama_models_path is not None:
        environment["OLLAMA_MODELS"] = str(settings.ollama_models_path)
    if endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The managed model server must use a loopback address")
    try:
        process = subprocess.Popen(
            [str(executable), "serve"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        logging.getLogger(__name__).warning(
            "Configured local highlight model server could not start; "
            "analysis will report unavailable if no local server is reachable"
        )
        yield
        return
    try:
        deadline = time.monotonic() + 15
        while process.poll() is None and time.monotonic() < deadline:
            if _healthy(settings):
                break
            time.sleep(0.2)
        yield
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
