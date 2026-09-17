#!/bin/sh
set -eu

skill_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_command=${PYTHON:-python3}
if [ -f "$skill_root/bin/macos/lzstudio" ]; then
  chmod u+x "$skill_root/bin/macos/lzstudio"
fi

"$python_command" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo 'Install Python 3.10+ and add python3 to PATH.' >&2
  exit 1
}
command -v ffmpeg >/dev/null 2>&1 || { echo 'Install ffmpeg and add it to PATH.' >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo 'Install ffprobe and add it to PATH.' >&2; exit 1; }

"$python_command" -m venv "$skill_root/.venv"
venv_python="$skill_root/.venv/bin/python"
"$venv_python" -m pip install -e "$skill_root[test]"

"$venv_python" "$skill_root/scripts/sttn_setup.py"

if ! "$venv_python" "$skill_root/scripts/video_translate.py" credential-status --json | grep -q 'elevenlabs'; then
  echo 'Opening the secure ElevenLabs setup wizard. The key is stored in a private local user file (mode 600).'
  "$venv_python" "$skill_root/scripts/video_translate.py" credential-setup --json || \
    echo 'Credential setup was not completed. It will open again when video translation starts.' >&2
fi

"$venv_python" "$skill_root/scripts/video_translate.py" preflight --json || {
  echo 'Installation completed, but preflight still reports an item that needs attention.' >&2
  exit 0
}
