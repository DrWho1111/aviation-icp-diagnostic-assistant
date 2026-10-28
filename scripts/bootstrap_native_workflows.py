"""Publish native workflows using deployment-specific endpoints and private keys."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.dify_bootstrap import DifyConsole
from scripts.dify_native_main import build as main_graph
from scripts.dify_native_diagnosis import build as diagnostic_graph


def bootstrap(update=False):
    settings=dotenv_values(ROOT/'.env');token=settings.get('INTERNAL_TOOL_TOKEN')
    if not token:raise RuntimeError('Configure the private internal tool credential first')
    base=os.getenv('ICP_TOOL_SERVICE_URL','http://host.docker.internal:8000')
    if not base.startswith('http://host.docker.internal:'):raise ValueError('Review the local deployment callback before using a different host')
    console=DifyConsole()
    try:
        console.login()
        for name,builder,filename in [('ICP_Main_Agent_Native',main_graph,'dify-native-main-production.json'),
                ('ICP_Fault_Reasoning_Native',diagnostic_graph,'dify-native-diagnosis-production.json')]:
            path=ROOT/'.secrets'/filename
            if path.exists() and not update:
                print(json.dumps({'app':name,'status':'existing_private_mapping_retained'}));continue
            document=copy.deepcopy(builder());document['app']['name']=name
            for variable in document['workflow']['environment_variables']:
                if variable['name']=='INTERNAL_TOOL_TOKEN':variable['value']=token
                if variable['name']=='TOOL_SERVICE_URL':variable['value']=base
            if path.exists():
                state=json.loads(path.read_text(encoding='utf-8'));route='/apps/'+state['app_id']+'/workflows'
                draft=console.request('GET',route+'/draft')
                console.request('POST',route+'/draft',json={'graph':document['workflow']['graph'],'features':draft['features'],
                    'hash':draft.get('hash'),'conversation_variables':draft.get('conversation_variables',[])})
                console.request('POST',route+'/draft/environment-variables',json={'environment_variables':document['workflow']['environment_variables'],'patch':True})
                console.request('POST',route+'/publish',json={'marked_name':'native deployment'})
            else:state=console.import_tool(name,document)
            path.write_text(json.dumps(state),encoding='utf-8')
            print(json.dumps({'app':name,'status':'published_not_yet_verified'}),flush=True)
    finally:console.client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--update',action='store_true')
    try:bootstrap(parser.parse_args().update)
    except Exception as exc:print('Native bootstrap stopped; error type='+type(exc).__name__);raise SystemExit(1)
