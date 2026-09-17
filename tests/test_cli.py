import json
from io import StringIO
import os
import subprocess
import sys

import pytest

from video_translate.cli import main, parser
from video_translate.config import PROJECT_ROOT
from video_translate.credentials import credential_storage_name
from video_translate import credentials


@pytest.fixture(autouse=True)
def empty_system_keyring(monkeypatch):
    monkeypatch.setattr("video_translate.config.read_credential", lambda provider: None)
    monkeypatch.setattr("video_translate.cli.policy_capabilities", lambda: {
        "policy_cli_available": True, "policy_credential_available": True,
        "policy_workflow_id": "VideoTranslatePolicyV1", "policy_errors": []})


@pytest.mark.parametrize("providers,requested,exit_code", [
    ([], "elevenlabs", 1), (["MINIMAX_API_KEY"], "elevenlabs", 1),
    (["ELEVENLABS_API_KEY"], "elevenlabs", 0),
    (["MINIMAX_API_KEY"], "minimax", 0),
    (["MINIMAX_API_KEY", "ELEVENLABS_API_KEY"], "elevenlabs", 0),
])
def test_preflight_requires_selected_provider(monkeypatch, capsys, providers, requested, exit_code):
    for name in ("MINIMAX_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name in providers:
        monkeypatch.setenv(name, "private-preflight-key")
    monkeypatch.setattr("video_translate.cli.Media.capabilities", lambda self: {"capabilities": {}, "errors": []})
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    assert main(["preflight", "--provider", requested, "--json"]) == exit_code
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert "private-preflight-key" not in raw
    assert len(result["configured_providers"]) == len(providers)
    assert result["required_provider"] == requested
    assert result["provider_access_verified"] is False
    assert result["full_pipeline_ready"] is (exit_code == 0)
    if requested == "elevenlabs" and "ELEVENLABS_API_KEY" not in providers:
        assert result["errors"] == ["ELEVENLABS_CREDENTIAL_REQUIRED"]
        assert result["credential_setup_command"] == "credential-setup"


def test_validation_errors_hide_secret_input(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("minimax_api_key: unique-secret-value\n", encoding="utf-8")
    assert main(["preflight", "--config", str(bad)]) == 2
    assert "unique-secret-value" not in capsys.readouterr().out


def test_invalid_runtime_root_has_correct_error_code(tmp_path, capsys):
    bad = tmp_path / "bad-path.yaml"
    bad.write_text("runtime_root: [invalid, path]\n", encoding="utf-8")
    assert main(["preflight", "--config", str(bad)]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "INVALID_CONFIG_OR_STATE"


def test_transcribe_requires_language_choices():
    with pytest.raises(SystemExit) as error:
        main(["transcribe", "--job", "abc"])
    assert error.value.code == 2


def test_preflight_reports_cleanup_dependencies(monkeypatch, capsys):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "private-preflight-key")
    monkeypatch.setattr("video_translate.cli.Media.capabilities", lambda self: {"capabilities": {}, "errors": []})
    available = {"faster_whisper", "cv2"}
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object() if name in available else None)
    assert main(["preflight", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["errors"] == ["RAPIDOCR_UNAVAILABLE", "ONNXRUNTIME_UNAVAILABLE"]


def test_credential_status_never_outputs_values(monkeypatch, capsys):
    monkeypatch.setattr("video_translate.cli.read_credential",
                        lambda provider, strict=False: "hidden-value" if provider == "elevenlabs" else None)
    assert main(["credential-status", "--json"]) == 0
    raw = capsys.readouterr().out
    assert "hidden-value" not in raw
    assert json.loads(raw)["stored_providers"] == ["elevenlabs"]


def test_credential_set_uses_secure_store(monkeypatch, capsys):
    called = []
    monkeypatch.setattr("video_translate.cli.store_credential", called.append)
    assert main(["credential-set", "--provider", "elevenlabs"]) == 0
    assert called == ["elevenlabs"]
    assert json.loads(capsys.readouterr().out)["stored"] is True


def test_credential_set_stdin_uses_stream_store_without_echo(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("video_translate.cli.store_credential_from_stream", calls.append)
    monkeypatch.setattr("video_translate.cli.store_credential",
                        lambda _provider: pytest.fail("hidden prompt must not be used"))
    assert main(["credential-set", "--provider", "elevenlabs", "--stdin", "--json"]) == 0
    raw = capsys.readouterr().out
    assert calls == ["elevenlabs"]
    assert json.loads(raw) == {"ok": True, "provider": "elevenlabs", "stored": True,
                               "storage": credential_storage_name()}


def test_credential_set_stdin_reads_and_never_echoes_secret(monkeypatch, capsys):
    saved = []
    secret = "sensitive-value-123"
    monkeypatch.setattr(credentials, "_uses_local_user_file", lambda: False)
    monkeypatch.setattr(credentials.keyring, "set_password",
                        lambda service, provider, value: saved.append((service, provider, value)))
    monkeypatch.setattr(sys, "stdin", StringIO(secret + "\n"))
    assert main(["credential-set", "--provider", "elevenlabs", "--stdin", "--json"]) == 0
    raw = capsys.readouterr().out
    assert secret not in raw
    assert saved == [(credentials.SERVICE_NAME, "elevenlabs", secret)]


def test_credential_set_has_no_command_line_secret_argument():
    options = parser()._subparsers._group_actions[0].choices["credential-set"]._option_string_actions
    assert "--stdin" in options
    assert "--api-key" not in options
    assert "--key" not in options


def test_credential_setup_cli_returns_secret_free_result(monkeypatch, capsys):
    monkeypatch.setattr("video_translate.cli.run_credential_setup", lambda **kwargs: {
        "provider": "elevenlabs", "stored": True,
        "access_verified": True, "storage": "system-keyring",
    })
    assert main(["credential-setup", "--timeout-seconds", "30", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"ok": True, "provider": "elevenlabs", "stored": True,
                      "access_verified": True, "storage": "system-keyring"}


def test_preflight_and_voices_default_to_elevenlabs():
    assert parser().parse_args(["preflight"]).provider == "elevenlabs"
    assert parser().parse_args(["voices", "--job", "job-id"]).provider == "elevenlabs"


@pytest.mark.media
def test_create_status_from_unrelated_cwd(source_video, tmp_path):
    entry = PROJECT_ROOT / "scripts" / "video_translate.py"
    env = os.environ.copy()
    env["MINIMAX_API_KEY"] = "never-persist-this-key"
    command = [sys.executable, str(entry)]
    created = subprocess.run(command + ["create", "--input", str(source_video),
        "--runtime-root", str(tmp_path), "--json"], cwd=source_video.parent,
        env=env, shell=False, capture_output=True, text=True, encoding="utf-8", check=True)
    record = json.loads(created.stdout)
    job_id = record["manifest"]["job_id"]
    checked = subprocess.run(command + ["status", "--job", job_id,
        "--runtime-root", str(tmp_path)], cwd=tmp_path, shell=False, capture_output=True,
        text=True, encoding="utf-8", check=True)
    assert json.loads(checked.stdout)["integrity_errors"] == []
    assert "never-persist-this-key" not in created.stdout + checked.stdout
    source_video.write_bytes(b"changed after creation")
    assert main(["status", "--job", job_id, "--runtime-root", str(tmp_path)]) == 1




def test_create_accepts_local_cleanup_only():
    import pytest
    args = parser().parse_args(["create", "--input", "video.mp4", "--cleanup-mode", "local"])
    assert args.cleanup_mode == "local"
    with pytest.raises(SystemExit):
        parser().parse_args(["create", "--input", "video.mp4", "--cleanup-mode", "remote"])


def test_cleanup_config_rejects_remote_mode():
    import pytest
    from pydantic import ValidationError
    from video_translate.config import CleanupConfig
    assert CleanupConfig().mode == "local"
    with pytest.raises(ValidationError):
        CleanupConfig(mode="remote")
