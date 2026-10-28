"""Prepare isolated validation apps and private environment; leave production keys unchanged."""
import json
from pathlib import Path
import sys
import yaml
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.dify_bootstrap import DifyConsole


def main():
    env=dict(dotenv_values(ROOT/'.env'));kb=json.loads((ROOT/'data/processed/knowledge.json').read_text(encoding='utf-8'))
    state=json.loads((ROOT/('.secrets/ragflow-import-'+kb['version']+'.json')).read_text(encoding='utf-8'))
    expected=len(kb['chunks'])+len(kb['entity_chunks'])
    if sum(len(v) for v in state['chunks'].values())!=expected:raise RuntimeError('Finish the isolated index first')
    private=ROOT/'.secrets/validation-apps.json';apps=json.loads(private.read_text()) if private.exists() else {}
    console=DifyConsole()
    try:
        console.login()
        for path in sorted((ROOT/'deploy/dify-tools').glob('*.yml')):
            name=path.stem
            if name in apps:continue
            template=yaml.safe_load(path.read_text(encoding='utf-8'))
            for value in template['workflow']['environment_variables']:
                if value['name']=='TOOL_SERVICE_URL':value['value']='http://host.docker.internal:8002'
                if value['name']=='INTERNAL_TOOL_TOKEN':value['value']=env['INTERNAL_TOOL_TOKEN']
            apps[name]=console.import_tool('ICP_Validation_'+name,template)
            private.write_text(json.dumps(apps),encoding='utf-8')
        native=json.loads((ROOT/'.secrets/dify-native-diagnosis.json').read_text())
        route='/apps/'+native['app_id']+'/workflows'
        draft=console.request('GET',route+'/draft')
        value=next(v for v in draft['environment_variables'] if v['name']=='TOOL_SERVICE_URL')
        value['value']='http://host.docker.internal:8002'
        console.request('POST',route+'/draft/environment-variables',json={'environment_variables':[value],'patch':True})
        console.request('POST',route+'/publish',json={'marked_name':'isolated validation'})
    finally:console.client.close()
    (ROOT/'.secrets/validation-tool-keys.json').write_text(json.dumps({n:v['api_key'] for n,v in apps.items()}),encoding='utf-8')
    synthetic=[v for n,v in state['datasets'].items() if n not in {'research','entity'} and not n.startswith('original-')]
    env.update({'DATABASE_URL':'sqlite:////app/data/runtime/validation.sqlite3','READ_KB_FROM_POSTGRES':'false','GRAPH_BACKEND':'local',
        'LLM_BASE_URL':'http://host.docker.internal:11434/v1','DIFY_BASE_URL':'http://host.docker.internal:8081/v1',
        'RAGFLOW_BASE_URL':'http://host.docker.internal:9380','DIFY_TOOL_KEYS_FILE':'/run/secrets/tool_keys',
        'DIFY_NATIVE_DIAGNOSIS_KEYS_FILE':'/run/secrets/native_diagnosis',
        'RAGFLOW_SYNTHETIC_DATASET_IDS':','.join(synthetic),'RAGFLOW_DATASET_IDS':','.join(synthetic),
        'RAGFLOW_RESEARCH_DATASET_IDS':state['datasets']['research'],'RAGFLOW_ENTITY_DATASET_IDS':state['datasets']['entity'],
        'RAGFLOW_FUNCTION_DOCUMENT_IDS':state['function_document_id']})
    if (ROOT/'.secrets/dify-native-main.json').exists():env['DIFY_NATIVE_MAIN_KEYS_FILE']='/run/secrets/native_main'
    (ROOT/'.secrets/validation.env').write_text('\n'.join(k+'='+str(v) for k,v in env.items() if v is not None)+'\n',encoding='utf-8')
    print('Ten isolated validation workflows and private runtime configuration prepared; production untouched.')


if __name__=='__main__':
    try:main()
    except Exception as exc:print('Validation preparation stopped; error type='+type(exc).__name__);raise SystemExit(1)
