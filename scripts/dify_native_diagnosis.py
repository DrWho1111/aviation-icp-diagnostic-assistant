"""Build a native locator/confirmation/four-branch diagnostic workflow for isolated validation."""
import copy
import argparse
import json
from pathlib import Path
import sys
import yaml
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.dify_bootstrap import DifyConsole


def build():
    template=yaml.safe_load((ROOT/'deploy/dify-tools/End.yml').read_text(encoding='utf-8'))
    template['app']['name']='ICP_Native_Diagnosis_Validation'
    original={n['id']:n for n in template['workflow']['graph']['nodes']}
    nodes=[];edges=[]
    def node(nid,data,x=0,y=100):
        v={'id':nid,'type':'custom','position':{'x':x,'y':y},'positionAbsolute':{'x':x,'y':y},'width':244,'height':100,
           'data':{'selected':False,'desc':'',**data},'sourcePosition':'right','targetPosition':'left'}
        nodes.append(v);return v
    def edge(a,b,handle='source'):
        types={n['id']:n['data']['type'] for n in nodes}
        edges.append({'id':a+'-'+handle+'-'+b,'type':'custom','source':a,'sourceHandle':handle,'target':b,'targetHandle':'target',
            'data':{'sourceType':types[a],'targetType':types[b],'isInIteration':False,'isInLoop':False}})
    def code(nid,title,text,variables,outputs,x):
        return node(nid,{'type':'code','title':title,'code_language':'python3','code':text,
            'variables':[{'variable':k,'value_selector':v} for k,v in variables.items()],
            'outputs':{k:{'type':t,'children':None} for k,t in outputs.items()}},x)
    def http(nid,tool,selector,x,y=100):
        data=copy.deepcopy(original['request']['data']);data.update(title=tool,url='{{#env.TOOL_SERVICE_URL#}}/internal/tools/'+tool)
        data['body']['data'][0]['value']='{{#'+'.'.join(selector)+'#}}'
        # This graph owns the one retry; backend nested transport is single-attempt.
        data['retry_config']={'retry_enabled':True,'max_retries':1,'retry_interval':200}
        if tool!='Function_Locator':
            fallback={'result':json.dumps({'status':'error','data':[],'evidence':[],'missing_fields':[],
                       'coverage':{},'error':'分支服务重试后仍未完成'},ensure_ascii=False)}
            data.update(error_strategy='default-value',default_value=[{'key':'body','type':'string','value':json.dumps(fallback,ensure_ascii=False)}])
        return node(nid,data,x,y)
    nodes.append(copy.deepcopy(original['start']))
    code('input','定位请求','''import json
def main(input: str):
 try:p=json.loads(input)
 except json.JSONDecodeError:p={'query':input}
 if isinstance(p,str):p={'query':p}
 if not isinstance(p,dict) or not isinstance(p.get('query'),str) or not p['query'].strip():raise ValueError('Nonempty diagnostic question required')
 return {'body':json.dumps({'input':json.dumps({'query':p['query']},ensure_ascii=False)},ensure_ascii=False),'query':p['query']}
''',{'input':['start','input']},{'body':'string','query':'string'},300)
    http('locate','Function_Locator',['input','body'],600)
    code('located','候选与原始来源','''import json
def main(body: str):
 r=json.loads(json.loads(body)['result'])
 if r['status']!='ok' or len(r['data'])!=3:raise ValueError('Three sourced candidates required')
 return {'ids':[x['id'] for x in r['data']],'candidates':json.dumps(r,ensure_ascii=False)}
''',{'body':['locate','body']},{'ids':'array[string]','candidates':'string'},900)
    node('confirm',{'type':'human-input','title':'用户确认失效功能','form_content':'请根据现象确认功能。\n{{#located.candidates#}}\n{{#$output.function_id#}}',
        'inputs':[{'type':'select','output_variable_name':'function_id','option_source':{'type':'variable','selector':['located','ids'],'value':[]}}],
        'user_actions':[{'id':'approve','title':'确认并检索','button_style':'primary'}],
        'delivery_methods':[{'type':'webapp','enabled':True,'config':{}}],'timeout':24,'timeout_unit':'hour'},1200)
    code('arguments','确认后准备分支','''import json
def main(function_id: str, candidates: str, query: str):
 located=json.loads(candidates)
 function=next(r for r in located['data'] if r['id']==function_id)
 out={'function':json.dumps(function,ensure_ascii=False),'version':located['corpus_version']}
 for n in ['FTA_Query','FMECA_Query','RBD_Query','Fault_History_Query']:
  q=function_id if n in ['FTA_Query','RBD_Query'] else query
  out[n]=json.dumps({'input':json.dumps({'query':q,'function_id':function_id},ensure_ascii=False)},ensure_ascii=False)
 return out
''',{'function_id':['confirm','function_id'],'candidates':['located','candidates'],'query':['input','query']},
        {n:'string' for n in ['function','version','FTA_Query','FMECA_Query','RBD_Query','Fault_History_Query']},1500)
    branches=['FTA_Query','FMECA_Query','RBD_Query','Fault_History_Query']
    for i,n in enumerate(branches):http(n,n,['arguments',n],1800,i*150)
    code('merge','合并四路实际结果','''import json
def main(function: str, version: str, fta: str, fmeca: str, rbd: str, history: str):
 names=['FTA_Query','FMECA_Query','RBD_Query','Fault_History_Query']
 values=[]
 for body in [fta,fmeca,rbd,history]:
  try:
   v=json.loads(json.loads(body)['result'])
   if not isinstance(v,dict) or v.get('status') not in ['ok','not_found','not_covered','error','needs_input'] or not isinstance(v.get('evidence'),list):raise ValueError('Invalid branch shape')
   if v['status']=='error' and not v.get('corpus_version'):v['corpus_version']=version
   if v.get('corpus_version')!=version:raise ValueError('Knowledge version differs')
  except (ValueError,KeyError,TypeError):
   v={'status':'error','data':[],'evidence':[],'missing_fields':[],'coverage':{},'error':'分支返回格式或知识版本校验失败','corpus_version':version}
  values.append(v)
 failed=[n for n,v in zip(names,values) if v['status']=='error']
 missing=[n for n,v in zip(names,values) if v['status']=='needs_input']
 result={'status':'error' if failed else 'needs_input' if missing else 'ok','data':{'function':json.loads(function),'branches':dict(zip(names,values))},
  'evidence':list({e['id']:e for v in values for e in v['evidence']}.values()),'missing_fields':[],
  'coverage':{n:v['status'] for n,v in zip(names,values)},'error':'、'.join(failed) if failed else None,'corpus_version':version}
 return {'result':json.dumps(result,ensure_ascii=False)}
''',{'function':['arguments','function'],'version':['arguments','version'],'fta':['FTA_Query','body'],'fmeca':['FMECA_Query','body'],'rbd':['RBD_Query','body'],'history':['Fault_History_Query','body']},{'result':'string'},2100)
    node('end',{'type':'end','title':'输出四路诊断证据','outputs':[{'variable':'result','value_selector':['merge','result']}]},2400)
    for a,b in [('start','input'),('input','locate'),('locate','located'),('located','confirm')]:edge(a,b)
    edge('confirm','arguments','approve')
    for n in branches:edge('arguments',n);edge(n,'merge')
    edge('merge','end')
    template['workflow']['graph'].update(nodes=nodes,edges=edges)
    return template


def main(update=False):
    template=build();directory=ROOT/'deploy/native';directory.mkdir(exist_ok=True)
    (directory/'Fault_Reasoning_Engine.yml').write_text(yaml.safe_dump(template,allow_unicode=True,sort_keys=False),encoding='utf-8')
    path=ROOT/'.secrets/dify-native-diagnosis.json'
    if path.exists() and not update:print('Native diagnostic validation app already exists; no live app changed.');return
    private=copy.deepcopy(template);env=dotenv_values(ROOT/'.env')
    for item in private['workflow']['environment_variables']:
        if item['name']=='INTERNAL_TOOL_TOKEN':item['value']=env['INTERNAL_TOOL_TOKEN']
    console=DifyConsole()
    try:
        console.login()
        if path.exists():
            state=json.loads(path.read_text());route='/apps/'+state['app_id']+'/workflows'
            draft=console.request('GET',route+'/draft')
            console.request('POST',route+'/draft',json={'graph':private['workflow']['graph'],'features':draft['features'],'hash':draft.get('hash'),'conversation_variables':draft.get('conversation_variables',[])})
            console.request('POST',route+'/publish',json={'marked_name':'native validation'})
        else:state=console.import_tool(template['app']['name'],private)
        path.write_text(json.dumps(state),encoding='utf-8')
    finally:console.client.close()
    print('Native diagnostic validation app published separately; production keys unchanged.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--update',action='store_true')
    try:main(parser.parse_args().update)
    except Exception as exc:
        print('Native diagnostic creation stopped; error type='+type(exc).__name__);raise SystemExit(1)
