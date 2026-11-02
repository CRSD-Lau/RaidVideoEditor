"""Offline compatibility checks for the optional faster-whisper decoder."""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import pytest


def test_faster_whisper_decodes_a_synthetic_wav_without_a_model() -> None:
    """Keep the PyAV decoder API compatible without downloading or loading a model."""
    audio = pytest.importorskip("faster_whisper.audio")
    numpy = pytest.importorskip("numpy")

    with tempfile.TemporaryDirectory() as temporary:
        recording = Path(temporary) / "silence.wav"
        with wave.open(str(recording), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(b"\x00\x00" * 1_600)

        samples = audio.decode_audio(str(recording), sampling_rate=16_000)

    assert samples.dtype == numpy.float32
    assert samples.shape == (1_600,)
    assert numpy.isfinite(samples).all()
    assert float(numpy.abs(samples).max()) == 0.0
