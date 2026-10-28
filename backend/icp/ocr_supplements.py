"""Admit only source-bound, visually reviewed page text to ingestion."""
import hashlib
import json
from pathlib import Path


def load_pages(path: Path, documents: list[dict]) -> dict:
    if not path.exists():return {}
    value=json.loads(path.read_text(encoding='utf-8'))
    docs={d['id']:d for d in documents};pages={}
    for item in value['pages']:
        doc=docs.get(item['document_id']);key=(item['document_id'],item['page'])
        if (not doc or doc['corpus']!='research' or not item.get('reviewed')
                or doc['sha256']!=item['source_sha256'] or not item.get('text','').strip()
                or not isinstance(item['page'],int) or item['page']<1 or key in pages):
            raise ValueError('OCR supplement is unreviewed, duplicated or bound to another source')
        pages[key]=item
    return pages


def supplement_digest(path: Path) -> bytes:
    return hashlib.sha256(path.read_bytes()).digest() if path.exists() else b''
