"""Publish credential-free provenance for completed native graph exports."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def export_manifest():
    knowledge=json.loads((ROOT/'data/processed/knowledge.json').read_text(encoding='utf-8'))
    directory=ROOT/'outputs/scoped-graphs';target=directory/'manifest.json'
    private=ROOT/('.secrets/scoped-graphs-'+knowledge['version']+'.json')
    if not private.exists():
        return json.loads(target.read_text(encoding='utf-8')) if target.exists() else {'units':{}}
    state=json.loads(private.read_text(encoding='utf-8'))
    profile=json.loads((ROOT/'outputs/graph-model-profile.json').read_text(encoding='utf-8'))
    units={}
    for rid,item in state['documents'].items():
        raw=directory/(rid+'.json')
        current=(raw.exists() and not item.get('needs_recompile',False)
            and item.get('execution_model_name')==profile['name']
            and item.get('compiled_template_sha256')==state['groups'][item['group']].get('template_sha256'))
        units[rid]={'current_export_verified':bool(current),'source_packet_sha256':item.get('sha256'),
            'model_name':item.get('execution_model_name'),'model_template_sha256':item.get('execution_model_profile'),
            'compilation_template_sha256':item.get('compiled_template_sha256'),
            'export_sha256':hashlib.sha256(raw.read_bytes()).hexdigest() if raw.exists() else None}
    report={'corpus_version':knowledge['version'],'expected_model_name':profile['name'],'units':units,
        'scope':'execution/configuration provenance only; extraction semantics are audited separately'}
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    value=export_manifest()
    print(json.dumps({'exports':len(value['units']),'current':sum(v['current_export_verified'] for v in value['units'].values())}))
