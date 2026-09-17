"""Explicit configuration; relative paths are anchored to the project root."""
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from .credentials import read_credential

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class MediaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    timeout_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)


class ASRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str = "small"
    device: str = "auto"
    compute_type: str = "auto"
    beam_size: int = Field(default=5, ge=1)
    vad: bool = True
    word_timestamps: bool = True
    min_segment_ms: int = Field(default=1000, ge=0)
    max_segment_ms: int = Field(default=6000, ge=1)


class AlignmentConfig(BaseModel):
    """Execution-facing alignment config; stretch decisions live in the remote policy workflow."""
    model_config = ConfigDict(extra="forbid", frozen=True)


class SubtitleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    font: str = Field(default="Microsoft YaHei", pattern=r"^[^,\r\n]+$")
    font_size: float = Field(default=22.4, gt=0, allow_inf_nan=False)
    # Retained for compatibility with older manifests; rendering uses one fixed size.
    font_scale: float = Field(default=1.0, gt=0, le=3, allow_inf_nan=False)
    max_width_ratio: float = Field(default=0.88, gt=0, le=1, allow_inf_nan=False)
    background_alpha: int = Field(default=176, ge=0, le=255)
    margin_v: int = Field(default=24, ge=0)
    outline: float = Field(default=1.5, ge=0, allow_inf_nan=False)
    alignment: int = Field(default=2, ge=1, le=9)


class ManualWatermarkRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside_frame(self):
        if self.x + self.w > 1 or self.y + self.h > 1:
            raise ValueError("Manual watermark region must stay inside the frame")
        return self


class CleanupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    mode: Literal["local"] = "local"
    backend: Literal["auto", "sttn", "opencv"] = "auto"
    sttn_chunk_seconds: float = Field(default=2.5, gt=0, le=2.5, allow_inf_nan=False)
    sttn_timeout_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    sample_fps: float = Field(default=4.0, gt=0, allow_inf_nan=False)
    max_ocr_frames: int = Field(default=240, ge=1)
    detect_subtitles: bool = True
    detect_watermarks: bool = True
    min_ocr_confidence: float = Field(default=0.45, ge=0, le=1)
    mask_dilate_px_720p: int = Field(default=2, ge=0)
    mask_roi_padding_px_720p: int = Field(default=6, ge=0)
    inpaint_radius_px_720p: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    # Read legacy manifests without imposing a total cleanup deadline.
    max_runtime_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False,
                                            exclude=True, deprecated=True)
    manual_watermark_regions: list[ManualWatermarkRegion] = Field(default_factory=list)


class PublicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    runtime_root: Path
    media: MediaConfig = Field(default_factory=MediaConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    subtitles: SubtitleConfig = Field(default_factory=SubtitleConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    minimax_host: str = "https://api.minimaxi.com"
    minimax_model: str = "speech-02-turbo"
    elevenlabs_model: str = "eleven_multilingual_v2"

    @field_validator("minimax_model", "elevenlabs_model", mode="before")
    @classmethod
    def model_defaults(cls, value, info):
        return value or ("speech-02-turbo" if info.field_name=="minimax_model" else "eleven_multilingual_v2")

    @field_validator("minimax_host")
    @classmethod
    def https_origin(cls, value):
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("MINIMAX_API_HOST must be an HTTPS origin")
        return f"https://{parsed.netloc}"


class Config(PublicConfig):
    minimax_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    elevenlabs_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)

    def snapshot(self) -> PublicConfig:
        return PublicConfig.model_validate(self.model_dump())

    def configured_providers(self) -> list[str]:
        return [name for name, key in (("minimax", self.minimax_api_key),
                                       ("elevenlabs", self.elevenlabs_api_key)) if key]

def anchored_path(value: str | Path, base: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def load_config(path: str | Path | None = None, *, runtime_root=None, environ=None) -> Config:
    env = os.environ if environ is None else environ
    use_system_keyring = environ is None
    config_path = anchored_path(path or "config/default.yaml")
    with config_path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a YAML mapping")
    # Secrets belong only to environment variables; YAML accepts public fields.
    public = PublicConfig.model_validate({**data, "runtime_root": (
        runtime_root if runtime_root is not None else data.get("runtime_root", "."))})
    values = public.model_dump()
    values["runtime_root"] = anchored_path(public.runtime_root)
    for variable, field in (("MINIMAX_API_HOST", "minimax_host"),
                            ("MINIMAX_TTS_MODEL", "minimax_model"),
                            ("ELEVENLABS_MODEL_ID", "elevenlabs_model")):
        if env.get(variable, "").strip():
            values[field] = env[variable].strip()
    for provider, variable, field in (("minimax", "MINIMAX_API_KEY", "minimax_api_key"),
                                      ("elevenlabs", "ELEVENLABS_API_KEY", "elevenlabs_api_key")):
        environment_value = env.get(variable, "").strip()
        values[field] = environment_value or (
            read_credential(provider) if use_system_keyring else None
        )
    return Config.model_validate(values)
