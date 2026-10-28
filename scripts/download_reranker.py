"""Resume the pinned reranker weight download and verify its Hugging Face LFS digest."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil
import time

import httpx

ROOT=Path(__file__).resolve().parents[1]
SIZE=2271071852
SHA256="d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286"
REVISION="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"


def prepare_metadata(folder,proxy):
    lock=json.loads((ROOT/'deploy/reranker-model.lock.json').read_text(encoding='utf-8'))
    if lock['revision']!=REVISION:raise RuntimeError('Reranker revision mismatch')
    with httpx.Client(proxy=proxy,trust_env=False,follow_redirects=True,timeout=120) as client:
        for name,expected in lock['required_files_sha256'].items():
            target=folder/name
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest()!=expected:raise RuntimeError('Existing model metadata digest mismatch; no file overwritten')
                continue
            partial=target.with_suffix(target.suffix+'.part')
            verified=False
            for endpoint in ['https://huggingface.co','https://hf-mirror.com']:
                try:
                    with client.stream('GET',endpoint+'/BAAI/bge-reranker-v2-m3/resolve/'+REVISION+'/'+name) as response:
                        response.raise_for_status()
                        with partial.open('wb') as handle:
                            for chunk in response.iter_bytes(1024*1024):handle.write(chunk)
                    if hashlib.sha256(partial.read_bytes()).hexdigest()!=expected:raise RuntimeError('Model metadata digest mismatch')
                    partial.replace(target);verified=True;break
                except Exception:continue
            if not verified:raise RuntimeError('Pinned tokenizer/config download incomplete; rerun to continue')
    print('Pinned tokenizer, config and model documentation verified.',flush=True)


def download_segments(client,folder,partial,endpoints,workers):
    prefix=partial.stat().st_size if partial.exists() else 0
    segments=folder/"download-segments";segments.mkdir(exist_ok=True)
    blocks=[(start,min(start+32*1024*1024,SIZE)) for start in range(prefix,SIZE,32*1024*1024)]
    def block(bounds):
        start,end=bounds;path=segments/f"{start}-{end}.part"
        for endpoint in endpoints:
            for attempt in range(3):
                size=path.stat().st_size if path.exists() else 0
                if size==end-start: return path
                if size>end-start: raise RuntimeError("Segment exceeds requested range")
                offset=start+size
                try:
                    with client.stream("GET",endpoint+"/BAAI/bge-reranker-v2-m3/resolve/"+REVISION+"/model.safetensors",headers={"Range":f"bytes={offset}-{end-1}","Accept-Encoding":"identity"}) as response:
                        response.raise_for_status()
                        if response.status_code!=206 or response.headers.get("Content-Range")!=f"bytes {offset}-{end-1}/{SIZE}":
                            raise RuntimeError("Server did not honor the requested segment")
                        with path.open("ab") as handle:
                            for chunk in response.iter_bytes(1024*1024):
                                if handle.tell()+len(chunk)>end-start: raise RuntimeError("Unexpected segment length")
                                handle.write(chunk)
                    if path.stat().st_size==end-start: return path
                except Exception:
                    time.sleep(1)
        raise RuntimeError("Segment download incomplete; downloaded bytes retained")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(block,bounds) for bounds in blocks]
        for future in as_completed(futures):
            path=future.result()
            print(json.dumps({"completed_segment":path.name,"retained_prefix_bytes":prefix}),flush=True)
    with partial.open("ab") as handle:
        for start,end in blocks:
            with (segments/f"{start}-{end}.part").open("rb") as source: shutil.copyfileobj(source,handle)


def main(proxy=None,workers=1):
    folder=ROOT/".models/bge-reranker-v2-m3";folder.mkdir(parents=True,exist_ok=True)
    prepare_metadata(folder,proxy)
    final=folder/"model.safetensors";partial=folder/"model.safetensors.part"
    if final.exists():
        with final.open("rb") as handle: digest=hashlib.file_digest(handle,"sha256").hexdigest()
        if digest==SHA256:
            print("Pinned reranker weight already verified.");return True
        raise RuntimeError("Existing model digest mismatch; no file overwritten")
    # Preserve bytes left by interrupted CLI attempts. The final digest validates the whole file.
    candidates=[p for p in (folder/".cache/huggingface/download").glob("*.incomplete") if SHA256 in p.name]
    best=max(candidates,key=lambda p:p.stat().st_size,default=None)
    if best and best.stat().st_size>(partial.stat().st_size if partial.exists() else 0):
        shutil.copyfile(best,partial)
    endpoints=["https://huggingface.co","https://hf-mirror.com"]
    with httpx.Client(proxy=proxy,trust_env=False,follow_redirects=True,timeout=httpx.Timeout(60,connect=20)) as client:
        if workers>1: download_segments(client,folder,partial,endpoints,workers)
        for endpoint in ([] if workers>1 else endpoints):
            for attempt in range(3):
                start=partial.stat().st_size if partial.exists() else 0
                if start==SIZE: break
                if start>SIZE: raise RuntimeError("Partial file exceeds expected size")
                print(json.dumps({"downloaded_bytes":start,"expected_bytes":SIZE,"attempt":attempt+1,"host":httpx.URL(endpoint).host}),flush=True)
                try:
                    with client.stream("GET",endpoint+"/BAAI/bge-reranker-v2-m3/resolve/"+REVISION+"/model.safetensors",headers={"Range":f"bytes={start}-","Accept-Encoding":"identity"}) as response:
                        response.raise_for_status()
                        if response.status_code==206:
                            if not response.headers.get("Content-Range","").startswith(f"bytes {start}-"):
                                raise RuntimeError("Unexpected download range")
                        elif response.status_code==200: start=0
                        else: raise RuntimeError("Unexpected download status")
                        with partial.open("ab" if start else "wb") as handle:
                            last=time.monotonic()
                            for chunk in response.iter_bytes(1024*1024):
                                handle.write(chunk)
                                if time.monotonic()-last>=20:
                                    handle.flush();last=time.monotonic()
                                    print(json.dumps({"downloaded_bytes":handle.tell(),"expected_bytes":SIZE}),flush=True)
                    if partial.stat().st_size==SIZE: break
                except Exception as exc:
                    print(json.dumps({"download_error_type":type(exc).__name__,"partial_retained":True}),flush=True)
                    time.sleep(1)
            if partial.exists() and partial.stat().st_size==SIZE: break
    if not partial.exists() or partial.stat().st_size!=SIZE: return False
    with partial.open("rb") as handle: digest=hashlib.file_digest(handle,"sha256").hexdigest()
    if digest!=SHA256: raise RuntimeError("Downloaded model digest mismatch; model not enabled")
    partial.replace(final)
    (ROOT/"outputs/reranker-download.json").write_text(json.dumps({"model":"BAAI/bge-reranker-v2-m3","revision":REVISION,"bytes":SIZE,"sha256":digest,"verified":True},indent=2),encoding="utf-8")
    print("Pinned BGE reranker downloaded and checksum verified.",flush=True)
    return True


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--proxy");parser.add_argument("--workers",type=int,choices=range(1,5),default=1)
    args=parser.parse_args()
    try: raise SystemExit(not main(args.proxy,args.workers))
    except Exception as exc:
        print("Reranker download stopped; error type="+type(exc).__name__)
        raise SystemExit(1)
