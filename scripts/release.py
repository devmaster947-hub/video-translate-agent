"""Whitelist source release and offline credential scan; never reads environment keys."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FILES = ('SKILL.md', 'README.md', 'LICENSE.md', 'THIRD_PARTY_NOTICES.md',
         'pyproject.toml', 'install.ps1', 'install.sh',
         'video_translate/vendor/sttn/LICENSE.md')
TREES = {'scripts': {'.py'}, 'video_translate': {'.py'}, 'agents': {'.yaml'},
         'config': {'.yaml', '.json'}, 'docs': {'.md'}, 'tests': {'.py'}}
FORBIDDEN = {'jobs', 'output', '.venv', '__pycache__', '.pytest_cache', '.git',
             '.env', 'node_modules', '.cache', 'models'}
MARKERS = ('sk-', 'MINIMAX_API_KEY=', 'ELEVENLABS_API_KEY=', 'LINGZHI_API_KEY=', 'LZSTUDIO_API_KEY=', 'Authorization:', 'Bearer ')
FAKES = {'a', 'b', 'first-secret', 'second-secret', 'sensitive-value-123',
         'never-persist-this-key', 'private-test-key', 'fixture-key', 'Bearer secret', 'secret', 'test'}
BINARY_FILES = ()


def payload(root):
    paths = [root / name for name in (*FILES, *BINARY_FILES)]
    for folder, extensions in TREES.items():
        assert (root / folder).is_dir(), folder
        paths += [p for p in (root / folder).rglob('*') if p.is_file()
                  and p.suffix in extensions and not FORBIDDEN.intersection(p.relative_to(root).parts)]
    for p in paths:
        assert p.is_file() and not p.is_symlink(), str(p)
        assert p.resolve().is_relative_to(root.resolve()), str(p)
    for name in ('ARCHITECTURE.md', 'PIPELINE.md', 'PYVIDEOTRANS_REFERENCE.md', 'VALIDATION.md'):
        assert root / 'docs' / name in paths, name
    return sorted(paths)


def scan(root):
    suspects, hits = set(), {}
    for path in payload(root):
        rel = path.relative_to(root).as_posix()
        if rel in BINARY_FILES:
            continue  # User-provided executables: byte integrity, not source-text scanning.
        content = path.read_text(encoding='utf-8-sig')
        found = [m for m in MARKERS if m in content]
        if found:
            hits[rel] = found
        # Scan literal key-like tokens regardless of file or test classification.
        if re.search(r'\bsk-[A-Za-z0-9_-]{16,}|\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.', content):
            suspects.add(rel)
        for line in content.splitlines():
            if re.match(r'\s*(?:MINIMAX|ELEVENLABS|LINGZHI|LZSTUDIO)_API_KEY\s*=', line):
                if line.split('=', 1)[1].strip():
                    suspects.add(rel)
            if re.search(r'Bearer\s+[A-Za-z0-9_.-]{16,}', line):
                suspects.add(rel)
            if re.match(r'\s*Authorization:\s*\S+', line):
                suspects.add(rel)
        if path.suffix == '.py':
            tree = ast.parse(content)
            # Detect literal secrets in mappings, assignments and keyword arguments.
            for node in ast.walk(tree):
                pairs = []
                if isinstance(node, ast.Dict):
                    pairs = [(k.value, v) for k, v in zip(node.keys, node.values)
                             if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                elif isinstance(node, ast.Assign):
                    pairs = [(ast.unparse(t), node.value) for t in node.targets]
                elif isinstance(node, ast.keyword):
                    pairs = [(node.arg or '', node.value)]
                for key, value in pairs:
                    if any(s in key.lower() for s in ('api_key', 'authorization', 'xi-api-key')):
                        if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.strip():
                            if not (rel.startswith('tests/') and value.value in FAKES):
                                suspects.add(rel)
    result = {'ok': not suspects, 'files_scanned': len([p for p in payload(root) if p.relative_to(root).as_posix() not in BINARY_FILES]),
              'binary_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in BINARY_FILES if (root / name).is_file()},
              'suspect_paths': sorted(suspects), 'marker_paths': hits,
              'scope': 'source text only; no environment values read; heuristic plus reviewed test fixtures'}
    if suspects:
        raise RuntimeError('Suspected credentials in: ' + ', '.join(sorted(suspects)))
    return result


def build():
    report = scan(ROOT)
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    staging = dist / 'staging-v2.3.3-marketplace' / 'video-translate-agent'
    # Fail instead of deleting an existing staging directory.
    staging.mkdir(parents=True, exist_ok=False)
    for source in payload(ROOT):
        target = staging / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    scan(staging)
    archive = dist / 'video-translate-agent-v2.3.3-marketplace.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as z:
        for p in payload(staging):
            z.write(p, 'video-translate-agent/' + p.relative_to(staging).as_posix())
    with tempfile.TemporaryDirectory(prefix='video-skill-zip-') as tmp:
        with zipfile.ZipFile(archive) as z:
            assert z.testzip() is None
            for name in z.namelist():
                assert not FORBIDDEN.intersection(Path(name).parts), name
                assert '..' not in Path(name).parts and not Path(name).is_absolute(), name
            z.extractall(tmp)
        extracted = Path(tmp) / 'video-translate-agent'
        scan(extracted)
        for source in payload(staging):
            assert source.read_bytes() == (extracted / source.relative_to(staging)).read_bytes()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.sha256').write_text(digest + '  ' + archive.name + '\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': digest, 'scan': report}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('scan', 'build'))
    args = parser.parse_args()
    if args.command == 'scan':
        print(json.dumps(scan(ROOT), ensure_ascii=False))
    else:
        build()
