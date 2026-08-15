"""Shared serialized models used at module boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PullType = Literal[
    "trash_pull",
    "boss_attempt",
    "boss_kill",
    "boss_wipe",
    "recovery",
    "run_back",
    "loot",
    "downtime",
    "unknown",
]
PullResult = Literal["kill", "wipe", "success", "unknown", "not_applicable"]
DifficultyMode = Literal["10N", "10H", "25N", "25H", "UNKNOWN"]
DifficultyConfidence = Literal["high", "medium", "low", "none"]
HighlightCategory = Literal["funny", "reaction", "intense", "movement", "clutch"]


class PullCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    duration_seconds: float | None = Field(default=None, gt=0)
    type: PullType = "unknown"
    encounter: str | None = None
    result: PullResult = "unknown"
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    include: bool = True
    title: str | None = None
    notes: str = ""
    difficulty: DifficultyMode = "UNKNOWN"
    difficulty_confidence: DifficultyConfidence = "none"
    difficulty_evidence: list[str] = Field(default_factory=list)
    difficulty_reason: str = ""

    @model_validator(mode="after")
    def valid_window(self) -> PullCandidate:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        actual = self.end_seconds - self.start_seconds
        if self.duration_seconds is None:
            self.duration_seconds = actual
        elif abs(self.duration_seconds - actual) > 0.05:
            raise ValueError("duration_seconds does not match start/end")
        return self


class HighlightCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    peak_seconds: float = Field(ge=0)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    category: HighlightCategory
    score: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)
    encounter: str | None = None
    include: bool = False
    title: str
    notes: str = ""

    @model_validator(mode="after")
    def valid_window_and_peak(self) -> HighlightCandidate:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        if not self.start_seconds <= self.peak_seconds <= self.end_seconds:
            raise ValueError("peak_seconds must fall inside the highlight window")
        return self


class TimelineClip(BaseModel):
    source_in: float = Field(ge=0)
    source_out: float = Field(gt=0)
    timeline_in: float = Field(ge=0)
    label: str
    type: PullType
    result: PullResult
    encounter: str | None = None
    difficulty: DifficultyMode = "UNKNOWN"
    transition_in: str | None = None
    transition_out: str | None = None
    pull_ids: list[str] = Field(default_factory=list)


class TimelineDocument(BaseModel):
    schema_version: int = 1
    timeline_name: str
    source: Path
    source_duration_seconds: float = Field(gt=0)
    source_fps: float = Field(gt=0)
    retained_audio_stream_indexes: list[int]
    excluded_microphone_stream_index: int | None = None
    clips: list[TimelineClip]

    @property
    def duration_seconds(self) -> float:
        return sum(clip.source_out - clip.source_in for clip in self.clips)
