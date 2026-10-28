"""Update only this project's existing planner prompt and keep its API key."""
from pathlib import Path
import hashlib
import json
import sys
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'backend'))
from scripts.dify_bootstrap import DifyConsole
from icp.planner import PLANNER_PROMPT


def main():
    state=json.loads((ROOT/'.secrets/dify-main.json').read_text(encoding='utf-8'))
    console=DifyConsole()
    try:
        console.login();path='/apps/'+state['app_id']+'/workflows'
        draft=console.request('GET',path+'/draft')
        backup=ROOT/'.secrets/dify-main-before-open-questions.json'
        if not backup.exists():backup.write_text(json.dumps(draft,ensure_ascii=False),encoding='utf-8')
        nodes=[n for n in draft['graph']['nodes'] if n.get('id')=='planner']
        if len(nodes)!=1:raise RuntimeError('Expected one project planner node; no workflow changed')
        messages=nodes[0]['data']['prompt_template']
        system=[m for m in messages if m.get('role')=='system']
        if len(system)!=1:raise RuntimeError('Expected one system prompt; no workflow changed')
        system[0]['text']=PLANNER_PROMPT
        profile_path=ROOT/'outputs/model-resource-profile.json'
        if profile_path.is_file():
            profile=json.loads(profile_path.read_text(encoding='utf-8'))
            nodes[0]['data']['model']['completion_params']['num_gpu']=int(profile['gpu_layers'])
        template_path=ROOT/'deploy/ICP_Main_Agent.yml'
        template=yaml.safe_load(template_path.read_text(encoding='utf-8'))
        exported=[n for n in template['workflow']['graph']['nodes'] if n.get('id')=='planner']
        if len(exported)!=1:raise RuntimeError('Expected one exported planner node; no workflow changed')
        exported_system=[m for m in exported[0]['data']['prompt_template'] if m.get('role')=='system']
        if len(exported_system)!=1:raise RuntimeError('Expected one exported system prompt; no workflow changed')
        exported_system[0]['text']=PLANNER_PROMPT
        exported[0]['data']['model']['completion_params']['num_gpu']=nodes[0]['data']['model']['completion_params'].get('num_gpu',36)
        console.request('POST',path+'/draft',json={'graph':draft['graph'],'features':draft['features'],'hash':draft.get('hash'),'conversation_variables':draft.get('conversation_variables',[])})
        console.request('POST',path+'/publish',json={'marked_name':'ICP open questions'})
        template_path.write_text(yaml.safe_dump(template,allow_unicode=True,sort_keys=False),encoding='utf-8')
        report={'published':True,'scope':'Existing planner prompt and declared GPU layer profile; app identity and API credentials retained','prompt_sha256':hashlib.sha256(PLANNER_PROMPT.encode()).hexdigest()}
        (ROOT/'outputs/planner-update.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('Project planner updated and published; credentials retained privately.')
    finally:console.client.close()


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('Planner update stopped; error type='+type(exc).__name__)
        raise SystemExit(1)
