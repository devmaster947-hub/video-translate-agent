"""Single-stage CLI; Agent supplies the two human choices and polish editing."""
import argparse
import json
from pydantic import ValidationError
import yaml

from .config import anchored_path, load_config
from .credentials import (CredentialError, PROVIDERS, CREDENTIAL_PROVIDERS, STORED_PROVIDERS, credential_storage_name,
                          delete_credential, read_credential, store_credential,
                          store_credential_from_stream)
from .credential_setup import CredentialSetupError, run_credential_setup
from .media import Media, MediaError
from .models import Phase
from .asr import ASRError
from .workflow import transcribe_job, clean_job, polish_job, local_translate_job, voices_job, dub_job, align_job, render_job
from .files import read_json
from .workflow import next_action, job_config
from .network import ProviderError
from .policy_client import PolicyError, capabilities as policy_capabilities
from .state import JobStore, StateError


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Local Agent video translation workflow")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("credential-setup", "credential-set", "credential-status", "credential-delete", "preflight", "create", "status", "transcribe", "clean", "validate-polish", "validate-translation", "voices", "dub", "align", "render"):
        command = commands.add_parser(name)
        command.add_argument("--json", action="store_true", help="JSON output (also the default)")
        if name in ("credential-set", "credential-delete"):
            command.add_argument("--provider", required=True, choices=CREDENTIAL_PROVIDERS)
        if name == "credential-set":
            command.add_argument("--stdin", action="store_true",
                                 help="read one API key from standard input")
        if name == "credential-setup":
            command.add_argument("--timeout-seconds", type=int, default=600)
            command.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
        if name.startswith("credential-"):
            continue
        command.add_argument("--config")
        command.add_argument("--runtime-root")
        if name in ("preflight", "voices"):
            command.add_argument("--provider", choices=PROVIDERS, default="elevenlabs")
        if name == "create":
            command.add_argument("--input", required=True)
            command.add_argument("--cleanup-mode", choices=("local",),
                                 help="local media cleanup (the only supported mode)")
        if name not in ("preflight", "create"):
            command.add_argument("--job", required=True)
        if name == "transcribe":
            command.add_argument("--source", required=True)
            command.add_argument("--target", required=True)
        if name == "dub":
            command.add_argument("--provider", choices=("minimax","elevenlabs"))
            command.add_argument("--voice-id")
    return root


def execute(args) -> tuple[dict, int]:
    if args.command == "credential-setup":
        return run_credential_setup(timeout_seconds=args.timeout_seconds,
                                    open_browser=not args.no_open), 0
    if args.command == "credential-set":
        if args.provider == "lzstudio":
            args.provider = "lingzhi"
        if args.stdin:
            store_credential_from_stream(args.provider)
        else:
            store_credential(args.provider)
        return {"provider": args.provider, "stored": True,
                "storage": credential_storage_name()}, 0
    if args.command == "credential-status":
        configured = [provider for provider in STORED_PROVIDERS if read_credential(provider, strict=True)]
        return {"stored_providers": configured, "storage": credential_storage_name()}, 0
    if args.command == "credential-delete":
        if args.provider == "lzstudio":
            args.provider = "lingzhi"
        deleted = delete_credential(args.provider)
        return {"provider": args.provider, "deleted": deleted,
                "storage": credential_storage_name()}, 0
    config = load_config(args.config, runtime_root=args.runtime_root)
    media = Media(config.media)
    store = JobStore(config.runtime_root)
    if args.command == "preflight":
        report = media.capabilities()
        providers = config.configured_providers()
        required_provider = args.provider
        if required_provider not in providers:
            report["errors"].append("ELEVENLABS_CREDENTIAL_REQUIRED" if required_provider == "elevenlabs"
                                    else "NO_TTS_PROVIDER_CONFIGURED")
        report.update(configured_providers=providers, provider_access_verified=False,
                      required_provider=required_provider,
                      credential_setup_command=("credential-setup" if required_provider == "elevenlabs"
                                                and required_provider not in providers else None),
                      optional_missing_key_variables=[variable for name, variable in (
                          ("minimax", "MINIMAX_API_KEY"), ("elevenlabs", "ELEVENLABS_API_KEY"))
                          if name not in providers],
                      implementation_phase=9, full_pipeline_ready=False)
        import importlib.util
        report["asr_dependency_available"]=bool(importlib.util.find_spec("faster_whisper"))
        if not report["asr_dependency_available"]:
            report["errors"].append("FASTER_WHISPER_UNAVAILABLE")
        for module, field, error in (("rapidocr", "rapidocr_dependency_available", "RAPIDOCR_UNAVAILABLE"),
                                     ("onnxruntime", "onnxruntime_dependency_available", "ONNXRUNTIME_UNAVAILABLE"),
                                     ("cv2", "opencv_dependency_available", "OPENCV_UNAVAILABLE")):
            report[field] = bool(importlib.util.find_spec(module))
            if not report[field]:
                report["errors"].append(error)
        policy = policy_capabilities()
        report.update({key: value for key, value in policy.items() if key != "policy_errors"})
        # A Lingzhi key is not needed for local setup, job creation, or
        # transcription. Keep reporting its presence, but defer the blocking
        # credential gate until the first policy-backed stage (`clean`).
        deferred_policy_errors = [
            error for error in policy["policy_errors"]
            if error == "LINGZHI_CREDENTIAL_REQUIRED"
        ]
        report["errors"].extend(
            error for error in policy["policy_errors"]
            if error != "LINGZHI_CREDENTIAL_REQUIRED"
        )
        report["deferred_policy_errors"] = deferred_policy_errors
        report["policy_credential_required_at"] = "clean"
        from .sttn import capabilities as sttn_capabilities
        report.update(sttn_capabilities())
        report["startup_ready"] = not report["errors"]
        report["full_pipeline_ready"] = not report["errors"] and not deferred_policy_errors
        return report, 1 if report["errors"] else 0
    if args.command == "create":
        source = anchored_path(args.input)
        info = media.require_video(source)
        if args.cleanup_mode:
            config = config.model_copy(update={"cleanup": config.cleanup.model_copy(update={"mode": args.cleanup_mode})})
        manifest = store.create(source, config.snapshot())
        return {"manifest": manifest.model_dump(mode="json"),
                "duration_ms": info["duration_ms"], "next_action": "CONFIRM_LANGUAGES",
                "next_stage_implemented": True}, 0
    # Persisted public settings own job execution; runtime credentials come from env/keyring.
    manifest=store.load(args.job)
    config=job_config(manifest,config)
    media=Media(config.media)
    if args.command == "transcribe":
        manifest = transcribe_job(store, args.job, config, media, args.source, args.target)
        return {"manifest": manifest.model_dump(mode="json")}, 0
    if args.command == "clean":
        manifest = clean_job(store, args.job, config, media)
        return {"manifest": manifest.model_dump(mode="json"),
                "cleanup_report": read_json(store.directory(args.job) / "cleanup_report.json")}, 0
    if args.command == "validate-polish":
        manifest = polish_job(store, args.job)
        return {"manifest": manifest.model_dump(mode="json")}, 0
    if args.command == "validate-translation":
        manifest = local_translate_job(store, args.job)
        return {"manifest": manifest.model_dump(mode="json")}, 0
    if args.command == "voices":
        manifest = voices_job(store, args.job, config, provider_name=args.provider)
        return {"manifest":manifest.model_dump(mode="json"), **read_json(store.directory(args.job) / "voices.json")}, 0
    if args.command == "dub":
        manifest = dub_job(store,args.job,config,media,args.provider,args.voice_id)
        return {"manifest":manifest.model_dump(mode="json")},0
    if args.command == "align":
        manifest=align_job(store,args.job,config,media)
        return {"manifest":manifest.model_dump(mode="json")},0
    if args.command == "render":
        manifest=render_job(store,args.job,config,media)
        return {"manifest":manifest.model_dump(mode="json")},0
    manifest = store.load(args.job)
    problems = store.verify(manifest)
    return {"manifest": manifest.model_dump(mode="json"), "integrity_errors": problems,
            "resume_from": manifest.last_successful_phase.value,
            "next_action": next_action(manifest),
            "next_stage_implemented": True}, 1 if problems else 0


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result, code = execute(args)
    except (ValidationError, StateError, CredentialError, CredentialSetupError, ValueError, yaml.YAMLError) as exc:
        # ValidationError may embed submitted secret values. Never stringify it.
        error = str(exc) if isinstance(exc, (CredentialError, CredentialSetupError)) else "INVALID_CONFIG_OR_STATE"
        result, code = {"error": error, "stage": args.command}, 2
    except (MediaError, ASRError, ProviderError, PolicyError) as exc:
        result, code = {"error": str(exc), "stage": args.command}, 1
    except OSError:
        result, code = {"error": "FILESYSTEM_ERROR", "stage": args.command}, 1
    except Exception as exc:
        from .cleanup import CleanupError
        if isinstance(exc, CleanupError):
            result, code = {"error": exc.code, "stage": args.command}, 1
        else:
            result, code = {"error":"UNEXPECTED_STAGE_FAILURE", "stage":args.command},1
    from .service_access import SERVICE_ERROR, SERVICE_MESSAGE
    if result.get("error") == SERVICE_ERROR or result.get("manifest", {}).get("error") == SERVICE_ERROR:
        result["message"] = SERVICE_MESSAGE
    print(json.dumps({"ok": code == 0, **result}, ensure_ascii=False))
    return code
