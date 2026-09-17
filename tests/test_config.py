import pytest
from pydantic import ValidationError

from video_translate.config import PROJECT_ROOT, load_config


@pytest.mark.parametrize("env,expected", [({}, []), ({"MINIMAX_API_KEY": "first-secret"}, ["minimax"]),
    ({"ELEVENLABS_API_KEY": "second-secret"}, ["elevenlabs"]),
    ({"MINIMAX_API_KEY": "a", "ELEVENLABS_API_KEY": "b"}, ["minimax", "elevenlabs"]),
    ({"MINIMAX_API_KEY": "  "}, [])])
def test_provider_configuration(env, expected):
    config = load_config(environ=env)
    assert config.configured_providers() == expected


def test_secrets_not_in_snapshot_or_repr():
    config = load_config(environ={"MINIMAX_API_KEY": "sensitive-value-123"})
    assert "sensitive-value-123" not in config.model_dump_json() + repr(config) + config.snapshot().model_dump_json()


def test_cwd_independent_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    config = load_config(environ={})
    assert config.runtime_root == PROJECT_ROOT


def test_explicit_root_and_host(tmp_path):
    config = load_config(runtime_root=tmp_path, environ={"MINIMAX_API_HOST": "api.minimax.io/"})
    assert config.runtime_root == tmp_path
    assert config.minimax_host == "https://api.minimax.io"


@pytest.mark.parametrize("host", ["http://api.minimax.io", "https://user:password@api.minimax.io", "https://api.minimax.io/v1"])
def test_invalid_host(host):
    with pytest.raises(ValidationError):
        load_config(environ={"MINIMAX_API_HOST": host})


def test_yaml_cannot_contain_secrets(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("minimax_api_key: never-store-this\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path, environ={})
