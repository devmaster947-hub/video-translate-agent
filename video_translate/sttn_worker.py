"""Isolated STTN inference: process termination enforces per-chunk timeout."""
import sys,json
from pathlib import Path

def devices(torch):
    result=[]
    if torch.cuda.is_available(): result.append("cuda:0")
    if getattr(torch.backends,"mps",None) and torch.backends.mps.is_available(): result.append("mps")
    return result

def run(path,device,output=None):
    import numpy as np
    import cv2,torch
    from .vendor.sttn.model import InpaintGenerator
    torch.set_num_threads(4)
    model=InpaintGenerator(init_weights=False).to(device)
    weights=Path(__file__).parent/"vendor/sttn/sttn.pth"
    model.load_state_dict(torch.load(weights,map_location="cpu",weights_only=True)["netG"])
    model.eval()
    if path is None:
        frames=np.full((1,240,432,3),127,np.uint8)
        masks=np.zeros((1,240,432),np.uint8);masks[:,100:110,200:230]=255
    else:
        with np.load(path,allow_pickle=False) as data:
            frames=data["frames"];masks=data["masks"]
    n=len(frames)
    masks=np.stack([cv2.dilate(m,cv2.getStructuringElement(cv2.MORPH_CROSS,(3,3)),iterations=4) for m in masks])
    binary=(masks>0).astype(np.float32)[:,:,:,None]
    images=torch.from_numpy(frames).permute(0,3,1,2).float().to(device)/127.5-1
    mask=torch.from_numpy(binary).permute(0,3,1,2).to(device)
    completed=[None]*n
    with torch.inference_mode():
        # Encode in bounded groups; full encoder batches cause avoidable memory spikes.
        features=torch.cat([model.encoder(images[i:i+8]*(1-mask[i:i+8])) for i in range(0,n,8)])
        for f in range(0,n,5):
            neighbors=list(range(max(0,f-5),min(n,f+6)))
            refs=[i for i in range(0,n,10) if i not in neighbors]
            selected=neighbors+refs
            pred=model.infer(features[selected],mask[selected])
            pred=torch.tanh(model.decoder(pred[:len(neighbors)]))
            pred=((pred+1)*127.5).cpu().permute(0,2,3,1).numpy()
            for i,idx in enumerate(neighbors):
                img=pred[i]*binary[idx]+frames[idx]*(1-binary[idx])
                completed[idx]=img if completed[idx] is None else (completed[idx]+img)/2
    if output:
        np.savez_compressed(output,frames=np.stack(completed).astype(np.uint8))
    if device.startswith("cuda"):torch.cuda.synchronize()
    elif device=="mps":torch.mps.synchronize()

def main():
    try:
        import torch
        if sys.argv[1]=="devices":
            print(json.dumps({"devices":devices(torch)}));return
        if sys.argv[1]=="probe":run(None,sys.argv[2])
        else:run(sys.argv[2],sys.argv[3],sys.argv[4])
        print(json.dumps({"ok":True}))
    except Exception as exc:
        code="STTN_OUT_OF_MEMORY" if "out of memory" in str(exc).lower() else "STTN_INFERENCE_FAILED"
        print(json.dumps({"ok":False,"error":code}));raise SystemExit(1)

if __name__=="__main__":main()
