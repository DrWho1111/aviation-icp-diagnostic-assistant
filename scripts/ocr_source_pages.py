"""Inspect native source chunks, then OCR reviewed image pages without editing originals."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import re
import httpx
from dotenv import dotenv_values
import pymupdf

ROOT=Path(__file__).resolve().parents[1]
DOCKER=r'D:\Apps\Docker\resources\bin\docker.exe'


def main():
    kb=json.loads((ROOT/'data/processed/knowledge.json').read_text(encoding='utf-8'))
    review=json.loads((ROOT/'data/processed/page_review.json').read_text(encoding='utf-8'))
    docs={d['id']:d for d in kb['documents']}
    selected=[p for p in review['pages'] if p['classification'] in {'image_text','front_matter'}]
    directory=ROOT/'tmp/pdfs/final-ocr';directory.mkdir(parents=True,exist_ok=True)
    state=json.loads((ROOT/'.secrets/ragflow-import.json').read_text(encoding='utf-8'))
    env=dotenv_values(ROOT/'.env');native={}
    with httpx.Client(base_url=env['RAGFLOW_BASE_URL'],headers={'Authorization':'Bearer '+env['RAGFLOW_API_KEY']},timeout=60,trust_env=False) as client:
        for sid in sorted({p['document_id'] for p in selected}):
            ds=state['datasets']['original-research'];did=state['original_documents'][sid]
            chunks=[];page=1
            while True:
                value=client.get(f'/api/v1/datasets/{ds}/documents/{did}/chunks',params={'page':page,'page_size':100}).raise_for_status().json()
                if value.get('code')!=0:raise RuntimeError('Native chunk inspection failed')
                batch=value['data']['chunks'];chunks+=batch
                if len(batch)<100:break
                page+=1
            native[sid]=chunks
    inspected=[]
    for item in selected:
        sid=item['document_id'];pno=item['page'];doc=docs[sid];path=ROOT/doc['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==item['source_sha256']
        with pymupdf.open(path) as pdf:
            page=pdf[pno-1];page.get_pixmap(dpi=180).save(directory/f'{sid}-P{pno}.png')
        matches=[c for c in native[sid] if any(int(pos[0])==pno for pos in c.get('positions',[]) if pos)]
        has_positions=any(c.get('positions') for c in native[sid])
        inspected.append({**item,'native_page_mapping_available':has_positions,
            'native_chunks_on_page':len(matches) if has_positions else None,
            'native_characters':sum(len(c.get('content','')) for c in matches) if has_positions else None})
    worker=directory/'worker.py'
    worker.write_text('''import os,json
os.environ['ORT_NUM_THREADS']='2'
from pathlib import Path
from PIL import Image
import numpy as np
from deepdoc.vision import OCR
ocr=OCR();out=[]
for p in sorted(Path('/tmp/icp-final-ocr').glob('*.png')):
 rows=ocr(np.array(Image.open(p).convert('RGB')))
 out.append({'image':p.name,'lines':[{'box':r[0],'text':r[1][0],'confidence':float(r[1][1])} for r in rows]})
 Path('/tmp/icp-final-ocr/result.json').write_text(json.dumps(out,ensure_ascii=False),encoding='utf-8')
 print(p.name,flush=True)
''',encoding='utf-8')
    if not (directory/'result.json').exists():
        subprocess.run([DOCKER,'cp',str(directory),'icp-ragflow-ragflow-cpu-1:/tmp/icp-final-ocr'],check=True)
        subprocess.run([DOCKER,'exec','icp-ragflow-ragflow-cpu-1','python','/tmp/icp-final-ocr/worker.py'],check=True)
        subprocess.run([DOCKER,'cp','icp-ragflow-ragflow-cpu-1:/tmp/icp-final-ocr/result.json',str(directory/'result.json')],check=True)
    ocr=json.loads((directory/'result.json').read_text(encoding='utf-8'))
    normalize=lambda t:re.sub(r'\s+','',t).casefold()
    for p in inspected:
        o=next(o for o in ocr if o['image']==f"{p['document_id']}-P{p['page']}.png")
        corpus=normalize('\n'.join(c.get('content','') for c in native[p['document_id']]))
        phrases=[normalize(l['text']) for l in o['lines'] if len(normalize(l['text']))>=12]
        p['native_exact_line_matches']=sum(t in corpus for t in phrases)
        p['native_checked_lines']=len(phrases)
        p['native_inspection_note']='无页码元数据时仅比较正文片段；零匹配不证明完全未识别'
    report={'date':datetime.now().isoformat(),'corpus_version':kb['version'],'pages':inspected,
            'ocr':ocr,'reviewed':False,
            'method':'RAGFlow native chunk inspection plus DeepDoc OCR; original PDFs unchanged; manual correction required'}
    (ROOT/'outputs/source-ocr-candidates.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('OCR candidates saved; not yet admitted to the knowledge index.')


if __name__=='__main__':main()
