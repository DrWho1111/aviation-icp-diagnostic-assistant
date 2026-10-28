"""Seed the dedicated project databases before starting platform mode."""
import os
import subprocess
import sys
import time

for attempt in range(10):
    result=subprocess.run([sys.executable,"scripts/publish.py","--postgres","--neo4j"])
    if result.returncode==0: break
    if attempt==9: raise SystemExit("Project database initialization did not complete")
    time.sleep(2)
os.execv(sys.executable,[sys.executable,"-m","uvicorn","icp.app:app","--host","0.0.0.0","--port","8000"])
