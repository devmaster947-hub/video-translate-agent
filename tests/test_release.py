"""Release scanner rejects planted credentials without disclosing their values."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.mark.parametrize('kind', ['environment', 'token', 'authorization', 'python'])
def test_scanner_rejects_synthetic_secret(tmp_path, monkeypatch, kind):
    synthetic = 'sk-' + 'SYNTHETICRELEASETEST' * 2
    examples = {
        'environment': 'MINIMAX_API_KEY=' + synthetic,
        'token': synthetic,
        'authorization': 'Authorization: Bearer ' + synthetic,
        'python': 'settings = {"ELEVENLABS_API_KEY": ' + repr(synthetic) + '}',
    }
    path = tmp_path / ('fixture.py' if kind == 'python' else 'fixture.txt')
    path.write_text(examples[kind], encoding='utf-8')
    monkeypatch.setattr(release, 'payload', lambda root: [path])
    with pytest.raises(RuntimeError) as error:
        release.scan(tmp_path)
    assert path.name in str(error.value)
    assert synthetic not in str(error.value)


def test_release_payload_excludes_runtime_and_scan_passes():
    paths = release.payload(release.ROOT)
    assert release.ROOT / 'install.sh' in paths
    assert all(not release.FORBIDDEN.intersection(p.relative_to(release.ROOT).parts) for p in paths)
    assert release.scan(release.ROOT)['ok']


def test_release_uses_external_platform_cli_assets():
    paths = release.payload(release.ROOT)
    assert release.ROOT / 'bin/macos/lzstudio' not in paths
    assert release.ROOT / 'bin/windows/lzstudio.exe' not in paths
