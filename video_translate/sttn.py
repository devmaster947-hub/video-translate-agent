"""Optional GPU cleanup with bounded inference, verified caches and OpenCV fallback."""
import hashlib,importlib.util,json,platform,subprocess,sys,os,time,urllib.request
from pathlib import Path
from .config import PROJECT_ROOT
from .files import write_json,read_json
from .state import fingerprint

WEIGHTS=Path(__file__).parent/"vendor/sttn/sttn.pth"
# Frozen checkpoint. Marketplace packages fetch this release asset on GPU hosts.
WEIGHT_HASH='25b0c2c30042d82efd1893bd42ec726764262d94115393a1718f8d65d2a7817b'
WEIGHT_URL='https://github.com/devmaster947-hub/video-translate-agent/releases/download/v2.3.4/sttn.pth'
WEIGHT_MAX_BYTES=70*1024*1024

class STTNError(RuntimeError):pass

def worker(args,timeout):
    try:
        r=subprocess.run([sys.executable,"-m","video_translate.sttn_worker",*map(str,args)],
            cwd=PROJECT_ROOT,capture_output=True,text=True,timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    except subprocess.TimeoutExpired:raise STTNError("STTN_TIMEOUT") from None
    except OSError:raise STTNError("STTN_WORKER_FAILED") from None
    try: result=json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):raise STTNError("STTN_WORKER_FAILED") from None
    if r.returncode or result.get("ok") is False:raise STTNError(result.get("error","STTN_WORKER_FAILED"))
    return result

def hardware_hint():
    if platform.system()=="Darwin" and platform.machine().lower() in ("arm64","aarch64"):return "mps"
    try:
        r=subprocess.run(["nvidia-smi","-L"],capture_output=True,timeout=10)
        if r.returncode==0 and r.stdout.strip():return "cuda"
    except (OSError,subprocess.TimeoutExpired):pass
    return None

def setup_dependencies():
    if not hardware_hint():return False
    args=[sys.executable,"-m","pip","install","--upgrade","torch>=2.5,<3"]
    if hardware_hint()=="cuda":args+=["--force-reinstall","--index-url","https://download.pytorch.org/whl/cu128"]
    try:return subprocess.run(args,cwd=PROJECT_ROOT,capture_output=True,timeout=300).returncode==0
    except (OSError,subprocess.TimeoutExpired):return False

def capabilities():
    available=importlib.util.find_spec("torch") is not None
    devices=[]
    if available:
        try:devices=worker(["devices"],30).get("devices",[])
        except STTNError:pass
    return {"sttn_dependency_available":available,"sttn_weights_available":WEIGHTS.is_file(),
            "sttn_devices":devices,"sttn_hardware_hint":hardware_hint()}

def ensure_weights():
    if WEIGHTS.is_file() and fingerprint(WEIGHTS)==WEIGHT_HASH:return True
    WEIGHTS.parent.mkdir(parents=True,exist_ok=True)
    temporary=WEIGHTS.with_suffix(".pth.download")
    temporary.unlink(missing_ok=True)
    request=urllib.request.Request(WEIGHT_URL,headers={"User-Agent":"video-translate-agent/2.3.4"})
    try:
        total=0
        with urllib.request.urlopen(request,timeout=120) as response,temporary.open("wb") as target:
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>WEIGHT_MAX_BYTES:raise STTNError("STTN_WEIGHTS_DOWNLOAD_TOO_LARGE")
                target.write(chunk)
        if fingerprint(temporary)!=WEIGHT_HASH:raise STTNError("STTN_WEIGHTS_INVALID")
        temporary.replace(WEIGHTS)
        return True
    except (OSError,STTNError):
        temporary.unlink(missing_ok=True)
        return False

def select_backend(backend,repair=True):
    if backend=="opencv":return "opencv","cpu",None
    caps=capabilities()
    if not caps["sttn_devices"] and repair and caps["sttn_hardware_hint"]:
        setup_dependencies();caps=capabilities()
    if caps["sttn_devices"] and (not WEIGHTS.is_file() or fingerprint(WEIGHTS)!=WEIGHT_HASH):
        ensure_weights()
        caps=capabilities()
    if not caps["sttn_devices"]:
        reason="STTN_NO_GPU" if caps["sttn_dependency_available"] or not caps["sttn_hardware_hint"] else "STTN_DEPENDENCY_UNAVAILABLE"
        return "opencv","cpu",reason if backend=="sttn" or caps["sttn_hardware_hint"] else None
    if not WEIGHTS.is_file():return "opencv","cpu","STTN_WEIGHTS_MISSING"
    if fingerprint(WEIGHTS)!=WEIGHT_HASH:return "opencv","cpu","STTN_WEIGHTS_INVALID"
    failure=None
    for device in caps["sttn_devices"]:
        try:
            worker(["probe",device],300);return "sttn",device,None
        except STTNError as exc:failure=str(exc)
    return "opencv","cpu",failure

def repair_chunk(frames,masks,directory,device,config):
    import cv2,numpy as np
    if not np.any(masks):return frames.copy()
    union=np.max(masks,axis=0);ys,xs=np.where(union>0)
    height,width=union.shape
    # Context follows eligible tracks, regardless of source resolution or text location.
    pad=max(32,round(height*.06))
    x1=max(0,int(xs.min())-pad);x2=min(width,int(xs.max())+pad+1)
    y1=max(0,int(ys.min())-pad);y2=min(height,int(ys.max())+pad+1)
    low=np.stack([cv2.resize(cv2.cvtColor(f[y1:y2,x1:x2],cv2.COLOR_BGR2RGB),(432,240)) for f in frames])
    low_masks=np.stack([cv2.resize(m[y1:y2,x1:x2],(432,240),interpolation=cv2.INTER_NEAREST) for m in masks])
    signature=hashlib.sha256(low.tobytes()+low_masks.tobytes()+
        (WEIGHT_HASH+fingerprint(Path(__file__))+fingerprint(Path(__file__).with_name("sttn_worker.py"))+fingerprint(WEIGHTS.with_name("model.py"))+device).encode()).hexdigest()
    directory.mkdir(parents=True,exist_ok=True)
    inp=directory/"input.npz";out=directory/"result.npz";receipt=directory/"receipt.json"
    try:cached=read_json(receipt) if receipt.exists() else {}
    except (ValueError,OSError):cached={}
    valid=out.exists() and cached.get("signature")==signature and cached.get("sha256")==fingerprint(out)
    if not valid:
        np.savez_compressed(inp,frames=low,masks=low_masks)
        out.unlink(missing_ok=True)
        worker(["infer",inp,device,out],config.sttn_timeout_seconds)
        write_json(receipt,{"signature":signature,"sha256":fingerprint(out),"frames":len(frames)})
    with np.load(out,allow_pickle=False) as data:pred=data["frames"]
    if pred.shape!=low.shape:raise STTNError("STTN_FRAME_COUNT_MISMATCH")
    result=frames.copy()
    for i in range(len(frames)):
        restored=cv2.cvtColor(cv2.resize(pred[i],(x2-x1,y2-y1),interpolation=cv2.INTER_CUBIC),cv2.COLOR_RGB2BGR)
        # No changes outside eligible pixels, including protected scene text.
        alpha=masks[i,y1:y2,x1:x2].astype(np.float32)[:,:,None]/255
        result[i,y1:y2,x1:x2]=np.clip(restored*alpha+frames[i,y1:y2,x1:x2]*(1-alpha),0,255)
    return result

def repair_with_retries(frames,masks,directory,device,config,depth=0):
    import numpy as np
    try:return repair_chunk(frames,masks,directory,device,config)
    except STTNError as exc:
        if str(exc)!="STTN_OUT_OF_MEMORY" or depth>=2 or len(frames)<2:raise
        middle=len(frames)//2
        return np.concatenate([repair_with_retries(frames[:middle],masks[:middle],directory/"a",device,config,depth+1),
            repair_with_retries(frames[middle:],masks[middle:],directory/"b",device,config,depth+1)])

def clean_sttn(source,directory,analysis,layout,config,media,device):
    import cv2,numpy as np
    from .overlay_mask import track_mask,dynamic_track_mask,manual_region_mask
    started=time.perf_counter()
    source_hash=fingerprint(source)
    clean=directory/"clean";clean.mkdir(exist_ok=True)
    mask_dir=directory/"masks";mask_dir.mkdir(exist_ok=True)
    h,w=analysis["video"]["height"],analysis["video"]["width"]
    anchors=[]
    for track in analysis["tracks"]:
        if track.get("cleanup_eligible") is True:
            anchor=track_mask(source,track,config)
            relative=f"masks/{track['track_id']}.png"
            if not cv2.imwrite(str(directory/relative),anchor):raise STTNError("MASK_WRITE_FAILED")
            track["mask"]=relative;anchors.append((track,anchor))
    for i,region in enumerate(config.manual_watermark_regions,1):
        track={"track_id":f"watermark_manual_{i:04d}","kind":"watermark","manual":True,"start_ms":0,
            "end_ms":analysis["video"]["duration_ms"],"observations":[],"bbox_norm":region.model_dump()}
        anchor=manual_region_mask(w,h,region)
        track["mask"]=f"masks/{track['track_id']}.png";cv2.imwrite(str(directory/track["mask"]),anchor)
        anchors.append((track,anchor));analysis["tracks"].append(track)
    cap=cv2.VideoCapture(str(source));fps=cap.get(cv2.CAP_PROP_FPS)
    if not cap.isOpened() or fps<=0:
        cap.release();raise STTNError("STTN_VIDEO_OPEN_FAILED")
    expected=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));size=max(1,round(config.sttn_chunk_seconds*fps))
    raw=clean/".sttn-frames.avi"
    writer=cv2.VideoWriter(str(raw),cv2.VideoWriter_fourcc(*"MJPG"),fps,(w,h))
    if not writer.isOpened():
        cap.release();writer.release();raw.unlink(missing_ok=True);raise STTNError("STTN_ENCODER_UNAVAILABLE")
    count=0;chunk=0;preview_saved=False
    try:
        while True:
            frames=[];masks=[]
            for _ in range(size):
                ok,f=cap.read()
                if not ok:break
                ms=round((count+len(frames))/fps*1000)
                mask=np.zeros((h,w),np.uint8)
                for track,anchor in anchors:
                    if track["start_ms"]<=ms<=track["end_ms"]:
                        dynamic=anchor if track.get("manual") else dynamic_track_mask(f,track,anchor,ms,config)
                        mask=cv2.bitwise_or(mask,dynamic)
                frames.append(f);masks.append(mask)
            if not frames:break
            repaired=repair_with_retries(np.stack(frames),np.stack(masks),directory/"sttn_chunks"/f"{chunk:06d}",device,config)
            if not preview_saved and np.any(masks):
                index=next(i for i,m in enumerate(masks) if np.any(m))
                preview=directory/"preview";preview.mkdir(exist_ok=True)
                for name,image in [("cleanup_before.png",frames[index]),("cleanup_mask.png",masks[index]),
                                   ("cleanup_after.png",repaired[index])]:
                    if not cv2.imwrite(str(preview/name),image):raise STTNError("STTN_PREVIEW_WRITE_FAILED")
                preview_saved=True
            for f in repaired:writer.write(f)
            count+=len(frames);chunk+=1
            print(f"STTN {device}: {count}/{expected} frames",flush=True,file=sys.stderr)
    except BaseException:
        cap.release();writer.release();raw.unlink(missing_ok=True)
        raise
    finally:cap.release();writer.release()
    build=clean/".sttn-building.mp4";output=clean/"video.mp4"
    try:
        if count!=expected or not count:raise STTNError("STTN_FRAME_COUNT_MISMATCH")
        media.run(media.config.ffmpeg,["-v","error","-y","-i",str(raw),"-an","-c:v","libx264",
            "-preset","veryfast","-crf","18","-pix_fmt","yuv420p","-movflags","+faststart",str(build)],timeout_seconds=None)
        if abs(media.require_video(build)["duration_ms"]-media.require_video(source)["duration_ms"])>100:raise STTNError("STTN_DURATION_MISMATCH")
        media.run(media.config.ffmpeg,["-v","error","-xerror","-i",str(build),"-f","null","-"])
        frame_probe=json.loads(media.run(media.config.ffprobe,["-v","error","-count_frames",
            "-select_streams","v:0","-show_entries","stream=nb_read_frames","-of","json",str(build)]))
        if int(frame_probe["streams"][0]["nb_read_frames"])!=count:raise STTNError("STTN_FRAME_COUNT_MISMATCH")
        if fingerprint(source)!=source_hash:raise STTNError("STTN_SOURCE_CHANGED")
        os.replace(build,output)
    finally:raw.unlink(missing_ok=True);build.unlink(missing_ok=True)
    return {"video":output,"analysis":analysis,"layout":layout,"report":{
        "mode":"local","backend":"sttn","device":device,"fallback_reason":None,
        "processed_frames":count,"chunks":chunk,"ocr_seconds":analysis["ocr_seconds"],
        "total_seconds":round(time.perf_counter()-started,3),"removable_track_count":len(anchors)}}


def cached_analysis(source,directory,segments,config,analyzer,**kwargs):
    signature=hashlib.sha256((fingerprint(source)+json.dumps(config.model_dump(),sort_keys=True)+
        json.dumps([vars(s) for s in segments],sort_keys=True)+
        fingerprint(Path(__file__).with_name("overlay_detection.py"))+
        fingerprint(Path(__file__).with_name("cleanup_policy.py"))).encode()).hexdigest()
    path=directory/"sttn_analysis.json"
    receipt=directory/"sttn_analysis_receipt.json"
    try:
        cached=read_json(receipt)
        if cached.get("signature")==signature and cached.get("sha256")==fingerprint(path):
            data=read_json(path);return data["analysis"],data["layout"]
    except (OSError,ValueError):pass
    analysis,layout=analyzer(source,segments,config,**kwargs)
    write_json(path,{"analysis":analysis,"layout":layout})
    write_json(receipt,{"signature":signature,"sha256":fingerprint(path)})
    return analysis,layout
