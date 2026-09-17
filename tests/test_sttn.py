from types import SimpleNamespace as NS
import json
import numpy as np
import pytest
from video_translate import sttn,cleanup
from video_translate.config import CleanupConfig
from video_translate.sttn_worker import devices

@pytest.mark.parametrize("cuda,mps,expected",[
    (True,True,["cuda:0","mps"]),(True,False,["cuda:0"]),(False,True,["mps"]),(False,False,[])])
def test_device_order(cuda,mps,expected):
    torch=NS(cuda=NS(is_available=lambda:cuda),backends=NS(mps=NS(is_available=lambda:mps)))
    assert devices(torch)==expected

def caps(device_list,dependency=True,hint=None):
    return {"sttn_devices":device_list,"sttn_dependency_available":dependency,
        "sttn_weights_available":True,"sttn_hardware_hint":hint}

def test_verified_cuda_then_mps(monkeypatch,tmp_path):
    weights=tmp_path/"sttn.pth";weights.write_bytes(b"verified")
    monkeypatch.setattr(sttn,"WEIGHTS",weights)
    monkeypatch.setattr(sttn,"fingerprint",lambda path:sttn.WEIGHT_HASH if path==weights else "other")
    monkeypatch.setattr(sttn,"capabilities",lambda:caps(["cuda:0","mps"]))
    calls=[]
    def worker(args,timeout):
        calls.append(args[1])
        if args[1]=="cuda:0":raise sttn.STTNError("STTN_INFERENCE_FAILED")
        return {"ok":True}
    monkeypatch.setattr(sttn,"worker",worker)
    assert sttn.select_backend("auto")==("sttn","mps",None)
    assert calls==["cuda:0","mps"]

def test_no_gpu_does_not_install_torch(monkeypatch):
    monkeypatch.setattr(sttn,"capabilities",lambda:caps([],False))
    monkeypatch.setattr(sttn,"setup_dependencies",lambda:pytest.fail("No GPU must not install torch"))
    assert sttn.select_backend("auto")==("opencv","cpu",None)

def test_dependency_repair_failure_falls_back(monkeypatch):
    monkeypatch.setattr(sttn,"capabilities",lambda:caps([],False,"mps"))
    calls=[]
    monkeypatch.setattr(sttn,"setup_dependencies",lambda:calls.append(True))
    assert sttn.select_backend("auto")==("opencv","cpu","STTN_DEPENDENCY_UNAVAILABLE")
    assert len(calls)==1

def test_opencv_override_skips_gpu(monkeypatch):
    monkeypatch.setattr(sttn,"capabilities",lambda:pytest.fail("forced OpenCV must skip torch"))
    assert sttn.select_backend("opencv")==("opencv","cpu",None)

def test_oom_splits_without_dropping_or_repeating_frames(monkeypatch,tmp_path):
    frames=np.arange(8)[:,None,None,None]
    calls=[]
    def repair(frames,masks,*args):
        calls.append(len(frames))
        if len(frames)>2:raise sttn.STTNError("STTN_OUT_OF_MEMORY")
        return frames.copy()
    monkeypatch.setattr(sttn,"repair_chunk",repair)
    result=sttn.repair_with_retries(frames,np.zeros((8,1,1)),tmp_path,"mps",CleanupConfig())
    assert np.array_equal(result,frames)
    assert calls==[8,4,2,2,4,2,2]

def test_oom_has_bounded_retries(monkeypatch,tmp_path):
    monkeypatch.setattr(sttn,"repair_chunk",lambda *a:(_ for _ in ()).throw(sttn.STTNError("STTN_OUT_OF_MEMORY")))
    with pytest.raises(sttn.STTNError,match="STTN_OUT_OF_MEMORY"):
        sttn.repair_with_retries(np.zeros((16,1,1,3)),np.zeros((16,1,1)),tmp_path,"mps",CleanupConfig())

def test_chunk_cache_validates_input_and_output(monkeypatch,tmp_path):
    calls=[]
    def worker(args,timeout):
        calls.append(True)
        with np.load(args[1]) as data:np.savez_compressed(args[3],frames=data["frames"])
        return {"ok":True}
    monkeypatch.setattr(sttn,"worker",worker)
    frames=np.full((2,48,64,3),90,np.uint8)
    masks=np.zeros((2,48,64),np.uint8);masks[:,20:24,30:34]=255
    config=CleanupConfig()
    assert np.array_equal(sttn.repair_chunk(frames,masks,tmp_path,"mps",config),frames)
    sttn.repair_chunk(frames,masks,tmp_path,"mps",config)
    assert len(calls)==1
    (tmp_path/"result.npz").write_bytes(b"corrupt")
    sttn.repair_chunk(frames,masks,tmp_path,"mps",config)
    assert len(calls)==2
    frames[:,20:24,30:34]=110
    sttn.repair_chunk(frames,masks,tmp_path,"mps",config)
    assert len(calls)==3

def test_failure_falls_back_using_same_ocr(monkeypatch,tmp_path):
    analysis={"tracks":[]}
    monkeypatch.setattr(sttn,"select_backend",lambda b:("sttn","mps",None))
    ocr=[]
    def analyze(*a,**kw):ocr.append(True);return analysis,{}
    monkeypatch.setattr(cleanup,"analyze_video",analyze)
    monkeypatch.setattr(sttn,"cached_analysis",lambda source,directory,segments,config,analyzer,**kw:analyzer())
    monkeypatch.setattr(sttn,"clean_sttn",lambda *a:(_ for _ in ()).throw(sttn.STTNError("STTN_TIMEOUT")))
    def local(*a,**kw):
        assert kw["prepared"][0] is analysis
        return {"report":{}}
    monkeypatch.setattr(cleanup,"_clean_video_local",local)
    result=cleanup.clean_video(tmp_path/"source.mp4",tmp_path,[],CleanupConfig(),None)
    assert len(ocr)==1
    assert result["report"]=={"backend":"opencv","device":"cpu","fallback_reason":"STTN_TIMEOUT"}

def test_missing_weights_falls_back(monkeypatch,tmp_path):
    monkeypatch.setattr(sttn,"WEIGHTS",tmp_path/"missing.pth")
    monkeypatch.setattr(sttn,"PROJECT_ROOT",tmp_path)
    monkeypatch.setattr(sttn,"capabilities",lambda:caps(["mps"]))
    monkeypatch.setattr(sttn,"ensure_weights",lambda:False)
    assert sttn.select_backend("auto")==("opencv","cpu","STTN_WEIGHTS_MISSING")

def test_analysis_cache_reuses_ocr_and_invalidates_source(tmp_path):
    source=tmp_path/"source.mp4";source.write_bytes(b"source-a")
    calls=[]
    def analyze(*a,**kw):calls.append(True);return {"tracks":[],"video":{}},{}
    segments=[NS(id=1,start_ms=0,end_ms=1000,text="speech",raw_text="speech")]
    first=sttn.cached_analysis(source,tmp_path,segments,CleanupConfig(),analyze)
    assert sttn.cached_analysis(source,tmp_path,segments,CleanupConfig(),analyze)==first
    assert len(calls)==1
    source.write_bytes(b"source-b")
    sttn.cached_analysis(source,tmp_path,segments,CleanupConfig(),analyze)
    assert len(calls)==2

def test_worker_timeout_is_reported(monkeypatch):
    import subprocess
    monkeypatch.setattr(sttn.subprocess,"run",lambda *a,**kw:(_ for _ in ()).throw(subprocess.TimeoutExpired("worker",1)))
    with pytest.raises(sttn.STTNError,match="STTN_TIMEOUT"):sttn.worker(["probe","mps"],1)


def test_all_probes_fail_report_fallback(monkeypatch,tmp_path):
    weights=tmp_path/"sttn.pth";weights.write_bytes(b"verified")
    monkeypatch.setattr(sttn,"WEIGHTS",weights)
    monkeypatch.setattr(sttn,"fingerprint",lambda path:sttn.WEIGHT_HASH if path==weights else "other")
    monkeypatch.setattr(sttn,"capabilities",lambda:caps(["cuda:0","mps"]))
    monkeypatch.setattr(sttn,"worker",lambda *a:(_ for _ in ()).throw(sttn.STTNError("STTN_INFERENCE_FAILED")))
    assert sttn.select_backend("auto")==("opencv","cpu","STTN_INFERENCE_FAILED")
