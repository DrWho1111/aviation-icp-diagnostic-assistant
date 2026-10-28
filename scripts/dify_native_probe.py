"""Exercise native pause/resume in an isolated Dify test app, never the live app."""
import copy
import argparse
import json
from pathlib import Path
import sys
import time
import httpx
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.dify_bootstrap import DifyConsole


def main(native_loop=False):
    template=yaml.safe_load((ROOT/'deploy/dify-tools/End.yml').read_text(encoding='utf-8'))
    template['app']['name']='ICP_Native_Pause_Probe'
    graph=template['workflow']['graph'];start=copy.deepcopy(graph['nodes'][0]);start['data']['variables']=[]
    def node(nid,data,x):
        return {'id':nid,'type':'custom','position':{'x':x,'y':100},'positionAbsolute':{'x':x,'y':100},'width':244,'height':100,'data':{'selected':False,'desc':'',**data},'sourcePosition':'right','targetPosition':'left'}
    human=node('confirm',{'type':'human-input','title':'测试确认','form_content':'请选择测试功能 {{#$output.function_id#}}',
        'inputs':[{'type':'select','output_variable_name':'function_id','option_source':{'type':'constant','value':['FUNC-410','FUNC-420']}}],
        'user_actions':[{'id':'approve','title':'确认','button_style':'primary'}],
        'delivery_methods':[{'type':'webapp','enabled':True,'config':{}}],'timeout':1,'timeout_unit':'hour'},300)
    end=node('end',{'type':'end','title':'结束','outputs':[{'variable':'result','value_selector':['confirm','function_id'],'value_type':'string'}]},600)
    edge=lambda a,b,h='source':{'id':a+'-'+h+'-'+b,'source':a,'target':b,'sourceHandle':h,'targetHandle':'target','type':'custom','data':{'sourceType':'start' if a=='start' else 'human-input','targetType':'end' if b=='end' else 'human-input','isInIteration':False,'isInLoop':False}}
    graph['nodes']=[start,human,end];graph['edges']=[edge('start','confirm'),edge('confirm','end','approve'),edge('confirm','end','timeout')]
    if native_loop:
        template['app']['name']='ICP_Native_Loop_Pause_Probe'
        loop=node('loop',{'type':'loop','title':'原生循环','start_node_id':'loop-start','loop_count':1,
            'break_conditions':[],'logical_operator':'and','loop_variables':[{'label':'selected','var_type':'string','value_type':'constant','value':''}]},300)
        begin=node('loop-start',{'type':'loop-start','title':'循环开始'},40)
        assign=node('assign',{'type':'assigner','version':'2','title':'保存确认值','items':[{'variable_selector':['loop','selected'],'input_type':'variable','operation':'over-write','value':['confirm','function_id']}]},600)
        for n in [begin,human,assign]:
            n['parentId']='loop';n['extent']='parent';n['data']['loop_id']='loop'
        loop.update(width=950,height=400)
        end['data']['outputs'][0]['value_selector']=['loop','selected']
        graph['nodes']=[start,loop,begin,human,assign,end]
        graph['edges']=[edge('start','loop'),edge('loop','end'),edge('loop-start','confirm'),edge('confirm','assign','approve')]
        types={n['id']:n['data']['type'] for n in graph['nodes']}
        for e in graph['edges']:
            e['data'].update(sourceType=types[e['source']],targetType=types[e['target']],isInLoop=e['source'] in {'loop-start','confirm'})
    template['workflow']['environment_variables']=[]
    folder=ROOT/'deploy/native';folder.mkdir(exist_ok=True)
    prefix='loop-pause-probe' if native_loop else 'pause-probe'
    (folder/(prefix+'.yml')).write_text(yaml.safe_dump(template,allow_unicode=True,sort_keys=False),encoding='utf-8')
    console=DifyConsole();statefile=ROOT/('.secrets/dify-native-'+prefix+'.json')
    try:
        console.login()
        if not statefile.exists():statefile.write_text(json.dumps(console.import_tool(template['app']['name'],template)),encoding='utf-8')
    finally:console.client.close()
    state=json.loads(statefile.read_text(encoding='utf-8'));events=[];form=None;runid=None
    with httpx.Client(base_url='http://127.0.0.1:8081/v1',headers={'Authorization':'Bearer '+state['api_key']},timeout=45,trust_env=False) as client:
        with client.stream('POST','/workflows/run',json={'inputs':{},'response_mode':'streaming','user':'icp-native-probe'}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith('data: '):continue
                value=json.loads(line[6:]);events.append(value.get('event'))
                runid=value.get('workflow_run_id') or runid
                if value.get('event')=='human_input_required':form=value;break
                if value.get('event') in {'error','workflow_finished','workflow_paused'}:break
        (ROOT/'.secrets/native-probe-events.json').write_text(json.dumps({'form':form,'run_id':runid}),encoding='utf-8')
        report={'date':'2026-10-24','scope':'isolated native human-input test; not production diagnosis','events':events,'paused':bool(form),'resumed_same_run':False,'passed':False}
        if form:
            data=form.get('data',form);token=data.get('form_token') or form.get('form_token')
            if token:
                client.get('/form/human_input/'+token).raise_for_status()
                client.post('/form/human_input/'+token,json={'action':'approve','inputs':{'function_id':'FUNC-410'},'user':'icp-native-probe'}).raise_for_status()
                for _ in range(30):
                    result=client.get('/workflows/run/'+runid).raise_for_status().json()
                    if result.get('status') in {'succeeded','failed','stopped'}:break
                    time.sleep(.5)
                report.update(resumed_same_run=result.get('id')==runid,final_status=result.get('status'),result=result.get('outputs'))
                report['passed']=result.get('status')=='succeeded' and report['resumed_same_run'] and result.get('outputs',{}).get('result')=='FUNC-410'
        (ROOT/('outputs/dify-native-'+prefix+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--loop',action='store_true')
    try:main(parser.parse_args().loop)
    except Exception as exc:
        print('Native test stopped; error type='+type(exc).__name__)
        raise SystemExit(1)
