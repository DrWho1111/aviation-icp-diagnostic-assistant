"""Read a platform service's recent logs with known private values masked."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.pull_platform_images import docker_cli


def main(platform,service,tail):
    candidates=[ROOT/".env",*(ROOT/".secrets/platforms").glob("*/.env")]
    private=[]
    for path in candidates:
        if path.exists():
            for key,value in dotenv_values(path).items():
                if value and len(value)>=8 and any(word in key for word in ["KEY","PASSWORD","SECRET","TOKEN"]):
                    private.extend([value,quote(value,safe="")])
    result=subprocess.run([docker_cli(),"logs","--tail",str(tail),f"icp-{platform}-{service}-1"],capture_output=True,text=True,encoding="utf-8",errors="replace")
    text=result.stdout+result.stderr
    for value in sorted(set(private),key=len,reverse=True): text=text.replace(value,"<REDACTED>")
    text=re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----","<REDACTED PRIVATE KEY>",text,flags=re.S)
    print(text)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("platform",choices=["dify","ragflow","workbench","reranker"])
    parser.add_argument("service");parser.add_argument("--tail",type=int,default=35)
    args=parser.parse_args();main(args.platform,args.service,args.tail)
