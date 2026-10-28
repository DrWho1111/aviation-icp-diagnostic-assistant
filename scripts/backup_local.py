"""Online SQLite backup, or restore into a NEW file. Never overwrite live state."""
import argparse
from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]


def backup(source:Path,destination:Path):
    if destination.exists(): raise ValueError("Backup/restore destination already exists; choose a new file")
    destination.parent.mkdir(parents=True,exist_ok=True)
    src=sqlite3.connect(source.as_uri()+"?mode=ro",uri=True)
    dst=sqlite3.connect(destination)
    try:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchone()[0]!="ok": raise ValueError("Database integrity check failed")
    finally: src.close();dst.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=ROOT/"data/runtime/sessions.sqlite3")
    parser.add_argument("--destination",type=Path)
    args=parser.parse_args()
    target=args.destination or ROOT/"data/backups"/(datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")+".sqlite3")
    backup(args.source.resolve(),target.resolve())
    print("SQLite backup/restore completed and integrity checked; destination="+str(target))
