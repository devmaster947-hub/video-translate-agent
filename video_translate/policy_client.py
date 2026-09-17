"""Remote cleanup and alignment decisions via LZStudio; client validates and executes."""
from __future__ import annotations

import json
import hashlib
import os
import platform
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .config import PROJECT_ROOT
from .credentials import read_credential
from .models import AlignmentPlan
from .service_access import SERVICE_ERROR, service_blocked, sanitize_response

DEFAULT_WORKFLOW_ID = "VideoTranslatePolicyV1"
TERMINAL_SUCCESS = {"succeeded", "success", "completed", "complete"}
TERMINAL_FAILURE = {"failed", "error", "cancelled", "canceled"}
RUNNING = {"created", "pending", "queued", "running", "processing", "inprogress", "in_progress"}
CLI_MAX_BYTES = 12 * 1024 * 1024
CLI_ASSETS = {
    ("darwin", "arm64"): (
        "bin/macos/lzstudio",
        "https://github.com/devmaster947-hub/video-translate-agent/releases/download/v2.3.5/lzstudio-macos-arm64",
        "7af107fa2087782763fcfb7528aa8759326c9ca4b8a04c447b42fc55528b0e7d",
    ),
    ("darwin", "aarch64"): (
        "bin/macos/lzstudio",
        "https://github.com/devmaster947-hub/video-translate-agent/releases/download/v2.3.5/lzstudio-macos-arm64",
        "7af107fa2087782763fcfb7528aa8759326c9ca4b8a04c447b42fc55528b0e7d",
    ),
    ("windows", "amd64"): (
        "bin/windows/lzstudio.exe",
        "https://github.com/devmaster947-hub/video-translate-agent/releases/download/v2.3.5/lzstudio-windows-x64.exe",
        "f1c61d3fd5ec0ee5b6a58957494ff21cf220098e4350a4c2f89081f60bf55ab0",
    ),
    ("windows", "x86_64"): (
        "bin/windows/lzstudio.exe",
        "https://github.com/devmaster947-hub/video-translate-agent/releases/download/v2.3.5/lzstudio-windows-x64.exe",
        "f1c61d3fd5ec0ee5b6a58957494ff21cf220098e4350a4c2f89081f60bf55ab0",
    ),
}


class PolicyError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _candidate_binaries() -> list[Path]:
    candidates: list[Path] = []
    override = os.environ.get("LZSTUDIO_CLI", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    system = platform.system().lower()
    if system == "windows":
        candidates.append(PROJECT_ROOT / "bin" / "windows" / "lzstudio.exe")
    elif system == "darwin":
        candidates.append(PROJECT_ROOT / "bin" / "macos" / "lzstudio")
    for name in ("lzstudio.exe", "lzstudio"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    return candidates


def ensure_cli() -> Path | None:
    asset = CLI_ASSETS.get((platform.system().lower(), platform.machine().lower()))
    if asset is None:
        return None
    relative, url, expected_hash = asset
    target = PROJECT_ROOT / relative
    try:
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected_hash:
            if os.name != "nt":
                target.chmod(0o755)
            return target.resolve()
    except OSError:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".download")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "video-translate-agent/2.3.5"})
    try:
        total = 0
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > CLI_MAX_BYTES:
                    raise OSError("CLI download exceeds size limit")
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != expected_hash:
            raise OSError("CLI digest mismatch")
        if os.name != "nt":
            temporary.chmod(0o755)
        temporary.replace(target)
        return target.resolve()
    except OSError:
        temporary.unlink(missing_ok=True)
        return None


def find_cli() -> Path | None:
    for candidate in _candidate_binaries():
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return ensure_cli()


def _api_key() -> str:
    key = (os.environ.get("LINGZHI_API_KEY") or "").strip() or (os.environ.get("LZSTUDIO_API_KEY") or "").strip()
    if not key:
        key = read_credential("lingzhi", strict=True) or ""
    if not key:
        raise PolicyError("LINGZHI_CREDENTIAL_REQUIRED")
    return key


def _workflow_id() -> str:
    return os.environ.get("VIDEO_TRANSLATE_POLICY_WORKFLOW_ID", DEFAULT_WORKFLOW_ID).strip() or DEFAULT_WORKFLOW_ID


def capabilities() -> dict:
    cli = find_cli()
    try:
        has_key = bool(_api_key())
    except PolicyError:
        has_key = False
    return {
        "policy_cli_available": cli is not None,
        "policy_credential_available": has_key,
        "policy_workflow_id": _workflow_id(),
        "policy_errors": (["LZSTUDIO_CLI_UNAVAILABLE"] if cli is None else [])
                         + (["LINGZHI_CREDENTIAL_REQUIRED"] if not has_key else []),
    }


def _run_cli(arguments: list[str], *, timeout: float) -> dict:
    cli = find_cli()
    if cli is None:
        raise PolicyError("LZSTUDIO_CLI_UNAVAILABLE")
    key = _api_key()
    command = [str(cli), *arguments[:2], "--api-key", key, *arguments[2:]]
    try:
        completed = subprocess.run(command, shell=False, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PolicyError("POLICY_CLI_TIMEOUT") from None
    except OSError:
        raise PolicyError("POLICY_CLI_EXEC_FAILED") from None
    if service_blocked(completed.stdout) or service_blocked(getattr(completed, "stderr", "")):
        raise PolicyError(SERVICE_ERROR) from None
    if completed.returncode != 0:
        raise PolicyError("POLICY_CLI_FAILED")
    try:
        value = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        raise PolicyError("POLICY_CLI_INVALID_JSON") from None
    if not isinstance(value, dict):
        raise PolicyError("POLICY_CLI_INVALID_JSON")
    return sanitize_response(value)


def _submit(payload: dict, timeout: float, *, workflow_id: str | None = None) -> str:
    record = _run_cli(["task", "submit", "--workflow-id", workflow_id or _workflow_id(),
                       "--input", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))],
                      timeout=timeout)
    task_id = record.get("id") or record.get("taskId") or record.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise PolicyError("POLICY_TASK_ID_MISSING")
    return task_id.strip()


def _unwrap_output(value):
    # LZStudio returns the workflow result under output. Some workflow gateways
    # wrap a Respond-to-Webhook body once more; unwrap only well-known wrappers.
    for _ in range(4):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                break
        if not isinstance(value, dict):
            break
        if set(value).issubset({"body", "headers", "statusCode"}) and "body" in value:
            value = value["body"]
            continue
        if set(value).issubset({"result", "success", "errorMessage"}) and "result" in value:
            value = value["result"]
            continue
        break
    return value


def _poll(task_id: str, *, timeout: float, interval: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        record = _run_cli(["task", "fetch", "--id", task_id], timeout=min(30.0, max(1.0, timeout)))
        if service_blocked(record):
            raise PolicyError(SERVICE_ERROR)
        status = str(record.get("status") or "").replace(" ", "").lower()
        if status in TERMINAL_SUCCESS:
            output = _unwrap_output(record.get("output"))
            if not isinstance(output, dict):
                raise PolicyError("POLICY_OUTPUT_INVALID")
            if service_blocked(output):
                raise PolicyError(SERVICE_ERROR)
            return sanitize_response(output)
        if status in TERMINAL_FAILURE:
            raise PolicyError("REMOTE_POLICY_FAILED")
        if status and status not in RUNNING:
            raise PolicyError("POLICY_STATUS_UNKNOWN")
        if time.monotonic() >= deadline:
            raise PolicyError("POLICY_TIMEOUT")
        time.sleep(interval)


def request_task(payload: dict, *, workflow_id: str, timeout: float = 120.0, poll_interval: float = 0.5) -> dict:
    """Submit one arbitrary LZStudio workflow task, then fetch the same task until terminal."""
    task_id = _submit(payload, timeout=min(30.0, max(1.0, timeout)), workflow_id=workflow_id)
    return _poll(task_id, timeout=timeout, interval=poll_interval)


def request_policy(payload: dict, *, timeout: float = 120.0, poll_interval: float = 0.5) -> dict:
    """Submit exactly once to the policy workflow, then fetch the same task until terminal."""
    if payload.get('action') not in {'alignment_policy', 'cleanup_policy'}:
        raise PolicyError('UNSUPPORTED_POLICY_ACTION')
    return request_task(payload, workflow_id=_workflow_id(), timeout=timeout, poll_interval=poll_interval)


def _segment_payload(segments) -> list[dict]:
    return [{"id": int(s.id), "start_ms": int(s.start_ms), "end_ms": int(s.end_ms)} for s in segments]


def cleanup_policy_payload(*, job_id: str | None, video: dict, sample_count: int,
                           tracks: list[dict], segments, config) -> dict:
    compact_tracks = []
    for track in tracks:
        compact_tracks.append({
            "track_id": str(track["track_id"]),
            "text": str(track.get("text", "")),
            "start_ms": int(track["start_ms"]),
            "end_ms": int(track["end_ms"]),
            "bbox_norm": {name: float(track["bbox_norm"][name]) for name in ("x", "y", "w", "h")},
            "observation_count": len(track.get("observations", [])),
        })
    return {
        "schema_version": 1,
        "action": "cleanup_policy",
        "job_id": job_id,
        "video": {
            "width": int(video["width"]), "height": int(video["height"]),
            "duration_ms": int(video["duration_ms"]),
        },
        "sample_count": int(sample_count),
        "segments": _segment_payload(segments),
        "tracks": compact_tracks,
        "config": {
            "detect_subtitles": bool(config.detect_subtitles),
            "detect_watermarks": bool(config.detect_watermarks),
        },
    }


def merge_cleanup_policy(tracks: list[dict], response: dict) -> dict:
    if response.get("success") is not True:
        raise PolicyError("REMOTE_POLICY_FAILED")
    returned = response.get("tracks")
    layout = response.get("subtitle_layout")
    if not isinstance(returned, list) or not isinstance(layout, dict):
        raise PolicyError("POLICY_OUTPUT_INVALID")
    indexed = {str(item.get("track_id")): item for item in returned if isinstance(item, dict)}
    if len(returned) != len(tracks) or len(indexed) != len(tracks):
        raise PolicyError("POLICY_TRACK_COUNT_MISMATCH")
    allowed_kinds = {"subtitle", "watermark", "scene_text"}
    for track in tracks:
        item = indexed.get(str(track["track_id"]))
        if item is None or item.get("kind") not in allowed_kinds:
            raise PolicyError("POLICY_TRACK_INVALID")
        if not all(isinstance(item.get(k), bool) for k in ("layout_eligible", "cleanup_eligible")):
            raise PolicyError("POLICY_TRACK_INVALID")
        if item["kind"] == "scene_text" and item["cleanup_eligible"]:
            raise PolicyError("POLICY_TRACK_INVALID")
        track["kind"] = item["kind"]
        track["speech_overlap"] = round(float(item.get("speech_overlap", 0.0)), 4)
        track["persistence"] = round(float(item.get("persistence", 0.0)), 4)
        track["layout_eligible"] = bool(item.get("layout_eligible", False))
        track["cleanup_eligible"] = bool(item.get("cleanup_eligible", False))
        if item.get("protected_reason"):
            track["protected_reason"] = str(item["protected_reason"])
    return layout


def cached_policy(payload: dict, cache_path: Path | None = None, requester=None) -> dict:
    """Reuse a fingerprint-checked result or poll the previously submitted task."""
    from .files import read_json, write_json
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"), allow_nan=False).encode()
    digest = lambda value: hashlib.sha256(canonical(value)).hexdigest()
    input_hash = digest(payload)
    state = {}
    if cache_path is not None and cache_path.exists():
        state = read_json(cache_path)
        if state.get("input_fingerprint") != input_hash:
            raise PolicyError("POLICY_CACHE_INPUT_CHANGED")
        if "output" in state:
            if state.get("output_fingerprint") != digest(state["output"]):
                raise PolicyError("POLICY_CACHE_INTEGRITY_ERROR")
            return state["output"]
    if requester is not None:
        response = requester(payload)
    elif cache_path is None:
        response = request_policy(payload)
    else:
        task_id = state.get("task_id")
        if not task_id:
            if state.get("submission_pending"):
                # A crash/timeout may have occurred after the gateway accepted the task.
                # Recover the real task ID; never blindly submit again.
                raise PolicyError("POLICY_SUBMISSION_UNCERTAIN")
            state = {"input_fingerprint": input_hash, "submission_pending": True}
            write_json(cache_path, state)
            task_id = _submit(payload, timeout=30.0, workflow_id=_workflow_id())
            state = {"input_fingerprint": input_hash, "task_id": task_id}
            write_json(cache_path, state)
        response = _poll(task_id, timeout=120.0, interval=0.5)
    if service_blocked(response):
        raise PolicyError(SERVICE_ERROR)
    if response.get("success") is not True or response.get("action", payload["action"]) != payload["action"]:
        raise PolicyError("REMOTE_POLICY_FAILED")
    if cache_path is not None:
        write_json(cache_path, {**state, "input_fingerprint": input_hash,
                   "output": response, "output_fingerprint": digest(response),
                   "policy_version": response.get("policy_version")})
    return response


def resolve_cleanup_policy(*, job_id: str | None, video: dict, sample_count: int,
                           tracks: list[dict], segments, config,
                           requester: Callable[[dict], dict] | None = None,
                           cache_path: Path | None = None) -> dict:
    payload = cleanup_policy_payload(job_id=job_id, video=video, sample_count=sample_count,
                                     tracks=tracks, segments=segments, config=config)
    response = cached_policy(payload, cache_path, requester)
    return merge_cleanup_policy(tracks, response)


def alignment_policy_payload(*, job_id: str | None, segments, video_duration_ms: int, config) -> dict:
    return {
        "schema_version": 1,
        "action": "alignment_policy",
        "job_id": job_id,
        "video_duration_ms": int(video_duration_ms),
        "segments": [{"id": int(s.id), "start_ms": int(s.start_ms),
                      "tts_duration_ms": int(s.tts_duration_ms)} for s in segments],
    }


def resolve_alignment_policy(*, job_id: str | None, segments, video_duration_ms: int, config,
                             requester: Callable[[dict], dict] | None = None,
                             cache_path: Path | None = None) -> AlignmentPlan:
    payload = alignment_policy_payload(job_id=job_id, segments=segments,
                                       video_duration_ms=video_duration_ms, config=config)
    response = cached_policy(payload, cache_path, requester)
    if service_blocked(response):
        raise PolicyError(SERVICE_ERROR)
    if response.get("success") is False:
        raise PolicyError("REMOTE_POLICY_FAILED")
    plan_data = response.get("alignment_plan")
    if not isinstance(plan_data, dict):
        raise PolicyError("POLICY_OUTPUT_INVALID")
    try:
        plan = AlignmentPlan.model_validate(plan_data)
    except ValidationError:
        raise PolicyError("POLICY_ALIGNMENT_INVALID") from None
    if [slot.segment_id for slot in plan.slots] != [segment.id for segment in segments]:
        raise PolicyError("POLICY_ALIGNMENT_SEGMENT_MISMATCH")
    return plan
