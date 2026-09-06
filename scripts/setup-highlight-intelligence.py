"""Install pinned local inference assets; never transmit raid media or dialogue.

Author: Neil Mitchell
Run after: uv sync --extra dev --extra speech --extra intelligence
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib
import json
import socket
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

from raid_editor.config.models import HighlightIntelligenceConfig
from raid_editor.highlights.local_runtime import managed_local_intelligence
from raid_editor.util.paths import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
OLLAMA_VERSION = "0.33.3"
OLLAMA_SHA256 = "52cb36a62e7e501f61514f60212dec7117b6c098811357585e02fffe32d2fcd7"
WHISPER_REVISION = "a29b04bd15381511a9af671baec01072039215e3"


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=HighlightIntelligenceConfig().model,
        help="Local Ollama model tag to install for editorial and visual analysis",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=ROOT,
        help="Checkout directory in which to install .tools and .models (default: this checkout)",
    )
    return parser


def _require_unused_endpoint(parser: argparse.ArgumentParser) -> None:
    """Do not install into a different server's model directory by reusing its port."""
    try:
        connection = socket.create_connection(("127.0.0.1", 11435), timeout=0.5)
    except OSError:
        return
    connection.close()
    parser.error(
        "Local port 11435 is already in use. Stop the existing local model server before setup "
        "so downloads go into the selected runtime directory."
    )


def main(argv: list[str] | None = None) -> None:
    parser = argument_parser()
    arguments = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("This portable-runtime installer requires Windows x64.")
    try:
        snapshot_download = importlib.import_module("huggingface_hub").snapshot_download
    except ImportError:
        parser.error(
            "Install the optional dependencies first: uv sync --extra intelligence --frozen"
        )
    _require_unused_endpoint(parser)
    root = arguments.runtime_root.expanduser().resolve()
    runtime = root / ".tools" / f"ollama-{OLLAMA_VERSION}"
    runtime.mkdir(parents=True, exist_ok=True)
    archive = runtime / "ollama-windows-amd64.zip"
    url = (
        f"https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/"
        "ollama-windows-amd64.zip"
    )
    if not archive.is_file():
        temporary = archive.with_suffix(".downloading")
        print("Downloading portable Ollama from its official release...", flush=True)
        with urlopen(url, timeout=120) as response, temporary.open("wb") as output:  # noqa: S310
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(archive)
    with archive.open("rb") as source:
        checksum = hashlib.file_digest(source, "sha256").hexdigest()
    if checksum != OLLAMA_SHA256:
        raise ValueError("Portable Ollama release checksum mismatch")
    if not (runtime / "ollama.exe").is_file():
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if not (runtime / member.filename).resolve().is_relative_to(runtime.resolve()):
                    raise ValueError("Unsafe path inside portable runtime archive")
            package.extractall(runtime)
    print("Preparing pinned local English speech model...", flush=True)
    speech = root / ".models" / "faster-whisper-medium.en"
    snapshot_download(
        "Systran/faster-whisper-medium.en",
        revision=WHISPER_REVISION,
        local_dir=speech,
        allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*"],
    )
    settings = HighlightIntelligenceConfig(
        enabled=True,
        whisper_model_path=speech,
        model=arguments.model,
        ollama_executable=runtime / "ollama.exe",
        ollama_models_path=root / ".models" / "ollama",
    )
    print("Preparing local editorial and vision model...", flush=True)
    with managed_local_intelligence(settings):
        connection = http.client.HTTPConnection("127.0.0.1", 11435, timeout=900)
        try:
            connection.request(
                "POST",
                "/api/pull",
                json.dumps({"model": settings.model, "stream": False}),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            result = json.loads(response.read(1024 * 1024))
            if response.status != 200 or result.get("status") != "success":
                raise RuntimeError("The local editorial model download did not finish")
            connection.request("GET", "/api/tags")
            response = connection.getresponse()
            models = json.loads(response.read(1024 * 1024))
        finally:
            connection.close()
    provenance = {
        "schema_version": 1,
        "author": "Neil Mitchell",
        "last_modified_by": "Neil Mitchell",
        "ollama_version": OLLAMA_VERSION,
        "ollama_sha256": checksum,
        "whisper_repository": "Systran/faster-whisper-medium.en",
        "whisper_revision": WHISPER_REVISION,
        "local_models": models,
        "settings": settings.model_dump(mode="json"),
    }
    atomic_write_json(root / ".models" / "highlight-intelligence-provenance.json", provenance)
    print("Local highlight models installed; no raid content was transmitted.", flush=True)


if __name__ == "__main__":
    main()
