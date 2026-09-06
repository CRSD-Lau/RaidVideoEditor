"""Local-only adapters. Speech, decoded audio, and frames stay in memory."""

from __future__ import annotations

import base64
import ctypes
import gc
import hashlib
import http.client
import importlib
import importlib.metadata
import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from raid_editor.config.models import HighlightIntelligenceConfig


class IntelligenceError(RuntimeError):
    """A safe, fixed diagnostic; never include backend responses or speech."""


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    start: float
    end: float
    probability: float


@dataclass(frozen=True, slots=True)
class Utterance:
    id: str
    role: str
    start: float
    end: float
    text: str
    words: tuple[Word, ...]

    def prompt_data(self) -> dict[str, object]:
        """Transient inference input. Never serialize this into a report."""
        return {
            "id": self.id,
            "role": self.role,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
        }


def dependency_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def model_fingerprint(path: Path | None) -> str | None:
    """Metadata fingerprint without reading multi-GB weights on every cache check."""
    if path is None or not path.is_dir():
        return None
    digest = hashlib.sha256()
    try:
        for item in sorted(path.rglob("*")):
            if item.is_file():
                info = item.stat()
                digest.update(item.relative_to(path).as_posix().encode())
                digest.update(f"{info.st_size}:{info.st_mtime_ns}".encode())
    except OSError:
        return None
    return digest.hexdigest()


class LocalOllama:
    """HTTPConnection avoids environment proxies; only IPv4 loopback is allowed."""

    def __init__(self, settings: HighlightIntelligenceConfig) -> None:
        try:
            url = urlsplit(settings.ollama_url)
            port = url.port or 80
        except ValueError:
            raise IntelligenceError("invalid_local_endpoint") from None
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise IntelligenceError("nonlocal_endpoint_refused")
        self.port = port
        self.timeout = settings.request_timeout_seconds
        self.model = settings.model

    def _request(self, method: str, path: str, data: dict[str, object] | None) -> Any:
        # Do not substitute a requests session or urllib opener: their proxy and
        # redirect defaults can send dialogue outside this machine.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        try:
            body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
            connection.request(method, path, body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise IntelligenceError("local_model_http_error")
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise IntelligenceError("local_model_response_too_large")
            return json.loads(raw)
        except IntelligenceError:
            raise
        except (OSError, ValueError, http.client.HTTPException):
            raise IntelligenceError("local_model_unavailable") from None
        finally:
            connection.close()

    def model_digest(self) -> str:
        payload = self._request("GET", "/api/tags", None)
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise IntelligenceError("invalid_local_model_inventory")
        for item in payload["models"]:
            if not isinstance(item, dict):
                continue
            if item.get("name") == self.model or item.get("model") == self.model:
                digest = item.get("digest")
                if isinstance(digest, str) and 1 <= len(digest) <= 128:
                    # Model digests are hexadecimal, never arbitrary server text.
                    normalized = digest.removeprefix("sha256:")
                    if all(char in "0123456789abcdef" for char in normalized.lower()):
                        return digest
                raise IntelligenceError("invalid_local_model_digest")
        raise IntelligenceError("local_model_not_installed")

    def chat(
        self,
        *,
        instruction: str,
        payload: dict[str, object],
        schema: dict[str, Any],
        images: list[str] | None = None,
    ) -> object:
        content = json.dumps(payload, ensure_ascii=False)
        # Ollama's format constrains token decoding; it does not explain the
        # schema's field meanings to the model. Ground those fields explicitly.
        instruction = instruction + "\nResponse JSON schema:\n" + json.dumps(schema)
        # A byte is a conservative upper bound on text tokenizer pieces. Leave
        # room for the chat template and 2K generated tokens within a 32K context.
        # Do not silently let the server truncate setup or payoff utterances.
        context_bytes = (
            len(content.encode("utf-8"))
            + len(instruction.encode("utf-8"))
            + len(json.dumps(schema).encode("utf-8"))
        )
        if context_bytes > 28000:
            raise IntelligenceError("semantic_context_budget_exhausted")
        user_message: dict[str, object] = {
            "role": "user",
            "content": content,
        }
        if images:
            user_message["images"] = images
        request: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instruction},
                user_message,
            ],
            "stream": False,
            "format": schema,
            "keep_alive": 0,
            "options": {
                "temperature": 0,
                "num_ctx": 16384 if context_bytes <= 12000 else 32768,
                "num_predict": 2048,
            },
        }
        if self.model.casefold().startswith("qwen3"):
            # Keep the bounded output budget available for the evidence schema.
            # Models without thinking support need not receive this option.
            request["think"] = False
        result = self._request("POST", "/api/chat", request)
        if not isinstance(result, dict) or result.get("done") is not True:
            raise IntelligenceError("incomplete_local_model_response")
        if result.get("done_reason") not in {None, "stop"}:
            raise IntelligenceError("incomplete_local_model_response")
        message = result.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise IntelligenceError("invalid_local_model_message")
        output_content = message.get("content")
        if not isinstance(output_content, str):
            raise IntelligenceError("invalid_local_model_content")
        try:
            return json.loads(output_content)
        except ValueError:
            raise IntelligenceError("invalid_local_model_json") from None


def _ffmpeg(command: list[str], *, timeout: float) -> bytes:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise IntelligenceError("ffmpeg_unavailable")
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-loglevel", "error", "-nostdin", *command],
            capture_output=True,
            check=False,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise IntelligenceError("ffmpeg_unavailable") from None
    except (OSError, subprocess.TimeoutExpired):
        raise IntelligenceError("media_decode_failed") from None
    if result.returncode != 0:
        raise IntelligenceError("media_decode_failed")
    return result.stdout


def decode_audio(recording: Path, stream_index: int, start: float, duration: float) -> Any:
    numpy = importlib.import_module("numpy")
    pcm = _ffmpeg(
        [
            "-ss",
            str(start),
            "-i",
            str(recording),
            "-t",
            str(duration),
            "-map",
            f"0:{stream_index}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "pipe:1",
        ],
        timeout=max(60, duration * 2),
    )
    if not pcm or len(pcm) % 4:
        raise IntelligenceError("audio_chunk_empty_or_invalid")
    # Detect a missing/truncated track; a silent PCM chunk still contains samples.
    if len(pcm) / 64000 < max(0, duration - 1.0):
        raise IntelligenceError("audio_chunk_incomplete")
    return numpy.frombuffer(pcm, dtype="<f4").copy()


def extract_frames(recording: Path, times: list[float]) -> list[str]:
    frames = []
    for moment in times:
        raw = _ffmpeg(
            [
                "-ss",
                str(moment),
                "-i",
                str(recording),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=768:-2",
                "-q:v",
                "4",
                "-f",
                "image2pipe",
                "-c:v",
                "mjpeg",
                "pipe:1",
            ],
            timeout=45,
        )
        if not raw or len(raw) > 2_000_000:
            raise IntelligenceError("visual_frame_unavailable")
        frames.append(base64.b64encode(raw).decode("ascii"))
    return frames


class LocalWhisper:
    """Load only the supplied on-disk CTranslate2 model; never download one."""

    def __init__(self, settings: HighlightIntelligenceConfig) -> None:
        path = settings.whisper_model_path
        if path is None or not path.is_dir() or not (path / "model.bin").is_file():
            raise IntelligenceError("local_speech_model_unavailable")
        self.settings = settings
        self.diagnostics: list[str] = []
        self._dll_handles: list[Any] = []
        self._loaded_libraries: list[Any] = []
        self._register_windows_dlls()
        try:
            self._backend = importlib.import_module("faster_whisper")
            try:
                self._load(settings.whisper_device, settings.whisper_compute_type)
            except (OSError, RuntimeError, ValueError):
                if settings.whisper_device != "auto":
                    raise
                self._load("cpu", "int8")
                self.diagnostics.append("speech_auto_cpu_fallback")
        except (ImportError, OSError, RuntimeError, ValueError):
            self._close_dll_handles()
            raise IntelligenceError("local_speech_runtime_unavailable") from None
        self.language = settings.language

    def _register_windows_dlls(self) -> None:
        """Register optional wheel DLLs for this process, without changing PATH."""
        if os.name != "nt":
            return
        directories: list[Path] = []
        for package, relative in (
            ("nvidia-cublas-cu12", "nvidia/cublas/bin"),
            ("nvidia-cudnn-cu12", "nvidia/cudnn/bin"),
            ("nvidia-cuda-nvrtc-cu12", "nvidia/cuda_nvrtc/bin"),
            ("nvidia-cuda-runtime-cu12", "nvidia/cuda_runtime/bin"),
        ):
            try:
                directory = Path(
                    str(importlib.metadata.distribution(package).locate_file(relative))
                )
            except importlib.metadata.PackageNotFoundError:
                continue
            if directory.is_dir():
                try:
                    self._dll_handles.append(os.add_dll_directory(str(directory)))
                    directories.append(directory)
                except OSError:
                    self.diagnostics.append("optional_gpu_dll_registration_failed")
        # Some CTranslate2 Windows builds call LoadLibrary without the directory
        # search flags. Loading these exact wheel-owned paths handles that case.
        for name in ("cudart64_12.dll", "cublasLt64_12.dll", "cublas64_12.dll", "cudnn64_9.dll"):
            for directory in directories:
                library = directory / name
                if library.is_file():
                    try:
                        self._loaded_libraries.append(ctypes.WinDLL(str(library)))
                    except OSError:
                        self.diagnostics.append("optional_gpu_dll_preload_failed")
                    break

    def _load(self, device: str, compute_type: str) -> None:
        self.model = self._backend.WhisperModel(
            str(self.settings.whisper_model_path),
            device=device,
            compute_type=compute_type,
            local_files_only=True,
        )
        self.actual_device = str(getattr(getattr(self.model, "model", None), "device", device))
        self.actual_compute_type = str(
            getattr(getattr(self.model, "model", None), "compute_type", compute_type)
        )

    def _close_dll_handles(self) -> None:
        for handle in self._dll_handles:
            handle.close()
        self._dll_handles.clear()

    def transcribe(
        self,
        recording: Path,
        *,
        stream_index: int,
        role: str,
        start: float,
        end: float,
        chunk_index: int,
    ) -> list[Utterance]:
        samples: Any = None
        try:
            samples = decode_audio(recording, stream_index, start, end - start)
            try:
                return self._recognize(samples, role, start, end, chunk_index)
            except (OSError, RuntimeError, ValueError):
                # GPU DLL/allocation failures often occur only when the lazy
                # segment iterator is consumed, not when Whisper is constructed.
                if self.settings.whisper_device != "auto" or self.actual_device == "cpu":
                    raise
                del self.model
                gc.collect()
                self._load("cpu", "int8")
                self.diagnostics.append("speech_auto_cpu_fallback")
                return self._recognize(samples, role, start, end, chunk_index)
        except IntelligenceError:
            raise
        except (ImportError, OSError, RuntimeError, ValueError):
            raise IntelligenceError("local_transcription_failed") from None
        finally:
            del samples

    def _recognize(
        self,
        samples: Any,
        role: str,
        start: float,
        end: float,
        chunk_index: int,
    ) -> list[Utterance]:
        segments, _ = self.model.transcribe(
            samples,
            language=self.language,
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
            beam_size=5,
        )
        utterances = []
        for ordinal, segment in enumerate(segments):
            if getattr(segment, "no_speech_prob", 0) > 0.75:
                continue
            words = []
            for word in segment.words or ():
                a, b = float(word.start) + start, float(word.end) + start
                probability = float(word.probability)
                if (
                    not all(math.isfinite(value) for value in (a, b, probability))
                    or a < start - 0.1
                    or b > end + 0.1
                    or b <= a
                    or not 0 <= probability <= 1
                ):
                    continue
                words.append(Word(str(word.word).strip(), max(start, a), min(end, b), probability))
            if not words:
                continue
            text = " ".join(word.text for word in words)
            if not text.strip():
                continue
            utterances.append(
                Utterance(
                    id=f"{role}:{chunk_index}:{ordinal}",
                    role=role,
                    start=words[0].start,
                    end=words[-1].end,
                    text=text,
                    words=tuple(words),
                )
            )
        return utterances

    def close(self) -> None:
        # CTranslate2 owns the model allocation; releasing it before Ollama is
        # essential on a single 12-GB GPU. Nothing is written to a transcript.
        if hasattr(self, "model"):
            del self.model
        gc.collect()
        self._close_dll_handles()
