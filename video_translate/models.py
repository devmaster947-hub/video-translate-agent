"""Validated persisted contracts. No processing or provider calls."""
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import PublicConfig

NonNegative = Annotated[int, Field(strict=True, ge=0)]
Positive = Annotated[int, Field(strict=True, gt=0)]
Provider = Literal["minimax", "elevenlabs"]


def relative_artifact(value: str) -> str:
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if (not value or value == "." or posix.is_absolute() or windows.drive
            or windows.root or ".." in posix.parts or ".." in windows.parts
            or ":" in value):
        raise ValueError("Artifact must be a job-relative path without traversal")
    return value.replace("\\", "/")


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Segment(Record):
    id: Positive
    start_ms: NonNegative
    end_ms: NonNegative
    raw_text: str
    polished_text: str | None = None
    translated_text: str | None = None
    tts_file: str | None = None
    tts_duration_ms: Positive | None = None
    final_start_ms: NonNegative | None = None
    final_end_ms: NonNegative | None = None

    @field_validator("tts_file")
    @classmethod
    def local_tts(cls, value):
        return relative_artifact(value) if value is not None else None

    @model_validator(mode="after")
    def valid_times(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must exceed start_ms")
        if (self.tts_file is None) != (self.tts_duration_ms is None):
            raise ValueError("TTS file and duration must be set together")
        if (self.final_start_ms is None) != (self.final_end_ms is None):
            raise ValueError("Final timestamps must be set together")
        if self.final_start_ms is not None and self.final_end_ms <= self.final_start_ms:
            raise ValueError("Final end must exceed final start")
        return self


class Voice(Record):
    provider: Provider
    voice_id: str = Field(min_length=1)
    voice_name: str = Field(min_length=1)
    language: str | None = None
    host: str | None = None
    display_index: Positive


class AlignmentSlot(Record):
    segment_id: Positive
    source_start_ms: NonNegative
    source_end_ms: Positive
    source_duration_ms: Positive
    tts_duration_ms: Positive
    target_ms: Positive
    audio_speed: float = Field(ge=1, allow_inf_nan=False)
    video_pts: float = Field(ge=1, allow_inf_nan=False)


class AlignmentPlan(Record):
    leading_ms: NonNegative
    slots: list[AlignmentSlot]


class Phase(str, Enum):
    CREATED = "CREATED"
    LANG_CONFIRMED = "LANG_CONFIRMED"
    TRANSCRIBED = "TRANSCRIBED"
    CLEANED = "CLEANED"
    POLISHED = "POLISHED"
    TRANSLATED = "TRANSLATED"
    WAITING_VOICE = "WAITING_VOICE"
    DUBBED = "DUBBED"
    ALIGNED = "ALIGNED"
    RENDERED = "RENDERED"
    DONE = "DONE"
    FAILED = "FAILED"


class Artifact(Record):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: Positive

    _path = field_validator("path")(relative_artifact)


class Manifest(Record):
    schema_version: Literal[1] = 1
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    input_video: str
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_language: str | None = None
    target_language: str | None = None
    detected_source_language: str | None = None
    phase: Phase = Phase.CREATED
    last_successful_phase: Phase = Phase.CREATED
    failed_stage: Phase | None = None
    # Only stable machine codes, never raw exception messages or HTTP bodies.
    error: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,79}$")
    tts_provider: Provider | None = None
    voice_id: str | None = None
    voice_name: str | None = None
    output_file: str | None = None
    output_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_snapshot: PublicConfig
    artifacts: dict[str, Artifact] = Field(default_factory=dict)

    @field_validator("input_video", "output_file")
    @classmethod
    def absolute_media_path(cls, value):
        if value is not None and not Path(value).is_absolute():
            raise ValueError("Media paths must be absolute")
        return value

    @model_validator(mode="after")
    def consistent_state(self):
        if self.last_successful_phase == Phase.FAILED:
            raise ValueError("Last successful phase cannot be FAILED")
        if self.phase == Phase.FAILED:
            if self.failed_stage is None or self.failed_stage == Phase.FAILED or not self.error:
                raise ValueError("FAILED requires failed stage and error code")
            order = tuple(phase for phase in Phase if phase != Phase.FAILED)
            legacy_polish_failure = (self.last_successful_phase == Phase.TRANSCRIBED
                                     and self.failed_stage == Phase.POLISHED)
            if (self.last_successful_phase == Phase.DONE
                    or (self.failed_stage != order[order.index(self.last_successful_phase) + 1]
                        and not legacy_polish_failure)):
                raise ValueError("Failure must refer to the next phase")
        elif (self.phase != self.last_successful_phase or self.failed_stage is not None
              or self.error is not None):
            raise ValueError("Successful state must match last_successful_phase and clear failure")
        if self.last_successful_phase != Phase.CREATED and (
                not self.source_language or not self.target_language or self.target_language == "auto"):
            raise ValueError("Confirmed state requires source and explicit target language")
        return self
