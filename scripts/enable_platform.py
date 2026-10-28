"""Bind privately generated platform mappings; configuration is not verification."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.configure_platforms import replace_env


def configure(root,native=False):
    root=Path(root);private=root/'.secrets'
    def read(name):return json.loads((private/name).read_text(encoding='utf-8'))
    knowledge=json.loads((root/'data/processed/knowledge.json').read_text(encoding='utf-8'))
    state=read('ragflow-import.json')
    if state.get('version')!=knowledge['version']:raise ValueError('Import checkpoint is not the current knowledge version')
    if sum(len(v) for v in state.get('chunks',{}).values())!=len(knowledge['chunks'])+len(knowledge['entity_chunks']):
        raise ValueError('Complete curated knowledge import first')
    datasets=state['datasets'];keys=read('dify-tools.json');planner=read('dify-main.json')
    expected={'Hybrid_Search','Function_Locator','HMC_Query','MFL_PFL_Query','FMECA_Query','FTA_Query','RBD_Query','Fault_History_Query','Fault_Reasoning_Engine','End'}
    if set(keys)!=expected or any(not isinstance(v,str) or not v for v in keys.values()):raise ValueError('Ten tool mappings are required')
    if not planner.get('api_key') or not state.get('function_document_id'):raise ValueError('Planner or function index mapping is missing')
    synthetic=[did for name,did in datasets.items() if name not in {'research','entity'} and not name.startswith('original-')]
    if not synthetic or not datasets.get('research') or not datasets.get('entity'):raise ValueError('Required corpus groups are missing')
    changes={'TOOL_BACKEND':'dify','DIFY_AGENT_API_KEY':planner['api_key'],
        'DIFY_TOOL_KEYS_FILE':(private/'dify-tools.json').resolve().as_posix(),
        'RAGFLOW_DATASET_IDS':','.join(synthetic),'RAGFLOW_SYNTHETIC_DATASET_IDS':','.join(synthetic),
        'RAGFLOW_RESEARCH_DATASET_IDS':datasets['research'],'RAGFLOW_ENTITY_DATASET_IDS':datasets['entity'],
        'RAGFLOW_FUNCTION_DOCUMENT_IDS':state['function_document_id']}
    if native:
        for variable,filename in [('DIFY_NATIVE_MAIN_KEYS_FILE','dify-native-main-production.json'),
                ('DIFY_NATIVE_DIAGNOSIS_KEYS_FILE','dify-native-diagnosis-production.json')]:
            if not read(filename).get('api_key'):raise ValueError('Native workflow mapping is missing')
            changes[variable]=(private/filename).resolve().as_posix()
    target=root/'.env'
    target.write_text(replace_env(target.read_text(encoding='utf-8'),changes),encoding='utf-8')
    return {'stage':'configured_not_probed','native':native,'curated_chunks':sum(len(v) for v in state['chunks'].values()),
        'note':'Restart the app and run actual platform/retrieval/workflow checks; no verification is inferred here.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native',action='store_true')
    try:print(json.dumps(configure(ROOT,parser.parse_args().native)))
    except Exception as exc:print('Platform binding stopped; error type='+type(exc).__name__);raise SystemExit(1)
