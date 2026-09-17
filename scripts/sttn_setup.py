"""Optional local GPU dependency setup; no TTS credentials required."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from video_translate.sttn import capabilities, ensure_weights, setup_dependencies
if __name__ == "__main__":
    caps=capabilities()
    if caps["sttn_hardware_hint"] and not caps["sttn_devices"]:
        setup_dependencies()
    caps=capabilities()
    if caps["sttn_devices"] and not caps["sttn_weights_available"]:
        ensure_weights()
    print(json.dumps(capabilities()))
