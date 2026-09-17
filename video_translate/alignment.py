"""Both alignment, adapted from pyVideoTrans _rate.py ad0b8bd8 (GPL-3.0).

Execution deliberately preserves entire audio and uses measured media boundaries;
no upstream silent placeholders, skipped clips, empirical PTS offsets or truncation.
"""
import math
import os
import shutil
import wave
from fractions import Fraction
from pathlib import Path

from .config import AlignmentConfig
from .files import write_json
from .media import MediaError
from .models import Segment

RATE = 48000


def atempo_chain(speed):
    if not math.isfinite(speed) or speed < 1:
        raise ValueError("Audio speed must be finite and at least one")
    factors=[]
    while speed > 2:
        factors.append("atempo=2")
        speed /= 2
    factors.append(f"atempo={speed:.12g}")
    return ",".join(factors)


def wav_frames(path):
    with wave.open(str(path),"rb") as wav:
        if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth(),wav.getcomptype()) != (RATE,2,2,"NONE"):
            raise MediaError("INVALID_ALIGNMENT_WAV")
        frames=wav.getnframes()
        if frames <= 0:
            raise MediaError("EMPTY_ALIGNMENT_WAV")
        return frames


def video_seconds(info):
    video=next(s for s in info["streams"] if s.get("codec_type")=="video")
    if video.get("duration_ts") is not None and video.get("time_base"):
        return Fraction(video["duration_ts"])*Fraction(video["time_base"])
    return Fraction(str(video.get("duration",info["duration_ms"]/1000)))


def stretch(source, output, speed, media):
    if speed <= 1:
        shutil.copyfile(source,output)
        return "copy"
    if shutil.which("rubberband"):
        intermediate=output.with_name(output.stem+"-rb.wav")
        try:
            media.run("rubberband",["--time",f"{1/speed:.12g}",str(source),str(intermediate)])
            media.normalize_audio(intermediate,output)
            wav_frames(output)
            return "rubberband"
        except (MediaError, OSError):
            output.unlink(missing_ok=True)
        finally:
            intermediate.unlink(missing_ok=True)
    media.run(media.config.ffmpeg,["-v","error","-nostdin","-y","-i",str(source),
        "-af",atempo_chain(speed),"-ar",str(RATE),"-ac","2","-c:a","pcm_s16le",str(output)])
    wav_frames(output)
    return "atempo"


def cut_video(source, output, start_ms, source_ms, target_seconds, fps, media):
    # Quantize each output slot to full frames and measure the encoded result.
    frames=max(1,math.ceil(target_seconds*fps))
    quantized=Fraction(frames,1)/fps
    pts=max(1,float(target_seconds)/(source_ms/1000))
    filters=f"setpts={pts:.12g}*(PTS-STARTPTS),fps={fps},tpad=stop_mode=clone:stop_duration={float(quantized):.9f}"
    media.run(media.config.ffmpeg,["-v","error","-nostdin","-y","-ss",f"{start_ms/1000:.6f}",
        "-t",f"{source_ms/1000:.6f}","-i",str(source),"-map","0:v:0","-an","-sn","-dn",
        "-vf",filters,"-frames:v",str(frames),"-c:v","libx264","-preset","veryfast",
        "-crf","18","-pix_fmt","yuv420p","-bf","0","-video_track_timescale","90000",str(output)])
    info=media.require_video(output)
    actual=video_seconds(info)
    if actual < target_seconds or abs(actual-quantized) > Fraction(1,1000):
        raise MediaError("VIDEO_SLOT_DURATION_MISMATCH")
    return actual


def _silence(writer, frames):
    while frames:
        count=min(frames,RATE)
        writer.writeframesraw(b"\0"*(count*4))
        frames-=count


def align_segments(segments, source: Path, directory: Path, media, config=AlignmentConfig(), *, job_id=None, policy_requester=None):
    info=media.require_video(source)
    duration=round(video_seconds(info)*1000)
    # Always remeasure originals; metadata alone is not sufficient proof of TTS duration.
    measured=[Segment.model_validate(s.model_dump() | {"tts_duration_ms":round(wav_frames(directory/s.tts_file)*1000/RATE)})
              for s in segments]
    from .policy_client import resolve_alignment_policy
    kwargs = {} if policy_requester is None else {"requester": policy_requester}
    planner = resolve_alignment_policy(job_id=job_id, segments=measured,
        video_duration_ms=duration, config=config,
        cache_path=directory / "alignment_policy_result.json", **kwargs)
    write_json(directory/"alignment_plan.json",planner.model_dump())
    aligned=directory/"aligned"
    aligned.mkdir(exist_ok=True)
    video_stream=next(s for s in info["streams"] if s.get("codec_type")=="video")
    fps=Fraction(video_stream.get("avg_frame_rate") or "30")
    if fps <= 0:
        fps=Fraction(30)
    clips=[]
    audio_parts=[]
    outcomes=[]
    cursor=0
    if planner.leading_ms:
        clip=aligned/"head.mp4"
        actual=cut_video(source,clip,0,planner.leading_ms,Fraction(planner.leading_ms,1000),fps,media)
        cursor=round(actual*RATE)
        clips.append(clip)
        audio_parts.append((None,cursor))
    result=[]
    for item, slot in zip(measured,planner.slots):
        audio=aligned/f"audio_{item.id:04d}.wav"
        engine=stretch(directory/item.tts_file,audio,slot.audio_speed,media)
        samples=wav_frames(audio)
        required=max(Fraction(slot.target_ms,1000),Fraction(samples,RATE))
        clip=aligned/f"video_{item.id:04d}.mp4"
        actual=cut_video(source,clip,slot.source_start_ms,slot.source_duration_ms,required,fps,media)
        slot_samples=round(actual*RATE)
        if slot_samples < samples:
            raise MediaError("AUDIO_EXCEEDS_VIDEO_SLOT")
        result.append(Segment.model_validate(item.model_dump() | {
            "final_start_ms":round(cursor*1000/RATE),"final_end_ms":round((cursor+samples)*1000/RATE)}))
        outcomes.append({"segment_id":item.id,"audio_engine":engine,"audio_samples":samples,
                         "slot_samples":slot_samples,"start_sample":cursor,
                         "actual_video_seconds":float(actual),"planned_ms":slot.target_ms})
        cursor+=slot_samples
        audio_parts.append((audio,slot_samples))
        clips.append(clip)
    temporary=aligned/"target-building.wav"
    with wave.open(str(temporary),"wb") as writer:
        writer.setparams((2,2,RATE,0,"NONE","not compressed"))
        for audio, samples in audio_parts:
            used=0
            if audio:
                with wave.open(str(audio),"rb") as reader:
                    while block:=reader.readframes(RATE):
                        writer.writeframesraw(block)
                        used+=len(block)//4
            if used > samples:
                raise MediaError("AUDIO_TRUNCATION_FORBIDDEN")
            _silence(writer,samples-used)
    os.replace(temporary,directory/"target.wav")
    # Controlled basenames avoid concat escaping for arbitrary user directory paths.
    (aligned/"concat.txt").write_text("\n".join(f"file '{clip.name}'" for clip in clips),encoding="utf-8")
    video=aligned/"video.mp4"
    media.run(media.config.ffmpeg,["-v","error","-nostdin","-y","-f","concat","-safe","0",
        "-i","concat.txt","-c","copy","video.mp4"],cwd=aligned)
    final_info=media.require_video(video)
    if abs(float(video_seconds(final_info))-cursor/RATE) > 0.05:
        raise MediaError("CONCAT_DURATION_MISMATCH")
    write_json(directory/"alignment_result.json",{"slots":outcomes,"total_samples":cursor,
        "sample_rate":RATE,"video":"aligned/video.mp4","audio":"target.wav"})
    return result
