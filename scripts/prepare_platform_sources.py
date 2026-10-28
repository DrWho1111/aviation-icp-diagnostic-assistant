"""Fetch pinned upstream Docker assets without installing or starting software."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {
    "dify": ("langgenius/dify", "1.17.1"),
    "ragflow": ("infiniflow/ragflow", "v0.27.2"),
}


def prepare():
    lock_path = ROOT / "deploy/platform-sources.lock.json"
    previous = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.exists() else {}
    locked = {}
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for name, (repo, tag) in PLATFORMS.items():
            cache = ROOT / f".tools/platform-sources/{name}-{tag}.zip"
            cache.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://codeload.github.com/{repo}/zip/refs/tags/{tag}"
            if not cache.exists():
                partial = cache.with_suffix(".zip.partial")
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    expected = int(response.headers.get("content-length", "0"))
                    total = 0
                    with partial.open("wb") as handle:
                        for block in response.iter_bytes(1024 * 1024):
                            handle.write(block)
                            total += len(block)
                    if expected and total != expected:
                        raise RuntimeError("Incomplete upstream archive")
                partial.replace(cache)
            digest = hashlib.sha256(cache.read_bytes()).hexdigest()
            if name in previous and previous[name]["archive_sha256"] != digest:
                raise RuntimeError("Upstream archive differs from recorded lock; original assets retained")
            destination = ROOT / f"deploy/vendor/{name}"
            destination.mkdir(parents=True, exist_ok=True)
            count = 0
            with zipfile.ZipFile(cache) as archive:
                for member in archive.infolist():
                    path = PurePosixPath(member.filename)
                    parts = path.parts[1:]
                    if not parts or member.is_dir():
                        continue
                    if parts[0] != "docker" and not (len(parts) == 1 and parts[0].startswith("LICENSE")):
                        continue
                    if path.is_absolute() or ".." in parts or stat.S_ISLNK(member.external_attr >> 16):
                        raise RuntimeError("Unsafe upstream archive member")
                    target = destination.joinpath(*parts).resolve()
                    if not target.is_relative_to(destination.resolve()):
                        raise RuntimeError("Upstream member outside vendor directory")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
                    count += 1
            locked[name] = {"repository": f"https://github.com/{repo}", "tag": tag,
                            "archive_url": url, "archive_sha256": digest,
                            "scope": "docker assets and upstream license", "files": count}
            lock_path.write_text(json.dumps(locked, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"platform": name, "tag": tag, "assets": count, "sha256": digest}), flush=True)
    return locked


if __name__ == "__main__":
    try:
        prepare()
    except Exception as exc:
        print("Platform source preparation failed. Error type=" + type(exc).__name__)
        raise SystemExit(1)
