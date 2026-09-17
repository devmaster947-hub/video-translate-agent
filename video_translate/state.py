"""Atomic manifest storage and OS-released single-writer job locks."""
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
import threading

from .config import PublicConfig
from .models import Artifact, Manifest, Phase, relative_artifact

ORDER = tuple(phase for phase in Phase if phase != Phase.FAILED)


class StateError(ValueError):
    pass


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_manifest(path: Path, manifest: Manifest) -> None:
    # Revalidate even if a caller used model_copy(update=...), which bypasses validation.
    validated = Manifest.model_validate(manifest.model_dump(mode="json"))
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=".manifest-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(validated.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class JobStore:
    def __init__(self, runtime_root: Path):
        if not runtime_root.is_absolute():
            raise StateError("Runtime root must be absolute")
        self.root = runtime_root.resolve() / "jobs"
        self._held = threading.local()

    def directory(self, job_id: str) -> Path:
        import re
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise StateError("Invalid job ID")
        path = (self.root / job_id).resolve()
        if path.parent != self.root.resolve():
            raise StateError("Job path escapes runtime root")
        return path

    @contextmanager
    def lock(self, job_id: str):
        directory = self.directory(job_id)
        if not directory.is_dir():
            raise StateError("Job does not exist")
        with (directory / ".lock").open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StateError("Job is being written by another command") from exc
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    @contextmanager
    def execution(self, job_id):
        """Hold the same OS lock across media/API work and state publication."""
        with self.lock(job_id):
            self._held.job = job_id
            try:
                yield
            finally:
                self._held.job = None

    @contextmanager
    def write_lock(self, job_id):
        if getattr(self._held, "job", None) == job_id:
            yield
        else:
            with self.lock(job_id):
                yield

    def create(self, input_video: Path, config: PublicConfig) -> Manifest:
        if not input_video.is_absolute() or not input_video.is_file():
            raise StateError("Input must be an existing absolute file")
        if input_video.stat().st_size == 0:
            raise StateError("Input file is empty")
        digest = fingerprint(input_video)
        job_id = uuid4().hex
        directory = self.directory(job_id)
        directory.mkdir(parents=True, exist_ok=False)
        manifest = Manifest(job_id=job_id, input_video=str(input_video.resolve()),
                            input_fingerprint=digest, config_snapshot=config)
        with self.lock(job_id):
            atomic_manifest(directory / "manifest.json", manifest)
        return manifest

    def load(self, job_id: str) -> Manifest:
        manifest = Manifest.model_validate_json(
            (self.directory(job_id) / "manifest.json").read_text(encoding="utf-8"))
        if manifest.job_id != job_id:
            raise StateError("Manifest job ID does not match directory")
        return manifest

    def artifact_path(self, job_id: str, relative: str) -> Path:
        directory = self.directory(job_id)
        path = (directory / relative_artifact(relative)).resolve()
        if not path.is_relative_to(directory):
            raise StateError("Artifact escapes job directory")
        return path

    def record_artifact(self, job_id: str, name: str, relative: str) -> Manifest:
        with self.write_lock(job_id):
            manifest = self.load(job_id)
            path = self.artifact_path(job_id, relative)
            artifact = Artifact(path=relative, sha256=fingerprint(path), size_bytes=path.stat().st_size)
            data = manifest.model_dump(mode="json")
            data["artifacts"][name] = artifact.model_dump()
            updated = Manifest.model_validate(data)
            atomic_manifest(self.directory(job_id) / "manifest.json", updated)
            return updated

    def transition(self, job_id: str, target: Phase, **updates) -> Manifest:
        with self.write_lock(job_id):
            current = self.load(job_id)
            previous = current.last_successful_phase
            if previous == Phase.DONE or target == Phase.FAILED:
                raise StateError("Invalid successful transition")
            expected = ORDER[ORDER.index(previous) + 1]
            legacy_polish_failure = (current.phase == Phase.FAILED
                                     and current.last_successful_phase == Phase.TRANSCRIBED
                                     and current.failed_stage == Phase.POLISHED
                                     and target == Phase.CLEANED)
            if target != expected or (current.phase == Phase.FAILED
                                      and current.failed_stage != target and not legacy_polish_failure):
                raise StateError("Only the next phase or the failed phase may complete")
            allowed = {"source_language", "target_language", "detected_source_language",
                       "tts_provider", "voice_id", "voice_name", "output_file", "output_fingerprint", "artifacts"}
            if set(updates) - allowed:
                raise StateError("Cannot modify immutable manifest fields")
            for field in ("source_language", "target_language", "tts_provider", "voice_id", "voice_name"):
                value = getattr(current, field)
                if value is not None and field in updates and updates[field] != value:
                    raise StateError("Confirmed choices cannot be changed in place")
            data = current.model_dump(mode="json")
            data.update(updates, phase=target, last_successful_phase=target,
                        failed_stage=None, error=None)
            updated = Manifest.model_validate(data)
            atomic_manifest(self.directory(job_id) / "manifest.json", updated)
            return updated

    def fail(self, job_id: str, stage: Phase, code: str) -> Manifest:
        with self.write_lock(job_id):
            current = self.load(job_id)
            previous = current.last_successful_phase
            if previous == Phase.DONE or stage != ORDER[ORDER.index(previous) + 1]:
                raise StateError("Failure must belong to the next phase")
            data = current.model_dump(mode="json")
            data.update(phase=Phase.FAILED, failed_stage=stage, error=code)
            updated = Manifest.model_validate(data)
            atomic_manifest(self.directory(job_id) / "manifest.json", updated)
            return updated

    def choose_voice(self, job_id, provider, voice_id, voice_name):
        with self.write_lock(job_id):
            current = self.load(job_id)
            if current.last_successful_phase != Phase.WAITING_VOICE:
                raise StateError("Voice may only be chosen at WAITING_VOICE")
            if current.voice_id and (current.voice_id != voice_id or current.tts_provider != provider):
                raise StateError("Voice is already fixed")
            updated = Manifest.model_validate(current.model_dump() | {
                "tts_provider":provider,"voice_id":voice_id,"voice_name":voice_name})
            atomic_manifest(self.directory(job_id) / "manifest.json", updated)
            return updated

    def verify(self, manifest: Manifest) -> list[str]:
        problems = []
        source = Path(manifest.input_video)
        if not source.is_file() or fingerprint(source) != manifest.input_fingerprint:
            problems.append("INPUT_CHANGED_OR_MISSING")
        for name, artifact in manifest.artifacts.items():
            path = self.artifact_path(manifest.job_id, artifact.path)
            if (not path.is_file() or path.stat().st_size != artifact.size_bytes
                    or fingerprint(path) != artifact.sha256):
                problems.append(f"ARTIFACT_CHANGED_OR_MISSING:{name}")
        if manifest.output_fingerprint:
            path = Path(manifest.output_file)
            if not path.is_file() or fingerprint(path) != manifest.output_fingerprint:
                problems.append("OUTPUT_CHANGED_OR_MISSING")
        return problems
