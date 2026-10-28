"""Build Dify-owned bounded ReAct loop with native parameter/functional confirmation."""
import argparse
import copy
import json
from pathlib import Path
import sys
import yaml
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.dify_bootstrap import DifyConsole
from scripts.dify_native_diagnosis import build as diagnosis_graph


def build():
    template=yaml.safe_load((ROOT/'deploy/dify-tools/End.yml').read_text(encoding='utf-8'))
    template['app']['name']='ICP_Native_ReAct_Validation';original={n['id']:n for n in template['workflow']['graph']['nodes']}
    nodes=[copy.deepcopy(original['start'])];edges=[]
    def node(nid,data,inside=True):
        n={'id':nid,'type':'custom','position':{'x':len(nodes)*100,'y':100},'positionAbsolute':{'x':len(nodes)*100,'y':100},'width':244,'height':100,
            'data':{'selected':False,'desc':'',**data},'sourcePosition':'right','targetPosition':'left'}
        if inside:n.update(parentId='loop',extent='parent');n['data']['loop_id']='loop'
        nodes.append(n);return n
    def code(nid,title,source,variables,outputs,inside=True):
        return node(nid,{'type':'code','title':title,'code_language':'python3','code':source,
            'variables':[{'variable':k,'value_selector':v} for k,v in variables.items()],
            'outputs':{k:{'type':v,'children':None} for k,v in outputs.items()}},inside)
    def edge(a,b,handle='source'):
        types={n['id']:n['data']['type'] for n in nodes}
        edges.append({'id':a+'-'+handle+'-'+b,'type':'custom','source':a,'sourceHandle':handle,'target':b,'targetHandle':'target',
            'data':{'sourceType':types[a],'targetType':types[b],'isInIteration':False,'isInLoop':a not in {'start','init','loop'}}})
    def http(nid,operation,selector):
        data=copy.deepcopy(original['request']['data']);data.update(title=operation,url='{{#env.TOOL_SERVICE_URL#}}/internal/react/'+operation)
        data['body']['data'][0]['value']='{{#'+'.'.join(selector)+'#}}'
        data['retry_config']={'retry_enabled':True,'max_retries':1,'retry_interval':200}
        return node(nid,data)
    code('init','绑定应用任务','''import json
def main(input: str):
 p=json.loads(input)
 return {'root':p['root_run_id'],'body':json.dumps({'input':json.dumps({'root_run_id':p['root_run_id']})})}
''',{'input':['start','input']},{'root':'string','body':'string'},False)
    node('loop',{'type':'loop','title':'单工具ReAct循环（最多12轮）','start_node_id':'loop-start','loop_count':12,
        'logical_operator':'and','break_conditions':[{'variable_selector':['loop','done'],'comparison_operator':'is','value':'1'}],
        'loop_variables':[{'label':'done','var_type':'string','value_type':'constant','value':'0'}]},False)
    node('loop-start',{'type':'loop-start','title':'本轮开始'})
    http('plan','plan',['init','body'])
    code('decision','读取经校验的单工具决策','''import json
def main(body: str):
 p=json.loads(json.loads(body)['result'])
 return {'kind':p['kind'],'action_id':p.get('action_id',''),'diagnosis_input':p.get('diagnosis_input','{}'),'note':p.get('note','')}
''',{'body':['plan','body']},{'kind':'string','action_id':'string','diagnosis_input':'string','note':'string'})
    node('dispatch',{'type':'if-else','title':'只执行本轮相关动作','cases':[{'case_id':k,'logical_operator':'and','conditions':[{'variable_selector':['decision','kind'],'comparison_operator':'is','value':k}]} for k in ['done','execute','diagnosis','input']]})
    code('action_body','绑定本轮动作','''import json
def main(root: str, action: str):return {'body':json.dumps({'input':json.dumps({'root_run_id':root,'action_id':action})})}
''',{'root':['init','root'],'action':['decision','action_id']},{'body':'string'})
    http('execute','execute',['action_body','body'])
    code('executed','记录观察后继续','''import json
def main(body: str):return {'done':json.loads(json.loads(body)['result'])['done']}
''',{'body':['execute','body']},{'done':'string'})
    code('finished','信息足够或边界停止',"def main():return {'done':'1'}",{},{'done':'string'})
    node('additional',{'type':'human-input','title':'缺参先询问','form_content':'{{#decision.note#}}\n{{#$output.additional_input#}}',
        'inputs':[{'type':'paragraph','output_variable_name':'additional_input'}],
        'user_actions':[{'id':'supply','title':'补充后继续','button_style':'primary'}],
        'delivery_methods':[{'type':'webapp','enabled':True,'config':{}}],'timeout':24,'timeout_unit':'hour'})
    http('continue','continue',['init','body'])
    code('continued','补参后进入下一轮',"def main(body: str):return {'done':'0'}",{'body':['continue','body']},{'done':'string'})
    diag=diagnosis_graph()['workflow']['graph'];oldids={n['id'] for n in diag['nodes']}
    def rewrite(value):
        if isinstance(value,dict):return {k:rewrite(v) for k,v in value.items()}
        if isinstance(value,list):
            if value and isinstance(value[0],str) and value[0] in oldids:return ['diag_'+value[0],*value[1:]]
            return [rewrite(v) for v in value]
        if isinstance(value,str):
            for nid in oldids:value=value.replace('{{#'+nid+'.','{{#diag_'+nid+'.')
        return value
    for n in diag['nodes']:
        if n['id'] in {'start','end'}:continue
        old=n['id'];n=rewrite(copy.deepcopy(n));n['id']='diag_'+old;n.update(parentId='loop',extent='parent');n['data']['loop_id']='loop'
        if old=='input':n['data']['variables']=[{'variable':'input','value_selector':['decision','diagnosis_input']}]
        nodes.append(n)
    for e in diag['edges']:
        if e['source']=='start' or e['target']=='end':continue
        e=copy.deepcopy(e);e.update(id='diag_'+e['id'],source='diag_'+e['source'],target='diag_'+e['target']);e['data']['isInLoop']=True;edges.append(e)
    code('observation','提交四路实际观察','''import json
def main(root: str, action: str, result: str):return {'body':json.dumps({'input':json.dumps({'root_run_id':root,'action_id':action,'result':result},ensure_ascii=False)},ensure_ascii=False)}
''',{'root':['init','root'],'action':['decision','action_id'],'result':['diag_merge','result']},{'body':'string'})
    http('observe','observe',['observation','body'])
    code('observed','根据分支状态继续或停止','''import json
def main(body: str):return {'done':json.loads(json.loads(body)['result'])['done']}
''',{'body':['observe','body']},{'done':'string'})
    for source in ['executed','finished','continued','observed']:
        node('assign-'+source,{'type':'assigner','version':'2','title':'更新循环停止条件',
            'items':[{'variable_selector':['loop','done'],'input_type':'variable','operation':'over-write','value':[source,'done']}]})
    node('end',{'type':'end','title':'任务状态已持久化','outputs':[{'variable':'root_run_id','value_selector':['init','root']}]},False)
    for a,b in [('start','init'),('init','loop'),('loop','end'),('loop-start','plan'),('plan','decision'),('decision','dispatch'),
                ('action_body','execute'),('execute','executed'),('continue','continued'),('diag_merge','observation'),('observation','observe'),('observe','observed')]:edge(a,b)
    for case,target in [('done','finished'),('execute','action_body'),('diagnosis','diag_input'),('input','additional')]:edge('dispatch',target,case)
    edge('additional','continue','supply')
    for source in ['executed','finished','continued','observed']:edge(source,'assign-'+source)
    import networkx as nx
    children={n['id']:n for n in nodes if n.get('parentId')=='loop'}
    layout=nx.DiGraph();layout.add_nodes_from(children)
    layout.add_edges_from((e['source'],e['target']) for e in edges if e['source'] in children and e['target'] in children)
    generations=list(nx.topological_generations(layout))
    for level,group in enumerate(generations):
        for lane,nid in enumerate(sorted(group)):
            children[nid]['position']={'x':40+level*320,'y':70+lane*170}
            children[nid]['positionAbsolute']={'x':740+level*320,'y':170+lane*170}
    frame=next(n for n in nodes if n['id']=='loop');frame.update(width=len(generations)*320+100,height=max(map(len,generations))*170+180)
    frame['position']={'x':700,'y':100};frame['positionAbsolute']=frame['position']
    end=next(n for n in nodes if n['id']=='end');end['position']={'x':1000+frame['width'],'y':100};end['positionAbsolute']=end['position']
    template['workflow']['graph'].update(nodes=nodes,edges=edges)
    return template


def main(update=False):
    template=build();(ROOT/'deploy/native/ICP_Main_Agent.yml').write_text(yaml.safe_dump(template,allow_unicode=True,sort_keys=False),encoding='utf-8')
    path=ROOT/'.secrets/dify-native-main.json'
    if path.exists() and not update:print('Native main test app already exists.');return
    env=dotenv_values(ROOT/'.env');private=copy.deepcopy(template)
    for v in private['workflow']['environment_variables']:
        if v['name']=='INTERNAL_TOOL_TOKEN':v['value']=env['INTERNAL_TOOL_TOKEN']
        if v['name']=='TOOL_SERVICE_URL':v['value']='http://host.docker.internal:8002'
    c=DifyConsole()
    try:
        c.login()
        if path.exists():
            state=json.loads(path.read_text());route='/apps/'+state['app_id']+'/workflows';draft=c.request('GET',route+'/draft')
            c.request('POST',route+'/draft',json={'graph':private['workflow']['graph'],'features':draft['features'],'hash':draft.get('hash'),'conversation_variables':draft.get('conversation_variables',[])})
            c.request('POST',route+'/publish',json={'marked_name':'native bounded ReAct'})
        else:state=c.import_tool(template['app']['name'],private)
        path.write_text(json.dumps(state),encoding='utf-8')
    finally:c.client.close()
    print('Native bounded ReAct app published for isolated testing; production unchanged.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--update',action='store_true')
    try:main(parser.parse_args().update)
    except Exception as exc:print('Native main creation stopped; error type='+type(exc).__name__);raise SystemExit(1)
