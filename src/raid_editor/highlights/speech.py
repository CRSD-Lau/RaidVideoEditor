"""Local, transcript-minimizing spoken-command detection for highlights."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from raid_editor.config.models import SpeechTriggerConfig

SpeechRole = Literal["discord", "microphone"]
SpeechStatus = Literal["disabled", "complete", "partial", "unavailable", "failed", "truncated"]
_WORD = re.compile(r"[^a-z0-9]+")


class SpeechTriggerError(RuntimeError):
    """Expected offline recognizer or audio-decoding failure."""


@dataclass(frozen=True, slots=True)
class RecognizedWord:
    """One normalized recognizer word with recording-relative timing."""

    word: str
    start_seconds: float
    end_seconds: float
    confidence: float


@dataclass(frozen=True, slots=True)
class SpeechTriggerEvent:
    """Minimal retained evidence for an exact spoken highlight command."""

    start_seconds: float
    end_seconds: float
    phrase: str
    source_role: SpeechRole
    confidence: float
    backend: str
    backend_version: str
    model_identity: str


@dataclass(frozen=True, slots=True)
class SpeechTriggerResult:
    """Recognition coverage, diagnostics, and matched commands only."""

    status: SpeechStatus
    events: tuple[SpeechTriggerEvent, ...] = ()
    diagnostics: tuple[str, ...] = ()
    requested_sources: tuple[SpeechRole, ...] = ()
    completed_sources: tuple[SpeechRole, ...] = ()
    backend: str = "vosk"
    backend_version: str | None = None
    model_identity: str | None = None
    truncated_count: int = 0

    @property
    def cacheable(self) -> bool:
        """Only cache runs that completed every configured recognition pass."""

        return self.status in {"complete", "truncated", "disabled"}

    def to_dict(self) -> dict[str, object]:
        """Return the machine-readable, transcript-free status payload."""

        return {
            "status": self.status,
            "events": [asdict(event) for event in self.events],
            "diagnostics": list(self.diagnostics),
            "requested_sources": list(self.requested_sources),
            "completed_sources": list(self.completed_sources),
            "backend": self.backend,
            "backend_version": self.backend_version,
            "model_identity": self.model_identity,
            "truncated_count": self.truncated_count,
            "transcript_persisted": False,
            "cacheable": self.cacheable,
        }


def speech_trigger_result_from_dict(payload: dict[str, object]) -> SpeechTriggerResult:
    """Validate the small persisted speech-result payload used by the cache."""

    valid_statuses: set[str] = {
        "disabled",
        "complete",
        "partial",
        "unavailable",
        "failed",
        "truncated",
    }
    raw_status = str(payload.get("status", "failed"))
    if raw_status not in valid_statuses:
        raise ValueError(f"Unknown speech trigger status: {raw_status}")
    raw_events = payload.get("events", [])
    if not isinstance(raw_events, list):
        raise ValueError("Speech trigger events must be a list")
    events = tuple(SpeechTriggerEvent(**row) for row in raw_events if isinstance(row, dict))
    raw_diagnostics = payload.get("diagnostics", [])
    raw_requested = payload.get("requested_sources", [])
    raw_completed = payload.get("completed_sources", [])
    if not isinstance(raw_diagnostics, list):
        raise ValueError("Speech trigger diagnostics must be a list")
    if not isinstance(raw_requested, list) or not isinstance(raw_completed, list):
        raise ValueError("Speech trigger source coverage must use lists")
    requested = tuple(
        cast(SpeechRole, value) for value in raw_requested if value in {"discord", "microphone"}
    )
    completed = tuple(
        cast(SpeechRole, value) for value in raw_completed if value in {"discord", "microphone"}
    )
    raw_truncated_count = payload.get("truncated_count", 0)
    if not isinstance(raw_truncated_count, int | str):
        raise ValueError("Speech trigger truncated_count must be an integer")
    return SpeechTriggerResult(
        status=cast(SpeechStatus, raw_status),
        events=events,
        diagnostics=tuple(str(value) for value in raw_diagnostics),
        requested_sources=requested,
        completed_sources=completed,
        backend=str(payload.get("backend", "vosk")),
        backend_version=(
            str(payload["backend_version"]) if payload.get("backend_version") is not None else None
        ),
        model_identity=(
            str(payload["model_identity"]) if payload.get("model_identity") is not None else None
        ),
        truncated_count=int(raw_truncated_count),
    )


class SpeechRecognizer(Protocol):
    """Backend-neutral recognizer contract used by deterministic tests."""

    backend: str
    backend_version: str
    model_identity: str

    def recognize(
        self,
        recording: Path,
        *,
        stream_index: int,
        source_role: SpeechRole,
        settings: SpeechTriggerConfig,
    ) -> list[SpeechTriggerEvent]:
        """Return exact matched commands for one absolute audio stream."""


def normalize_word(value: str) -> str:
    """Normalize recognizer tokens for exact, punctuation-insensitive matching."""

    return _WORD.sub("", value.casefold())


def match_phrase_words(
    words: list[RecognizedWord],
    *,
    phrases: list[str],
    minimum_word_confidence: float,
    maximum_word_gap_seconds: float,
) -> list[tuple[str, float, float, float]]:
    """Match only exact adjacent phrase tokens with bounded gaps and confidence."""

    normalized_phrases = [(phrase, phrase.split()) for phrase in phrases]
    matches: list[tuple[str, float, float, float]] = []
    tokens = [normalize_word(word.word) for word in words]
    for phrase, phrase_tokens in normalized_phrases:
        width = len(phrase_tokens)
        if width == 0:
            continue
        for start_index in range(0, len(words) - width + 1):
            window = words[start_index : start_index + width]
            if tokens[start_index : start_index + width] != phrase_tokens:
                continue
            if any(word.confidence < minimum_word_confidence for word in window):
                continue
            if any(
                current.start_seconds - previous.end_seconds > maximum_word_gap_seconds
                for previous, current in zip(window, window[1:], strict=False)
            ):
                continue
            matches.append(
                (
                    phrase,
                    window[0].start_seconds,
                    window[-1].end_seconds,
                    min(word.confidence for word in window),
                )
            )
    return matches


def _model_identity(model_path: Path | None) -> str | None:
    if model_path is None or not model_path.is_dir():
        return None
    digest = hashlib.sha256()
    files = sorted(path for path in model_path.rglob("*") if path.is_file())
    for path in files:
        stat = path.stat()
        digest.update(path.relative_to(model_path).as_posix().encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return f"{model_path.name}:{digest.hexdigest()[:16]}"


def speech_runtime_signature(settings: SpeechTriggerConfig) -> dict[str, object]:
    """Describe local runtime availability so installing a backend invalidates caches."""

    try:
        backend_version: str | None = importlib.metadata.version(settings.backend)
    except importlib.metadata.PackageNotFoundError:
        backend_version = None
    return {
        "backend": settings.backend,
        "backend_version": backend_version,
        "model_identity": _model_identity(settings.model_path),
    }


def _stream_start_seconds(recording: Path, stream_index: int) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=index,start_time",
        "-of",
        "json",
        str(recording),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise SpeechTriggerError("ffprobe is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown FFprobe error").strip()
        raise SpeechTriggerError(
            f"Could not inspect speech stream timing: {detail[-1000:]}"
        ) from exc
    try:
        streams = json.loads(completed.stdout).get("streams", [])
        stream = next(item for item in streams if int(item.get("index", -1)) == stream_index)
        raw = stream.get("start_time")
        return float(raw) if raw not in (None, "", "N/A") else 0.0
    except (json.JSONDecodeError, StopIteration, TypeError, ValueError) as exc:
        raise SpeechTriggerError(
            f"Could not find absolute audio stream {stream_index} for speech detection"
        ) from exc


def _words_from_payload(payload: str, *, offset_seconds: float) -> list[RecognizedWord]:
    try:
        rows = json.loads(payload).get("result", [])
    except json.JSONDecodeError:
        return []
    words: list[RecognizedWord] = []
    for row in rows:
        try:
            word = normalize_word(str(row["word"]))
            if not word:
                continue
            words.append(
                RecognizedWord(
                    word=word,
                    start_seconds=offset_seconds + float(row["start"]),
                    end_seconds=offset_seconds + float(row["end"]),
                    confidence=float(row.get("conf", 0.0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return words


class VoskKeywordRecognizer:
    """Grammar-constrained, local Vosk adapter that streams FFmpeg PCM."""

    backend = "vosk"

    def __init__(self, settings: SpeechTriggerConfig) -> None:
        if settings.model_path is None or not settings.model_path.is_dir():
            raise SpeechTriggerError(
                f"Vosk model directory is missing: {settings.model_path or '<not configured>'}"
            )
        try:
            vosk: Any = importlib.import_module("vosk")
            self.backend_version = importlib.metadata.version("vosk")
        except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
            raise SpeechTriggerError(
                "Vosk is unavailable. Install the locked speech extra with "
                "`uv sync --extra speech`."
            ) from exc
        identity = _model_identity(settings.model_path)
        if identity is None:
            raise SpeechTriggerError(f"Could not fingerprint Vosk model: {settings.model_path}")
        self.model_identity = identity
        try:
            vosk.SetLogLevel(-1)
            self._vosk = vosk
            self._model = vosk.Model(str(settings.model_path))
        except Exception as exc:
            raise SpeechTriggerError(f"Could not load Vosk model: {exc}") from exc

    def recognize(
        self,
        recording: Path,
        *,
        stream_index: int,
        source_role: SpeechRole,
        settings: SpeechTriggerConfig,
    ) -> list[SpeechTriggerEvent]:
        offset = _stream_start_seconds(recording, stream_index)
        grammar = json.dumps([*settings.phrases, "[unk]"])
        recognizer = self._vosk.KaldiRecognizer(
            self._model,
            settings.sample_rate_hz,
            grammar,
        )
        recognizer.SetWords(True)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(recording),
            "-map",
            f"0:{stream_index}",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            str(settings.sample_rate_hz),
            "-f",
            "s16le",
            "pipe:1",
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise SpeechTriggerError("ffmpeg is not installed or not on PATH") from exc
        if process.stdout is None or process.stderr is None:
            process.kill()
            raise SpeechTriggerError("FFmpeg speech decoder streams were not created")

        maximum_tokens = max(len(phrase.split()) for phrase in settings.phrases)
        pending: list[RecognizedWord] = []
        matches: list[tuple[str, float, float, float]] = []
        latest_emitted_end = -1.0

        def consume(payload: str) -> None:
            nonlocal pending, latest_emitted_end
            pending.extend(_words_from_payload(payload, offset_seconds=offset))
            found = match_phrase_words(
                pending,
                phrases=settings.phrases,
                minimum_word_confidence=settings.minimum_word_confidence,
                maximum_word_gap_seconds=settings.maximum_word_gap_seconds,
            )
            for match in found:
                if match[2] > latest_emitted_end + 1e-6:
                    matches.append(match)
                    latest_emitted_end = match[2]
            pending = pending[-max(1, maximum_tokens - 1) :]

        try:
            while chunk := process.stdout.read(8_000):
                if recognizer.AcceptWaveform(chunk):
                    consume(recognizer.Result())
            consume(recognizer.FinalResult())
            stderr = process.stderr.read().decode("utf-8", errors="replace")
            return_code = process.wait()
        except Exception:
            process.kill()
            process.wait()
            raise
        if return_code != 0:
            raise SpeechTriggerError(
                f"FFmpeg speech decode failed for {source_role}: {stderr.strip()[-1000:]}"
            )
        return [
            SpeechTriggerEvent(
                start_seconds=start,
                end_seconds=end,
                phrase=phrase,
                source_role=source_role,
                confidence=confidence,
                backend=self.backend,
                backend_version=self.backend_version,
                model_identity=self.model_identity,
            )
            for phrase, start, end, confidence in matches
        ]


def _dedupe_events(
    events: list[SpeechTriggerEvent], *, dedupe_seconds: float
) -> list[SpeechTriggerEvent]:
    source_order = {"microphone": 0, "discord": 1}
    ranked = sorted(
        events,
        key=lambda event: (
            -event.confidence,
            source_order[event.source_role],
            event.end_seconds,
            event.start_seconds,
        ),
    )
    selected: list[SpeechTriggerEvent] = []
    for event in ranked:
        if all(
            event.phrase != kept.phrase
            or abs(event.end_seconds - kept.end_seconds) >= dedupe_seconds
            for kept in selected
        ):
            selected.append(event)
    return sorted(selected, key=lambda event: (event.end_seconds, event.source_role))


def detect_spoken_commands(
    recording: Path,
    *,
    source_streams: dict[SpeechRole, int | None],
    settings: SpeechTriggerConfig,
    recognizer: SpeechRecognizer | None = None,
) -> SpeechTriggerResult:
    """Detect exact local commands while retaining no full transcript."""

    requested = tuple(settings.source_roles)
    if not settings.enabled:
        return SpeechTriggerResult(status="disabled", requested_sources=requested)
    diagnostics: list[str] = []
    if recognizer is None:
        try:
            recognizer = VoskKeywordRecognizer(settings)
        except SpeechTriggerError as exc:
            if settings.required:
                raise
            runtime = speech_runtime_signature(settings)
            raw_version = runtime["backend_version"]
            return SpeechTriggerResult(
                status="unavailable",
                diagnostics=(str(exc),),
                requested_sources=requested,
                backend=settings.backend,
                backend_version=(str(raw_version) if raw_version is not None else None),
                model_identity=_model_identity(settings.model_path),
            )

    events: list[SpeechTriggerEvent] = []
    completed: list[SpeechRole] = []
    attempted = 0
    for role in requested:
        stream_index = source_streams.get(role)
        if stream_index is None:
            diagnostics.append(f"{role} speech source is not configured")
            continue
        attempted += 1
        try:
            events.extend(
                recognizer.recognize(
                    recording,
                    stream_index=stream_index,
                    source_role=role,
                    settings=settings,
                )
            )
            completed.append(role)
        except (OSError, SpeechTriggerError, subprocess.SubprocessError) as exc:
            if settings.required:
                raise SpeechTriggerError(f"{role} speech recognition failed: {exc}") from exc
            diagnostics.append(f"{role} speech recognition failed: {exc}")

    deduped = _dedupe_events(events, dedupe_seconds=settings.dedupe_seconds)
    truncated_count = max(0, len(deduped) - settings.maximum_matches)
    kept = tuple(deduped[: settings.maximum_matches])
    if truncated_count:
        diagnostics.append(
            f"Speech trigger safety limit omitted {truncated_count} additional matches"
        )
        status: SpeechStatus = "truncated" if len(completed) == len(requested) else "partial"
    elif len(completed) == len(requested):
        status = "complete"
    elif completed:
        status = "partial"
    elif attempted:
        status = "failed"
    else:
        status = "unavailable"
    return SpeechTriggerResult(
        status=status,
        events=kept,
        diagnostics=tuple(diagnostics),
        requested_sources=requested,
        completed_sources=tuple(completed),
        backend=recognizer.backend,
        backend_version=recognizer.backend_version,
        model_identity=recognizer.model_identity,
        truncated_count=truncated_count,
    )
