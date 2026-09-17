"""ASS hard burn using a controlled basename and cwd; never map source audio."""
import os
import shutil
from pathlib import Path
from .media import MediaError
from .subtitles import write_subtitles
from .state import fingerprint
from .files import read_json


def verify_output(output, media, expected_ms):
    info=media.probe(output)
    videos=[s for s in info["streams"] if s.get("codec_type")=="video"]
    audios=[s for s in info["streams"] if s.get("codec_type")=="audio"]
    if len(info["streams"])!=2 or len(videos)!=1 or len(audios)!=1:
        raise MediaError("INVALID_OUTPUT_STREAMS")
    if videos[0].get("codec_name")!="h264" or audios[0].get("codec_name")!="aac":
        raise MediaError("INVALID_OUTPUT_CODECS")
    if abs(info["duration_ms"]-expected_ms)>100:
        raise MediaError("OUTPUT_DURATION_MISMATCH")
    media.run(media.config.ffmpeg,["-v","error","-xerror","-nostdin","-i",str(output),"-f","null","-"])
    return info


def render_video(manifest, segments, directory, media):
    video=directory/"aligned/video.mp4"
    audio=directory/"target.wav"
    info=media.require_video(video)
    stream=next(s for s in info["streams"] if s.get("codec_type")=="video")
    layout_path=directory/"subtitle_layout.json"
    layout=read_json(layout_path) if layout_path.exists() else None
    write_subtitles(directory,segments,manifest.config_snapshot.subtitles,stream["width"],stream["height"],layout=layout)
    render_dir=directory/"render"
    render_dir.mkdir(exist_ok=True)
    shutil.copyfile(directory/"target.ass",render_dir/"target.ass")
    build=render_dir/"output.mp4"
    media.run(media.config.ffmpeg,["-v","error","-nostdin","-y","-i",str(video),"-i",str(audio),
        "-filter_complex","[0:v]ass=filename=target.ass[v]","-map","[v]","-map","1:a:0",
        "-c:v","libx264","-preset","veryfast","-crf","18","-pix_fmt","yuv420p",
        "-c:a","aac","-ar","48000","-ac","2","-movflags","+faststart","output.mp4"],cwd=render_dir)
    # Probe before publication; full decode is also repeated in DONE verification.
    verify_output(build,media,info["duration_ms"])
    output_root=manifest.config_snapshot.runtime_root/"output"
    name=f"{Path(manifest.input_video).stem}_{manifest.target_language}_dubbed.mp4"
    output=output_root/name
    output_root.mkdir(parents=True,exist_ok=True)
    if output.exists():
        output=output_root/manifest.job_id/name
        output.parent.mkdir(parents=True,exist_ok=True)
    if output.resolve()==Path(manifest.input_video).resolve():
        raise MediaError("SOURCE_OVERWRITE_FORBIDDEN")
    if output.exists():
        # A publication may have succeeded before a crash. Only reuse identical bytes.
        if fingerprint(output)!=fingerprint(build):
            raise MediaError("OUTPUT_ALREADY_EXISTS")
    else:
        os.link(build,output)
    return output
