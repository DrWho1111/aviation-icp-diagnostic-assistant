"""Pull only a platform's selected images, retain progress, and bound network retries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def docker_cli():
    found=shutil.which("docker")
    if found: return found
    candidate=Path("D:/Apps/Docker/resources/bin/docker.exe")
    if candidate.exists(): return str(candidate)
    raise RuntimeError("Docker CLI not installed")


def main(platform):
    cli=docker_cli();folder=ROOT/".secrets/platforms"/platform
    discovered=subprocess.run([cli,"compose","-p","icp-"+platform,"--env-file",".env","-f","icp.compose.yaml","config","--images"],cwd=folder,capture_output=True,text=True)
    if discovered.returncode: raise RuntimeError("Compose image discovery failed")
    images=list(dict.fromkeys(line.strip() for line in discovered.stdout.splitlines() if line.strip()))
    output=ROOT/f"outputs/image-pull-{platform}.json"
    report={"platform":platform,"status":"pulling","images":[]}
    def save(): output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    for image in images:
        cached=subprocess.run([cli,"image","inspect",image,"--format","{{json .RepoDigests}}"],capture_output=True,text=True)
        item={"image":image,"passed":cached.returncode==0,"cached":cached.returncode==0,"attempts":0}
        if not item["passed"]:
            log=ROOT/"outputs"/("pull-"+re.sub(r"[^a-zA-Z0-9.-]","_",image)+".log")
            for attempt in range(1,4):
                print(json.dumps({"platform":platform,"image":image,"attempt":attempt}),flush=True)
                with log.open("w",encoding="utf-8") as handle:
                    result=subprocess.run([cli,"pull",image],stdout=handle,stderr=subprocess.STDOUT)
                item["attempts"]=attempt
                item["passed"]=result.returncode==0
                if item["passed"]: break
                if attempt<3: time.sleep(2)
        if item["passed"]:
            inspected=subprocess.run([cli,"image","inspect",image,"--format","{{json .RepoDigests}}"],capture_output=True,text=True)
            item["repo_digests"]=json.loads(inspected.stdout)
        report["images"].append(item);save()
        print(json.dumps({"image":image,"passed":item["passed"],"cached":item["cached"]}),flush=True)
    report["status"]="completed" if all(i["passed"] for i in report["images"]) else "network_retry_required"
    save()
    return report["status"]=="completed"


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("platform",choices=["dify","ragflow"])
    try: raise SystemExit(not main(parser.parse_args().platform))
    except Exception as exc:
        print("Platform image pull stopped; cached layers retained. Error type="+type(exc).__name__)
        raise SystemExit(1)
