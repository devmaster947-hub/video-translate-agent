import pytest

from video_translate.config import Config, MediaConfig
from video_translate.media import Media
from video_translate.state import JobStore


@pytest.fixture
def config(tmp_path):
    return Config(runtime_root=tmp_path.resolve())


@pytest.fixture
def job(config):
    source = config.runtime_root / "source.mp4"
    source.write_bytes(b"state fixture: media validity is checked at CLI boundary")
    store = JobStore(config.runtime_root)
    return store, store.create(source, config.snapshot())


@pytest.fixture(scope="session")
def real_media():
    # Required integration coverage: missing tools fail rather than silently skip.
    return Media(MediaConfig())


@pytest.fixture
def source_video(tmp_path, real_media):
    directory = tmp_path / "中文 路径 ' 测试"
    directory.mkdir()
    output = directory / "原视频 两条声音.mp4"
    real_media.run(real_media.config.ffmpeg, [
        "-hide_banner", "-nostdin", "-v", "error", "-n",
        "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=25:d=0.6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.6",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=0.6",
        "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(output)])
    return output
